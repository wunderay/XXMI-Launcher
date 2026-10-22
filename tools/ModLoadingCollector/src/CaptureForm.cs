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
        private static readonly Color Ink = CapturePalette.Text;
        private static readonly Color Muted = CapturePalette.SecondaryText;
        private static readonly Color Canvas = CapturePalette.Canvas;
        private static readonly Color Card = CapturePalette.Surface;
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
        private readonly Label detail = TextLabel("Choose the running game, then start a capture.", 10.5f, Muted);
        private readonly Label elapsedLabel = TextLabel("00:00", 24, Ink);
        private readonly Label frameLabel = TextLabel("Optional", 11, Muted);
        private readonly Label markerLabel = TextLabel("0 markers", 11, Muted);
        private readonly ProgressBar progress = new ProgressBar();
        private readonly Button start = ActionButton("Start 60-second capture", true);
        private readonly Button stop = ActionButton("Finish & save", false);
        private readonly Button addMarker = ActionButton("Add marker", false);
        private readonly Button screen = ActionButton("Capture game screen", false);
        private readonly Button evidence = ActionButton("Attach evidence", false);
        private readonly Button openReport = ActionButton("Open report", false);
        private readonly Button openZip = ActionButton("Open saved ZIP", false);
        private readonly ToolTip tips = new ToolTip();
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
            ClientSize = new Size(940, 600);
            MinimumSize = new Size(640, 480);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Canvas;
            ForeColor = Ink;
            Font = new Font("Segoe UI", 11);
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
            duration.BackColor = Card; duration.ForeColor = Ink; duration.BorderStyle = BorderStyle.FixedSingle;
            start.Click += async (s, e) => await BeginCaptureAsync();
            stop.Click += async (s, e) => await FinishCaptureAsync();
            addMarker.Click += (s, e) => AddManualMarker();
            screen.Click += async (s, e) => await CaptureScreenAsync();
            evidence.Click += async (s, e) => await AttachEvidenceAsync();
            duration.ValueChanged += (s, e) => { start.Text = "Start " + duration.Value + "-second capture"; start.AccessibleName = start.Text; };
            openZip.Click += (s, e) => { if (session != null) OpenPath(session.DirectoryPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + ".zip"); };
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
            FormClosed += (s, e) => { timer.Dispose(); tips.Dispose(); if (frameCapture != null) frameCapture.Dispose(); };
            UpdateControls();
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            var area = Screen.FromControl(this).WorkingArea;
            const int margin = 16;
            int availableWidth = Math.Max(1, area.Width - margin * 2);
            int availableHeight = Math.Max(1, area.Height - margin * 2);
            // Autoscaling may grow MinimumSize beyond a small display's work area.
            MinimumSize = new Size(Math.Min(MinimumSize.Width, availableWidth), Math.Min(MinimumSize.Height, availableHeight));
            Size = new Size(Math.Min(Width, availableWidth), Math.Min(Height, availableHeight));
            Location = new Point(area.Left + (area.Width - Width) / 2, area.Top + (area.Height - Height) / 2);
        }

        private void BuildLayout()
        {
            // The footer remains reachable while optional sections scroll.
            var footer = new FlowLayoutPanel { Name = "CaptureActions", Dock = DockStyle.Bottom, AutoSize = true,
                Padding = new Padding(20, 12, 20, 12), BackColor = Card, WrapContents = true };
            footer.Controls.AddRange(new Control[] { start, stop, openReport, openZip, openFolder });
            var scroll = new Panel { Name = "CaptureContent", Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(20) };
            var root = Stack("CaptureLayout"); root.Dock = DockStyle.Top;
            scroll.Controls.Add(root); Controls.Add(scroll); Controls.Add(footer);

            AddRow(root, TextLabel("XXMI Capture", 22, Ink));
            AddRow(root, TextLabel("Choose your game, capture what happens, then open the report.", 11, Muted));
            var notice = TextLabel("Screenshots are optional. You can complete and share a capture without taking any.", 11, Ink);
            notice.Padding = new Padding(12); notice.BackColor = Card; AddRow(root, notice);

            var game = SettingsGrid("GameSelection");
            AddSetting(game, 0, "Running game", processes, "Refresh games", async () => await RefreshProcessesAsync());
            AddRow(root, game);
            AddRow(root, TextLabel("Launch your game first. Choose its process from the list; use Refresh games if it is missing.", 10.5f, Muted));
            processes.DropDownWidth = 560;
            tips.SetToolTip(processes, "Select the running game process, not the launcher. The process ID helps distinguish multiple windows.");

            var state = Stack("CaptureStatus"); state.BackColor = Card; state.Padding = new Padding(12);
            status.Font = new Font("Segoe UI", 17); status.AccessibleRole = AccessibleRole.StaticText;
            AddRow(state, status); AddRow(state, detail);
            var counters = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, WrapContents = true };
            elapsedLabel.AutoSize = true; elapsedLabel.Margin = new Padding(0, 4, 20, 4);
            markerLabel.AutoSize = true; markerLabel.Margin = new Padding(0, 12, 0, 4);
            counters.Controls.AddRange(new Control[] { elapsedLabel, markerLabel }); AddRow(state, counters);
            progress.Dock = DockStyle.Fill; progress.Height = 8; AddRow(state, progress); AddRow(root, state);

            var settings = Stack("OptionalSettings");
            var paths = SettingsGrid("CaptureSettings");
            AddSetting(paths, 0, "Save captures to", output, "Browse…", () => { ChooseFolder(output); return Task.CompletedTask; });
            AddSetting(paths, 1, "Native trace folder", traces, "Browse…", () => { ChooseFolder(traces); return Task.CompletedTask; });
            AddSetting(paths, 2, "PresentMon (optional)", presentMon, "Browse…", () => { ChooseFile(presentMon, "PresentMon executable|*.exe"); return Task.CompletedTask; });
            AddRow(settings, paths);
            var metadata = SettingsGrid("RunDetails");
            AddField(metadata, 0, "Duration (seconds)", duration);
            AddField(metadata, 1, "Scenario", scenario); AddField(metadata, 2, "Run type", runKind); AddField(metadata, 3, "Notes", notes);
            setupControls.AddRange(new Control[] { duration, scenario, runKind, notes }); AddRow(settings, metadata);
            AddRow(settings, TextLabel("Native traces only: with the diagnostic DLL, set mod_load_trace=1 in [Logging] and press F10. Before saving, set it to 0 and press F10 to export. This app does not change game settings.", 10.5f, Muted));
            AddRow(settings, TextLabel("Exported a trace after saving? Refresh below to update the report and ZIP. Review coverage and loss counts before comparing runs.", 10.5f, Muted));
            AddRow(settings, refreshTraces);
            AddOptional(root, "Capture settings and native traces (optional)", settings);

            var events = Stack("OptionalEvidence");
            AddRow(events, TextLabel("Optional evidence and observations. Observer timestamps do not identify an exact rendered frame.", 10.5f, Muted));
            var evidenceActions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, WrapContents = true };
            evidenceActions.Controls.AddRange(new Control[] { screen, evidence }); AddRow(events, evidenceActions);
            AddRow(events, frameLabel);
            var observations = SettingsGrid("ObservationEntry");
            AddField(observations, 0, "Observation", marker); AddField(observations, 1, "Observation note", markerNote);
            AddRow(events, observations); AddRow(events, addMarker);
            timeline.Dock = DockStyle.Fill; timeline.Height = 160; timeline.View = View.Details;
            timeline.FullRowSelect = true; timeline.HideSelection = false; timeline.BackColor = Card; timeline.ForeColor = Ink;
            timeline.BorderStyle = BorderStyle.FixedSingle;
            timeline.Columns.Add("Time", 90); timeline.Columns.Add("Observation", 230); timeline.Columns.Add("Note", 420);
            AddRow(events, timeline); AddOptional(root, "Screenshots, evidence and observations (optional)", events);

            NameControl(processes, "RunningGame", "Running game"); NameControl(duration, "CaptureDuration", "Capture duration in seconds");
            NameControl(traces, "NativeTraceFolder", "Optional native trace folder"); NameControl(output, "OutputFolder", "Capture save folder");
            NameControl(presentMon, "PresentMonPath", "Optional PresentMon executable"); NameControl(scenario, "Scenario", "Capture scenario");
            NameControl(runKind, "RunKind", "Run type"); NameControl(notes, "RunNotes", "Run notes");
            NameControl(marker, "Observation", "Observation type"); NameControl(markerNote, "ObservationNote", "Observation note");
            NameControl(timeline, "Observations", "Recorded observations"); NameControl(status, "CaptureState", "Capture status");
            NameControl(detail, "CaptureDetail", "Capture status details");
            tips.SetToolTip(screen, "Optional: minimizes this window and waits three seconds so you can switch to the game.");
            tips.SetToolTip(evidence, "Optional: attach a screenshot, recording or configuration during or after capture.");
            tips.SetToolTip(openZip, "Opens the saved ZIP beside the capture folder. Review its contents before sharing.");
            tips.SetToolTip(refreshTraces, "After exporting native traces, rebuild the saved report and ZIP with the new traces.");
            AssignTabOrder(this);
            AcceptButton = start;
        }

        private static void AssignTabOrder(Control parent)
        {
            int index = 0;
            foreach (Control child in parent.Controls) { child.TabIndex = index++; AssignTabOrder(child); }
        }

        private static void NameControl(Control control, string name, string accessibleName)
        { control.Name = name; control.AccessibleName = accessibleName; }

        private static TableLayoutPanel Stack(string name)
        {
            var panel = new TableLayoutPanel { Name = name, ColumnCount = 1, RowCount = 0, AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink, Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 12) };
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); return panel;
        }

        private static void AddRow(TableLayoutPanel panel, Control control)
        {
            int row = panel.RowCount++; panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            control.Margin = new Padding(0, 0, 0, 10); panel.Controls.Add(control, 0, row);
        }

        private static TableLayoutPanel SettingsGrid(string name)
        {
            var panel = new TableLayoutPanel { Name = name, Dock = DockStyle.Fill, AutoSize = true, ColumnCount = 3, RowCount = 0 };
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 168));
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); panel.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            return panel;
        }

        private static void AddField(TableLayoutPanel panel, int row, string label, Control control)
        {
            panel.RowCount = row + 1; panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            var caption = TextLabel(label, 10.5f, Muted); caption.Margin = new Padding(0, 7, 8, 7);
            panel.Controls.Add(caption, 0, row); control.Dock = DockStyle.Fill;
            control.Margin = new Padding(0, 5, 8, 5); panel.Controls.Add(control, 1, row);
            panel.SetColumnSpan(control, 2);
        }

        private void AddOptional(TableLayoutPanel root, string title, Control content)
        {
            var toggle = ActionButton("Show " + title, false); toggle.TextAlign = ContentAlignment.MiddleLeft;
            toggle.AccessibleName = toggle.Text; toggle.AccessibleDescription = "Collapsed"; toggle.Name = content.Name + "Toggle"; toggle.Dock = DockStyle.Fill;
            content.Visible = false;
            toggle.Click += (s, e) => { content.Visible = !content.Visible; toggle.Text = (content.Visible ? "Hide " : "Show ") + title; toggle.AccessibleName = toggle.Text; toggle.AccessibleDescription = content.Visible ? "Expanded" : "Collapsed"; };
            AddRow(root, toggle); AddRow(root, content);
        }

        private void AddSetting(TableLayoutPanel panel, int row, string label, Control control, string buttonText, Func<Task> action)
        {
            AddField(panel, row, label, control); panel.SetColumnSpan(control, 1);
            var button = ActionButton(buttonText, false); button.Margin = new Padding(0, 3, 0, 3);
            button.AccessibleName = buttonText + " " + label; button.Name = panel.Name + "Action" + row;
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
                SetStatus("Recording — switch back to your game", "Play the scene you want to capture. This saves automatically after " + targetSeconds + " seconds; Finish & save ends it sooner. Screenshots are optional.");
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
                SetStatus("Capture saved", "Open the report or saved ZIP below. Saved to: " + session.DirectoryPath + ". Native trace refresh is in optional capture settings.");
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
            openZip.Enabled = !busy && !running && reportPath != null && session != null && File.Exists(session.DirectoryPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + ".zip");
            start.Visible = !running; stop.Visible = running;
            ((CaptureButton)start).Primary = reportPath == null;
            ((CaptureButton)stop).Primary = true;
            ((CaptureButton)openReport).Primary = reportPath != null && !running;
            // Enter only starts a ready capture; stopping always requires an explicit action.
            AcceptButton = !busy && !running && reportPath == null ? start : null;
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
        private static ComboBox Combo() { return new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill, BackColor = Card, ForeColor = Ink, FlatStyle = FlatStyle.Standard, Margin = new Padding(0, 4, 8, 4) }; }
        private static TextBox Input() { return new TextBox { Dock = DockStyle.Fill, BackColor = Card, ForeColor = Ink, BorderStyle = BorderStyle.FixedSingle, Margin = new Padding(0, 4, 8, 4), MaxLength = 16384 }; }
        private static Label TextLabel(string text, float size, Color color) { return new Label { Text = text, Font = new Font("Segoe UI", size), ForeColor = color, Dock = DockStyle.Fill, AutoSize = true, TextAlign = ContentAlignment.MiddleLeft }; }
        private static Button ActionButton(string text, bool primary)
        {
            return new CaptureButton { Text = text, AccessibleName = text, Name = text.Replace(" ", ""), Primary = primary,
                AutoSize = true, MinimumSize = new Size(108, 38), Padding = new Padding(12, 6, 12, 6),
                Margin = new Padding(0, 0, 8, 0), Cursor = Cursors.Hand };
        }

        // Windows themes can override disabled button colors. Paint every state explicitly
        // so its label and boundary stay readable, including in Windows high contrast.
        private sealed class CaptureButton : Button
        {
            private bool primary;
            private bool pressed;
            internal CaptureButton()
            {
                FlatStyle = FlatStyle.Flat; UseVisualStyleBackColor = false; UseMnemonic = false;
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            }
            protected override void OnMouseDown(MouseEventArgs e) { pressed = true; Invalidate(); base.OnMouseDown(e); }
            protected override void OnMouseUp(MouseEventArgs e) { pressed = false; Invalidate(); base.OnMouseUp(e); }
            protected override void OnMouseLeave(EventArgs e) { pressed = false; Invalidate(); base.OnMouseLeave(e); }
            internal bool Primary { get { return primary; } set { primary = value; Invalidate(); } }
            protected override void OnPaint(PaintEventArgs e)
            {
                Color background = !Enabled ? CapturePalette.DisabledSurface : primary ? CapturePalette.Primary : Card;
                Color foreground = !Enabled ? CapturePalette.DisabledText : primary ? CapturePalette.OnPrimary : Ink;
                e.Graphics.Clear(background);
                ControlPaint.DrawBorder(e.Graphics, ClientRectangle, CapturePalette.Border, ButtonBorderStyle.Solid);
                var bounds = ClientRectangle; bounds.Inflate(-8, -4);
                TextFormatFlags alignment = TextFormatFlags.VerticalCenter;
                if (TextAlign == ContentAlignment.TopLeft || TextAlign == ContentAlignment.MiddleLeft || TextAlign == ContentAlignment.BottomLeft)
                    alignment |= TextFormatFlags.Left;
                else if (TextAlign == ContentAlignment.TopRight || TextAlign == ContentAlignment.MiddleRight || TextAlign == ContentAlignment.BottomRight)
                    alignment |= TextFormatFlags.Right;
                else alignment |= TextFormatFlags.HorizontalCenter;
                TextRenderer.DrawText(e.Graphics, Text, Font, bounds, foreground,
                    alignment | TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
                if (pressed && Enabled) ControlPaint.DrawBorder(e.Graphics, Rectangle.Inflate(ClientRectangle, -2, -2), foreground, ButtonBorderStyle.Solid);
                if (Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(e.Graphics, Rectangle.Inflate(ClientRectangle, -4, -4), foreground, background);
            }
        }
        [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out Rect rect);
        [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    }
}
