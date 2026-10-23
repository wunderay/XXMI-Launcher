using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using XxmiCollector;

// Changes only the DACLs of this executable's disposable synthetic children.
internal static class WindowsPermissionsSmoke
{
    private const uint Query = 0x0400, LimitedQuery = 0x1000, Synchronize = 0x00100000;
    private static string destination;

    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--child")
        {
            Console.WriteLine("ready");
            // Framework's redirected StandardInput writer can emit an encoding
            // preamble on a UTF-8 Windows console. Decode it as a preamble rather
            // than letting it become part of the ASCII fixture command.
            using (var input = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false), true))
            {
                string command = input.ReadLine();
                Console.WriteLine("received=" + (command ?? "<EOF>") + "; utf16=" +
                    (command == null ? "" : string.Join(" ", command.Select(c => ((int)c).ToString("X4")))));
                Console.Out.Flush();
                if (command == "exit259") ExitProcess(259);
                if (command == "exit") return 0;
                return 73; // Unexpected input must never look like successful cleanup.
            }
        }
        if (args.Length != 1) { Console.Error.WriteLine("Usage: WindowsPermissionsSmoke.exe <evidence-directory>"); return 2; }
        try
        {
            Require(Environment.OSVersion.Platform == PlatformID.Win32NT, "This test requires real Windows and .NET Framework 4.8.");
            destination = Path.GetFullPath(args[0]);
            Directory.CreateDirectory(destination);
            Run().GetAwaiter().GetResult();
            Console.WriteLine("PASS: synthetic Windows process permission checks.");
            File.WriteAllText(Path.Combine(destination, "assertions.txt"), "PASS: limited-query capture, exit code 259, retained process identity, unknown lifetime duration and user stop.\n");
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("FAIL: " + ex);
            if (destination != null) File.WriteAllText(Path.Combine(destination, "assertions.txt"), ex.ToString());
            return 1;
        }
    }

    private static async Task Run()
    {
        // Framework ProcessManager initializes SeDebugPrivilege when available.
        // Initialize it against ourselves before disabling the privilege, otherwise
        // its later first use can bypass the disposable children's DACLs on CI.
        using (var current = Process.GetCurrentProcess())
            Require(current.ProcessName.Length > 0, "Cannot initialize Framework ProcessManager against the test process.");
        using (var privilege = new DisabledDebugPrivilege())
        {
            await LimitedQueryExit();
            await UnknownLifetime(false);
            await UnknownLifetime(true);
        }
    }

    private static async Task LimitedQueryExit()
    {
        using (var child = new OwnedChild(Query | Synchronize))
        {
            child.AssertDenied(Query, "full query");
            child.AssertDenied(Synchronize, "synchronize");
            using (var original = Process.GetProcessById(child.Id))
            {
                try { bool ignored = original.HasExited; throw new Exception("Precondition: old Process.HasExited unexpectedly succeeded under the restricted DACL."); }
                catch (Win32Exception ex) { Require(ex.NativeErrorCode == 5, "Old Process.HasExited must fail with ACCESS_DENIED (5), got " + ex.NativeErrorCode); }
            }
            IntPtr limited = OpenProcess(LimitedQuery, false, child.Id);
            Require(limited != IntPtr.Zero, "Precondition: limited-query OpenProcess failed: " + Marshal.GetLastWin32Error());
            try
            {
                long creation, exit, kernel, user;
                Require(GetProcessTimes(limited, out creation, out exit, out kernel, out user), "Limited-query GetProcessTimes failed.");
                Require(creation != 0 && exit == 0, "Child must be alive with creation time available through limited query.");
            }
            finally { CloseHandle(limited); }

            CaptureSession session = null;
            try
            {
                session = await Start(child, "limited-query", 15);
                IntPtr identityHandle = SessionIdentityHandle(session);
                Require(identityHandle != IntPtr.Zero && GetProcessId(identityHandle) == child.Id, "Capture must retain a handle to the selected original process.");
                await Task.Delay(1200);
                Require(SessionIdentityHandle(session) == identityHandle, "Capture must retain the same process identity handle across samples.");
                Require(session.IsRunning, "Limited-query capture stopped while owned child was alive; " + Diagnostics(session));
                var rows = SampleRows(session);
                Require(rows.Length >= 2, "Limited-query capture needs multiple live samples; " + Diagnostics(session));
                foreach (var row in rows)
                {
                    if (row[3].Length == 0 || row[4].Length == 0)
                        Require(row[3] == "" && row[4] == "" && row[7].IndexOf("memory", StringComparison.OrdinalIgnoreCase) >= 0,
                            "Unavailable memory fields must both be empty with explicit memory diagnostics.");
                    else
                    {
                        long working, bytes;
                        Require(long.TryParse(row[3], out working) && working > 0 && long.TryParse(row[4], out bytes) && bytes > 0,
                            "Available memory counters must be positive native values.");
                    }
                }
                Require(rows.Skip(1).Any(x => x[2].Length > 0), "CPU must be available through limited-query GetProcessTimes after the first sample.");
                AssertNoExitMarker(session);
                long beforeCreation, beforeExit, kernel, user;
                Require(GetProcessTimes(child.Handle, out beforeCreation, out beforeExit, out kernel, out user), "Cannot read owned original process identity.");
                child.Exit259();
                long afterCreation, afterExit;
                Require(GetProcessTimes(child.Handle, out afterCreation, out afterExit, out kernel, out user), "Cannot read retained original process after exit.");
                Require(beforeCreation == afterCreation && afterExit != 0, "Retained original identity must have a nonzero exit FILETIME.");
                uint code;
                bool readCode = GetExitCodeProcess(child.Handle, out code);
                int codeError = readCode ? 0 : Marshal.GetLastWin32Error();
                Console.WriteLine("Fixture child exit: GetExitCodeProcess=" + readCode + ", code=" + code + ", Win32=" + codeError + "; stdout=" + child.ExitResponse);
                Require(readCode && code == 259, "Synthetic child must actually exit with code 259 (STILL_ACTIVE ambiguity); GetExitCodeProcess=" + readCode +
                    ", actualCode=" + code + ", Win32=" + codeError + "; child stdout=" + child.ExitResponse);
                Require(child.ExitResponse.Contains("received=exit259;"), "Child did not acknowledge the expected exit259 command: " + child.ExitResponse);
                // Keep a different process alive while capture detects the original exit.
                using (var successor = new OwnedChild(0))
                {
                    await Until(() => session.Status == "Stopped", "Selected child exit did not stop limited-query capture", session);
                    await session.StopAsync();
                    Require(ReadShared(Path.Combine(session.DirectoryPath, "markers.csv")).Contains(",\"process_exited\","), "Real exit must produce process_exited marker; " + Diagnostics(session));
                    Require(successor.IsAlive, "Successor child must remain alive; capture must follow the original identity.");
                    string manifest = ReadShared(Path.Combine(session.DirectoryPath, "session.json"));
                    Require(manifest.Contains("\"processId\":" + child.Id), "Manifest retargeted away from selected child.");
                    await Task.Delay(600);
                    Require(session.Status == "Stopped", "Stopped session unexpectedly resumed against another process.");
                }
                Console.WriteLine("PASS: limited query survives full-query denial; retained original exit with code 259 stops capture.");
            }
            finally { if (session != null) await session.StopAsync(); }
        }
    }

    private static async Task UnknownLifetime(bool userStop)
    {
        using (var child = new OwnedChild(Query | LimitedQuery | Synchronize))
        {
            child.AssertDenied(Query, "full query");
            child.AssertDenied(LimitedQuery, "limited query");
            CaptureSession session = null;
            try
            {
                session = await Start(child, userStop ? "unknown-user-stop" : "unknown-duration", userStop ? 0 : 3);
                await Task.Delay(1100);
                Require(child.IsAlive && session.IsRunning, "Unknown lifetime must keep recording while owned child exists; " + Diagnostics(session));
                var rows = SampleRows(session);
                Require(rows.Length >= 2, "Unknown lifetime must emit samples; " + Diagnostics(session));
                Require(rows.All(x => x.Skip(2).Take(5).All(v => v.Length == 0)), "Unverified identity must leave all metric fields unavailable.");
                Require(rows.All(x => x[7].IndexOf("unknown", StringComparison.OrdinalIgnoreCase) >= 0), "Unknown lifetime must be explicit in sample status.");
                AssertNoExitMarker(session);
                if (userStop) await session.StopAsync();
                else await Until(() => session.Status == "Stopped", "Unknown lifetime ignored configured duration", session);
                await session.StopAsync();
                Require(child.IsAlive, "Stopping collector must not stop the synthetic child.");
                AssertNoExitMarker(session);
                Console.WriteLine("PASS: denied lifetime remains unknown and stops by " + (userStop ? "user" : "duration") + ".");
            }
            finally { if (session != null) await session.StopAsync(); }
        }
    }

    private static Task<CaptureSession> Start(OwnedChild child, string scenario, int duration)
    {
        return CaptureSession.StartAsync(new SessionOptions { ProcessId = child.Id, OutputRoot = Path.Combine(destination, scenario),
            Scenario = scenario, RunKind = "synthetic-permission-smoke", DurationSeconds = duration });
    }

    private static string[][] SampleRows(CaptureSession session)
    {
        return ReadShared(Path.Combine(session.DirectoryPath, "samples.csv")).Split(new[] { "\r\n", "\n" }, StringSplitOptions.None).Skip(1)
            .Where(x => x.Length > 0).Select(x => x.Split(new[] { ',' }, 8)).ToArray();
    }

    private static void AssertNoExitMarker(CaptureSession session)
    {
        Require(!ReadShared(Path.Combine(session.DirectoryPath, "markers.csv")).Contains(",\"process_exited\","),
            "Permission denial was incorrectly reported as process exit; " + Diagnostics(session));
    }

    private static string Diagnostics(CaptureSession session)
    {
        if (session == null) return "no session";
        string path = Path.Combine(session.DirectoryPath, "session.json");
        return "status=" + session.Status + "; evidence=" + session.DirectoryPath + "; manifest=" + (File.Exists(path) ? ReadShared(path) : "missing");
    }

    private static string ReadShared(string path)
    {
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        using (var reader = new StreamReader(stream)) return reader.ReadToEnd();
    }

    private static async Task Until(Func<bool> condition, string message, CaptureSession session)
    {
        var clock = Stopwatch.StartNew();
        while (!condition() && clock.Elapsed.TotalSeconds < 8) await Task.Delay(100);
        Require(condition(), message + "; " + Diagnostics(session));
    }

    private static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }

    private static IntPtr SessionIdentityHandle(CaptureSession session)
    {
        var field = typeof(CaptureSession).GetField("processHandle", BindingFlags.Instance | BindingFlags.NonPublic);
        Require(field != null, "CaptureSession retained identity handle field is missing; update this focused identity assertion if the field is renamed.");
        return (IntPtr)field.GetValue(session);
    }

    private sealed class DisabledDebugPrivilege : IDisposable
    {
        private IntPtr token;
        private TokenPrivileges previous;

        public DisabledDebugPrivilege()
        {
            Require(OpenProcessToken(GetCurrentProcess(), 0x0028, out token), "Cannot open own process token: " + Marshal.GetLastWin32Error());
            try
            {
                Luid debug;
                Require(LookupPrivilegeValue(null, "SeDebugPrivilege", out debug), "Cannot resolve SeDebugPrivilege: " + Marshal.GetLastWin32Error());
                var disabled = new TokenPrivileges { Count = 1, Luid = debug, Attributes = 0 };
                uint returned;
                Require(AdjustTokenPrivileges(token, false, ref disabled, (uint)Marshal.SizeOf(typeof(TokenPrivileges)), out previous, out returned),
                    "Cannot disable own SeDebugPrivilege: " + Marshal.GetLastWin32Error());
                int error = Marshal.GetLastWin32Error();
                // Ordinary users may not possess this privilege at all. That is
                // suitable for the fixture; the denied-access assertions still run.
                Require(error == 0 || error == 1300, "Disabling own SeDebugPrivilege returned Win32 " + error);
                Console.WriteLine("Fixture: own SeDebugPrivilege disabled (previous attributes=" + previous.Attributes + ", Win32=" + error + ").");
            }
            catch { CloseHandle(token); token = IntPtr.Zero; throw; }
        }

        public void Dispose()
        {
            if (token == IntPtr.Zero) return;
            try
            {
                if (previous.Count != 0)
                {
                    TokenPrivileges ignored; uint returned;
                    Require(AdjustTokenPrivileges(token, false, ref previous, (uint)Marshal.SizeOf(typeof(TokenPrivileges)), out ignored, out returned),
                        "Cannot restore own SeDebugPrivilege: " + Marshal.GetLastWin32Error());
                    Require(Marshal.GetLastWin32Error() == 0, "Restoring own SeDebugPrivilege was not fully assigned.");
                }
            }
            finally { CloseHandle(token); token = IntPtr.Zero; }
        }
    }

    private sealed class OwnedChild : IDisposable
    {
        private readonly Process child;
        public IntPtr Handle { get; private set; }
        public string ExitResponse { get; private set; }
        public int Id { get { return child.Id; } }
        public bool IsAlive { get { return WaitForSingleObject(Handle, 0) == 0x102; } }

        public OwnedChild(uint denied)
        {
            child = Process.Start(new ProcessStartInfo(typeof(WindowsPermissionsSmoke).Assembly.Location, "--child")
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true });
            try
            {
                // Retained before restriction, solely for bounded cleanup and native identity assertions.
                Handle = OpenProcess(0x001FFFFF, false, child.Id);
                Require(Handle != IntPtr.Zero, "Cannot retain owned child handle: " + Marshal.GetLastWin32Error());
                Task<string> ready = child.StandardOutput.ReadLineAsync();
                Require(ready.Wait(5000) && ready.Result == "ready", "Synthetic child did not become ready within five seconds.");
                if (denied != 0)
                {
                    var sid = WindowsIdentity.GetCurrent().User;
                    var acl = new RawAcl(2, 2);
                    acl.InsertAce(0, new CommonAce(AceFlags.None, AceQualifier.AccessDenied, (int)denied, sid, false, null));
                    acl.InsertAce(1, new CommonAce(AceFlags.None, AceQualifier.AccessAllowed, 0x001FFFFF, sid, false, null));
                    var security = new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent | ControlFlags.DiscretionaryAclProtected, sid, sid, null, acl);
                    byte[] bytes = new byte[security.BinaryLength]; security.GetBinaryForm(bytes, 0);
                    Require(SetKernelObjectSecurity(Handle, 4, bytes), "SetKernelObjectSecurity on owned child failed: " + Marshal.GetLastWin32Error());
                }
            }
            catch { Dispose(); throw; }
        }

        public void AssertDenied(uint access, string label)
        {
            IntPtr probe = OpenProcess(access, false, Id);
            int error = Marshal.GetLastWin32Error();
            if (probe != IntPtr.Zero) CloseHandle(probe);
            Require(probe == IntPtr.Zero && error == 5, "DACL precondition failed for " + label + ": handle=" + probe + ", Win32=" + error);
        }

        public void Exit259()
        {
            child.StandardInput.WriteLine("exit259"); child.StandardInput.Flush();
            Require(WaitForSingleObject(Handle, 5000) == 0, "Owned child did not exit within five seconds.");
            var response = child.StandardOutput.ReadToEndAsync();
            Require(response.Wait(2000), "Owned child stdout did not close after process exit.");
            ExitResponse = response.Result.Trim();
        }

        public void Dispose()
        {
            try
            {
                if (Handle != IntPtr.Zero && IsAlive)
                {
                    try { child.StandardInput.WriteLine("exit"); child.StandardInput.Flush(); } catch (IOException) { }
                    if (WaitForSingleObject(Handle, 2000) != 0)
                    {
                        Require(TerminateProcess(Handle, 42), "Retained-handle cleanup failed: " + Marshal.GetLastWin32Error());
                        Require(WaitForSingleObject(Handle, 5000) == 0, "Owned child cleanup exceeded timeout.");
                    }
                }
                else if (Handle == IntPtr.Zero && !child.HasExited) { child.Kill(); child.WaitForExit(5000); }
            }
            finally { if (Handle != IntPtr.Zero) { CloseHandle(Handle); Handle = IntPtr.Zero; } child.Dispose(); }
        }
    }

    [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [StructLayout(LayoutKind.Sequential)] private struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] private struct TokenPrivileges { public uint Count; public Luid Luid; public uint Attributes; }
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool LookupPrivilegeValue(string system, string name, out Luid luid);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TokenPrivileges state, uint length, out TokenPrivileges previous, out uint returned);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool SetKernelObjectSecurity(IntPtr handle, uint information, byte[] descriptor);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr handle, out long creation, out long exit, out long kernel, out long user);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern int GetProcessId(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr handle, uint code);
    [DllImport("kernel32.dll")] private static extern void ExitProcess(uint code);
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
}

namespace XxmiCollector
{
    // Keep this integration test focused on real CaptureSession permissions/lifetime.
    // No WMI, module enumeration, native inventory queries, or external process access.
    internal static class NativeInventory
    {
        public static void Collect(Process process, string path, Action<string> failure, Func<bool> identityStillCurrent = null)
        { File.WriteAllText(path, "{\"syntheticPermissionTest\":true}"); }
    }
}
