using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.Serialization.Json;
using System.Text;
using XxmiCollector;

internal static class ReportTests
{
    private const string Header = "stage,kind,capture_session,start_ns,duration_ns,start_frame,end_frame,thread_id,object_id,bytes,result\n";
    private static int assertions;
    private static string root;
    private static void Check(bool condition, string message) { assertions++; if (!condition) throw new Exception(message); }
    private static string Make(string name)
    {
        string path = Path.Combine(root, name); Directory.CreateDirectory(path);
        File.WriteAllText(Path.Combine(path, "session.json"), "{\"scenario\":\"<script>scenario</script>\",\"runKind\":\"warm\",\"processId\":42,\"cpuNormalization\":\"all logical processors\"}");
        File.WriteAllText(Path.Combine(path, "inventory.json"), "{\"system\":{\"name\":\"<machine>\"},\"modules\":[]}");
        return path;
    }
    private static string Trace(string path, string name, string text)
    {
        string traces = Path.Combine(path, "native-traces"); Directory.CreateDirectory(traces);
        string result = Path.Combine(traces, name + ".csv"); File.WriteAllText(result, text); return result;
    }
    private static string Event(string stage, string kind, int start, string duration, int session = 1)
    { return String.Join(",", stage, kind, session, start, duration, 1, 1, 2, 3, 0, "") + "\n"; }
    private static string Footer(ulong accepted, ulong full = 0, ulong contention = 0, ulong drained = 0)
    { return "# cumulative_accepted=" + accepted + ";dropped_full=" + full + ";dropped_contention=" + contention + ";drained=" + drained + "\n"; }
    private static ReportBuilder.Analysis Run(string path)
    {
        string html = ReportBuilder.Generate(path); Check(File.Exists(html), "HTML returned exists");
        using (var stream = File.OpenRead(Path.Combine(path, "analysis.json")))
            return (ReportBuilder.Analysis)new DataContractJsonSerializer(typeof(ReportBuilder.Analysis), new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true }).ReadObject(stream);
    }
    public static int Main(string[] args)
    {
        root = args.Length == 0 ? Path.GetFullPath("work/analysis-tests/fixtures") : Path.GetFullPath(args[0]);
        if (Directory.Exists(root)) Directory.Delete(root, true); Directory.CreateDirectory(root);
        try
        {
            PercentilesAndEvidence(); CumulativeExportsAndCompleteness(); MissingAndEscaping(); CsvBoundsAndInvalidNumbers(); FramesMapping(); ProcessSamples(); JsonBounds(); PresentMonStatus();
            Console.WriteLine("PASS: 8 report fixture groups, " + assertions + " assertions; fixtures: " + root); return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
    private static void PercentilesAndEvidence()
    {
        string path = Make("percentiles");
        var csv = new StringBuilder(Header);
        for (int i = 1; i <= 100; i++) csv.Append(Event("file_read", "duration", i, (i * 1000000).ToString(CultureInfo.InvariantCulture)));
        csv.Append(Event("present_cpu", "duration", 101, "22000000"));
        csv.Append(Event("first_correct_frame", "instant", 102, "0"));
        csv.Append(Event("unknown", "duration", 103, "1"));
        csv.Append(Footer(103, 0, 0, 103)); Trace(path, "mod-load-trace-1", csv.ToString());
        var data = Run(path); var stats = data.NativeStageDurationsMs["file_read"];
        Check(stats.Count == 100 && stats.P50 == 50 && stats.P95 == 95 && stats.P99 == 99 && stats.Maximum == 100, "nearest rank and ns conversion");
        Check(data.NativeStageDurationsMs["present_cpu"].Maximum == 22, "Present CPU preserved as its own scope");
        Check(data.NativeInstantRows == 1 && !data.NativeStageDurationsMs.ContainsKey("first_correct_frame"), "instant correctness not a duration");
        Check(data.FrameTimesMs == null, "Present does not synthesize frames");
        Check(data.NativeFiles[0].MalformedRows == 1, "unknown stage rejected");
    }
    private static void CumulativeExportsAndCompleteness()
    {
        string path = Make("cumulative"); string one = Event("decode", "duration", 1, "1000000");
        Trace(path, "01", Header + one + Footer(10, 2, 3, 8));
        Trace(path, "02", Header + one + Event("decode", "duration", 2, "3000000", 3) + Footer(15, 4, 7, 12));
        Trace(path, "03", Header + Footer(15, 4, 7, 12) + "# export_incomplete=contention_retry_limit;queued_records_retained\n");
        Trace(path, "04", Header + Event("decode", "duration", 3, "4000000"));
        Trace(path, "05", Header + "# cumulative_accepted=19;dropped_full=4;dropped_contention=7;drained=1");
        Trace(path, "06", Header + "# cumulative_accepted=20;dropped_full=\n");
        var data = Run(path);
        Check(data.CumulativeAcceptedMaximum == 15 && data.CumulativeDroppedFullMaximum == 4 && data.CumulativeDroppedContentionMaximum == 7 && data.CumulativeDrainedMaximum == 12, "cumulative maxima never summed across sessions or reexports");
        Check(data.CumulativeCounterFooters == 3, "only full newline-terminated footers counted");
        Check(data.IncompleteContentionExports == 1 && data.NativeFiles.Count(file => file.Complete) == 2, "contention and missing/truncated footer incomplete");
        Check(data.DuplicateNativeRows == 1 && data.NativeRows == 3, "duplicate event identity dedup, unique capture sessions retained");
        Check(data.NativeStageDurationsMs["decode"].Count == 3, "repeated export does not inflate duration samples");
    }
    private static void MissingAndEscaping()
    {
        string path = Make("missing");
        File.WriteAllText(Path.Combine(path, "markers.csv"), "\uFEFFelapsed_ms,utc,label,note\r\n1,now,\"<img src=x onerror=evil()>\",\"quoted, \"\"note\"\"\nsecond line & text\"\r\n");
        File.WriteAllText(Path.Combine(path, "provenance.json"), "{\"source\":\"<foreign>\"}");
        var data = Run(path); string html = File.ReadAllText(Path.Combine(path, "report.html"));
        Check(data.NativeFiles.Count == 0 && data.FrameTimesMs == null && data.ProcessCpuPercent == null, "missing evidence stays missing");
        Check(data.Markers.Count == 1 && data.Markers[0].Note.Contains("quoted, \"note\"\nsecond line"), "BOM multiline quoted CSV parsed");
        Check(!html.Contains("<script>scenario") && !html.Contains("<img src=x") && html.Contains("&lt;script&gt;scenario") && html.Contains("&lt;foreign&gt;"), "metadata, markers, provenance escaped");
        Check(html.Contains("loss counts are unknown") && html.Contains("Correctness latency") && html.Contains("Unmeasured"), "honest missing counters and correctness");
        Check(!html.Contains("<script") && !html.Contains("https://") && html.Contains("default-src 'none'"), "offline report no scripts or remote assets");
    }
    private static void CsvBoundsAndInvalidNumbers()
    {
        string path = Make("bounds");
        Trace(path, "mod-load-trace-bad", Header + Event("decode", "duration", 1, "-1") + Event("decode", "duration", 2, "NaN") + Event("decode", "duration", 3, "Infinity") + Event("decode", "duration", 4, "1e6") + "\"" + new string('x', 70000) + "\n" + Event("decode", "duration", 5, "1000000") + Footer(5));
        File.WriteAllText(Path.Combine(path, "frame-times.csv"), "MsBetweenPresents\n-1\nNaN\nInfinity\n0\n1.5\n\"2.5\"\n\"unterminated");
        var data = Run(path);
        Check(data.NativeRows == 1 && data.NativeFiles[0].MalformedRows == 5 && !data.NativeFiles[0].Complete, "bad/oversized native rows rejected with recovery");
        Check(data.FrameTimesMs.Count == 2 && data.FrameTimesMs.P50 == 1.5 && data.FrameTimesMs.Maximum == 2.5, "nonfinite, negative, zero, malformed frame times rejected");
        Check(data.RejectedRows >= 5, "rejected general rows counted");
        string over = Make("oversize-file"); string file = Trace(over, "huge", "");
        using (var stream = File.OpenWrite(file)) stream.SetLength(64L * 1024 * 1024 + 1);
        var large = Run(over);
        Check(large.NativeFiles[0].LimitReached && !large.NativeFiles[0].Complete && large.NativeRows == 0, "oversize CSV rejected before reading");
    }
    private static void FramesMapping()
    {
        string path = Make("frames"); File.WriteAllText(Path.Combine(path, "frame-times.csv"), "FrameTime,MsBetweenPresents,CPUFrameTime\n55,10,2\n66,20,3\n");
        var data = Run(path); Check(data.FrameTimeColumn == "MsBetweenPresents" && data.FrameTimesMs.Maximum == 20, "explicit primary mapping wins");
        File.WriteAllText(Path.Combine(path, "frame-times.csv"), "FrameTime\n12.25\n13.5\n");
        data = Run(path); Check(data.FrameTimeColumn == "FrameTime" && data.FrameTimesMs.P50 == 12.25, "controlled FrameTime ms fallback");
        File.WriteAllText(Path.Combine(path, "frame-times.csv"), "CPUFrameTime\n10\n");
        data = Run(path); Check(data.FrameTimesMs == null && data.Warnings.Any(text => text.Contains("no usable rows")), "unknown frame field never inferred");
    }
    private static void ProcessSamples()
    {
        string path = Make("samples");
        File.WriteAllText(Path.Combine(path, "samples.csv"), "elapsed_ms,utc,cpu_percent,working_set_bytes,private_bytes,io_read_bytes,io_write_bytes,status\n0,now,10,100,200,1000,2000,ok\n500,now,20,150,250,1500,2200,ok\n1000,now,NaN,-1,Infinity,10,20,ok\n1500,now,30,200,300,110,70,ok\n-1,now,999,999,999,999,999,bad\n1200,now,999,999,999,999,999,bad\n");
        var data = Run(path);
        Check(data.SampleRows == 4 && data.ProcessCpuPercent.Count == 3 && data.WorkingSetBytes.Count == 3 && data.PrivateBytes.Count == 3, "sample metric validity independent; invalid elapsed/out-of-order excluded");
        Check(data.ObservedIoReadDeltaBytes == 600 && data.ObservedIoWriteDeltaBytes == 250, "counter reset excluded from IO deltas");
        Check(data.FrameTimesMs == null && data.SampleSpanMs == 1500, "samples do not become frame intervals");
        File.WriteAllText(Path.Combine(path, "samples.csv"), "elapsed_ms,io_read_bytes,io_write_bytes\n5000,100,200\n");
        data = Run(path);
        Check(data.SampleSpanMs == 0 && data.ObservedIoReadDeltaBytes == null && data.ObservedIoWriteDeltaBytes == null, "one observation cannot establish IO delta or sample span");
    }
    private static void PresentMonStatus()
    {
        string path = Make("presentmon-status");
        File.WriteAllText(Path.Combine(path, "frame-times.csv"), "FrameTime\n16.5\n");
        File.WriteAllText(Path.Combine(path, "presentmon.json"), "{\"executable\":\"<tool>.exe\",\"sha256\":\"abc123\",\"fileVersion\":\"2.3\",\"status\":\"Stopped early/forced\",\"forcedStop\":true,\"csvTailMayBeMissing\":true,\"exitCode\":-1,\"error\":\"<capture failed>\"}");
        var data = Run(path); string html = File.ReadAllText(Path.Combine(path, "report.html"));
        Check(data.FrameTimesMs.Count == 1 && data.FrameCaptureMayBeIncomplete, "partial frame rows retained with incomplete tool status");
        Check(data.Warnings.Any(text => text.Contains("CSV tail may be missing=True") && text.Contains("exitCode=-1")), "forced/nonzero/error capture warning retained");
        Check(data.PresentMonMetadataJson.Contains("abc123") && html.Contains("PresentMon tool provenance") && html.Contains("&lt;tool&gt;.exe") && html.Contains("&lt;capture failed&gt;"), "tool provenance preserved in JSON and escaped HTML");
        File.WriteAllText(Path.Combine(path, "presentmon.json"), "{\"status\":\"Complete\",\"forcedStop\":false,\"csvTailMayBeMissing\":false,\"exitCode\":0,\"error\":null}");
        data = Run(path);
        Check(!data.FrameCaptureMayBeIncomplete && data.FrameCaptureStatus == "Complete", "normal tool completion remains distinct from partial capture");
    }
    private static void JsonBounds()
    {
        string path = Make("json-invalid"); File.WriteAllText(Path.Combine(path, "session.json"), "{broken");
        File.WriteAllText(Path.Combine(path, "inventory.json"), new string(' ', 2 * 1024 * 1024 + 1));
        var data = Run(path); Check(data.Warnings.Any(text => text.StartsWith("session.json could not")) && data.Warnings.Any(text => text.Contains("JSON exceeds")), "malformed and oversized JSON handled");
        File.WriteAllText(Path.Combine(path, "session.json"), "42");
        data = Run(path); Check(data.Warnings.Any(text => text.Contains("Expected a JSON object")), "scalar JSON metadata rejected safely");
    }
}
