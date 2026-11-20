using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using XxmiCollector;

// These tests run only portable file/lifecycle behavior. Windows process/WMI probes
// deliberately remain outside this test executable and require Windows qualification.
namespace XxmiCollector
{
    internal static class NativeInventory
    {
        public static void Collect(Process process, string destination, Action<string> failure, Func<bool> canReadProcess = null)
        { throw new InvalidOperationException("Windows probes must not be invoked by portable tests."); }
    }
}

internal static class BackendTests
{
    private static int assertions;
    private static string root;

    private static void Check(bool condition, string description)
    {
        assertions++;
        if (!condition) throw new Exception(description);
    }

    private static object Get(object target, string name)
    { return target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(target); }

    private static void Set(object target, string name, object value)
    { target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value); }

    private static object Call(object target, string name, params object[] args)
    { return target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, args); }

    private static CaptureSession Session(string name, string traces = null)
    {
        var session = (CaptureSession)Activator.CreateInstance(typeof(CaptureSession), true);
        string folder = Path.Combine(root, name);
        Directory.CreateDirectory(folder);
        typeof(CaptureSession).GetProperty("DirectoryPath").SetValue(session, folder, null);
        typeof(CaptureSession).GetProperty("StartedUtc").SetValue(session, DateTime.UtcNow.AddMinutes(-1), null);
        Set(session, "options", new SessionOptions { ProcessId = 42, TraceDirectory = traces });
        Set(session, "manifest", new SessionManifest { StartedUtc = CaptureSession.Utc(session.StartedUtc) });
        Set(session, "stoppedUtc", DateTime.UtcNow);
        return session;
    }

    private static void PermissionAndLifetimeTests()
    {
        Check(CaptureSession.ClassifyLifetime(false, 0) == CaptureSession.ProcessLifetimeState.Unknown,
            "Denied/failed lifetime query remains unknown, not exited.");
        Check(CaptureSession.ClassifyLifetime(false, 123) == CaptureSession.ProcessLifetimeState.Unknown,
            "Failed query cannot consume a stale exit timestamp.");
        Check(CaptureSession.ClassifyLifetime(true, 0) == CaptureSession.ProcessLifetimeState.Running,
            "Zero exit FILETIME confirms running independent of exit-code value.");
        Check(CaptureSession.ClassifyLifetime(true, 123) == CaptureSession.ProcessLifetimeState.Exited,
            "Nonzero exit FILETIME confirms actual exit, including exit code 259.");
        var denied = new UnauthorizedAccessException("Access denied by folder policy");
        string output = Path.Combine(root, "denied-output", "samples.csv");
        try
        {
            CaptureSession.StartupFileOperation("Create capture file", output, () => { throw denied; });
            Check(false, "Denied startup operation must fail.");
        }
        catch (IOException ex)
        {
            Check(ex.Message.Contains("Create capture file") && ex.Message.Contains(output) && ex.Message.Contains(denied.Message),
                "Startup failure identifies precise stage, destination and original error.");
            Check(ReferenceEquals(ex.InnerException, denied), "Startup failure preserves the original exception.");
        }
    }

    private static void SnapshotTests()
    {
        var session = Session("snapshots");
        string source = Path.Combine(root, "source.csv");
        File.WriteAllText(source, "abc");
        var result = new FileEvidence { Status = "excluded" };
        Call(session, "CopyStable", source, "copied.csv", 100L, result, false);
        Check(result.Status == "copied" && result.SizeBytes == 3, "Stable file copies with byte count.");
        Check(result.Sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "SHA256 identifies exact copied bytes.");
        Check(File.ReadAllText(Path.Combine(session.DirectoryPath, "copied.csv")) == "abc", "Snapshot contents preserved.");
        result = new FileEvidence { Status = "excluded" };
        Call(session, "CopyStable", source, "too-big.csv", 2L, result, false);
        Check(result.Status == "excluded" && !File.Exists(Path.Combine(session.DirectoryPath, "too-big.csv")), "Oversize source excluded without partial output.");
        File.WriteAllText(source, "replacement");
        result = new FileEvidence { Status = "excluded" };
        Call(session, "CopyStable", source, "copied.csv", 100L, result, false);
        Check(result.Status == "excluded" && File.ReadAllText(Path.Combine(session.DirectoryPath, "copied.csv")) == "abc", "Unowned destination collision preserves existing file.");
        result = new FileEvidence { Status = "excluded" };
        Call(session, "CopyStable", source, "copied.csv", 100L, result, true);
        Check(result.Status == "copied" && File.ReadAllText(Path.Combine(session.DirectoryPath, "copied.csv")) == "replacement", "Owned snapshot can be replaced atomically.");
        Check(!Directory.GetFiles(session.DirectoryPath, "*.tmp").Any(), "Temporary copies cleaned after success and failure.");
        if (!OperatingSystem.IsWindows())
        {
            string link = Path.Combine(root, "linked.csv");
            File.CreateSymbolicLink(link, source);
            result = new FileEvidence { Status = "excluded" };
            Call(session, "CopyStable", link, "linked-copy.csv", 100L, result, false);
            Check(result.Status == "excluded" && result.Reason.Contains("Reparse"), "Symlink files excluded.");
        }
    }

    private static void TraceTests()
    {
        string traces = Path.Combine(root, "traces");
        Directory.CreateDirectory(traces);
        string unchanged = Path.Combine(traces, "mod-load-trace-42-unchanged.csv");
        File.WriteAllText(unchanged, "old");
        var session = Session("trace-output", traces);
        Call(session, "CaptureTraceBaseline");
        string fresh = Path.Combine(traces, "mod-load-trace-42-fresh.csv");
        File.WriteAllText(fresh, "fresh");
        string stale = Path.Combine(traces, "mod-load-trace-42-stale.csv");
        File.WriteAllText(stale, "stale");
        File.SetLastWriteTimeUtc(stale, DateTime.UtcNow.AddHours(-1));
        Directory.CreateDirectory(Path.Combine(traces, "nested"));
        File.WriteAllText(Path.Combine(traces, "nested", "mod-load-trace-42-hidden.csv"), "hidden");
        File.WriteAllText(Path.Combine(traces, "arbitrary.csv"), "irrelevant");
        File.WriteAllText(Path.Combine(traces, "mod-load-trace-43-wrong-process.csv"), "wrong PID");
        Call(session, "CaptureTraces", false);
        var manifest = (SessionManifest)Get(session, "manifest");
        Check(manifest.Traces.Count == 3, "Only top-level native trace filename pattern enumerated.");
        Check(manifest.Traces.All(x => !x.SourcePath.Contains("wrong-process")), "Different process PID trace excluded before candidate counting.");
        Check(manifest.Traces.Single(x => x.SourcePath == unchanged).Reason.Contains("Unchanged"), "Preexisting unchanged trace excluded.");
        Check(manifest.Traces.Single(x => x.SourcePath == stale).Status == "excluded", "Stale timestamp excluded.");
        Check(manifest.Traces.Single(x => x.SourcePath == fresh).Status == "copied", "New trace in capture window collected.");
        File.WriteAllText(fresh, "new export contents");
        Call(session, "CaptureTraces", true);
        Check(manifest.Traces.Count == 3 && File.ReadAllText(Path.Combine(session.DirectoryPath, "native-traces", Path.GetFileName(fresh))) == "new export contents", "Refresh merges and replaces owned trace without duplicate records.");
        Check(manifest.Traces.Single(x => x.SourcePath == fresh).Reason.Contains("post-stop"), "Post-stop event-window limitation recorded.");
        // Simulate export written well after the stop; collection is an explicit UI action.
        Set(session, "stoppedUtc", DateTime.UtcNow.AddSeconds(-10));
        string later = Path.Combine(traces, "mod-load-trace-42-later.csv");
        File.WriteAllText(later, "later");
        Call(session, "CaptureTraces", false);
        Check(manifest.Traces.Single(x => x.SourcePath == later).Status == "excluded", "Automatic stop excludes later export.");
        Call(session, "CaptureTraces", true);
        Check(manifest.Traces.Single(x => x.SourcePath == later).Status == "copied", "Explicit refresh accepts later export with limitation.");
    }

    private static async Task LifecycleTests()
    {
        var session = Session("lifecycle");
        Set(session, "status", "Recording");
        Set(session, "samplingTask", Task.CompletedTask);
        Set(session, "markers", new StreamWriter(Path.Combine(session.DirectoryPath, "markers.csv")));
        Set(session, "samples", new StreamWriter(Path.Combine(session.DirectoryPath, "samples.csv")));
        ((Stopwatch)Get(session, "clock")).Start();
        var calls = Enumerable.Range(0, 32).Select(_ => Task.Run(() => (object)session.StopAsync())).ToArray();
        object[] tasks = await Task.WhenAll(calls);
        Check(tasks.All(x => ReferenceEquals(x, tasks[0])), "Concurrent StopAsync callers share one finalization task.");
        await (Task)tasks[0];
        Check(session.Status == "Stopped" && !session.IsRunning, "Finalization reaches stopped state.");
        Check(File.Exists(Path.Combine(session.DirectoryPath, "session.json")), "Final manifest saved.");
        string before = File.ReadAllText(Path.Combine(session.DirectoryPath, "markers.csv"));
        session.AddMarker("late", "must be ignored");
        Check(File.ReadAllText(Path.Combine(session.DirectoryPath, "markers.csv")) == before, "Late marker ignored after writer cleanup.");
        FileEvidence attachment = await session.AddEvidenceAsync(Path.Combine(root, "source.csv"));
        Check(attachment.Status == "copied" && session.Status == "Stopped", "Post-stop attachment copied without reopening telemetry.");
        await session.RefreshTracesAsync();
        Check(session.Status == "Stopped", "Post-stop refresh remains safe without configured traces.");
    }

    public static async Task<int> Main(string[] args)
    {
        root = args.Length > 0 ? Path.Combine(Path.GetFullPath(args[0]), "run-" + Guid.NewGuid().ToString("N")) : throw new ArgumentException("Pass a task-owned scratch directory.");
        Directory.CreateDirectory(root);
        PermissionAndLifetimeTests();
        SnapshotTests();
        TraceTests();
        await LifecycleTests();
        Console.WriteLine("PASS: " + assertions + " portable backend assertions. Windows probes not exercised.");
        return 0;
    }
}
