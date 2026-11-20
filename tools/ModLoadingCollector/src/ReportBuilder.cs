using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Xml;

namespace XxmiCollector
{
    /// <summary>Offline, bounded analysis. No measurements are synthesized from missing evidence.</summary>
    public static class ReportBuilder
    {
        private const long MaxFileBytes = 64L * 1024 * 1024;
        private const int MaxRows = 250000;
        private const int MaxFiles = 128;
        private const int MaxRowChars = 65536;
        private static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;
        private static readonly HashSet<string> Stages = new HashSet<string>(new[] {
            "discovery", "file_read", "decode", "shader_preparation", "gpu_creation", "view_creation",
            "command_list", "config_reload", "readiness_wait", "activation", "first_correct_frame", "file_load_combined", "present_cpu" }, StringComparer.Ordinal);

        public static string Generate(string sessionDirectory)
        {
            if (String.IsNullOrWhiteSpace(sessionDirectory)) throw new ArgumentException("Session directory is required.", "sessionDirectory");
            string directory = Path.GetFullPath(sessionDirectory);
            if (!Directory.Exists(directory)) throw new DirectoryNotFoundException(directory);
            var report = new Analysis { GeneratedUtc = DateTime.UtcNow.ToString("o", Invariant) };
            var metadata = ReadJson(Path.Combine(directory, "session.json"), report);
            var inventory = ReadJson(Path.Combine(directory, "inventory.json"), report);
            var provenance = ReadJson(Path.Combine(directory, "imported-provenance.json"), report, true);
            if (provenance == null) provenance = ReadJson(Path.Combine(directory, "provenance.json"), report, true);
            var presentmon = ReadJson(Path.Combine(directory, "presentmon.json"), report, true);
            ReadPresentMonStatus(presentmon, report);
            ReadSamples(directory, report);
            ReadMarkers(directory, report);
            ReadNative(directory, report);
            ReadFrames(directory, report);
            string html = BuildHtml(report, metadata, inventory, provenance, presentmon);
            string analysisPath = Path.Combine(directory, "analysis.json");
            using (var stream = File.Create(analysisPath))
                new DataContractJsonSerializer(typeof(Analysis), new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true }).WriteObject(stream, report);
            string path = Path.Combine(directory, "report.html");
            File.WriteAllText(path, html, new UTF8Encoding(false));
            return path;
        }

        private sealed class JsonEvidence
        {
            internal string Text;
            internal Dictionary<string, string> Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        }

        private static JsonEvidence ReadJson(string path, Analysis report, bool optional = false)
        {
            if (!File.Exists(path)) { if (!optional) report.Warnings.Add(Path.GetFileName(path) + " is missing."); return null; }
            try
            {
                if (new FileInfo(path).Length > 2 * 1024 * 1024) throw new InvalidDataException("JSON exceeds 2 MiB limit.");
                byte[] bytes = File.ReadAllBytes(path);
                int offset = bytes.Length >= 3 && bytes[0] == 239 && bytes[1] == 187 && bytes[2] == 191 ? 3 : 0;
                var evidence = new JsonEvidence { Text = Encoding.UTF8.GetString(bytes, offset, bytes.Length - offset) };
                using (var reader = JsonReaderWriterFactory.CreateJsonReader(bytes, offset, bytes.Length - offset, new XmlDictionaryReaderQuotas { MaxDepth = 32, MaxStringContentLength = 2097152, MaxArrayLength = 250000, MaxBytesPerRead = 4096, MaxNameTableCharCount = 65536 }))
                {
                    var document = new XmlDocument { XmlResolver = null };
                    document.Load(reader);
                    if (document.DocumentElement == null || document.DocumentElement.GetAttribute("type") != "object") throw new SerializationException("Expected a JSON object.");
                    foreach (XmlNode child in document.DocumentElement.ChildNodes)
                        if (child.ChildNodes.Count <= 1 && child.Attributes["type"] != null && child.Attributes["type"].Value != "object" && child.Attributes["type"].Value != "array")
                            evidence.Fields[child.Name] = child.InnerText;
                }
                return evidence;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is InvalidDataException || ex is XmlException || ex is SerializationException || ex is ArgumentException)
            { report.Warnings.Add(Path.GetFileName(path) + " could not be read: " + ex.Message); return null; }
        }

        private static void ReadSamples(string directory, Analysis report)
        {
            var cpu = new List<double>(); var working = new List<double>(); var memory = new List<double>();
            double? previousRead = null, previousWrite = null; double readDelta = 0, writeDelta = 0;
            double previousTime = -1, firstTime = -1; int readIntervals = 0, writeIntervals = 0;
            ReadTable(Path.Combine(directory, "samples.csv"), report, (header, row) => {
                double elapsed;
                if (!Number(Get(header, row, "elapsed_ms"), out elapsed) || elapsed < previousTime) { report.RejectedRows++; return; }
                previousTime = elapsed;
                if (firstTime < 0) firstTime = elapsed;
                report.SampleRows++;
                AddNumber(cpu, Get(header, row, "cpu_percent"));
                AddNumber(working, Get(header, row, "working_set_bytes"));
                AddNumber(memory, Get(header, row, "private_bytes"));
                double value;
                if (Number(Get(header, row, "io_read_bytes"), out value)) { if (previousRead.HasValue && value >= previousRead.Value) { readDelta += value - previousRead.Value; readIntervals++; } previousRead = value; }
                if (Number(Get(header, row, "io_write_bytes"), out value)) { if (previousWrite.HasValue && value >= previousWrite.Value) { writeDelta += value - previousWrite.Value; writeIntervals++; } previousWrite = value; }
                report.SampleSpanMs = elapsed - firstTime;
            }, false);
            report.ProcessCpuPercent = Summarize(cpu); report.WorkingSetBytes = Summarize(working); report.PrivateBytes = Summarize(memory);
            if (readIntervals > 0 && !Double.IsInfinity(readDelta)) report.ObservedIoReadDeltaBytes = readDelta;
            if (writeIntervals > 0 && !Double.IsInfinity(writeDelta)) report.ObservedIoWriteDeltaBytes = writeDelta;
        }

        private static void ReadMarkers(string directory, Analysis report)
        {
            ReadTable(Path.Combine(directory, "markers.csv"), report, (header, row) => {
                double elapsed;
                if (!Number(Get(header, row, "elapsed_ms"), out elapsed)) { report.RejectedRows++; return; }
                if (report.Markers.Count < 2000) report.Markers.Add(new Marker { ElapsedMs = elapsed, Utc = Get(header, row, "utc"), Label = Get(header, row, "label"), Note = Get(header, row, "note") });
                else report.OmittedMarkers++;
            }, true);
        }

        private static void ReadPresentMonStatus(JsonEvidence evidence, Analysis report)
        {
            if (evidence == null) { report.FrameCaptureStatus = "No PresentMon tool metadata supplied"; return; }
            report.PresentMonMetadataJson = evidence.Text;
            report.FrameCaptureStatus = Field(evidence, "status");
            bool forced, missingTail; int code;
            string error = Field(evidence, "error"), status = report.FrameCaptureStatus;
            bool hasError = error != "Unavailable" && !String.IsNullOrWhiteSpace(error);
            bool hasExit = Int32.TryParse(Field(evidence, "exitCode"), NumberStyles.AllowLeadingSign, Invariant, out code);
            bool forcedStop = Boolean.TryParse(Field(evidence, "forcedStop"), out forced) && forced;
            bool tailMissing = Boolean.TryParse(Field(evidence, "csvTailMayBeMissing"), out missingTail) && missingTail;
            report.FrameCaptureMayBeIncomplete = forcedStop || tailMissing || hasError || (hasExit && code != 0) ||
                status.IndexOf("unavailable", StringComparison.OrdinalIgnoreCase) >= 0 ||
                status.IndexOf("failed", StringComparison.OrdinalIgnoreCase) >= 0 || !hasExit;
            if (report.FrameCaptureMayBeIncomplete)
                report.Warnings.Add("PresentMon capture may be incomplete or unavailable. Status: " + status +
                    "; forcedStop=" + forcedStop + "; CSV tail may be missing=" + tailMissing +
                    "; exitCode=" + (hasExit ? code.ToString(Invariant) : "unavailable") + (hasError ? "; error=" + error : "") +
                    ". Existing frame-time rows do not establish a complete capture.");
        }

        private static void ReadFrames(string directory, Analysis report)
        {
            string path = Path.Combine(directory, "frame-times.csv");
            if (!File.Exists(path)) { report.Warnings.Add("No frame-times.csv: frame-time evidence is unavailable. Present CPU scopes and process samples are not frame times."); return; }
            var frames = new List<double>();
            bool mapped = false;
            ReadTable(path, report, (header, row) => {
                string column = header.ContainsKey("MsBetweenPresents") ? "MsBetweenPresents" : header.ContainsKey("FrameTime") ? "FrameTime" : null;
                if (column == null) return;
                mapped = true; report.FrameTimeColumn = column;
                double value;
                if (Number(Get(header, row, column), out value) && value > 0) frames.Add(value); else report.RejectedRows++;
            }, false);
            report.FrameTimesMs = Summarize(frames);
            if (!mapped) report.Warnings.Add("Frame-time CSV has no usable rows with a supported explicit field. Supported millisecond fields: MsBetweenPresents (preferred), FrameTime. No aliases or unit inference are applied.");
            report.Warnings.Add("Imported frame-time rows are not independently verified or correlated to a process by this report; filter the export to the intended process and swapchain. No FPS or correctness verdict is inferred.");
        }

        private static void ReadNative(string directory, Analysis report)
        {
            string traceDirectory = Path.Combine(directory, "native-traces");
            if (!Directory.Exists(traceDirectory)) { report.Warnings.Add("Native trace directory is missing; native loading-stage evidence is unavailable."); return; }
            string[] paths;
            try { paths = Directory.EnumerateFiles(traceDirectory, "*.csv").Take(MaxFiles + 1).ToArray(); }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            { report.Warnings.Add("Native trace directory could not be enumerated: " + ex.Message); return; }
            Array.Sort(paths, StringComparer.Ordinal);
            if (paths.Length > MaxFiles) report.Warnings.Add("Native trace file limit reached; at most 128 exports were considered.");
            var durations = new Dictionary<string, List<double>>(StringComparer.Ordinal);
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (string path in paths.Take(MaxFiles))
            {
                var file = new TraceFile { Name = Path.GetFileName(path) };
                report.NativeFiles.Add(file);
                Dictionary<string, int> header = null;
                bool lastWasFooter = false;
                ReadCsv(path, report, csv => {
                    string first = csv.Fields.Count > 0 ? csv.Fields[0] : "";
                    if (first.StartsWith("#", StringComparison.Ordinal))
                    {
                        if (first.StartsWith("# cumulative_accepted=", StringComparison.Ordinal))
                        {
                            ulong a, f, c, d;
                            string[] parts = first.Substring(2).Split(';');
                            if (parts.Length == 4 && Counter(parts[0], "cumulative_accepted", out a) && Counter(parts[1], "dropped_full", out f) && Counter(parts[2], "dropped_contention", out c) && Counter(parts[3], "drained", out d) && csv.Terminated)
                            {
                                file.HasFooter = true; file.Accepted = a; file.DroppedFull = f; file.DroppedContention = c; file.Drained = d;
                                lastWasFooter = true;
                            }
                            else { file.MalformedRows++; lastWasFooter = false; }
                        }
                        else if (first.StartsWith("# export_incomplete=", StringComparison.Ordinal)) { file.Incomplete = true; lastWasFooter = true; }
                        else if (file.HasFooter) lastWasFooter = false;
                        return;
                    }
                    if (csv.Fields.Count == 1 && first.Length == 0) return;
                    lastWasFooter = false;
                    if (header == null)
                    {
                        header = Header(csv.Fields);
                        string[] required = { "stage", "kind", "capture_session", "start_ns", "duration_ns", "start_frame", "end_frame", "thread_id", "object_id", "bytes", "result" };
                        if (required.Any(key => !header.ContainsKey(key))) { file.MalformedRows++; header = null; }
                        return;
                    }
                    if (report.NativeRows >= MaxRows) { file.LimitReached = true; return; }
                    if (csv.Fields.Count != header.Count) { file.MalformedRows++; return; }
                    string stage = Get(header, csv.Fields, "stage"), kind = Get(header, csv.Fields, "kind");
                    ulong ns, temp;
                    string result = Get(header, csv.Fields, "result"); long signedResult;
                    if (!Stages.Contains(stage) || (kind != "duration" && kind != "instant") ||
                        !Unsigned(Get(header, csv.Fields, "duration_ns"), out ns) ||
                        new[] { "capture_session", "start_ns", "start_frame", "end_frame", "thread_id", "object_id", "bytes" }.Any(key => !Unsigned(Get(header, csv.Fields, key), out temp)) ||
                        (result.Length != 0 && !Int64.TryParse(result, NumberStyles.AllowLeadingSign, Invariant, out signedResult)))
                    { file.MalformedRows++; return; }
                    string identity = String.Join("\u001f", csv.Fields);
                    if (!seen.Add(identity)) { report.DuplicateNativeRows++; return; }
                    report.NativeRows++; file.Rows++;
                    if (kind == "instant") { report.NativeInstantRows++; return; }
                    List<double> values;
                    if (!durations.TryGetValue(stage, out values)) durations[stage] = values = new List<double>();
                    values.Add(ns / 1000000.0);
                }, () => file.MalformedRows++, () => file.LimitReached = true);
                file.Complete = header != null && file.HasFooter && lastWasFooter && !file.Incomplete && file.MalformedRows == 0 && !file.LimitReached;
                if (file.HasFooter)
                {
                    report.CumulativeCounterFooters++;
                    report.CumulativeAcceptedMaximum = Math.Max(report.CumulativeAcceptedMaximum, file.Accepted);
                    report.CumulativeDroppedFullMaximum = Math.Max(report.CumulativeDroppedFullMaximum, file.DroppedFull);
                    report.CumulativeDroppedContentionMaximum = Math.Max(report.CumulativeDroppedContentionMaximum, file.DroppedContention);
                    report.CumulativeDrainedMaximum = Math.Max(report.CumulativeDrainedMaximum, file.Drained);
                }
            }
            foreach (var stage in durations.OrderBy(pair => pair.Key, StringComparer.Ordinal)) report.NativeStageDurationsMs[stage.Key] = Summarize(stage.Value);
            report.IncompleteContentionExports = report.NativeFiles.Count(file => file.Incomplete);
            if (report.NativeFiles.Count == 0 || report.NativeRows == 0) report.Warnings.Add("No usable native trace events were found.");
            if (report.NativeFiles.Any(file => !file.Complete)) report.Warnings.Add("One or more native exports are incomplete, malformed, limited, or missing a complete footer; coverage is uncertain.");
        }

        private static bool Counter(string part, string name, out ulong value)
        { value = 0; return part.StartsWith(name + "=", StringComparison.Ordinal) && Unsigned(part.Substring(name.Length + 1), out value); }
        private static bool Unsigned(string text, out ulong value) { return UInt64.TryParse(text, NumberStyles.None, Invariant, out value); }
        private static bool Number(string text, out double value)
        { return Double.TryParse(text, NumberStyles.Float, Invariant, out value) && !Double.IsNaN(value) && !Double.IsInfinity(value) && value >= 0; }
        private static void AddNumber(List<double> values, string text) { double value; if (Number(text, out value)) values.Add(value); }
        private static Summary Summarize(List<double> values)
        {
            if (values.Count == 0) return null;
            values.Sort();
            return new Summary { Count = values.Count, P50 = Rank(values, .50), P95 = Rank(values, .95), P99 = Rank(values, .99), Maximum = values[values.Count - 1] };
        }
        private static double Rank(List<double> values, double p) { return values[Math.Max(0, (int)Math.Ceiling(p * values.Count) - 1)]; }
        private static Dictionary<string, int> Header(IList<string> fields)
        {
            var result = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < fields.Count; i++) { string name = fields[i].Trim().TrimStart('\uFEFF'); if (!result.ContainsKey(name)) result.Add(name, i); }
            return result;
        }
        private static string Get(Dictionary<string, int> header, IList<string> row, string name)
        { int index; return header.TryGetValue(name, out index) && index < row.Count ? row[index] : ""; }
        private static void ReadTable(string path, Analysis report, Action<Dictionary<string, int>, IList<string>> row, bool optional)
        {
            if (optional && !File.Exists(path)) return;
            Dictionary<string, int> header = null;
            ReadCsv(path, report, record => {
                if (record.Fields.Count == 1 && (record.Fields[0].Length == 0 || record.Fields[0].StartsWith("#", StringComparison.Ordinal))) return;
                if (header == null) { header = Header(record.Fields); return; }
                if (record.Fields.Count != header.Count) { report.RejectedRows++; return; }
                row(header, record.Fields);
            }, () => report.RejectedRows++, () => { });
        }

        private sealed class CsvRecord { internal List<string> Fields; internal bool Terminated; }
        // RFC-style quoted fields, escaped quotes, and multiline values. Limits apply before allocating unbounded input.
        private static void ReadCsv(string path, Analysis report, Action<CsvRecord> accept, Action reject, Action limited)
        {
            if (!File.Exists(path)) { report.Warnings.Add(Path.GetFileName(path) + " is missing."); limited(); return; }
            try
            {
                if (new FileInfo(path).Length > MaxFileBytes) { report.Warnings.Add(Path.GetFileName(path) + " exceeds the 64 MiB input limit."); limited(); return; }
                using (var reader = new StreamReader(path, Encoding.UTF8, true))
                {
                    var fields = new List<string>(); var field = new StringBuilder();
                    bool quoted = false, afterQuote = false, invalid = false, discard = false;
                    int chars = 0, rows = 0;
                    while (true)
                    {
                        int next = reader.Read(); bool end = next < 0; char ch = end ? '\0' : (char)next;
                        if (end && chars == 0 && fields.Count == 0 && field.Length == 0) break;
                        if (!end && ++chars > MaxRowChars) { invalid = true; discard = true; }
                        bool newline = ch == '\n' || ch == '\r';
                        if (discard)
                        {
                            if (!end && !newline) continue;
                        }
                        else if (quoted)
                        {
                            if (end) { invalid = true; }
                            else if (ch == '"') { if (reader.Peek() == '"') { reader.Read(); chars++; field.Append('"'); } else { quoted = false; afterQuote = true; } continue; }
                            else { field.Append(ch); continue; }
                        }
                        else if (!end && !newline)
                        {
                            if (ch == ',') { fields.Add(field.ToString()); field.Clear(); afterQuote = false; if (fields.Count > 128) { invalid = true; discard = true; } }
                            else if (ch == '"' && field.Length == 0 && !afterQuote) quoted = true;
                            else if (afterQuote || ch == '"') { invalid = true; discard = true; }
                            else field.Append(ch);
                            continue;
                        }
                        if (ch == '\r' && reader.Peek() == '\n') reader.Read();
                        if (invalid) reject();
                        else { fields.Add(field.ToString()); accept(new CsvRecord { Fields = fields, Terminated = !end }); }
                        if (++rows >= MaxRows) { report.Warnings.Add(Path.GetFileName(path) + " reached the 250000-row input limit."); limited(); break; }
                        fields = new List<string>(); field.Clear(); chars = 0; quoted = false; afterQuote = false; invalid = false; discard = false;
                        if (end) break;
                    }
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            { report.Warnings.Add(Path.GetFileName(path) + " could not be read: " + ex.Message); limited(); }
        }

        private static string Escape(string value)
        { return (value ?? "").Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace("\"", "&quot;").Replace("'", "&#39;"); }
        private static string Format(double value) { return value.ToString("0.###", Invariant); }
        private static string Field(JsonEvidence evidence, string key)
        { string value; return evidence != null && evidence.Fields.TryGetValue(key, out value) ? value : "Unavailable"; }
        private static void Metric(StringBuilder html, string title, string value, string description)
        { html.Append("<article class='card'><h3>").Append(Escape(title)).Append("</h3><strong>").Append(Escape(value)).Append("</strong><p>").Append(Escape(description)).Append("</p></article>"); }
        private static void SummaryRow(StringBuilder html, string name, Summary summary)
        {
            if (summary == null) return;
            html.Append("<tr><th scope='row'>").Append(Escape(name)).Append("</th><td>").Append(summary.Count).Append("</td><td>").Append(Format(summary.P50)).Append("</td><td>").Append(Format(summary.P95)).Append("</td><td>").Append(Format(summary.P99)).Append("</td><td>").Append(Format(summary.Maximum)).Append("</td></tr>");
        }
        private static void TableStart(StringBuilder html)
        { html.Append("<div class='scroll'><table><thead><tr><th>Measurement</th><th>n</th><th>p50</th><th>p95</th><th>p99</th><th>Max</th></tr></thead><tbody>"); }
        private static void TableEnd(StringBuilder html) { html.Append("</tbody></table></div>"); }
        private static void JsonDetails(StringBuilder html, string title, JsonEvidence evidence)
        {
            html.Append("<details><summary>").Append(Escape(title)).Append("</summary>");
            html.Append(evidence == null ? "<p>Unavailable.</p>" : "<pre>" + Escape(evidence.Text) + "</pre>"); html.Append("</details>");
        }
        private static string BuildHtml(Analysis report, JsonEvidence metadata, JsonEvidence inventory, JsonEvidence provenance, JsonEvidence presentmon)
        {
            var h = new StringBuilder(24000);
            h.Append("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>Mod loading · Evidence report</title><style>");
            h.Append("*{box-sizing:border-box}body{margin:0;background:#edf3f5;color:#20313f;font:16px/1.55 Segoe UI,Arial,sans-serif}main{max-width:1180px;margin:auto;padding:42px 28px}header{background:#132c39;color:#edfafa;padding:32px;border-radius:18px;border-top:5px solid #2dd4bf}h1{font-size:34px;line-height:1.15;margin:8px 0 16px}h2{font-size:23px;margin:34px 0 14px}h3{font-size:14px;letter-spacing:.035em;margin:0;color:#46616b}p{margin:10px 0}.eyebrow{color:#87e4d7;text-transform:uppercase;letter-spacing:.16em;font-size:12px}.sub{color:#b6ced5}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:24px 0}.card{background:white;padding:22px;border:1px solid #d5e1e5;border-radius:12px}.card strong{display:block;font-size:29px;color:#0e766e;margin:9px 0}.card p,small{font-size:13px;color:#506975}.notice{background:#fff8df;border-left:4px solid #b98524;padding:15px 20px;border-radius:6px;margin:20px 0}table{width:100%;border-collapse:collapse;background:white;font-size:14px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #deeaed;vertical-align:top}thead th{background:#dbe9ed;font-size:12px;text-transform:uppercase;letter-spacing:.03em}tbody th{font-weight:600}td{font-variant-numeric:tabular-nums}.scroll{overflow-x:auto;border:1px solid #d5e1e5;border-radius:10px}details{background:white;border:1px solid #d5e1e5;padding:16px 20px;border-radius:9px;margin:12px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 Consolas,monospace;max-height:450px;overflow:auto}.muted{color:#526d79}footer{font-size:12px;color:#526d79;border-top:1px solid #ccdce1;margin-top:36px;padding-top:18px}li{margin:7px 0}@media print{body{background:white}main{padding:0}header{break-inside:avoid}details pre{max-height:none}table{font-size:11px}}@media(max-width:600px){main{padding:20px 12px}header{padding:22px}h1{font-size:28px}}</style></head><body><main>");
            h.Append("<header><div class='eyebrow'>XXMI · Local evidence</div><h1>Mod loading report</h1><p>").Append(Escape(Field(metadata, "scenario"))).Append(" · ").Append(Escape(Field(metadata, "runKind"))).Append("</p><div class='sub'>").Append(Escape(Field(metadata, "processName"))).Append(" · PID ").Append(Escape(Field(metadata, "processId"))).Append(" · ").Append(Escape(Field(metadata, "startedUtc"))).Append("</div></header>");
            h.Append("<div class='grid'>");
            Metric(h, "Process samples", report.SampleRows.ToString(Invariant), "CPU and memory observations; not frames.");
            Metric(h, "Native events", report.NativeRows.ToString(Invariant), report.NativeFiles.Count + " export(s); " + report.DuplicateNativeRows + " duplicate row(s) excluded.");
            Metric(h, "Frame-time rows", report.FrameTimesMs == null ? "Unavailable" : report.FrameTimesMs.Count.ToString(Invariant), report.FrameTimesMs == null ? "An explicit PresentMon CSV is required." : "Imported " + report.FrameTimeColumn + " in milliseconds.");
            Metric(h, "Correctness latency", "Unmeasured", "No verified first-correct-frame correlation is established.");
            h.Append("</div><div class='notice'><strong>Measurement boundaries</strong><p>This report describes available observations, without a benchmark pass/fail verdict. Manual marker clicks are approximate. Native frame IDs provide attribution only. A present_cpu duration measures CPU wall time inside Present; it is not a displayed frame interval. Loading scopes may overlap or nest: do not sum their durations.</p></div>");
            if (report.Warnings.Count != 0)
            {
                h.Append("<h2>Evidence and uncertainty</h2><ul>"); foreach (string warning in report.Warnings) h.Append("<li>").Append(Escape(warning)).Append("</li>"); h.Append("</ul>");
            }
            h.Append("<h2>Native loading stages</h2><p class='muted'>Duration scopes in milliseconds. Instant events are excluded from duration statistics. Percentiles use nearest rank: sorted value at ceil(p × n), with one-based indexing. Small samples do not establish stable tail latency.</p>");
            if (report.NativeStageDurationsMs.Count == 0) h.Append("<p>No valid native duration scopes are available.</p>");
            else { TableStart(h); foreach (var stage in report.NativeStageDurationsMs) SummaryRow(h, stage.Key + " (ms)", stage.Value); TableEnd(h); }
            h.Append("<h2>Frame-time evidence</h2><p>Tool status: ").Append(Escape(report.FrameCaptureStatus)).Append(".</p>");
            if (report.FrameTimesMs == null) h.Append("<p>Unavailable. No frame times are inferred from process sampling or native Present CPU scopes.</p>");
            else { TableStart(h); SummaryRow(h, report.FrameTimeColumn + " (ms)", report.FrameTimesMs); TableEnd(h); }
            h.Append("<p class='muted'>Supported explicit millisecond columns: MsBetweenPresents, otherwise FrameTime. If both exist, MsBetweenPresents wins. Zero, negative, nonfinite, and malformed frame intervals are excluded. Other PresentMon columns are not mapped automatically. Imported provenance, when supplied, is shown below.</p>");
            h.Append("<h2>Process observations</h2><p class='muted'>CPU follows the session's recorded normalization: ").Append(Escape(Field(metadata, "cpuNormalization"))).Append(". Memory is bytes; samples are not allocation totals. IO deltas use monotonic observed counter differences and exclude resets; they do not identify which mod caused IO.</p>");
            TableStart(h); SummaryRow(h, "Process CPU (%)", report.ProcessCpuPercent); SummaryRow(h, "Working set (bytes)", report.WorkingSetBytes); SummaryRow(h, "Private bytes", report.PrivateBytes); TableEnd(h);
            h.Append("<p>Observed IO read delta: ").Append(report.ObservedIoReadDeltaBytes.HasValue ? Format(report.ObservedIoReadDeltaBytes.Value) + " bytes" : "unavailable").Append(" · write delta: ").Append(report.ObservedIoWriteDeltaBytes.HasValue ? Format(report.ObservedIoWriteDeltaBytes.Value) + " bytes" : "unavailable").Append(".</p>");
            h.Append("<h2>Native export coverage</h2><p class='muted'>Counters are cumulative in the native process across capture sessions. Maxima below are lower bounds across supplied exports, never sums. Exports from different processes or counter resets cannot be combined into exact totals. A complete footer means the file ended normally; it does not prove loss-free capture, an empty queue, or complete scenario coverage.</p>");
            if (report.CumulativeCounterFooters == 0) h.Append("<p>No complete cumulative counter footer is available; loss counts are unknown.</p>");
            else h.Append("<p>Cumulative maxima: accepted ").Append(report.CumulativeAcceptedMaximum).Append(" · dropped full ").Append(report.CumulativeDroppedFullMaximum).Append(" · dropped contention ").Append(report.CumulativeDroppedContentionMaximum).Append(" · drained ").Append(report.CumulativeDrainedMaximum).Append(". Explicit contention-incomplete exports: ").Append(report.IncompleteContentionExports).Append(".</p>");
            h.Append("<div class='scroll'><table><thead><tr><th>Export</th><th>Unique events</th><th>Footer</th><th>Coverage</th><th>Rejected rows</th></tr></thead><tbody>");
            foreach (TraceFile file in report.NativeFiles) h.Append("<tr><th scope='row'>").Append(Escape(file.Name)).Append("</th><td>").Append(file.Rows).Append("</td><td>").Append(file.HasFooter ? "Present" : "Missing / truncated").Append("</td><td>").Append(file.Complete ? "Normal export ending" : file.Incomplete ? "Contention retry limit" : "Uncertain / incomplete").Append("</td><td>").Append(file.MalformedRows).Append("</td></tr>");
            TableEnd(h);
            h.Append("<h2>Manual markers</h2><p class='muted'>Approximate operator timestamps. Marker labels, including first-correct-frame labels, are annotations and do not establish native activation or visual correctness.</p>");
            h.Append("<div class='scroll'><table><thead><tr><th>Elapsed ms</th><th>UTC</th><th>Label</th><th>Note</th></tr></thead><tbody>");
            foreach (Marker marker in report.Markers) h.Append("<tr><td>").Append(Format(marker.ElapsedMs)).Append("</td><td>").Append(Escape(marker.Utc)).Append("</td><th scope='row'>").Append(Escape(marker.Label)).Append("</th><td>").Append(Escape(marker.Note)).Append("</td></tr>");
            TableEnd(h);
            if (report.OmittedMarkers > 0) h.Append("<p>").Append(report.OmittedMarkers).Append(" additional markers omitted from the report.</p>");
            h.Append("<h2>Run, inventory and provenance</h2><p class='muted'>Metadata is supplied capture context, not proof of source identity or build equivalence. Inventory is a snapshot; missing GPU utilization, VRAM budgets, storage limits, visual correctness, and per-resource ownership remain unmeasured unless separately evidenced.</p>");
            JsonDetails(h, "Run metadata · session.json", metadata); JsonDetails(h, "System / CPU / GPU / modules · inventory.json", inventory); JsonDetails(h, "Imported provenance (optional)", provenance); JsonDetails(h, "PresentMon tool provenance / capture status · presentmon.json", presentmon);
            h.Append("<footer>Generated locally ").Append(Escape(report.GeneratedUtc)).Append(". No external assets or scripts. Input bounds: 64 MiB per CSV, 65,536 characters per row, 128 columns, 250,000 rows per file and unique native events, 128 native files; JSON 2 MiB / depth 32. Rejected general CSV rows: ").Append(report.RejectedRows).Append(". Raw inputs remain the source of record. analysis.json contains these summaries.</footer></main></body></html>");
            return h.ToString();
        }

        [DataContract]
        public sealed class Analysis
        {
            [DataMember] public int SchemaVersion = 1;
            [DataMember] public string GeneratedUtc;
            [DataMember] public string PercentileConvention = "nearest-rank: sorted[ceil(p*n)-1]";
            [DataMember] public List<string> Warnings = new List<string>();
            [DataMember] public int SampleRows;
            [DataMember] public double SampleSpanMs;
            [DataMember] public Summary ProcessCpuPercent;
            [DataMember] public Summary WorkingSetBytes;
            [DataMember] public Summary PrivateBytes;
            [DataMember] public double? ObservedIoReadDeltaBytes;
            [DataMember] public double? ObservedIoWriteDeltaBytes;
            [DataMember] public string FrameCaptureStatus;
            [DataMember] public bool FrameCaptureMayBeIncomplete;
            [DataMember] public string PresentMonMetadataJson;
            [DataMember] public string FrameTimeColumn;
            [DataMember] public Summary FrameTimesMs;
            [DataMember] public Dictionary<string, Summary> NativeStageDurationsMs = new Dictionary<string, Summary>();
            [DataMember] public List<TraceFile> NativeFiles = new List<TraceFile>();
            [DataMember] public int NativeRows;
            [DataMember] public int NativeInstantRows;
            [DataMember] public int DuplicateNativeRows;
            [DataMember] public int CumulativeCounterFooters;
            [DataMember] public ulong CumulativeAcceptedMaximum;
            [DataMember] public ulong CumulativeDroppedFullMaximum;
            [DataMember] public ulong CumulativeDroppedContentionMaximum;
            [DataMember] public ulong CumulativeDrainedMaximum;
            [DataMember] public int IncompleteContentionExports;
            [DataMember] public int RejectedRows;
            [DataMember] public List<Marker> Markers = new List<Marker>();
            [DataMember] public int OmittedMarkers;
        }
        [DataContract]
        public sealed class Summary
        {
            [DataMember] public int Count;
            [DataMember] public double P50;
            [DataMember] public double P95;
            [DataMember] public double P99;
            [DataMember] public double Maximum;
        }
        [DataContract]
        public sealed class TraceFile
        {
            [DataMember] public string Name;
            [DataMember] public int Rows;
            [DataMember] public int MalformedRows;
            [DataMember] public bool HasFooter;
            [DataMember] public bool Incomplete;
            [DataMember] public bool LimitReached;
            [DataMember] public bool Complete;
            [DataMember] public ulong Accepted;
            [DataMember] public ulong DroppedFull;
            [DataMember] public ulong DroppedContention;
            [DataMember] public ulong Drained;
        }
        [DataContract]
        public sealed class Marker
        {
            [DataMember] public double ElapsedMs;
            [DataMember] public string Utc;
            [DataMember] public string Label;
            [DataMember] public string Note;
        }
    }
}
