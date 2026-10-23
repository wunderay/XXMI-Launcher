using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace XxmiCollector
{
    public sealed class SessionOptions
    {
        public int ProcessId { get; set; }
        public string OutputRoot { get; set; }
        public string TraceDirectory { get; set; }
        public string Scenario { get; set; }
        public string RunKind { get; set; }
        public string Notes { get; set; }
        public int DurationSeconds { get; set; }
    }

    public sealed class ProcessChoice
    {
        public int Id { get; set; }
        public string Name { get; set; }
        public string DisplayName { get { return Name + " (PID " + Id + ")"; } }
        public override string ToString() { return DisplayName; }
    }

    [DataContract]
    public sealed class FileEvidence
    {
        [DataMember(Name = "sourcePath")] public string SourcePath { get; set; }
        [DataMember(Name = "status")] public string Status { get; set; }
        [DataMember(Name = "reason")] public string Reason { get; set; }
        [DataMember(Name = "path")] public string Path { get; set; }
        [DataMember(Name = "sizeBytes")] public long SizeBytes { get; set; }
        [DataMember(Name = "sha256")] public string Sha256 { get; set; }
        [DataMember(Name = "sourceLastWriteUtc")] public string SourceLastWriteUtc { get; set; }
    }

    [DataContract]
    internal sealed class SessionManifest
    {
        [DataMember(Name = "schemaVersion")] public int SchemaVersion = 1;
        [DataMember(Name = "processId")] public int ProcessId;
        [DataMember(Name = "processName")] public string ProcessName;
        [DataMember(Name = "processStartedUtc")] public string ProcessStartedUtc;
        [DataMember(Name = "startedUtc")] public string StartedUtc;
        [DataMember(Name = "stoppedUtc")] public string StoppedUtc;
        [DataMember(Name = "scenario")] public string Scenario;
        [DataMember(Name = "runKind")] public string RunKind;
        [DataMember(Name = "notes")] public string Notes;
        [DataMember(Name = "durationSeconds")] public int DurationSeconds;
        [DataMember(Name = "sampleIntervalMilliseconds")] public int SampleIntervalMilliseconds = 500;
        [DataMember(Name = "cpuNormalization")] public string CpuNormalization = "Percent of all logical processors; 100 = all logical processors fully busy";
        [DataMember(Name = "ioSemantics")] public string IoSemantics = "Cumulative process IO transfer bytes since process start; includes cached and non-disk IO";
        [DataMember(Name = "measurementLimits")] public string MeasurementLimits = "No frame timing, first-correct-frame proof, game-device VRAM budget, injection, or rendering correctness is measured by these process samples.";
        [DataMember(Name = "status")] public string Status;
        [DataMember(Name = "traceCollectionUtc")] public string TraceCollectionUtc;
        [DataMember(Name = "nativeTracePattern")] public string NativeTracePattern;
        [DataMember(Name = "failures")] public List<string> Failures = new List<string>();
        [DataMember(Name = "traces")] public List<FileEvidence> Traces = new List<FileEvidence>();
        [DataMember(Name = "evidence")] public List<FileEvidence> Evidence = new List<FileEvidence>();
    }

    public sealed class CaptureSession
    {
        private const int MaxTraceCandidates = 128;
        private const long MaxTraceBytes = 64L * 1024 * 1024;
        private const long MaxTraceTotalBytes = 256L * 1024 * 1024;
        private readonly object sync = new object();
        private readonly SemaphoreSlim evidenceGate = new SemaphoreSlim(1, 1);
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private readonly Stopwatch clock = new Stopwatch();
        private readonly Dictionary<string, Stamp> traceBaseline = new Dictionary<string, Stamp>(StringComparer.OrdinalIgnoreCase);
        private SessionOptions options;
        private SessionManifest manifest;
        private Process process;
        private StreamWriter samples;
        private StreamWriter markers;
        private Task samplingTask;
        private Task stopTask;
        private string status = "Starting";
        private long evidenceBytes;
        private int evidenceCount;
        // Never reopen by PID: this limited handle anchors all process telemetry.
        private IntPtr processHandle;
        private int processOpenError;
        private bool processExited;
        internal enum ProcessLifetimeState { Unknown, Running, Exited }
        internal sealed class ProcessLifetime
        {
            public ProcessLifetimeState State;
            public long Created, CpuTicks;
            public string Problem;
        }
        private bool stopping;
        private DateTime stoppedUtc;

        public string DirectoryPath { get; private set; }
        public DateTime StartedUtc { get; private set; }
        public TimeSpan Elapsed { get { return clock.Elapsed; } }
        public string Status { get { lock (sync) return status; } }
        public bool IsRunning { get { lock (sync) return !stopping && status == "Recording"; } }

        private CaptureSession() { }

        public static List<ProcessChoice> ListProcesses()
        {
            var choices = new List<ProcessChoice>();
            foreach (var item in Process.GetProcesses())
            {
                using (item)
                {
                    try { choices.Add(new ProcessChoice { Id = item.Id, Name = item.ProcessName }); }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                }
            }
            return choices.OrderBy(x => x.Name, StringComparer.OrdinalIgnoreCase).ThenBy(x => x.Id).ToList();
        }

        public static Task<CaptureSession> StartAsync(SessionOptions options)
        {
            if (options == null) throw new ArgumentNullException("options");
            // Copy mutable UI options before dispatching work.
            var snapshot = new SessionOptions { ProcessId = options.ProcessId, OutputRoot = options.OutputRoot,
                TraceDirectory = options.TraceDirectory, Scenario = options.Scenario, RunKind = options.RunKind,
                Notes = options.Notes, DurationSeconds = options.DurationSeconds };
            return Task.Run(() =>
            {
                var session = new CaptureSession();
                try { session.Start(snapshot); return session; }
                catch { session.Cleanup(); throw; }
            });
        }

        private void Start(SessionOptions supplied)
        {
            if (supplied.ProcessId <= 0) throw new ArgumentException("Select a running process.");
            if (string.IsNullOrWhiteSpace(supplied.OutputRoot)) throw new ArgumentException("Choose an output folder.");
            if (supplied.DurationSeconds < 0 || supplied.DurationSeconds > 86400)
                throw new ArgumentException("Duration must be between 0 (manual stop) and 86400 seconds.");
            options = supplied;
            StartupFileOperation("Resolve output folder", supplied.OutputRoot, () => options.OutputRoot = Path.GetFullPath(supplied.OutputRoot));
            if (!string.IsNullOrWhiteSpace(options.TraceDirectory))
                StartupFileOperation("Resolve trace folder", options.TraceDirectory, () => options.TraceDirectory = Path.GetFullPath(options.TraceDirectory));
            processHandle = OpenProcess(0x1000, false, options.ProcessId); // PROCESS_QUERY_LIMITED_INFORMATION only
            processOpenError = processHandle == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
            // ERROR_INVALID_PARAMETER identifies a PID that no longer exists. Access denied does not.
            if (processHandle == IntPtr.Zero && processOpenError == 87)
                throw new InvalidOperationException("The selected process no longer exists. Select the running game again.");
            var initialLifetime = ReadLifetime();
            if (initialLifetime.State == ProcessLifetimeState.Exited)
                throw new InvalidOperationException("The selected process has exited. Select the running game again.");
            string processName = "Unknown (PID " + options.ProcessId.ToString(CultureInfo.InvariantCulture) + ")";
            if (initialLifetime.State == ProcessLifetimeState.Running)
            {
                // This object is used only for guarded, optional module inventory.
                try { process = Process.GetProcessById(options.ProcessId); }
                catch (ArgumentException) { }
                catch (System.ComponentModel.Win32Exception) { }
                var image = new StringBuilder(32768);
                int length = image.Capacity;
                if (QueryFullProcessImageName(processHandle, 0, image, ref length))
                    processName = Path.GetFileNameWithoutExtension(image.ToString());
            }
            StartupFileOperation("Create output folder", options.OutputRoot, () => Directory.CreateDirectory(options.OutputRoot));
            DirectoryPath = Path.Combine(options.OutputRoot, "capture-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            StartupFileOperation("Create capture folder", DirectoryPath, () => Directory.CreateDirectory(DirectoryPath));
            StartedUtc = DateTime.UtcNow;
            clock.Start();
            manifest = new SessionManifest { ProcessId = options.ProcessId, ProcessName = processName,
                StartedUtc = Utc(StartedUtc), Scenario = Limit(options.Scenario, 512), RunKind = Limit(options.RunKind, 128),
                Notes = Limit(options.Notes, 16384), DurationSeconds = options.DurationSeconds, Status = "Starting" };
            manifest.NativeTracePattern = "mod-load-trace-" + options.ProcessId.ToString(CultureInfo.InvariantCulture) + "-*.csv";
            if (initialLifetime.State == ProcessLifetimeState.Running)
                manifest.ProcessStartedUtc = Utc(DateTime.FromFileTimeUtc(initialLifetime.Created));
            else Failure("Process telemetry unavailable: " + initialLifetime.Problem + ". Hardware inventory and manual markers remain available; process exit cannot be detected.");
            CaptureTraceBaseline();
            samples = Writer("samples.csv");
            StartupFileOperation("Write sample header", Path.Combine(DirectoryPath, "samples.csv"), () =>
                samples.WriteLine("elapsed_ms,utc,cpu_percent,working_set_bytes,private_bytes,io_read_bytes,io_write_bytes,status"));
            markers = Writer("markers.csv");
            StartupFileOperation("Write marker header", Path.Combine(DirectoryPath, "markers.csv"), () => markers.WriteLine("elapsed_ms,utc,label,note"));
            StartupFileOperation("Write initial manifest", Path.Combine(DirectoryPath, "session.json"), WriteManifest);
            // Inventory runs before sampling; its duration remains visible in elapsed times.
            StartupFileOperation("Write inventory", Path.Combine(DirectoryPath, "inventory.json"),
                () => NativeInventory.Collect(process, Path.Combine(DirectoryPath, "inventory.json"), Failure,
                    () => process != null && ReadLifetime().State == ProcessLifetimeState.Running));
            lock (sync) { status = "Recording"; manifest.Status = status; }
            StartupFileOperation("Write capture-start marker", Path.Combine(DirectoryPath, "markers.csv"), () =>
                AddMarker("capture_started", "Capture started. See sample status for process telemetry availability. Manual markers are observer timestamps, not frame measurements."));
            StartupFileOperation("Write recording manifest", Path.Combine(DirectoryPath, "session.json"), WriteManifest);
            samplingTask = Task.Run(SampleLoopAsync);
        }

        private StreamWriter Writer(string relative)
        {
            StreamWriter writer = null;
            StartupFileOperation("Create capture file", Path.Combine(DirectoryPath, relative), () =>
                writer = new StreamWriter(new FileStream(Path.Combine(DirectoryPath, relative), FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false)) { AutoFlush = true });
            return writer;
        }

        public void AddMarker(string label, string note = null)
        {
            lock (sync)
            {
                if (stopping || markers == null) return;
                markers.WriteLine(Number(clock.Elapsed.TotalMilliseconds) + "," + Csv(Utc(DateTime.UtcNow)) + "," + Csv(Limit(label, 512)) + "," + Csv(Limit(note, 8192)));
            }
        }

        private async Task SampleLoopAsync()
        {
            TimeSpan? previousCpu = null;
            double previousElapsed = 0;
            try
            {
                while (!cancellation.IsCancellationRequested)
                {
                    double now = clock.Elapsed.TotalMilliseconds;
                    string cpu = "", working = "", bytes = "", read = "", written = "";
                    var problems = new List<string>();
                    var lifetime = ReadLifetime();
                    if (lifetime.State == ProcessLifetimeState.Exited)
                    {
                        AddMarker("process_exited", "Sampling stopped because the selected process exited (confirmed by its held handle).");
                        break;
                    }
                    if (lifetime.State == ProcessLifetimeState.Running)
                    {
                        var total = TimeSpan.FromTicks(lifetime.CpuTicks);
                        if (previousCpu.HasValue && now > previousElapsed)
                            cpu = Number(Math.Max(0, (total - previousCpu.Value).TotalMilliseconds / (now - previousElapsed) / Math.Max(1, Environment.ProcessorCount) * 100));
                        previousCpu = total; previousElapsed = now;
                        MemoryCounters memory = new MemoryCounters();
                        memory.Size = (uint)Marshal.SizeOf(typeof(MemoryCounters));
                        if (GetProcessMemoryInfo(processHandle, ref memory, memory.Size))
                        { working = memory.WorkingSetSize.ToUInt64().ToString(CultureInfo.InvariantCulture); bytes = memory.PrivateUsage.ToUInt64().ToString(CultureInfo.InvariantCulture); }
                        else problems.Add("memory unavailable (Win32 " + Marshal.GetLastWin32Error() + ")");
                        IoCounters io;
                        if (GetProcessIoCounters(processHandle, out io))
                        { read = io.ReadTransferCount.ToString(CultureInfo.InvariantCulture); written = io.WriteTransferCount.ToString(CultureInfo.InvariantCulture); }
                        else problems.Add("io unavailable (Win32 " + Marshal.GetLastWin32Error() + ")");
                    }
                    else
                    {
                        previousCpu = null;
                        problems.Add("process lifetime unknown: " + lifetime.Problem);
                        problems.Add("process metrics unavailable: identity/lifetime could not be verified");
                    }
                    foreach (string problem in problems) Failure("Sample " + problem);
                    samples.WriteLine(Number(now) + "," + Utc(DateTime.UtcNow) + "," + cpu + "," + working + "," + bytes + "," + read + "," + written + "," + Csv(problems.Count == 0 ? "ok" : string.Join("; ", problems)));
                    if (options.DurationSeconds > 0 && clock.Elapsed.TotalSeconds >= options.DurationSeconds) break;
                    if (clock.Elapsed.TotalHours >= 24) { AddMarker("capture_limit", "Capture automatically stopped at the 24 hour safety limit."); break; }
                    await Task.Delay(500, cancellation.Token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { Failure("Sampling stopped: " + Describe(ex)); }
            finally
            {
                // StopAsync executes on another worker and awaits this loop; never await it here.
                var ignored = StopAsync();
            }
        }

        public Task StopAsync()
        {
            lock (sync)
            {
                if (stopTask != null) return stopTask;
                try { if (markers != null) markers.WriteLine(Number(clock.Elapsed.TotalMilliseconds) + "," + Utc(DateTime.UtcNow) + ",capture_stopped,"); }
                catch (Exception ex) { Failure("Stop marker: " + Describe(ex)); }
                stopping = true;
                stoppedUtc = DateTime.UtcNow;
                clock.Stop();
                status = "Finalizing";
                cancellation.Cancel();
                stopTask = Task.Run(FinishAsync);
                return stopTask;
            }
        }

        private async Task FinishAsync()
        {
            try
            {
                if (samplingTask != null) await samplingTask.ConfigureAwait(false);
                await evidenceGate.WaitAsync().ConfigureAwait(false);
                try { CaptureTraces(false); }
                finally { evidenceGate.Release(); }
            }
            catch (Exception ex) { Failure("Finalization: " + Describe(ex)); }
            finally
            {
                Cleanup();
                lock (sync) { status = "Stopped"; manifest.Status = status; manifest.StoppedUtc = Utc(stoppedUtc); }
                // A write failure propagates to the UI, which must not report a successfully saved session.
                WriteManifest();
            }
        }

        public async Task RefreshTracesAsync()
        {
            Task pending;
            lock (sync)
            {
                if (!stopping) throw new InvalidOperationException("Stop the capture before refreshing exported traces.");
                pending = stopTask;
            }
            if (pending != null) await pending.ConfigureAwait(false);
            await evidenceGate.WaitAsync().ConfigureAwait(false);
            try
            {
                await Task.Run(() => { CaptureTraces(true); WriteManifest(); }).ConfigureAwait(false);
            }
            finally { evidenceGate.Release(); }
        }

        public async Task<FileEvidence> AddEvidenceAsync(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("Choose an evidence file.");
            Task pending;
            lock (sync) pending = stopping ? stopTask : null;
            if (pending != null) await pending.ConfigureAwait(false);
            await evidenceGate.WaitAsync().ConfigureAwait(false);
            try
            {
                return await Task.Run(() =>
                {
                    string full = Path.GetFullPath(path);
                    var record = new FileEvidence { SourcePath = full, Status = "excluded" };
                    if (manifest.Evidence.Count >= 32) record.Reason = "Maximum 32 evidence attachment attempts per session.";
                    else if (evidenceCount >= 16) record.Reason = "Maximum 16 evidence files per session.";
                    else
                    {
                        string folder = Path.Combine(DirectoryPath, "evidence");
                        Directory.CreateDirectory(folder);
                        string relative = "evidence/" + (evidenceCount + 1).ToString("D2", CultureInfo.InvariantCulture) + "-" + Path.GetFileName(full);
                        CopyStable(full, relative, Math.Min(32L * 1024 * 1024, 128L * 1024 * 1024 - evidenceBytes), record);
                        if (record.Status == "copied") { evidenceCount++; evidenceBytes += record.SizeBytes; }
                    }
                    lock (sync) { if (manifest.Evidence.Count < 32) manifest.Evidence.Add(record); }
                    WriteManifest();
                    return record;
                }).ConfigureAwait(false);
            }
            finally { evidenceGate.Release(); }
        }

        private void CaptureTraceBaseline()
        {
            if (string.IsNullOrWhiteSpace(options.TraceDirectory)) return;
            try
            {
                foreach (string path in EnumerateTraces())
                    try { traceBaseline[path] = Stamp.Read(path); }
                    catch (Exception ex) { Failure("Trace baseline " + path + ": " + Describe(ex)); }
            }
            catch (Exception ex) { Failure("Trace baseline directory: " + Describe(ex)); }
        }

        private IEnumerable<string> EnumerateTraces()
        {
            int count = 0;
            string pattern = "mod-load-trace-" + options.ProcessId.ToString(CultureInfo.InvariantCulture) + "-*.csv";
            foreach (string path in Directory.EnumerateFiles(options.TraceDirectory, pattern, SearchOption.TopDirectoryOnly))
            {
                if (++count > MaxTraceCandidates) { Failure("Trace file limit reached; only first 128 top-level candidates considered."); yield break; }
                yield return path;
            }
        }

        private void CaptureTraces(bool explicitRefresh)
        {
            if (string.IsNullOrWhiteSpace(options.TraceDirectory)) { Failure("No native trace directory configured; native stage/frame evidence unavailable."); return; }
            long copied = manifest.Traces.Where(x => x.Status == "copied").Sum(x => x.SizeBytes);
            DateTime collectionUtc = DateTime.UtcNow;
            manifest.TraceCollectionUtc = Utc(collectionUtc);
            try
            {
                foreach (string path in EnumerateTraces())
                {
                    var record = new FileEvidence { SourcePath = path, Status = "excluded" };
                    FileEvidence previous = manifest.Traces.Find(x => string.Equals(x.SourcePath, path, StringComparison.OrdinalIgnoreCase));
                    if (previous == null && manifest.Traces.Count >= MaxTraceCandidates)
                    { Failure("Trace manifest limit reached; at most 128 source identities retained across refreshes."); break; }
                    try
                    {
                        Stamp current = Stamp.Read(path), original;
                        record.SourceLastWriteUtc = Utc(current.ModifiedUtc);
                        if (traceBaseline.TryGetValue(path, out original) && original.Equals(current)) record.Reason = "Unchanged file present before capture.";
                        else if (current.ModifiedUtc < StartedUtc.AddSeconds(-2) || current.ModifiedUtc > (explicitRefresh ? collectionUtc : stoppedUtc).AddSeconds(2)) record.Reason = "Last-write timestamp outside permitted capture/export window (2 second filesystem tolerance).";
                        else if (current.CreatedUtc > (explicitRefresh ? collectionUtc : stoppedUtc).AddSeconds(2)) record.Reason = "Created after capture/export window.";
                        else
                        {
                            Directory.CreateDirectory(Path.Combine(DirectoryPath, "native-traces"));
                            long previousSize = previous != null && previous.Status == "copied" ? previous.SizeBytes : 0;
                            CopyStable(path, "native-traces/" + Path.GetFileName(path), Math.Min(MaxTraceBytes, MaxTraceTotalBytes - copied + previousSize), record, previousSize > 0 || (previous != null && previous.Status == "copied"));
                            if (record.Status == "copied")
                            {
                                copied += record.SizeBytes - previousSize;
                                if (explicitRefresh) record.Reason += " Explicit post-stop refresh permits later exports; event timestamps must be checked separately and may extend outside this capture.";
                            }
                        }
                    }
                    catch (Exception ex) { record.Reason = Describe(ex); }
                    if (previous != null)
                    {
                        if (previous.Status == "copied" && record.Status != "copied")
                        { previous.Reason = "Retained previous stable snapshot. Latest collection excluded source: " + record.Reason; continue; }
                        manifest.Traces.Remove(previous);
                    }
                    manifest.Traces.Add(record);
                }
            }
            catch (Exception ex) { Failure("Native trace capture: " + Describe(ex)); }
        }

        private void CopyStable(string source, string relative, long limit, FileEvidence record, bool replaceExisting = false)
        {
            string destination = Path.Combine(DirectoryPath, relative.Replace('/', Path.DirectorySeparatorChar));
            string temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0) { record.Reason = "Reparse-point files are excluded."; return; }
                var before = Stamp.Read(source);
                record.SourceLastWriteUtc = Utc(before.ModifiedUtc);
                if (before.Size > limit || limit < 0) { record.Reason = "File or total byte limit exceeded."; return; }
                using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var hash = SHA256.Create())
                {
                    byte[] buffer = new byte[65536];
                    long total = 0;
                    int got;
                    while ((got = input.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        total += got;
                        if (total > limit) throw new IOException("File grew beyond capture byte limit.");
                        output.Write(buffer, 0, got);
                        hash.TransformBlock(buffer, 0, got, buffer, 0);
                    }
                    hash.TransformFinalBlock(new byte[0], 0, 0);
                    if (!before.Equals(Stamp.Read(source)) || total != before.Size) throw new IOException("File changed during snapshot; unstable file excluded.");
                    record.SizeBytes = total;
                    record.Sha256 = BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                }
                if (replaceExisting && File.Exists(destination)) File.Replace(temporary, destination, null);
                else File.Move(temporary, destination);
                record.Path = relative;
                record.Status = "copied";
                record.Reason = "Stable bounded snapshot; timestamp association does not prove trace contents match this process/session.";
            }
            catch (Exception ex)
            {
                record.Status = "excluded";
                record.Reason = "Unavailable, in use, or unstable: " + Describe(ex);
                try { if (File.Exists(temporary)) File.Delete(temporary); } catch (IOException) { }
            }
        }

        private void Failure(string reason)
        {
            lock (sync)
            {
                if (manifest != null && manifest.Failures.Count < 128 && !manifest.Failures.Contains(reason)) manifest.Failures.Add(reason);
            }
        }

        private void WriteManifest()
        {
            lock (sync) JsonFile.Write(Path.Combine(DirectoryPath, "session.json"), manifest);
        }

        private void Cleanup()
        {
            if (samples != null) { try { samples.Dispose(); } catch (Exception ex) { Failure("Samples close: " + Describe(ex)); } samples = null; }
            if (markers != null) { try { markers.Dispose(); } catch (Exception ex) { Failure("Markers close: " + Describe(ex)); } markers = null; }
            if (processHandle != IntPtr.Zero) { CloseHandle(processHandle); processHandle = IntPtr.Zero; }
            if (process != null) { process.Dispose(); process = null; }
        }

        // Exit time, rather than exit code, distinguishes a live process from an actual exit
        // with code 259 (STILL_ACTIVE). Unknown permissions must never mean exited.
        internal static ProcessLifetimeState ClassifyLifetime(bool querySucceeded, long exitTime)
        { return !querySucceeded ? ProcessLifetimeState.Unknown : exitTime == 0 ? ProcessLifetimeState.Running : ProcessLifetimeState.Exited; }

        private ProcessLifetime ReadLifetime()
        {
            if (processExited) return new ProcessLifetime { State = ProcessLifetimeState.Exited };
            if (processHandle == IntPtr.Zero)
                return new ProcessLifetime { State = ProcessLifetimeState.Unknown, Problem = "limited process query unavailable (Win32 " + processOpenError + ")" };
            long created, exited, kernel, user;
            bool success = GetProcessTimes(processHandle, out created, out exited, out kernel, out user);
            var state = ClassifyLifetime(success, exited);
            if (state == ProcessLifetimeState.Exited) processExited = true;
            return new ProcessLifetime { State = state, Created = created, CpuTicks = success ? kernel + user : 0,
                Problem = success ? null : "GetProcessTimes unavailable (Win32 " + Marshal.GetLastWin32Error() + ")" };
        }

        internal static void StartupFileOperation(string stage, string path, Action operation)
        {
            try { operation(); }
            catch (Exception ex) { throw new IOException(stage + " failed at '" + path + "': " + Describe(ex), ex); }
        }

        internal static string Describe(Exception ex) { return ex.GetType().Name + ": " + ex.Message; }
        internal static string Utc(DateTime value) { return value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture); }
        private static string Limit(string value, int limit) { return value == null ? "" : value.Substring(0, Math.Min(limit, value.Length)); }
        private static string Number(double value) { return value.ToString("0.###", CultureInfo.InvariantCulture); }
        private static string Csv(string value) { return "\"" + (value ?? "").Replace("\"", "\"\"") + "\""; }

        private struct Stamp : IEquatable<Stamp>
        {
            public long Size;
            public DateTime ModifiedUtc, CreatedUtc;
            public static Stamp Read(string path) { var info = new FileInfo(path); return new Stamp { Size = info.Length, ModifiedUtc = info.LastWriteTimeUtc, CreatedUtc = info.CreationTimeUtc }; }
            public bool Equals(Stamp other) { return Size == other.Size && ModifiedUtc == other.ModifiedUtc && CreatedUtc == other.CreatedUtc; }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct MemoryCounters
        {
            public uint Size, PageFaultCount;
            public UIntPtr PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage,
                QuotaPeakNonPagedPoolUsage, QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage, PrivateUsage;
        }
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out long creation, out long exit, out long kernel, out long user);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder image, ref int length);
        [DllImport("psapi.dll", SetLastError = true)] private static extern bool GetProcessMemoryInfo(IntPtr process, ref MemoryCounters counters, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessIoCounters(IntPtr process, out IoCounters counters);
        [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    }

    internal static class JsonFile
    {
        public static void Write<T>(string path, T value)
        {
            string temporary = path + ".tmp";
            using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
                new DataContractJsonSerializer(typeof(T)).WriteObject(stream, value);
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }
    }
}
