using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace XxmiCollector
{
    /// <summary>Owns exactly one optional, user-selected PresentMon process.</summary>
    public sealed class PresentMonCapture : IDisposable
    {
        private const int TextLimit = 32768;
        private readonly object gate = new object();
        private readonly BoundedText stdout = new BoundedText();
        private readonly BoundedText stderr = new BoundedText();
        private readonly string directory;
        private readonly string executable;
        private readonly int pid;
        private readonly int duration;
        private Process child;
        private Task monitor = Task.CompletedTask;
        private Task stop;
        private Task outputDrain;
        private Task errorDrain;
        private Stopwatch elapsed;
        private string status = "Not started";
        private string arguments = "";
        private string hash = "";
        private string version = "";
        private string help = "";
        private string error = "";
        private DateTime? startedUtc;
        private DateTime? endedUtc;
        private int? exitCode;
        private bool forcedStop;
        private bool disposed;
        private bool running;
        private bool exitConfirmed = true; // No child exists before Start succeeds.
        private Task reaper;

        private PresentMonCapture(string executable, int pid, int duration, string directory)
        {
            this.executable = executable;
            this.pid = pid;
            this.duration = duration;
            this.directory = directory;
        }

        public string Status { get { lock (gate) return status; } }
        public bool IsRunning { get { lock (gate) return running; } }

        public static Task<PresentMonCapture> StartAsync(string executable, int pid,
            int durationSeconds, string sessionDirectory)
        {
            if (String.IsNullOrWhiteSpace(executable)) throw new ArgumentException("Select PresentMon.exe", "executable");
            if (pid <= 0) throw new ArgumentOutOfRangeException("pid");
            if (durationSeconds < 5 || durationSeconds > 3600) throw new ArgumentOutOfRangeException("durationSeconds");
            if (String.IsNullOrWhiteSpace(sessionDirectory)) throw new ArgumentException("Session directory is required", "sessionDirectory");
            var capture = new PresentMonCapture(Path.GetFullPath(executable), pid, durationSeconds,
                Path.GetFullPath(sessionDirectory));
            return Task.Run(async () => { await capture.StartCoreAsync().ConfigureAwait(false); return capture; });
        }

        private async Task StartCoreAsync()
        {
            try
            {
                Directory.CreateDirectory(directory);
                if (!File.Exists(executable)) throw new FileNotFoundException("The selected PresentMon executable does not exist", executable);
                using (var stream = File.OpenRead(executable))
                using (var sha = SHA256.Create())
                    hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                version = FileVersionInfo.GetVersionInfo(executable).FileVersion ?? "";
                help = await ProbeHelpAsync(executable).ConfigureAwait(false);
                string[] required = { "--process_id", "--output_file", "--timed", "--terminate_after_timed" };
                foreach (string flag in required)
                    if (!HasFlag(help, flag)) throw new NotSupportedException("Incompatible PresentMon: --help does not advertise " + flag);
                string output = Path.Combine(directory, "frame-times.csv");
                // Never silently reuse old data, or overwrite a pre-existing capture.
                if (File.Exists(output)) throw new IOException("frame-times.csv already exists in the session directory");
                arguments = "--process_id " + pid.ToString(CultureInfo.InvariantCulture) +
                    " --output_file " + QuoteArgument(output) + " --timed " + duration.ToString(CultureInfo.InvariantCulture) +
                    " --terminate_after_timed" + (HasFlag(help, "--no_console_stats") ? " --no_console_stats" : "");
                child = new Process { StartInfo = CreateStartInfo(executable, arguments) };
                if (!child.Start()) throw new IOException("PresentMon did not start");
                exitConfirmed = false;
                startedUtc = DateTime.UtcNow;
                elapsed = Stopwatch.StartNew();
                outputDrain = DrainAsync(child.StandardOutput, stdout);
                errorDrain = DrainAsync(child.StandardError, stderr);
                lock (gate) { running = true; status = "Capturing frame times"; }
                Persist();
                monitor = Task.Run(() => MonitorAsync());
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
                if (child != null && !exitConfirmed)
                {
                    KillOwned();
                    try { exitConfirmed = child.WaitForExit(2000); }
                    catch (Exception waitError) when (waitError is InvalidOperationException || waitError is System.ComponentModel.Win32Exception)
                    { exitConfirmed = false; }
                }
                if (exitConfirmed && child != null) { child.Dispose(); child = null; }
                lock (gate) { running = !exitConfirmed; status = "Frame capture unavailable: " + ex.Message; }
                if (exitConfirmed) endedUtc = DateTime.UtcNow;
                Persist();
                if (!exitConfirmed)
                {
                    monitor = Task.FromException(new IOException("PresentMon startup cleanup is incomplete: owned child exit could not be confirmed.", ex));
                    reaper = Task.Run(() => ReapOwnedAsync());
                }
            }
        }

        private async Task MonitorAsync()
        {
            try
            {
                while (!child.WaitForExit(200))
                {
                    // A tool that ignores --timed must never become an unattended orphan.
                    if (elapsed.Elapsed.TotalSeconds > duration + 3)
                    {
                        forcedStop = true;
                        KillOwned();
                        if (!child.WaitForExit(2000)) throw new IOException("Owned PresentMon did not exit after stop request");
                    }
                }
                exitConfirmed = true;
                exitCode = child.ExitCode;
                await Task.WhenAny(Task.WhenAll(outputDrain ?? Task.CompletedTask, errorDrain ?? Task.CompletedTask), Task.Delay(1000)).ConfigureAwait(false);
                lock (gate)
                {
                    status = forcedStop ? "Stopped early/forced; partial frame data (CSV tail may be missing)" :
                        exitCode != 0 ? "PresentMon exited with code " + exitCode + "; frame data may be incomplete" :
                        !File.Exists(Path.Combine(directory, "frame-times.csv")) ? "PresentMon completed without frame data" : "Frame capture completed";
                }
            }
            catch (Exception ex)
            {
                error = ex.GetType().Name + ": " + ex.Message;
                lock (gate) status = "Frame capture failed: " + ex.Message;
                KillOwned();
                try { exitConfirmed = child != null && child.WaitForExit(2000); }
                catch (Exception waitError) when (waitError is InvalidOperationException || waitError is System.ComponentModel.Win32Exception)
                { exitConfirmed = false; }
                if (!exitConfirmed)
                    throw new IOException("PresentMon stop is incomplete: owned child exit could not be confirmed. Raw session data must be preserved without reporting a completed archive.", ex);
            }
            finally
            {
                lock (gate) running = !exitConfirmed;
                if (exitConfirmed) endedUtc = DateTime.UtcNow;
                Persist();
                lock (gate)
                {
                    if (exitConfirmed && child != null) { child.Dispose(); child = null; }
                    else if (child != null) reaper = Task.Run(() => ReapOwnedAsync());
                }
            }
        }

        // A failed monitor keeps ownership of its handle. Cleanup may continue in the
        // background, but the faulted monitor/stop task permanently rejects archiving.
        private async Task ReapOwnedAsync()
        {
            while (true)
            {
                bool exited = false;
                lock (gate)
                {
                    if (child == null) return;
                    try { exited = child.WaitForExit(0); }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                    if (exited) { exitConfirmed = true; exitCode = child.ExitCode; }
                }
                if (exited) break;
                await Task.Delay(1000).ConfigureAwait(false);
            }
            await Task.WhenAny(Task.WhenAll(outputDrain ?? Task.CompletedTask, errorDrain ?? Task.CompletedTask), Task.Delay(1000)).ConfigureAwait(false);
            lock (gate)
            {
                running = false;
                endedUtc = DateTime.UtcNow;
                status = "PresentMon exited after an incomplete stop; raw partial session retained";
            }
            Persist();
            lock (gate) { child.Dispose(); child = null; }
        }

        /// <summary>Returns promptly to the UI. At the selected duration, allows a short natural CSV flush.</summary>
        public Task StopAsync()
        {
            lock (gate)
            {
                if (stop == null) stop = Task.Run(() => StopCoreAsync());
                return stop;
            }
        }

        private async Task StopCoreAsync()
        {
            // Success means the monitor has also finished CSV/log/metadata cleanup.
            // IsRunning may already be false while that final cleanup is still in progress.
            if (IsRunning && elapsed.Elapsed.TotalSeconds >= duration - 0.25)
                await Task.WhenAny(monitor, Task.Delay(2000)).ConfigureAwait(false);
            if (IsRunning)
            {
                lock (gate)
                {
                    if (child != null && !child.HasExited) { forcedStop = true; KillOwned(); }
                }
            }
            Task completed = await Task.WhenAny(monitor, Task.Delay(4000)).ConfigureAwait(false);
            if (completed != monitor)
            {
                KillOwned();
                throw new TimeoutException("PresentMon stop is incomplete: the owned capture monitor did not settle within four seconds. Preserve the raw partial session; do not report or archive it as complete.");
            }
            await monitor.ConfigureAwait(false);
            lock (gate)
            {
                if (!exitConfirmed)
                    throw new IOException("PresentMon stop is incomplete: owned child exit has not been confirmed.");
            }
        }

        private void KillOwned()
        {
            lock (gate)
            {
                try { if (child != null && !child.HasExited) child.Kill(); }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception ex) { error = "Could not stop owned PresentMon: " + ex.Message; }
            }
        }

        public void Dispose()
        {
            lock (gate)
            {
                if (disposed) return;
                disposed = true;
                if (child != null && running)
                {
                    forcedStop = true;
                    KillOwned();
                }
            }
            // No blocking WaitForExit on a caller/UI thread. The monitor drains and disposes.
        }

        private static ProcessStartInfo CreateStartInfo(string executable, string arguments)
        {
            return new ProcessStartInfo
            {
                FileName = executable, Arguments = arguments, UseShellExecute = false,
                CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                WorkingDirectory = Path.GetDirectoryName(executable)
            };
        }

        private static async Task<string> ProbeHelpAsync(string executable)
        {
            var probe = new Process { StartInfo = CreateStartInfo(executable, "--help") };
            bool started = false;
            try
            {
                var output = new BoundedText();
                var errors = new BoundedText();
                started = probe.Start();
                if (!started) throw new IOException("Could not probe PresentMon --help");
                var outTask = DrainAsync(probe.StandardOutput, output);
                var errTask = DrainAsync(probe.StandardError, errors);
                if (!probe.WaitForExit(4000))
                {
                    TryKillProbe(probe);
                    bool exited = false;
                    try { exited = probe.WaitForExit(1000); }
                    catch (InvalidOperationException) { }
                    catch (System.ComponentModel.Win32Exception) { }
                    throw new TimeoutException("PresentMon --help exceeded four seconds" +
                        (exited ? "" : "; owned probe exit is unconfirmed and background cleanup retains its handle"));
                }
                await Task.WhenAny(Task.WhenAll(outTask, errTask), Task.Delay(500)).ConfigureAwait(false);
                return output.ToString() + "\n" + errors.ToString();
            }
            catch
            {
                if (started) TryKillProbe(probe);
                throw;
            }
            finally
            {
                if (started)
                {
                    // Reaper retains this exact owned Process until exit is confirmed,
                    // including when Kill is denied or its bounded wait times out.
                    Task cleanup = ReapProbeAsync(probe);
                }
                else probe.Dispose(); // Start failed: there is no owned child.
            }
        }

        private static void TryKillProbe(Process probe)
        {
            try { if (!probe.HasExited) probe.Kill(); }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception) { }
        }

        private static async Task ReapProbeAsync(Process probe)
        {
            while (true)
            {
                bool exited = false;
                try { exited = probe.WaitForExit(0); }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception) { }
                if (exited) { probe.Dispose(); return; }
                await Task.Delay(1000).ConfigureAwait(false);
            }
        }

        private static bool HasFlag(string text, string flag)
        {
            return Regex.IsMatch(text, @"(?<![\w-])" + Regex.Escape(flag) + @"(?![\w-])", RegexOptions.CultureInvariant);
        }

        // Windows CommandLineToArgvW/MS CRT quoting: preserve spaces, quotes, and trailing slashes.
        internal static string QuoteArgument(string value)
        {
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') result.Append('\\', slashes * 2 + 1).Append('"');
                else result.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }

        private static async Task DrainAsync(StreamReader reader, BoundedText destination)
        {
            var buffer = new char[2048];
            try
            {
                int count;
                while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) > 0)
                    destination.Append(buffer, count);
            }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
        }

        private void Persist()
        {
            try
            {
                File.WriteAllText(Path.Combine(directory, "presentmon.log"), "STDOUT (bounded):\r\n" + stdout + "\r\nSTDERR (bounded):\r\n" + stderr, new UTF8Encoding(false));
                string json = "{\n" +
                    Field("executable", executable) + Field("sha256", hash) + Field("fileVersion", version) +
                    Field("arguments", arguments) + Field("help", help) + Field("status", Status) + Field("error", error) +
                    Field("startedUtc", startedUtc.HasValue ? startedUtc.Value.ToString("o", CultureInfo.InvariantCulture) : null) +
                    Field("endedUtc", endedUtc.HasValue ? endedUtc.Value.ToString("o", CultureInfo.InvariantCulture) : null) +
                    "  \"pid\": " + pid.ToString(CultureInfo.InvariantCulture) + ",\n" +
                    "  \"durationSeconds\": " + duration.ToString(CultureInfo.InvariantCulture) + ",\n" +
                    "  \"exitConfirmed\": " + (exitConfirmed ? "true" : "false") + ",\n" +
                    "  \"forcedStop\": " + (forcedStop ? "true" : "false") + ",\n" +
                    "  \"csvTailMayBeMissing\": " + (forcedStop ? "true" : "false") + ",\n" +
                    "  \"exitCode\": " + (exitCode.HasValue ? exitCode.Value.ToString(CultureInfo.InvariantCulture) : "null") + "\n}\n";
                File.WriteAllText(Path.Combine(directory, "presentmon.json"), json, new UTF8Encoding(false));
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is System.Security.SecurityException)
            {
                lock (gate) status += " (capture metadata could not be saved: " + ex.Message + ")";
            }
        }

        private static string Field(string name, string value) { return "  " + Json(name) + ": " + Json(value) + ",\n"; }
        private static string Json(string value)
        {
            if (value == null) return "null";
            var result = new StringBuilder("\"");
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': result.Append("\\\""); break;
                    case '\\': result.Append("\\\\"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (c < 32 || Char.IsSurrogate(c)) result.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else result.Append(c);
                        break;
                }
            }
            return result.Append('"').ToString();
        }

        private sealed class BoundedText
        {
            private readonly StringBuilder text = new StringBuilder();
            private bool truncated;
            public void Append(char[] buffer, int count)
            {
                lock (text)
                {
                    int keep = Math.Min(count, TextLimit - text.Length);
                    if (keep > 0) text.Append(buffer, 0, keep);
                    if (keep < count) truncated = true;
                }
            }
            public override string ToString() { lock (text) return text.ToString() + (truncated ? "\n[truncated]" : ""); }
        }
    }
}
