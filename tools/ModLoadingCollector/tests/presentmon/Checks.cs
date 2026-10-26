using System;
using System.IO;
using System.Threading.Tasks;
using XxmiCollector;
class Checks
{
    static int checks;
    static void Assert(bool value, string message) { if (!value) throw new Exception(message); checks++; }
    static async Task Main()
    {
        string root = Path.Combine(Path.GetFullPath("work/presentmon-tests"), "run " + Guid.NewGuid());
        Directory.CreateDirectory(root);
        string fake = Path.Combine(root, "fake tool.exe");
        File.WriteAllText(fake, "#!/usr/bin/python3\nimport sys,time\nif '--help' in sys.argv:\n print('--process_id --output_file --timed --terminate_after_timed --no_console_stats');sys.exit(0)\np=sys.argv[sys.argv.index('--output_file')+1]\nopen(p,'w').write('MsBetweenPresents\\n16.7\\n')\nprint('a'*100000,flush=True)\nprint('b'*100000,file=sys.stderr,flush=True)\ntime.sleep(int(sys.argv[sys.argv.index('--timed')+1]))\n");
        System.Diagnostics.Process.Start("chmod", "+x \"" + fake + "\"").WaitForExit();
        Assert(PresentMonCapture.QuoteArgument("a b\\") == "\"a b\\\\\"", "trailing slash quoting");
        Assert(PresentMonCapture.QuoteArgument("a\"b") == "\"a\\\"b\"", "quote quoting");
        string naturalDir = Path.Combine(root,"natural");
        using (var capture = await PresentMonCapture.StartAsync(fake, 42, 5, naturalDir))
        {
            Assert(capture.IsRunning,"started");
            await Task.Delay(5050);
            await capture.StopAsync();
            Assert(!capture.IsRunning,"natural stopped");
            Assert(capture.Status == "Frame capture completed",capture.Status);
            string metadata = File.ReadAllText(Path.Combine(naturalDir,"presentmon.json"));
            Assert(metadata.Contains("\"forcedStop\": false"),"natural metadata");
            Assert(metadata.Contains("--no_console_stats"),"optional flag");
            Assert(File.ReadAllText(Path.Combine(naturalDir,"presentmon.log")).Length < 66000,"bounded diagnostics");
            // Deterministically model a monitor that has not settled after child exit.
            // No unkillable process or production test-only seam is needed.
            var pending = new TaskCompletionSource<bool>();
            var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
            typeof(PresentMonCapture).GetField("monitor", flags).SetValue(capture, pending.Task);
            typeof(PresentMonCapture).GetField("stop", flags).SetValue(capture, null);
            bool incomplete = false;
            var returnTimer = System.Diagnostics.Stopwatch.StartNew();
            Task stopping = capture.StopAsync();
            Assert(returnTimer.ElapsedMilliseconds < 500,"stop does not block caller");
            try { await stopping; }
            catch (TimeoutException ex) { incomplete = ex.Message.Contains("incomplete"); }
            finally { pending.SetResult(true); }
            Assert(incomplete,"unsettled monitor fails stop instead of reporting success");
            // A settled monitor alone does not prove child exit. Simulate the failed
            // cleanup state after real child completion without creating an unkillable process.
            typeof(PresentMonCapture).GetField("exitConfirmed", flags).SetValue(capture, false);
            typeof(PresentMonCapture).GetField("monitor", flags).SetValue(capture, Task.CompletedTask);
            typeof(PresentMonCapture).GetField("stop", flags).SetValue(capture, null);
            bool unconfirmed = false;
            try { await capture.StopAsync(); }
            catch (IOException ex) { unconfirmed = ex.Message.Contains("exit has not been confirmed"); }
            Assert(unconfirmed,"settled monitor without exit confirmation rejects archive");
            typeof(PresentMonCapture).GetField("monitor", flags).SetValue(capture,
                Task.FromException(new IOException("owned child exit could not be confirmed")));
            typeof(PresentMonCapture).GetField("stop", flags).SetValue(capture, null);
            bool faultObserved = false;
            try { await capture.StopAsync(); }
            catch (IOException ex) { faultObserved = ex.Message.Contains("could not be confirmed"); }
            Assert(faultObserved,"faulted monitor cannot become a successful stop");
            bool retryRejected = false;
            try { await capture.StopAsync(); }
            catch (IOException) { retryRejected = true; }
            Assert(retryRejected,"failed stop remains explicitly unrecoverable for archive retries");
            typeof(PresentMonCapture).GetField("exitConfirmed", flags).SetValue(capture, true);
        }
        string earlyDir = Path.Combine(root,"early");
        using (var capture = await PresentMonCapture.StartAsync(fake,42,5,earlyDir))
        {
            await Task.Delay(200);
            var timer = System.Diagnostics.Stopwatch.StartNew();
            await capture.StopAsync();
            Assert(timer.ElapsedMilliseconds < 3000,"bounded early stop");
            Assert(!capture.IsRunning && capture.Status.Contains("partial"),"partial marked");
            Assert(File.ReadAllText(Path.Combine(earlyDir,"presentmon.json")).Contains("\"forcedStop\": true"),"forced metadata");
        }
        using (var capture = await PresentMonCapture.StartAsync(Path.Combine(root,"missing.exe"),42,5,Path.Combine(root,"missing")))
            Assert(!capture.IsRunning && capture.Status.Contains("unavailable"),"missing tool degrades");
        File.WriteAllText(fake,"#!/usr/bin/python3\nprint('--process_id --output_file --timed')\n");
        using (var capture = await PresentMonCapture.StartAsync(fake,42,5,Path.Combine(root,"incompatible")))
            Assert(!capture.IsRunning && capture.Status.Contains("Incompatible"),"missing flag refused");
        File.WriteAllText(fake,"#!/usr/bin/python3\nimport time\ntime.sleep(30)\n");
        using (var capture = await PresentMonCapture.StartAsync(fake,42,5,Path.Combine(root,"timeout")))
            Assert(!capture.IsRunning && capture.Status.Contains("four seconds"),"probe timeout");
        // Invoke the actual probe reaper with a live, controlled short-lived process.
        // This models a bounded stop that could not yet confirm exit, without OS kill failures.
        File.WriteAllText(fake,"#!/usr/bin/python3\nimport time\ntime.sleep(1)\n");
        var ownedProbe = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            { FileName = fake, UseShellExecute = false });
        var reaperMethod = typeof(PresentMonCapture).GetMethod("ReapProbeAsync",
            System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
        Task probeCleanup = (Task)reaperMethod.Invoke(null, new object[] { ownedProbe });
        Assert(!probeCleanup.IsCompleted && !ownedProbe.HasExited,"live probe handle retained");
        Task settledProbe = await Task.WhenAny(probeCleanup,Task.Delay(4000));
        Assert(settledProbe == probeCleanup,"probe reaper settles after confirmed natural exit");
        await probeCleanup;
        bool probeDisposed = false;
        try { ownedProbe.WaitForExit(0); }
        catch (InvalidOperationException) { probeDisposed = true; }
        Assert(probeDisposed,"probe handle disposed only after confirmed exit");
        Console.WriteLine(checks + " assertions passed; Linux fake process only");
    }
}
