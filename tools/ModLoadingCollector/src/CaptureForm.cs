using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace XxmiCollector
{
    internal sealed class CaptureForm : Form
    {
        private static readonly Color Ink = Color.FromArgb(226, 234, 242);
        private static readonly Color Muted = Color.FromArgb(151, 171, 188);
        private static readonly Color Canvas = Color.FromArgb(15, 23, 34);
        private static readonly Color Card = Color.FromArgb(24, 36, 50);
        private static readonly Color Accent = Color.FromArgb(61, 218, 184);
        private readonly ComboBox processes = Combo();
        private readonly ComboBox scenario = Combo();
        private readonly ComboBox runKind = Combo();
        private readonly ComboBox marker = Combo();
        private readonly TextBox traces = Input();
        private readonly TextBox output = Input();
        private readonly TextBox presentMon = Input();
        private readonly TextBox notes = Input();
        private readonly TextBox markerNote = Input();
        private readonly NumericUpDown duration = new NumericUpDown();
        private readonly ListView timeline = new ListView();
        private readonly Label status = TextLabel("Ready when you are", 18, Ink);
        private readonly Label detail = TextLabel("Choose the running game, then start a capture.", 10, Muted);
        private readonly Label elapsedLabel = TextLabel("00:00", 24, Ink);
        private readonly Label frameLabel = TextLabel("Optional", 11, Muted);
        private readonly Label markerLabel = TextLabel("0 markers", 11, Muted);
        private readonly ProgressBar progress = new ProgressBar();
        private readonly Button start = ActionButton("Start capture", true);
        private readonly Button stop = ActionButton("Finish & save", false);
        private readonly Button addMarker = ActionButton("Add marker", false);
        private readonly Button screen = ActionButton("Capture game screen", false);
        private readonly Button evidence = ActionButton("Attach evidence", false);
        private readonly Button openReport = ActionButton("Open report", false);
        private readonly Button openFolder = ActionButton("Open folder", false);
        private readonly Button refreshTraces = ActionButton("Refresh exported traces", false);
        private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        private readonly Stopwatch recordingClock = new Stopwatch();
        private readonly List<Control> setupControls = new List<Control>();
        private CaptureSession session;
        private PresentMonCapture frameCapture;
        private bool busy;
        private bool running;
        private bool closingRequested;
        private bool allowClose;
        private bool refreshingProcesses;
        private int selectedPid;
        private int targetSeconds;
        private int markerCount;
        private string reportPath;

        public CaptureForm()
        {
            Text = "XXMI Capture";
            ClientSize = new Size(1120, 900);
            MinimumSize = new Size(1000, 820);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Canvas;
            ForeColor = Ink;
            Font = new Font("Segoe UI", 10);
            AutoScaleMode = AutoScaleMode.Dpi;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            BuildLayout();
            output.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "XXMI Captures");
            scenario.Items.AddRange(new object[] { "First character appearance", "Character revisit", "Outfit / toggle", "LOD transition", "F10 unchanged refresh", "New or modified mod", "Custom scenario" });
            scenario.SelectedIndex = 0;
            runKind.Items.AddRange(new object[] { "Baseline", "Candidate", "Investigation" });
            runKind.SelectedIndex = 2;
            marker.Items.AddRange(new object[] { "Action requested", "Character appeared", "Outfit / toggle", "LOD transition", "F10 pressed", "Correct appearance observed", "Stall observed", "Other observation" });
            marker.SelectedIndex = 0;
            duration.Minimum = 5; duration.Maximum = 3600; duration.Value = 60;
            duration.BackColor = Canvas; duration.ForeColor = Ink; duration.BorderStyle = BorderStyle.FixedSingle;
            start.Click += async (s, e) => await BeginCaptureAsync();
            stop.Click += async (s, e) => await FinishCaptureAsync();
            addMarker.Click += (s, e) => AddManualMarker();
            screen.Click += async (s, e) => await CaptureScreenAsync();
            evidence.Click += async (s, e) => await AttachEvidenceAsync();
            openReport.Click += (s, e) => OpenPath(reportPath);
            openFolder.Click += (s, e) => { if (session != null) OpenPath(session.DirectoryPath); };
            refreshTraces.Click += async (s, e) => await RefreshTracesAsync();
            timer.Interval = 250;
            timer.Tick += async (s, e) => {
                if (!running) return;
                elapsedLabel.Text = recordingClock.Elapsed.ToString(@"mm\:ss", CultureInfo.InvariantCulture);
                frameLabel.Text = frameCapture == null ? "No frame capture selected" : frameCapture.Status;
                if (!busy && (recordingClock.Elapsed.TotalSeconds >= targetSeconds || !session.IsRunning))
                    await FinishCaptureAsync();
            };
            Shown += async (s, e) => await RefreshProcessesAsync();
            FormClosing += async (s, e) => {
                if (allowClose) return;
                if (busy) { e.Cancel = true; closingRequested = true; return; }
                if (running) { e.Cancel = true; closingRequested = true; await FinishCaptureAsync(); }
            };
            FormClosed += (s, e) => { timer.Dispose(); if (frameCapture != null) frameCapture.Dispose(); };
            UpdateControls();
        }

        private void BuildLayout()
        {
            var root = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(26), ColumnCount = 1, RowCount = 7, BackColor = Canvas };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 76));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 204));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 120));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 116));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 70));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 62));
            Controls.Add(root);

            var header = new Panel { Dock = DockStyle.Fill };
            var title = TextLabel("XXMI  /  CAPTURE", 22, Accent); title.Dock = DockStyle.None; title.Location = new Point(0, 0); title.Size = new Size(500, 38);
            var subtitle = TextLabel("A clear record of what happened, ready to compare and share.", 10, Muted); subtitle.Dock = DockStyle.None; subtitle.Location = new Point(2, 43); subtitle.AutoSize = true;
            var badge = TextLabel("LOCAL ONLY  •  ON DEMAND", 9, Accent); badge.Dock = DockStyle.None; badge.AutoSize = true; badge.Anchor = AnchorStyles.Top | AnchorStyles.Right; badge.Location = new Point(790, 14);
            header.Controls.AddRange(new Control[] { title, subtitle, badge }); root.Controls.Add(header, 0, 0);

            var settings = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Card, Padding = new Padding(14), ColumnCount = 3, RowCount = 4 };
            settings.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 160)); settings.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); settings.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
            for (int i = 0; i < 4; ++i) settings.RowStyles.Add(new RowStyle(SizeType.Percent, 25));
            AddSetting(settings, 0, "Running game", processes, "Refresh", async () => await RefreshProcessesAsync());
            AddSetting(settings, 1, "Native trace folder", traces, "Browse…", () => { ChooseFolder(traces); return Task.CompletedTask; });
            AddSetting(settings, 2, "Save captures to", output, "Browse…", () => { ChooseFolder(output); return Task.CompletedTask; });
            AddSetting(settings, 3, "PresentMon (optional)", presentMon, "Browse…", () => { ChooseFile(presentMon, "PresentMon executable|*.exe"); return Task.CompletedTask; });
            root.Controls.Add(settings, 0, 1);

            var sessionPanel = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(0, 12, 0, 4), ColumnCount = 6, RowCount = 3 };
            sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 76)); sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 55)); sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 72)); sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 45)); sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 90)); sessionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 88));
            sessionPanel.RowStyles.Add(new RowStyle(SizeType.Absolute, 36)); sessionPanel.RowStyles.Add(new RowStyle(SizeType.Absolute, 36)); sessionPanel.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
            sessionPanel.Controls.Add(TextLabel("Scenario", 10, Muted), 0, 0); sessionPanel.Controls.Add(scenario, 1, 0);
            sessionPanel.Controls.Add(TextLabel("Run", 10, Muted), 2, 0); sessionPanel.Controls.Add(runKind, 3, 0);
            sessionPanel.Controls.Add(TextLabel("Seconds", 10, Muted), 4, 0); sessionPanel.Controls.Add(duration, 5, 0);
            sessionPanel.Controls.Add(TextLabel("Notes", 10, Muted), 0, 1); sessionPanel.Controls.Add(notes, 1, 1); sessionPanel.SetColumnSpan(notes, 5);
            var note = TextLabel("Keep the same scene and settings for baseline/candidate runs. Review the ZIP before sharing it.", 9, Muted); note.AutoSize = true; sessionPanel.Controls.Add(note, 0, 2); sessionPanel.SetColumnSpan(note, 6);
            setupControls.AddRange(new Control[] { scenario, runKind, duration, notes }); root.Controls.Add(sessionPanel, 0, 2);

            var state = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Card, Padding = new Padding(15), ColumnCount = 2, RowCount = 3 };
            state.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 80)); state.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 20));
            state.RowStyles.Add(new RowStyle(SizeType.Absolute, 34)); state.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); state.RowStyles.Add(new RowStyle(SizeType.Absolute, 8));
            state.Controls.Add(status, 0, 0); state.Controls.Add(elapsedLabel, 1, 0); state.Controls.Add(detail, 0, 1); state.Controls.Add(markerLabel, 1, 1);
            progress.Dock = DockStyle.Fill; state.Controls.Add(progress, 0, 2); state.SetColumnSpan(progress, 2); root.Controls.Add(state, 0, 3);

            var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(0, 12, 0, 6), WrapContents = false };
            actions.Controls.AddRange(new Control[] { start, stop, screen, evidence, frameLabel }); frameLabel.Width = 300; frameLabel.Height = 40; root.Controls.Add(actions, 0, 4);

            var events = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2 };
            events.RowStyles.Add(new RowStyle(SizeType.Absolute, 42)); events.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            var markerRow = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1 };
            markerRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 245)); markerRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); markerRow.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 130));
            markerRow.Controls.Add(marker, 0, 0); markerRow.Controls.Add(markerNote, 1, 0); markerRow.Controls.Add(addMarker, 2, 0); events.Controls.Add(markerRow, 0, 0);
            timeline.Dock = DockStyle.Fill; timeline.View = View.Details; timeline.FullRowSelect = true; timeline.HideSelection = false; timeline.BackColor = Card; timeline.ForeColor = Ink; timeline.BorderStyle = BorderStyle.None;
            timeline.Columns.Add("Time", 90); timeline.Columns.Add("Observation", 250); timeline.Columns.Add("Note", 620); events.Controls.Add(timeline, 0, 1); root.Controls.Add(events, 0, 5);

            var results = new FlowLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(0, 14, 0, 0), WrapContents = false };
            results.Controls.AddRange(new Control[] { openReport, openFolder, refreshTraces }); root.Controls.Add(results, 0, 6);
        }

        private void AddSetting(TableLayoutPanel panel, int row, string label, Control control, string buttonText, Func<Task> action)
        {
            panel.Controls.Add(TextLabel(label, 10, Muted), 0, row);
            control.Dock = DockStyle.Fill; control.Margin = new Padding(4, 7, 10, 7); panel.Controls.Add(control, 1, row);
            var button = ActionButton(buttonText, false); button.Dock = DockStyle.Fill; button.Margin = new Padding(0, 4, 0, 4);
            button.Click += async (s, e) => { if (!busy && !running) { try { await action(); } catch (Exception ex) { ShowError(ex); } } };
            panel.Controls.Add(button, 2, row); setupControls.Add(control); setupControls.Add(button);
        }

        private async Task RefreshProcessesAsync()
        {
            if (running || busy || refreshingProcesses) return;
            refreshingProcesses = true;
            try {
                int previous = processes.SelectedItem is ProcessChoice ? ((ProcessChoice)processes.SelectedItem).Id : 0;
                var choices = await Task.Run(() => CaptureSession.ListProcesses());
                if (running || busy || IsDisposed) return;
                processes.BeginUpdate(); processes.Items.Clear();
                foreach (var item in choices) processes.Items.Add(item);
                processes.EndUpdate();
                var selected = choices.FirstOrDefault(p => p.Id == previous) ?? choices.FirstOrDefault(p => p.Name.IndexOf("Zenless", StringComparison.OrdinalIgnoreCase) >= 0);
                if (selected != null) processes.SelectedItem = selected;
            } catch (Exception ex) { if (!IsDisposed) ShowError(ex); }
            finally { refreshingProcesses = false; }
        }

        private async Task BeginCaptureAsync()
        {
            if (busy || running) return;
            var process = processes.SelectedItem as ProcessChoice;
            if (process == null) { SetStatus("Choose a running game", "Use Refresh after launching the game."); return; }
            if (String.IsNullOrWhiteSpace(output.Text)) { SetStatus("Choose a save folder", "Each capture gets a separate local folder."); return; }
            busy = true; UpdateControls(); SetStatus("Preparing capture…", "Collecting hardware and loaded-DLL details. The window remains available.");
            try
            {
                if (frameCapture != null) { frameCapture.Dispose(); frameCapture = null; }
                reportPath = null; timeline.Items.Clear(); markerCount = 0; markerLabel.Text = "0 markers"; elapsedLabel.Text = "00:00";
                selectedPid = process.Id; targetSeconds = (int)duration.Value;
                session = await CaptureSession.StartAsync(new SessionOptions {
                    ProcessId = selectedPid, OutputRoot = output.Text, TraceDirectory = traces.Text,
                    Scenario = scenario.Text, RunKind = runKind.Text, Notes = notes.Text, DurationSeconds = 0
                });
                if (!String.IsNullOrWhiteSpace(presentMon.Text))
                    frameCapture = await PresentMonCapture.StartAsync(presentMon.Text, selectedPid, targetSeconds, session.DirectoryPath);
                recordingClock.Restart(); running = true; timer.Start();
                session.AddMarker("guided_recording_started", "Requested duration: " + targetSeconds + " seconds. Setup precedes this marker.");
                SetStatus("Recording — perform your scenario", "With the diagnostic DLL: set mod_load_trace=1 in [Logging], then F10. Before finishing, set it to 0 and F10 to export. No settings are changed by this app.");
                frameLabel.Text = frameCapture == null ? "No frame capture selected" : frameCapture.Status;
            }
            catch (Exception ex)
            {
                if (session != null) { try { await session.StopAsync(); } catch { } }
                if (frameCapture != null) { try { await frameCapture.StopAsync(); } catch { } }
                running = false; ShowError(ex);
            }
            finally { busy = false; UpdateControls(); await CompleteCloseIfRequestedAsync(); }
        }

        private async Task FinishCaptureAsync()
        {
            if (busy || !running || session == null) return;
            busy = true; running = false; timer.Stop(); recordingClock.Stop(); UpdateControls();
            SetStatus("Saving the capture…", "Finishing process samples and optional frame capture, then building the report.");
            try
            {
                session.AddMarker("guided_recording_finished", "End requested by user or capture timer.");
                var processStop = session.StopAsync();
                if (frameCapture != null) await frameCapture.StopAsync();
                await processStop;
                await BuildReportAsync();
                SetStatus("Capture saved", "Open the report to see available measurements. If the native trace was exported afterward, click Refresh exported traces to update the report and ZIP.");
            }
            catch (Exception ex) { ShowError(ex); }
            finally { busy = false; UpdateControls(); if (closingRequested) { allowClose = true; Close(); } }
        }

        private async Task BuildReportAsync()
        {
            // Every save path (including refresh/attachments after a failed
            // stop) requires a settled optional frame-capture process.
            if (frameCapture != null) await frameCapture.StopAsync();
            string directory = session.DirectoryPath;
            reportPath = await Task.Run(() => {
                string report = ReportBuilder.Generate(directory);
                SessionArchive.Create(directory);
                return report;
            });
        }

        private async Task RefreshTracesAsync()
        {
            if (busy || running || session == null) return;
            busy = true; UpdateControls(); SetStatus("Checking exported traces…", "Use mod_load_trace=0 and F10 in the game first. Existing capture evidence is retained.");
            try { await session.RefreshTracesAsync(); await BuildReportAsync(); SetStatus("Report and ZIP updated", "Review the report's coverage and loss counts before using it for comparisons."); }
            catch (Exception ex) { ShowError(ex); }
            finally { busy = false; UpdateControls(); await CompleteCloseIfRequestedAsync(); }
        }

        private void AddManualMarker()
        {
            if (!running || busy || session == null) return;
            string label = marker.Text; string note = markerNote.Text;
            session.AddMarker(label, note);
            var item = new ListViewItem(recordingClock.Elapsed.ToString(@"mm\:ss\.fff", CultureInfo.InvariantCulture));
            item.SubItems.Add(label); item.SubItems.Add(note); timeline.Items.Add(item); item.EnsureVisible();
            markerCount++; markerLabel.Text = markerCount + " markers"; markerNote.Clear();
        }

        private async Task AttachEvidenceAsync()
        {
            if (busy || session == null) return;
            using (var dialog = new OpenFileDialog { Title = "Attach a screenshot, recording, or configuration", Filter = "Evidence files|*.png;*.jpg;*.jpeg;*.mp4;*.mkv;*.txt;*.ini;*.csv;*.json|All files|*.*" })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                busy = true; UpdateControls();
                try {
                    var result = await session.AddEvidenceAsync(dialog.FileName);
                    if (result.Status != "copied") throw new IOException(result.Reason ?? "The evidence could not be copied.");
                    detail.Text = "Evidence attached: " + Path.GetFileName(dialog.FileName);
                    if (!running) await BuildReportAsync();
                } catch (Exception ex) { ShowError(ex); }
                finally { busy = false; UpdateControls(); await CompleteCloseIfRequestedAsync(); }
            }
        }

        private async Task CaptureScreenAsync()
        {
            if (!running || busy || session == null) return;
            busy = true; UpdateControls(); var previous = WindowState;
            SetStatus("Screenshot in 3 seconds", "Switch to the game. The image records visible pixels; review it before treating it as rendering evidence.");
            try
            {
                WindowState = FormWindowState.Minimized;
                await Task.Delay(3000);
                if (!session.IsRunning) throw new InvalidOperationException("The capture ended before the screenshot was taken. Attach an image manually instead.");
                using (var process = Process.GetProcessById(selectedPid))
                {
                    var handle = process.MainWindowHandle; Rect rect;
                    if (handle == IntPtr.Zero || IsIconic(handle) || !GetWindowRect(handle, out rect))
                        throw new InvalidOperationException("The selected game window is unavailable or minimized. Attach a screenshot manually instead.");
                    var bounds = Rectangle.Intersect(Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom), SystemInformation.VirtualScreen);
                    if (bounds.Width <= 0 || bounds.Height <= 0 || bounds.Width > 8192 || bounds.Height > 8192)
                        throw new InvalidOperationException("The game window is outside the supported capture bounds.");
                    string temporary = Path.Combine(session.DirectoryPath, "screen-" + Guid.NewGuid().ToString("N") + ".png");
                    try {
                        using (var image = new Bitmap(bounds.Width, bounds.Height)) {
                            using (var graphics = Graphics.FromImage(image)) graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                            image.Save(temporary, ImageFormat.Png);
                        }
                        var result = await session.AddEvidenceAsync(temporary);
                        if (result.Status != "copied") throw new IOException(result.Reason ?? "Screenshot could not be saved.");
                    } finally { if (File.Exists(temporary)) File.Delete(temporary); }
                    session.AddMarker("screenshot_taken", "Visible window capture; correctness must be reviewed by the operator.");
                }
                SetStatus("Recording — screenshot saved", "Manual markers are observer timestamps. They do not identify an exact rendered frame.");
            }
            catch (Exception ex) { ShowError(ex); }
            finally { WindowState = previous; busy = false; UpdateControls(); await CompleteCloseIfRequestedAsync(); }
        }

        private async Task CompleteCloseIfRequestedAsync()
        {
            if (!closingRequested || busy) return;
            if (running) await FinishCaptureAsync();
            else { allowClose = true; Close(); }
        }

        private void UpdateControls()
        {
            foreach (var control in setupControls) control.Enabled = !busy && !running;
            start.Enabled = !busy && !running; stop.Enabled = !busy && running;
            marker.Enabled = markerNote.Enabled = addMarker.Enabled = screen.Enabled = !busy && running;
            evidence.Enabled = !busy && session != null;
            openReport.Enabled = !busy && !running && reportPath != null && File.Exists(reportPath);
            openFolder.Enabled = !busy && session != null;
            refreshTraces.Enabled = !busy && !running && session != null;
            progress.Style = busy || running ? ProgressBarStyle.Marquee : ProgressBarStyle.Blocks;
            progress.MarqueeAnimationSpeed = busy || running ? 30 : 0;
        }

        private void SetStatus(string title, string message) { status.Text = title; detail.Text = message; }
        private void ShowError(Exception ex) { SetStatus("Capture needs attention", ex.Message + (session == null ? "" : "  Partial evidence is retained in the capture folder.")); }
        private static void OpenPath(string path)
        {
            if (String.IsNullOrEmpty(path)) return;
            try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Open capture", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }
        private void ChooseFolder(TextBox target) { using (var dialog = new FolderBrowserDialog { SelectedPath = target.Text }) if (dialog.ShowDialog(this) == DialogResult.OK) target.Text = dialog.SelectedPath; }
        private void ChooseFile(TextBox target, string filter) { using (var dialog = new OpenFileDialog { Filter = filter }) if (dialog.ShowDialog(this) == DialogResult.OK) target.Text = dialog.FileName; }
        private static ComboBox Combo() { return new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill, BackColor = Card, ForeColor = Ink, FlatStyle = FlatStyle.Flat, Margin = new Padding(4, 4, 8, 4) }; }
        private static TextBox Input() { return new TextBox { Dock = DockStyle.Fill, BackColor = Canvas, ForeColor = Ink, BorderStyle = BorderStyle.FixedSingle, Margin = new Padding(4, 4, 8, 4), MaxLength = 16384 }; }
        private static Label TextLabel(string text, float size, Color color) { return new Label { Text = text, Font = new Font("Segoe UI", size), ForeColor = color, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft }; }
        private static Button ActionButton(string text, bool primary) {
            var button = new Button { Text = text, AutoSize = true, Height = 38, MinimumSize = new Size(104, 38), Padding = new Padding(10, 0, 10, 0), Margin = new Padding(0, 0, 8, 0), FlatStyle = FlatStyle.Flat, BackColor = primary ? Accent : Card, ForeColor = primary ? Canvas : Ink, Cursor = Cursors.Hand };
            button.FlatAppearance.BorderColor = primary ? Accent : Color.FromArgb(57, 78, 96); return button;
        }
        [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out Rect rect);
        [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    }
}
