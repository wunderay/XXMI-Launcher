// Read-only, bounded file-cache warmup before the game starts. Never creates
// GPU resources, retains model copies, or runs during gameplay.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

internal static class ModelPreloader
{
    internal const long GiB = 1024L * 1024 * 1024;
    internal const long ReserveBytes = 8 * GiB;
    internal sealed class Asset { public string Path; public long Bytes; }
    internal sealed class Result {
        public bool enabled = true;
        public string policy = "model-file-cache-v1";
        public int candidate_files, completed_files, skipped_files, failed_files;
        public int reader_threads = 1;
        public long candidate_bytes, bytes_read, budget_bytes, elapsed_ms;
        public string stop_reason = "complete";
        public string scope = "Read-only model files into reclaimable Windows file cache. GPU resources are not precreated; residency and game speedup are not guaranteed.";
    }
    [StructLayout(LayoutKind.Sequential)]
    struct MemoryStatus {
        public uint length, load;
        public ulong totalPhysical, availablePhysical, totalPageFile, availablePageFile, totalVirtual, availableVirtual, extendedVirtual;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);
    internal static long AvailableMemory() {
        var status = new MemoryStatus { length = (uint)Marshal.SizeOf(typeof(MemoryStatus)) };
        return GlobalMemoryStatusEx(ref status) ? (long)Math.Min(status.availablePhysical, (ulong)Int64.MaxValue) : 0;
    }
    internal static long Budget(long available) { return Math.Max(0, Math.Min(4 * GiB, available - ReserveBytes)); }
    internal static bool ModelName(string name) {
        if (name.StartsWith("DISABLED", StringComparison.OrdinalIgnoreCase)) return false;
        string extension = System.IO.Path.GetExtension(name);
        return extension.Equals(".buf", StringComparison.OrdinalIgnoreCase) || extension.Equals(".ib", StringComparison.OrdinalIgnoreCase)
            || extension.Equals(".vb", StringComparison.OrdinalIgnoreCase);
    }
    internal static List<Asset> Discover(string root, CancellationToken token) {
        var result = new List<Asset>(); var pending = new Stack<string>();
        if (!Directory.Exists(root)) return result;
        pending.Push(System.IO.Path.GetFullPath(root)); int directories = 0, entries = 0; var discoveryTime = Stopwatch.StartNew();
        while (pending.Count != 0) {
            token.ThrowIfCancellationRequested();
            if (++directories > 10000 || result.Count >= 20000) throw new IOException("Mod inventory exceeds the preload safety limit.");
            string folder = pending.Pop();
            try {
                if ((File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0) continue;
                foreach (string path in Directory.EnumerateFileSystemEntries(folder)) {
                    token.ThrowIfCancellationRequested();
                    if (++entries > 100000 || result.Count >= 20000 || discoveryTime.ElapsedMilliseconds > 5000)
                        throw new InvalidOperationException("Model inventory exceeded its bounded discovery window.");
                    if (System.IO.Path.GetFileName(path).StartsWith("DISABLED", StringComparison.OrdinalIgnoreCase)) continue;
                    var attributes = File.GetAttributes(path);
                    if ((attributes & FileAttributes.ReparsePoint) != 0) continue;
                    if ((attributes & FileAttributes.Directory) != 0) pending.Push(path);
                    else if (ModelName(path)) { long bytes = new FileInfo(path).Length; if (bytes > 0) result.Add(new Asset { Path = path, Bytes = bytes }); }
                }
            } catch (UnauthorizedAccessException) { /* An inaccessible optional mod is not a launch failure. */ }
              catch (IOException) { /* File changes during discovery are left to the game's ordinary loader. */ }
        }
        result.Sort(delegate(Asset a, Asset b) { return StringComparer.OrdinalIgnoreCase.Compare(a.Path, b.Path); });
        return result;
    }
    internal static Result Warm(IList<Asset> assets, long budget, Func<long> availableMemory,
        Func<Asset, Stream> open, IProgress<string> progress, CancellationToken token, Func<long> clock, long limitMs)
    {
        var result = new Result { candidate_files = assets.Count, budget_bytes = budget };
        foreach (var asset in assets) result.candidate_bytes += asset.Bytes;
        long started = clock(), lastProgress = -1000;
        var buffer = new byte[1024 * 1024];
        foreach (var asset in assets) {
            if (token.IsCancellationRequested) { result.stop_reason = "cancelled"; break; }
            if (clock() - started >= limitMs) { result.stop_reason = "time_limit"; break; }
            if (availableMemory() < ReserveBytes) { result.stop_reason = "memory_reserve"; break; }
            if (asset.Bytes > budget - result.bytes_read) { ++result.skipped_files; continue; }
            try {
                using (var stream = open(asset)) {
                    long remaining = asset.Bytes;
                    while (remaining != 0) {
                        if (token.IsCancellationRequested) { result.stop_reason = "cancelled"; break; }
                        if (clock() - started >= limitMs) { result.stop_reason = "time_limit"; break; }
                        if (availableMemory() < ReserveBytes) { result.stop_reason = "memory_reserve"; break; }
                        int count = stream.Read(buffer, 0, (int)Math.Min(remaining, buffer.Length));
                        if (count == 0) throw new EndOfStreamException();
                        remaining -= count; result.bytes_read += count;
                        if (progress != null && clock() - lastProgress >= 200) {
                            progress.Report(String.Format("Preparing models · {0:N0} / {1:N0} MiB", result.bytes_read / 1048576, Math.Min(budget, result.candidate_bytes) / 1048576));
                            lastProgress = clock();
                        }
                    }
                    if (remaining == 0) ++result.completed_files;
                }
            } catch (IOException) { ++result.failed_files; }
              catch (UnauthorizedAccessException) { ++result.failed_files; }
            if (result.stop_reason != "complete") break;
        }
        if (result.stop_reason == "complete" && result.skipped_files != 0) result.stop_reason = "byte_budget";
        if (result.stop_reason == "complete" && result.failed_files != 0) result.stop_reason = "read_errors";
        result.elapsed_ms = clock() - started; return result;
    }
    sealed class CountedReadStream : Stream {
        readonly Stream inner; readonly Action<int> count;
        internal CountedReadStream(Stream stream, Action<int> counter) { inner = stream; count = counter; }
        public override int Read(byte[] bytes, int offset, int length) { int read = inner.Read(bytes, offset, length); count(read); return read; }
        public override bool CanRead { get { return true; } } public override bool CanSeek { get { return false; } } public override bool CanWrite { get { return false; } }
        public override long Length { get { return inner.Length; } } public override long Position { get { return inner.Position; } set { throw new NotSupportedException(); } }
        public override void Flush() { } public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
        public override void SetLength(long value) { throw new NotSupportedException(); } public override void Write(byte[] b, int o, int c) { throw new NotSupportedException(); }
        protected override void Dispose(bool disposing) { if (disposing) inner.Dispose(); base.Dispose(disposing); }
    }
    internal static Result WarmParallel(IList<Asset> assets, long budget, Func<long> memory, Func<Asset, Stream> open,
        IProgress<string> progress, CancellationToken token, Func<long> clock, long limitMs)
    {
        long started = clock(), planned = 0, read = 0, lastProgress = -1000;
        var result = new Result { candidate_files = assets.Count, budget_bytes = budget, reader_threads = 4 };
        var groups = new List<Asset>[4]; var weights = new long[4]; var outcomes = new Result[4];
        for (int i = 0; i < 4; i++) groups[i] = new List<Asset>();
        foreach (var asset in assets) {
            result.candidate_bytes += asset.Bytes;
            if (asset.Bytes > budget - planned) { ++result.skipped_files; continue; }
            int lightest = 0; for (int i = 1; i < 4; i++) if (weights[i] < weights[lightest]) lightest = i;
            groups[lightest].Add(asset); weights[lightest] += asset.Bytes; planned += asset.Bytes;
        }
        var progressLock = new object();
        Parallel.For(0, 4, new ParallelOptions { MaxDegreeOfParallelism = 4 }, delegate(int i) {
            outcomes[i] = Warm(groups[i], weights[i], memory, delegate(Asset asset) {
                return new CountedReadStream(open(asset), delegate(int count) {
                    long current = Interlocked.Add(ref read, count);
                    if (progress != null) lock (progressLock) {
                        if (clock() - lastProgress >= 200) {
                            lastProgress = clock();
                            progress.Report(String.Format("Preparing models · {0:N0} / {1:N0} MiB", current / 1048576, planned / 1048576));
                        }
                    }
                });
            }, null, token, clock, Math.Max(0, limitMs - (clock() - started)));
        });
        foreach (var outcome in outcomes) {
            result.completed_files += outcome.completed_files; result.failed_files += outcome.failed_files;
            result.bytes_read += outcome.bytes_read;
            if (outcome.stop_reason != "complete") result.stop_reason = outcome.stop_reason;
        }
        if (token.IsCancellationRequested) result.stop_reason = "cancelled";
        else if (result.stop_reason == "complete" && result.skipped_files != 0) result.stop_reason = "byte_budget";
        result.elapsed_ms = clock() - started; return result;
    }
    internal static Result Prepare(string mods, IProgress<string> progress, CancellationToken token) {
        var watch = Stopwatch.StartNew();
        if (progress != null) progress.Report("Checking model files…");
        var assets = Discover(mods, token);
        return WarmParallel(assets, Budget(AvailableMemory()), AvailableMemory, delegate(Asset asset) {
            if ((File.GetAttributes(asset.Path) & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked file skipped.");
            // RandomAccess disables sequential cache trimming. Reads still run in
            // one pass per file; Windows can reclaim these pages under pressure.
            return new FileStream(asset.Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 4096, FileOptions.RandomAccess);
        }, progress, token, delegate { return watch.ElapsedMilliseconds; }, Math.Max(0, 30000 - watch.ElapsedMilliseconds));
    }
    internal static void SelfTest() {
        Action<bool, string> require = delegate(bool condition, string text) { if (!condition) throw new Exception(text); };
        require(Budget(7 * GiB) == 0 && Budget(10 * GiB) == 2 * GiB && Budget(32 * GiB) == 4 * GiB, "Memory budget");
        require(ModelName("Mesh.BUF") && ModelName("Mesh.ib") && !ModelName("texture.dds") && !ModelName("DISABLED-Mesh.buf"), "Model inventory filters");
        var assets = new[] { new Asset {Path = "a", Bytes = 4}, new Asset {Path = "b", Bytes = 4} };
        Func<Asset, Stream> open = delegate(Asset a) { return new MemoryStream(new byte[4], false); };
        var complete = Warm(assets, 8, delegate { return 12 * GiB; }, open, null, CancellationToken.None, delegate {return 0;}, 30000);
        require(complete.completed_files == 2 && complete.bytes_read == 8 && complete.stop_reason == "complete", "Full pass");
        var bounded = Warm(assets, 4, delegate { return 12 * GiB; }, open, null, CancellationToken.None, delegate {return 0;}, 30000);
        require(bounded.bytes_read == 4 && bounded.skipped_files == 1 && bounded.stop_reason == "byte_budget", "Byte budget");
        var low = Warm(assets, 8, delegate { return 7 * GiB; }, open, null, CancellationToken.None, delegate {return 0;}, 30000);
        require(low.bytes_read == 0 && low.stop_reason == "memory_reserve", "Memory pressure");
        var cancel = new CancellationTokenSource(); cancel.Cancel();
        require(Warm(assets, 8, delegate { return 12 * GiB; }, open, null, cancel.Token, delegate {return 0;}, 30000).stop_reason == "cancelled", "Cancellation");
        int ticks = 0;
        require(Warm(assets, 8, delegate { return 12 * GiB; }, open, null, CancellationToken.None, delegate {return ticks++ * 30000;}, 30000).stop_reason == "time_limit", "Deadline");
        var errors = Warm(assets, 8, delegate { return 12 * GiB; }, delegate(Asset a) {throw new IOException();}, null, CancellationToken.None, delegate {return 0;}, 30000);
        require(errors.failed_files == 2 && errors.stop_reason == "read_errors", "Read error fallback");
        var shortRead = Warm(assets, 8, delegate { return 12 * GiB; }, delegate(Asset a) {return new MemoryStream(new byte[2]);}, null, CancellationToken.None, delegate {return 0;}, 30000);
        require(shortRead.bytes_read == 4 && shortRead.completed_files == 0 && shortRead.failed_files == 2, "Truncated file");
        var parallel = WarmParallel(assets, 8, delegate { return 12 * GiB; }, open, null, CancellationToken.None, delegate { return 0; }, 30000);
        require(parallel.completed_files == 2 && parallel.bytes_read == 8 && parallel.stop_reason == "complete", "Parallel full pass");
        parallel = WarmParallel(assets, 4, delegate { return 12 * GiB; }, open, null, CancellationToken.None, delegate { return 0; }, 30000);
        require(parallel.completed_files == 1 && parallel.bytes_read == 4 && parallel.skipped_files == 1, "Parallel shared budget");
        parallel = WarmParallel(assets, 8, delegate { return 7 * GiB; }, open, null, CancellationToken.None, delegate { return 0; }, 30000);
        require(parallel.bytes_read == 0 && parallel.stop_reason == "memory_reserve", "Parallel memory pressure");
        parallel = WarmParallel(assets, 8, delegate { return 12 * GiB; }, open, null, cancel.Token, delegate { return 0; }, 30000);
        require(parallel.bytes_read == 0 && parallel.stop_reason == "cancelled", "Parallel cancellation");
    }
}
