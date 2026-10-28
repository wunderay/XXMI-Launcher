// Local entry point for the explicitly approved isolated diagnostic profile.
// User Start uses stock XXMI Quick Start without the intermediate launcher GUI.
// Its manifest requests elevation only when the user opens this entry.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class LocalLauncher
{
    const string DllHash = "523a9834a410d67ca5ea1d9de2efb8b4a6f70442d1c4235dcdcaf233e9d708b8";
    const string LauncherHash = "9db00209ee06911639dda7ece93893086137684e29ee58609a272fe44d0bab65";
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static string Root { get { return AppDomain.CurrentDomain.BaseDirectory; } }
    static string Profile { get { return Path.Combine(Root, ".collector-data", "collector-auto-20261023", "diagnostic-launcher"); } }
    static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
    static Dictionary<string, object> Obj(Dictionary<string, object> parent, string key)
    { return (Dictionary<string, object>)parent[key]; }
    static bool Is(Dictionary<string, object> value, string key, object expected)
    { object actual; return value.TryGetValue(key, out actual) && Object.Equals(actual, expected); }
    static string Hash(string path)
    {
        using (var algorithm = SHA256.Create())
        using (var stream = File.OpenRead(path))
            return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    static void ValidateConfiguration(Dictionary<string, object> config)
    {
        var launcher = Obj(config, "Launcher");
        var active = Obj(Obj(config, "Importers"), "ZZMI");
        var importer = Obj(active, "Importer");
        var migoto = Obj(active, "Migoto");
        Require(Is(launcher, "auto_update", false) && Is(launcher, "active_importer", "ZZMI"), "This local profile must keep automatic updates disabled and ZZMI selected.");
        Require(Is(importer, "game_launch", "DIRECT") && Is(importer, "start_method", "NATIVE"), "XXMI Quick Start requires the prepared Direct/Native game launch settings.");
        Require(Is(importer, "importer_folder", "ZZMI/") && Is(importer, "game_process_exe", "ZenlessZoneZero.exe"), "The isolated importer path or game target changed.");
        Require(Is(importer, "game_process_exe_enabled", true), "The game target must remain enabled.");
        Require(Is(importer, "configure_game", false) && Is(importer, "configure_platform_launch_options", false), "Automatic game/platform changes must remain disabled.");
        foreach (var name in new[] { "run_pre_launch", "custom_launch", "run_post_load", "extra_libraries" })
            Require(Is(importer, name + "_enabled", false) && Is(importer, name, ""), "External launch commands must remain disabled.");
        Require(Is(migoto, "unsafe_mode", true) && Is(migoto, "manage_xxmi_dll_config", false), "The approved diagnostic profile or native settings were changed.");
        Require(migoto.ContainsKey("unsafe_mode_signature") && !String.IsNullOrEmpty(migoto["unsafe_mode_signature"] as string), "The existing profile-local consent signature is missing.");
    }
    static Dictionary<string, string> ReadIni(string text)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        string section = "";
        foreach (string raw in text.Split('\n'))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith(";") || line.StartsWith("#")) continue;
            if (line.StartsWith("[") && line.EndsWith("]")) { section = line.Substring(1, line.Length - 2); continue; }
            if (!section.Equals("Loader", StringComparison.OrdinalIgnoreCase) && !section.Equals("ModLoading", StringComparison.OrdinalIgnoreCase) && !section.Equals("Logging", StringComparison.OrdinalIgnoreCase)) continue;
            int equal = line.IndexOf('=');
            if (equal < 0) continue;
            string key = section + "/" + line.Substring(0, equal).Trim();
            Require(!result.ContainsKey(key), "Duplicate native setting: " + key);
            result.Add(key, line.Substring(equal + 1).Trim());
        }
        return result;
    }
    static void ValidateIni(string text)
    {
        var values = ReadIni(text);
        Require(!values.ContainsKey("Loader/launch") || String.IsNullOrEmpty(values["Loader/launch"]), "Automatic game launch must be absent from the native INI.");
        foreach (var pair in new Dictionary<string, string> {
            {"Loader/target", "ZenlessZoneZero.exe"}, {"Loader/loader", "XXMI Launcher.exe"}, {"Loader/require_admin", "false"},
            {"ModLoading/adaptive_learning", "0"},
            {"ModLoading/preparation_loading_window", "0"}, {"ModLoading/transactional_activation", "0"}, {"ModLoading/general_fast_refresh", "0"},
            {"ModLoading/auto_activate", "0"}, {"ModLoading/startup_preload_seconds", "120"},
            {"ModLoading/disk_cache_mb", "0"}, {"ModLoading/create_budget_ms", "10"}, {"ModLoading/upload_budget_mb", "64"},
            {"ModLoading/max_asset_mb", "256"}, {"ModLoading/worker_threads", "4"},
            {"ModLoading/verified_shader_cache", "1"},
            {"Logging/mod_load_trace_commands", "0"}, {"Logging/mod_load_trace_seconds", "180"}
        })
        {
            string actual;
            Require(values.TryGetValue(pair.Key, out actual) && actual == pair.Value, "Native preparation setting changed: " + pair.Key);
        }
        Require(values.ContainsKey("ModLoading/mode") && values.ContainsKey("ModLoading/cpu_cache_enabled") && values.ContainsKey("ModLoading/cpu_cache_mb"), "Incomplete preparation preset.");
        Require(values.ContainsKey("Logging/mod_load_trace") && (values["Logging/mod_load_trace"] == "0" || values["Logging/mod_load_trace"] == "1"), "Invalid capture setting.");
        Require(values.ContainsKey("ModLoading/gpu_cache_enabled") && values.ContainsKey("ModLoading/gpu_cache_mb") && values.ContainsKey("ModLoading/startup_gpu_preload"), "Incomplete GPU preset.");
        bool baseline = values["ModLoading/mode"] == "off" && values["ModLoading/cpu_cache_enabled"] == "0" && values["ModLoading/cpu_cache_mb"] == "0" && values["ModLoading/gpu_cache_enabled"] == "0" && values["ModLoading/gpu_cache_mb"] == "0" && values["ModLoading/startup_gpu_preload"] == "0";
        bool optimized = values["ModLoading/mode"] == "prepared_profile" && values["ModLoading/cpu_cache_enabled"] == "1" && values["ModLoading/cpu_cache_mb"] == "3072" && values["ModLoading/gpu_cache_enabled"] == "1" && values["ModLoading/gpu_cache_mb"] == "2048" && values["ModLoading/startup_gpu_preload"] == "1";
        Require(baseline || optimized, "The native settings do not match either comparison preset.");
    }
    static string WithPreset(string text, bool optimized, bool record = false)
    {
        string section = "";
        var output = new StringBuilder();
        var updates = new Dictionary<string, string> {
            {"mode", optimized ? "prepared_profile" : "off"}, {"cpu_cache_enabled", optimized ? "1" : "0"}, {"cpu_cache_mb", optimized ? "3072" : "0"},
            {"gpu_cache_enabled", optimized ? "1" : "0"}, {"gpu_cache_mb", optimized ? "2048" : "0"}, {"startup_gpu_preload", optimized ? "1" : "0"},
            {"startup_preload_seconds", "120"}, {"preparation_loading_window", "0"}, {"worker_threads", "4"}, {"max_asset_mb", "256"}, {"upload_budget_mb", "64"}, {"create_budget_ms", "10"}
        };
        var existing = ReadIni(text);
        foreach (string raw in text.Split('\n'))
        {
            string line = raw.Trim();
            if (line.StartsWith("[") && line.EndsWith("]")) section = line;
            int equal = line.IndexOf('='); string replacement;
            if (section.Equals("[ModLoading]", StringComparison.OrdinalIgnoreCase) && equal > 0 && updates.TryGetValue(line.Substring(0, equal).Trim(), out replacement))
                output.AppendLine(line.Substring(0, equal).Trim() + " = " + replacement);
            else if (section.Equals("[Logging]", StringComparison.OrdinalIgnoreCase) && equal > 0 && line.Substring(0, equal).Trim().Equals("mod_load_trace", StringComparison.OrdinalIgnoreCase))
                output.AppendLine("mod_load_trace = " + (record ? "1" : "0"));
            else output.AppendLine(raw.TrimEnd('\r'));
            if (line.Equals("[ModLoading]", StringComparison.OrdinalIgnoreCase))
                foreach (var pair in updates) if (!existing.ContainsKey("ModLoading/" + pair.Key)) output.AppendLine(pair.Key + " = " + pair.Value);
        }
        string result = output.ToString().TrimEnd() + "\r\n";
        ValidateIni(result);
        return result;
    }
    static bool RecordCapture = false;
    static bool PreloadModels = false;
    static ModelPreloader.Result LastPreload;
    static string LatestResultSummary()
    {
        try {
            string folder = Path.Combine(Root, "captures", "loading-speed");
            string current = Path.Combine(folder, DllHash.Substring(0, 12));
            if (File.Exists(Path.Combine(current, "comparison.json"))) folder = current;
            var result = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(folder, "comparison.json")));
            if (!Is(result, "matched_capture_pair", true)) return "A complete matching pair is needed to compare performance.";
            var measurements = Obj(result, "measurements");
            var reload = Obj(measurements, "config_reload"); var frames = Obj(measurements, "present_intervals");
            return String.Format(System.Globalization.CultureInfo.InvariantCulture,
                "F10 reload  {0:0.00}s  /  {1:0.00}s       Frame interval p95  {2:0.0}ms  /  {3:0.0}ms",
                Convert.ToDouble(Obj(reload, "baseline")["median_ms"]) / 1000,
                Convert.ToDouble(Obj(reload, "optimized")["median_ms"]) / 1000,
                Convert.ToDouble(Obj(frames, "baseline")["p95_ms"]), Convert.ToDouble(Obj(frames, "optimized")["p95_ms"]));
        } catch { return "Your next capture will appear here. Baseline / CPU preparation."; }
    }
    static readonly Color Ink = Color.FromArgb(16, 17, 18);
    static readonly Color Paper = Color.FromArgb(245, 243, 234);
    static readonly Color Acid = Color.FromArgb(231, 255, 54);
    static readonly Color Tile = Color.FromArgb(36, 38, 40);
    static readonly Color Quiet = Color.FromArgb(202, 203, 195);
    static readonly Color Edge = Color.FromArgb(145, 148, 142);
    static bool HighContrast { get { return SystemInformation.HighContrast; } }
    static Color PageColor { get { return HighContrast ? SystemColors.Window : Ink; } }
    static Color TextColor { get { return HighContrast ? SystemColors.WindowText : Paper; } }
    static Color AccentColor { get { return HighContrast ? SystemColors.Highlight : Acid; } }
    static Color AccentText { get { return HighContrast ? SystemColors.HighlightText : Ink; } }

    sealed class LaunchForm : Form
    {
        public Action<int> ChangeTextSize;
        public bool PreviewEnter() { return ProcessDialogKey(Keys.Enter); }
        protected override bool ProcessCmdKey(ref Message message, Keys key)
        {
            if (key == (Keys.Control | Keys.Oemplus) || key == (Keys.Control | Keys.Add)) { ChangeTextSize(1); return true; }
            if (key == (Keys.Control | Keys.OemMinus) || key == (Keys.Control | Keys.Subtract)) { ChangeTextSize(-1); return true; }
            if (key == (Keys.Control | Keys.D0)) { ChangeTextSize(0); return true; }
            if (key == Keys.Escape) { Close(); return true; }
            return base.ProcessCmdKey(ref message, key);
        }
    }
    // Native RadioButton semantics and keyboard behavior, with a custom visual skin.
    sealed class ModeChoice : RadioButton
    {
        public string Detail;
        public string Number;
        public ModeChoice() {
            AutoSize = false; Appearance = Appearance.Button; AutoCheck = true;
            Dock = DockStyle.Top; Margin = new Padding(0, 0, 0, 12); Cursor = Cursors.Hand;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            AccessibleRole = AccessibleRole.RadioButton;
        }
        public override Size GetPreferredSize(Size proposed) {
            int width = Math.Max(180, proposed.Width > 0 ? proposed.Width : Width);
            int textHeight = TextRenderer.MeasureText("SELECTED / " + Detail, Font, new Size(Math.Max(80, width - 88), 0), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding).Height;
            using (var title = new Font(Font.FontFamily, Font.Size + 3, FontStyle.Bold)) {
                int titleHeight = TextRenderer.MeasureText(Text.Replace("&", ""), title, new Size(Math.Max(80, width - 88), 0), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding).Height;
                return new Size(width, Math.Max(106, titleHeight + textHeight + 44));
            }
        }
        protected override void OnSizeChanged(EventArgs e) { base.OnSizeChanged(e); if (Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnFontChanged(EventArgs e) { base.OnFontChanged(e); if (Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnCheckedChanged(EventArgs e) { base.OnCheckedChanged(e); Invalidate(); }
        protected override void OnEnabledChanged(EventArgs e) { base.OnEnabledChanged(e); Invalidate(); }
        protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
        protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
        protected override void OnPaint(PaintEventArgs e) {
            Color bg = HighContrast ? (Checked ? SystemColors.Highlight : SystemColors.Window) : Tile;
            Color fg = HighContrast && Checked ? SystemColors.HighlightText : TextColor;
            e.Graphics.Clear(bg);
            using (var pen = new Pen(Checked ? AccentColor : (HighContrast ? SystemColors.WindowText : Edge), Checked ? 3 : 2))
                e.Graphics.DrawRectangle(pen, 1, 1, Width - 3, Height - 3);
            using (var pen = new Pen(Checked && !HighContrast ? Acid : fg, 2)) {
                e.Graphics.DrawEllipse(pen, 22, 23, 24, 24);
                if (Checked) using (var brush = new SolidBrush(HighContrast ? fg : Acid)) e.Graphics.FillEllipse(brush, 28, 29, 12, 12);
            }
            int titleHeight;
            using (var title = new Font(Font.FontFamily, Font.Size + 3, FontStyle.Bold)) {
                titleHeight = TextRenderer.MeasureText(Text.Replace("&", ""), title, new Size(Width - 88, 0), TextFormatFlags.WordBreak | TextFormatFlags.NoPadding).Height;
                TextRenderer.DrawText(e.Graphics, Text.Replace("&", ""), title, new Rectangle(64, 15, Width - 88, titleHeight + 3), fg, TextFormatFlags.NoPadding | TextFormatFlags.WordBreak);
            }
            TextRenderer.DrawText(e.Graphics, (Checked ? "ACTIVE  /  " : Number + "  /  ") + Detail, Font,
                new Rectangle(64, 23 + titleHeight, Width - 88, Height - titleHeight - 36), fg,
                TextFormatFlags.NoPadding | TextFormatFlags.WordBreak);
            if (Focused) using (var pen = new Pen(fg, 3)) e.Graphics.DrawRectangle(pen, 6, 6, Width - 13, Height - 13);
        }
        public bool PreviewKey(Keys key) { Message message = Message.Create(Handle, 0x100, (IntPtr)key, IntPtr.Zero); return PreProcessMessage(ref message); }
    }
    sealed class FocusButton : Button
    {
        public bool PreviewKey(Keys key) { Message message = Message.Create(Handle, 0x100, (IntPtr)key, IntPtr.Zero); return PreProcessMessage(ref message); }
        public override Size GetPreferredSize(Size proposed) {
            int preferredWidth = TextRenderer.MeasureText(Text, Font).Width + Padding.Horizontal + 12;
            int width = Dock == DockStyle.Top && proposed.Width > 0 ? proposed.Width : preferredWidth;
            int textHeight = TextRenderer.MeasureText(Text, Font, new Size(Math.Max(48, width - Padding.Horizontal - 12), 0), TextFormatFlags.WordBreak).Height;
            return new Size(width, Math.Max(48, textHeight + Padding.Vertical + 12));
        }
        protected override void OnSizeChanged(EventArgs e) { base.OnSizeChanged(e); if (Dock == DockStyle.Top && Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnFontChanged(EventArgs e) { base.OnFontChanged(e); if (Dock == DockStyle.Top && Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnPaint(PaintEventArgs e) {
            e.Graphics.Clear(BackColor);
            using (var pen = new Pen(FlatAppearance.BorderColor, 2)) e.Graphics.DrawRectangle(pen, 1, 1, Width - 3, Height - 3);
            TextRenderer.DrawText(e.Graphics, Text, Font, new Rectangle(Padding.Left + 6, Padding.Top + 4, Width - Padding.Horizontal - 12, Height - Padding.Vertical - 8),
                Enabled ? ForeColor : (HighContrast ? SystemColors.GrayText : Quiet), TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak | (ShowKeyboardCues ? 0 : TextFormatFlags.HidePrefix));
            if (Focused) using (var pen = new Pen(ForeColor, 3)) e.Graphics.DrawRectangle(pen, 5, 5, Width - 11, Height - 11);
        }
    }
    sealed class CaptureChoice : CheckBox
    {
        public bool PreviewKey(Keys key) { Message message = Message.Create(Handle, 0x100, (IntPtr)key, IntPtr.Zero); return PreProcessMessage(ref message); }
        public CaptureChoice() {
            AutoSize = false; FlatStyle = FlatStyle.Standard; Cursor = Cursors.Hand;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }
        public override Size GetPreferredSize(Size proposed) {
            int width = Math.Max(180, proposed.Width > 0 ? proposed.Width : Width);
            return new Size(width, Math.Max(52, TextRenderer.MeasureText(Text, Font, new Size(Math.Max(80, width - 58), 0), TextFormatFlags.WordBreak).Height + 24));
        }
        protected override void OnSizeChanged(EventArgs e) { base.OnSizeChanged(e); if (Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnFontChanged(EventArgs e) { base.OnFontChanged(e); if (Width > 0) Height = GetPreferredSize(new Size(Width, 0)).Height; }
        protected override void OnCheckedChanged(EventArgs e) { base.OnCheckedChanged(e); Invalidate(); }
        protected override void OnGotFocus(EventArgs e) { base.OnGotFocus(e); Invalidate(); }
        protected override void OnLostFocus(EventArgs e) { base.OnLostFocus(e); Invalidate(); }
        protected override void OnEnabledChanged(EventArgs e) { base.OnEnabledChanged(e); Invalidate(); }
        protected override void OnPaint(PaintEventArgs e) {
            e.Graphics.Clear(BackColor);
            int top = (Height - 22) / 2;
            if (Checked) using (var brush = new SolidBrush(HighContrast ? SystemColors.Highlight : Acid)) e.Graphics.FillRectangle(brush, 10, top, 22, 22);
            using (var pen = new Pen(HighContrast ? SystemColors.WindowText : Edge, 2)) e.Graphics.DrawRectangle(pen, 10, top, 22, 22);
            if (Checked) using (var pen = new Pen(HighContrast ? SystemColors.HighlightText : Ink, 3))
                e.Graphics.DrawLines(pen, new[] { new Point(14, top + 11), new Point(19, top + 16), new Point(28, top + 6) });
            TextRenderer.DrawText(e.Graphics, Text, Font, new Rectangle(44, 12, Width - 58, Height - 24),
                ForeColor, TextFormatFlags.WordBreak | TextFormatFlags.VerticalCenter | (ShowKeyboardCues ? 0 : TextFormatFlags.HidePrefix));
            if (Focused) using (var pen = new Pen(TextColor, 3)) e.Graphics.DrawRectangle(pen, 3, 3, Width - 7, Height - 7);
        }
    }
    sealed class Hero : Panel
    {
        public Hero() { DoubleBuffered = true; }
        protected override void OnPaintBackground(PaintEventArgs e) {
            base.OnPaintBackground(e);
            if (HighContrast || Width < 660) return;
            // Decorative record grooves and a hazard stripe; no text or meaning in pixels.
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            int diameter = Math.Min(Height - 28, Width / 4), x = Width - diameter - 25;
            using (var pen = new Pen(Color.FromArgb(78, 80, 73), 2))
                for (int i = 0; i < 6; i++) e.Graphics.DrawEllipse(pen, x + i * 9, 12 + i * 9, diameter - i * 18, diameter - i * 18);
            using (var brush = new SolidBrush(Acid)) {
                e.Graphics.FillEllipse(brush, x + diameter / 2 - 12, 12 + diameter / 2 - 12, 24, 24);
                for (int i = Width - 160; i < Width; i += 22)
                    e.Graphics.FillPolygon(brush, new[] {new Point(i, Height-10),new Point(i+11,Height-10),new Point(i+21,Height),new Point(i+10,Height)});
            }
        }
    }
    static Label Copy(string text, float size, Color color, bool bold) {
        return new Label { Text = text, AutoSize = true, Dock = DockStyle.Top, Margin = new Padding(0, 0, 0, 10),
            Font = new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular), ForeColor = color,
            BackColor = Color.Transparent, UseMnemonic = false, AccessibleRole = AccessibleRole.StaticText };
    }
    static FocusButton ButtonFor(string text, string name, bool primary) {
        var button = new FocusButton { Text = text, AccessibleName = name, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, MinimumSize = new Size(48, 48),
            Padding = new Padding(18, 10, 18, 10), Margin = new Padding(0, 0, 10, 0), FlatStyle = FlatStyle.Flat,
            BackColor = primary ? AccentColor : PageColor, ForeColor = primary ? AccentText : TextColor,
            Font = new Font("Segoe UI", 11, FontStyle.Bold), Cursor = Cursors.Hand };
        button.FlatAppearance.BorderColor = HighContrast ? SystemColors.WindowText : Edge;
        button.FlatAppearance.BorderSize = 2;
        button.FlatAppearance.MouseOverBackColor = primary ? AccentColor : (HighContrast ? SystemColors.Window : Tile);
        button.FlatAppearance.MouseDownBackColor = button.BackColor;
        return button;
    }
    static IEnumerable<Control> Descendants(Control root) {
        foreach (Control child in root.Controls) { yield return child; foreach (Control nested in Descendants(child)) yield return nested; }
    }
    static Form MakePresetWindow(Action<string, bool> choose, Func<IProgress<string>, CancellationToken, Task> prepare = null)
    {
        var form = new LaunchForm { Text = "XXMI Prepared Loading â€” Zenless Zone Zero", ClientSize = new Size(880, 720), MinimumSize = new Size(320, 440),
            StartPosition = FormStartPosition.CenterScreen, BackColor = PageColor, ForeColor = TextColor,
            Font = new Font("Segoe UI", 11), AutoScaleMode = AutoScaleMode.Dpi, AutoScaleDimensions = new SizeF(96, 96) };
        try { form.Icon = Icon.ExtractAssociatedIcon(Path.Combine(Root, "XXMI Prepared Loading.exe")); } catch { }
        var viewport = new Panel { Dock = DockStyle.Fill, AutoScroll = true, TabStop = false };
        var stack = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 1, Padding = new Padding(28, 22, 28, 20), TabStop = false };
        stack.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        viewport.Controls.Add(stack); form.Controls.Add(viewport);
        Action<Control> row = delegate(Control control) { int index = stack.RowCount++; stack.RowStyles.Add(new RowStyle(SizeType.AutoSize)); stack.Controls.Add(control, 0, index); };
        var eyebrow = Copy("XXMI  /  PREPARED LOADING", 10, AccentText, true);
        eyebrow.BackColor = AccentColor; eyebrow.Padding = new Padding(12, 8, 12, 8); row(eyebrow);
        var hero = new Hero { Dock = DockStyle.Top, Height = 112, Margin = new Padding(0, 6, 0, 10), TabStop = false };
        var title = Copy("ZENLESS\nZONE ZERO", 28, TextColor, true);
        title.Font = new Font("Arial Black", 28, FontStyle.Bold); title.Dock = DockStyle.Top; title.AutoSize = true;
        hero.SizeChanged += delegate {
            title.MaximumSize = new Size(Math.Max(100, hero.ClientSize.Width), 0);
            int height = Math.Max((int)(112 * title.Font.Size / 28f), title.PreferredHeight + 8);
            if (hero.Height != height) hero.Height = height;
        };
        hero.Controls.Add(title); row(hero);
        row(Copy("NEW ERIDU IS CALLING.", 12, TextColor, true));
        row(Copy("Your mods. Original texture quality. One launch.", 11, HighContrast ? TextColor : Quiet, false));
        var controls = new FlowLayoutPanel { AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Dock = DockStyle.Top, WrapContents = true, Margin = new Padding(0, 8, 0, 4), TabIndex = 5 };
        var smaller = ButtonFor("Aâˆ’", "Decrease text size", false); smaller.TabIndex = 0;
        var larger = ButtonFor("A+", "Increase text size", false); larger.TabIndex = 1;
        var sizeLabel = Copy("Text 100%  Â·  Ctrl + / âˆ’", 11, TextColor, false); sizeLabel.Margin = new Padding(8, 13, 0, 8);
        controls.SizeChanged += delegate { sizeLabel.MaximumSize = new Size(Math.Max(80, controls.ClientSize.Width - 8), 0); };
        controls.Controls.Add(smaller); controls.Controls.Add(larger); controls.Controls.Add(sizeLabel);
        var modes = new GroupBox { Text = "01 / Preparation", AccessibleName = "Resource preparation mode", AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink, Dock = DockStyle.Top, ForeColor = TextColor,
            Padding = new Padding(12, 26, 12, 12), Margin = new Padding(0, 0, 0, 16), TabIndex = 1 };
        var modeStack = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = false, WrapContents = true, TabStop = false };
        var standard = new ModeChoice { Name = "standard", Text = "&Standard", Number = "01", Detail = "Shader reuse. Resources load when needed.",
            AccessibleName = "Standard", AccessibleDescription = "Shader reuse on. Resources load when needed. GPU preparation off.", TabIndex = 0, TabStop = false };
        var experimental = new ModeChoice { Name = "experimental", Text = "&GPU preparation", Number = "02", Detail = "Prepares resources before play. Same VRAM budget.",
            AccessibleName = "GPU preparation", AccessibleDescription = "Prepares supported resources in the game's graphics device at startup. Keeps the same memory limits and allows up to two minutes for startup preparation. First-visit stutter elimination has not yet been proven.", Checked = true, TabIndex = 1, TabStop = true };
        standard.Dock = experimental.Dock = DockStyle.None;
        standard.Margin = new Padding(0, 0, 12, 8); experimental.Margin = new Padding(0, 0, 0, 8);
        modeStack.Controls.Add(standard); modeStack.Controls.Add(experimental); modes.Controls.Add(modeStack); row(modes);
        Action layoutModes = delegate {
            int available = modeStack.ClientSize.Width;
            int width = available >= 720 ? (available - 12) / 2 : available;
            standard.Margin = new Padding(0, 0, available >= 720 ? 12 : 0, 8);
            standard.Width = experimental.Width = Math.Max(180, width);
            standard.Height = standard.GetPreferredSize(new Size(standard.Width, 0)).Height;
            experimental.Height = experimental.GetPreferredSize(new Size(experimental.Width, 0)).Height;
            int height = available >= 720 ? Math.Max(standard.Height, experimental.Height) + 8 : standard.Height + experimental.Height + 16;
            if (modeStack.Height != height) modeStack.Height = height;
        };
        modeStack.SizeChanged += delegate { layoutModes(); };
        var optionsToggle = ButtonFor("OPTIONS / CAPTURE / TEXT SIZE  +", "Show options, capture and text size", false);
        optionsToggle.Name = "options"; optionsToggle.Dock = DockStyle.Top; optionsToggle.TabIndex = 2;
        optionsToggle.Margin = new Padding(0, 2, 0, 12); row(optionsToggle);
        var options = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 1, Margin = new Padding(0), Padding = new Padding(0, 6, 0, 0), TabIndex = 3, Visible = false };
        options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); row(options);
        Action<Control> optionRow = delegate(Control control) { int index = options.RowCount++; options.RowStyles.Add(new RowStyle(SizeType.AutoSize)); options.Controls.Add(control, 0, index); };
        optionsToggle.Click += delegate {
            options.Visible = !options.Visible;
            optionsToggle.Text = options.Visible ? "OPTIONS / CAPTURE / TEXT SIZE  âˆ’" : "OPTIONS / CAPTURE / TEXT SIZE  +";
            optionsToggle.AccessibleName = options.Visible ? "Hide options, capture and text size" : "Show options, capture and text size";
        };
        optionRow(Copy("02 / OPTIONAL TOOLS", 10, HighContrast ? TextColor : Quiet, true));
        var preload = new CaptureChoice { Name = "preload", Text = "&Warm model files before launch", AccessibleName = "Warm model files before launch",
            AccessibleDescription = "Reads model geometry before launch into Windows file cache, up to 4 GiB while reserving 8 GiB of available RAM. Does not preload GPU resources or change quality.", Dock = DockStyle.Top,
            ForeColor = TextColor, Checked = false, Margin = new Padding(0), TabIndex = 2 };
        optionRow(preload);
        optionRow(Copy("Optional disk read-ahead. Adds startup time; does not create GPU resources.", 10, HighContrast ? TextColor : Quiet, false));
        var capture = new CaptureChoice { Name = "capture", Text = "&Record a 3-minute performance capture", AccessibleName = "Record a 3-minute performance capture",
            AccessibleDescription = "Automatically saves DLL inspection, native timings and CPU frame intervals. Off by default.", Dock = DockStyle.Top,
            ForeColor = TextColor, Checked = false, Margin = new Padding(0), TabIndex = 3 };
        optionRow(capture);
        optionRow(Copy("Saves DLL inspection, native timings and CPU frame intervals.", 10, HighContrast ? TextColor : Quiet, false));
        var optionSummary = Copy("File warmup: off  /  Capture: off", 10, HighContrast ? TextColor : Quiet, false); row(optionSummary);
        Action updateOptionSummary = delegate { optionSummary.Text = "File warmup: " + (preload.Checked ? "on" : "off") + "  /  Capture: " + (capture.Checked ? "on" : "off"); };
        preload.CheckedChanged += delegate { updateOptionSummary(); };
        capture.CheckedChanged += delegate { updateOptionSummary(); };
        var showResults = ButtonFor("VIEW SAVED COMPARISON  +", "Show previous comparison", false); showResults.Dock = DockStyle.Top; showResults.TabIndex = 4; showResults.Margin = new Padding(0, 6, 0, 8); optionRow(showResults);
        var results = new GroupBox { Text = "Saved results", AccessibleDescription = "Comparison from earlier builds. It does not prove this GPU preparation build reduces stutter.", AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Dock = DockStyle.Top, ForeColor = TextColor, Padding = new Padding(14, 24, 14, 14), Margin = new Padding(0, 10, 0, 20), TabStop = false };
        var resultText = Copy(LatestResultSummary(), 11, HighContrast ? TextColor : Quiet, false);
        results.Controls.Add(resultText); optionRow(results); results.Visible = false;
        showResults.Click += delegate {
            results.Visible = !results.Visible;
            showResults.Text = results.Visible ? "HIDE SAVED COMPARISON  âˆ’" : "VIEW SAVED COMPARISON  +";
            showResults.AccessibleName = results.Visible ? "Hide previous comparison" : "Show previous comparison";
        };
        optionRow(controls);
        var footer = new TableLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, ColumnCount = 1, Padding = new Padding(28, 12, 28, 16), BackColor = PageColor, TabIndex = 1 };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); form.Controls.Add(footer); viewport.BringToFront();
        var open = ButtonFor("&START MODDED ZENLESS  â†’", "Start modded Zenless", true); open.Name = "open"; open.Dock = DockStyle.Top; open.TabIndex = 0; open.Margin = new Padding(0, 0, 0, 12);
        open.AccessibleDescription = "Starts modded Zenless through XXMI Quick Start without another launcher window. Selected GPU preparation runs in game. Optional file warmup runs before launch.";
        var launchStatus = Copy("Preparation may take up to 2 minutes; memory limits are unchanged.", 10, HighContrast ? TextColor : Quiet, false);
        launchStatus.AccessibleRole = AccessibleRole.StatusBar;
        launchStatus.AccessibleName = launchStatus.Text;
        launchStatus.TextChanged += delegate { launchStatus.AccessibleName = launchStatus.Text; };
        experimental.CheckedChanged += delegate {
            launchStatus.Text = experimental.Checked ? "Preparation may take up to 2 minutes; memory limits are unchanged." : "Standard mode. Resources load when needed.";
        };
        bool preparing = false; CancellationTokenSource preparation = null;
        form.FormClosing += delegate(object sender, FormClosingEventArgs e) { if (preparing) { preparation.Cancel(); e.Cancel = true; launchStatus.Text = "Cancelling launchâ€¦"; } };
        open.Click += async delegate {
            if (preparing) { preparation.Cancel(); launchStatus.Text = "Cancelling launchâ€¦"; return; }
            if (prepare != null && preload.Checked) {
                preparing = true; preparation = new CancellationTokenSource();
                modes.Enabled = preload.Enabled = capture.Enabled = false;
                open.Text = "&CANCEL LAUNCH"; open.AccessibleName = "Cancel launch";
                try {
                    await prepare(new Progress<string>(delegate(string message) { launchStatus.Text = message; }), preparation.Token);
                    preparation.Token.ThrowIfCancellationRequested();
                } catch (OperationCanceledException) {
                    launchStatus.Text = "Launch cancelled. The game was not started.";
                    return;
                } catch (Exception error) {
                    launchStatus.Text = "Preload failed: " + error.Message + " Uncheck preload to start normally.";
                    return;
                } finally {
                    preparing = false; preparation.Dispose(); preparation = null;
                    modes.Enabled = preload.Enabled = capture.Enabled = true;
                    open.Text = "&START MODDED ZENLESS  â†’"; open.AccessibleName = "Start modded Zenless";
                }
            }
            PreloadModels = preload.Checked;
            choose(experimental.Checked ? "optimized" : "baseline", capture.Checked); form.Close();
        };
        footer.Controls.Add(open, 0, 0); footer.Controls.Add(launchStatus, 0, 1);
        form.AcceptButton = open;
        // Flow layout and explicit text zoom avoid clipped labels at 200% text size.
        var originalFonts = new Dictionary<Control, Font>(); foreach (var c in Descendants(form)) originalFonts[c] = c.Font;
        int zoom = 100;
        form.ChangeTextSize = delegate(int direction) {
            zoom = direction == 0 ? 100 : Math.Max(100, Math.Min(200, zoom + direction * 25));
            form.SuspendLayout();
            foreach (var pair in originalFonts) pair.Key.Font = new Font(pair.Value.FontFamily, pair.Value.Size * zoom / 100f, pair.Value.Style);
            hero.Height = Math.Max((int)(112 * zoom / 100f), title.PreferredHeight + 8);
            sizeLabel.Text = "Text " + zoom + "%  Â·  Ctrl + / âˆ’";
            smaller.Enabled = zoom > 100; larger.Enabled = zoom < 200;
            form.ResumeLayout(true); layoutModes(); modeStack.PerformLayout(); modes.PerformLayout(); stack.PerformLayout();
            foreach (var c in Descendants(viewport)) if (c.Focused) viewport.ScrollControlIntoView(c);
        };
        smaller.Click += delegate { form.ChangeTextSize(-1); }; larger.Click += delegate { form.ChangeTextSize(1); };
        smaller.Enabled = false;
        form.Shown += delegate {
            // The parent can establish its width before SizeChanged is subscribed above.
            // Resolve the first layout explicitly; resizing must not be required to size the cards.
            stack.PerformLayout(); modes.PerformLayout(); layoutModes(); modeStack.PerformLayout(); stack.PerformLayout();
            form.BeginInvoke(new Action(delegate {
                layoutModes(); modeStack.PerformLayout(); modes.PerformLayout(); stack.PerformLayout();
                experimental.Select(); experimental.Focus();
            }));
        };
        form.Resize += delegate { stack.PerformLayout(); };
        return form;
    }
    static double Contrast(Color a, Color b) {
        Func<Color, double> luminance = delegate(Color c) {
            Func<byte, double> linear = delegate(byte component) { double v = component / 255.0; return v <= .04045 ? v / 12.92 : Math.Pow((v + .055) / 1.055, 2.4); };
            return .2126 * linear(c.R) + .7152 * linear(c.G) + .0722 * linear(c.B);
        };
        double x = luminance(a), y = luminance(b); return (Math.Max(x, y) + .05) / (Math.Min(x, y) + .05);
    }
    static void CheckInterface() {
        var checks = new List<object>();
        foreach (var pair in new[] { new[] { Paper, Ink }, new[] { Quiet, Ink }, new[] { Paper, Tile }, new[] { Ink, Acid } }) {
            double ratio = Contrast(pair[0], pair[1]); Require(ratio >= 4.5, "Text contrast below 4.5:1");
            checks.Add(new { text_contrast = ratio });
        }
        Require(Contrast(Edge, Tile) >= 3 && Contrast(Edge, Ink) >= 3, "Control boundary contrast below 3:1");
        bool invoked = false;
        using (var form = (LaunchForm)MakePresetWindow(delegate(string value, bool capture) { invoked = true; })) {
            form.ShowInTaskbar = false; form.Opacity = 0; form.Show(); Application.DoEvents();
            ModeChoice normal = null, experimental = null; CaptureChoice recording = null, preload = null; Button open = null; FocusButton optionsToggle = null;
            foreach (var c in Descendants(form)) {
                if (c.Name == "standard") normal = (ModeChoice)c;
                if (c.Name == "experimental") experimental = (ModeChoice)c;
                if (c.Name == "capture") recording = (CaptureChoice)c;
                if (c.Name == "preload") preload = (CaptureChoice)c;
                if (c.Name == "open") open = (Button)c;
                if (c.Name == "options") optionsToggle = (FocusButton)c;
            }
            int initialCardWidth = (normal.Parent.ClientSize.Width - 12) / 2;
            Require(normal.Width >= initialCardWidth - 1 && experimental.Width >= initialCardWidth - 1, "First-open mode cards did not fill the available width");
            Require(normal.Height >= normal.GetPreferredSize(new Size(normal.Width, 0)).Height && experimental.Height >= experimental.GetPreferredSize(new Size(experimental.Width, 0)).Height, "First-open card text is clipped");
            Require(normal.Bottom <= normal.Parent.ClientSize.Height && experimental.Bottom <= experimental.Parent.ClientSize.Height, "First-open cards are clipped by their group");
            checks.Add(new { initial_layout = true, before_resize_or_disclosure = true, standard_width = normal.Width, gpu_width = experimental.Width, available_width = normal.Parent.ClientSize.Width });
            Require(!preload.Visible && optionsToggle.AccessibleName.StartsWith("Show"), "Optional tools must start collapsed");
            optionsToggle.PerformClick(); Application.DoEvents(); Require(preload.Visible, "Optional tools cannot be expanded");
            Require(normal.AccessibilityObject.Role == AccessibleRole.RadioButton && experimental.AccessibilityObject.Role == AccessibleRole.RadioButton, "Radio roles missing");
            Require((experimental.AccessibilityObject.State & AccessibleStates.Checked) != 0, "Selected state missing");
            Require(!preload.Checked && experimental.Enabled && experimental.Checked, "GPU preparation must default on without file warmup");
            normal.Checked = true;
            normal.Select(); normal.Focus(); Application.DoEvents();
            Require(normal.Focused, "Initial radio cannot receive focus");
            normal.PreviewKey(Keys.Right); Application.DoEvents();
            Require(experimental.Checked && !normal.Checked && experimental.Focused, "Arrow-key selection failed");
            experimental.PreviewKey(Keys.Left); Application.DoEvents(); Require(normal.Checked, "Reverse arrow selection failed");
            experimental.Checked = true;
            preload.AccessibilityObject.DoDefaultAction(); Require(preload.Checked && experimental.Checked && experimental.Enabled, "File warmup must remain independent of GPU preparation");
            preload.AccessibilityObject.DoDefaultAction(); Require(!preload.Checked, "Accessible file warmup toggle failed");
            Require(recording.AccessibilityObject.Role == AccessibleRole.CheckButton && open.AccessibilityObject.Role == AccessibleRole.PushButton, "Native control roles missing");
            experimental.Focus(); experimental.PreviewKey(Keys.Tab); Application.DoEvents(); Require(optionsToggle.Focused, "Tab order must leave mode group for options");
            optionsToggle.PreviewKey(Keys.Tab); Application.DoEvents(); Require(preload.Focused, "Tab order must continue to file warmup");
            preload.PreviewKey(Keys.Tab); Application.DoEvents(); Require(recording.Focused, "Tab order must continue to capture");
            recording.AccessibilityObject.DoDefaultAction(); Require(recording.Checked, "Accessible capture toggle failed");
            recording.AccessibilityObject.DoDefaultAction(); Require(!recording.Checked, "Accessible capture untoggle failed");
            foreach (int width in new[] { 880, 380, 320 }) {
                form.ClientSize = new Size(width, 790);
                Require(form.ClientSize.Width == width, "Requested reflow viewport was constrained");
                form.ChangeTextSize(0);
                for (int i = 0; i < 5; i++) {
                    Application.DoEvents();
                    Require(open.Width >= 44 && open.Height >= 44 && recording.Height >= 44 && normal.Height >= 44, "Target under 44 logical pixels");
                    Require(normal.Width >= 180 && normal.Width <= form.ClientSize.Width, "Mode does not reflow");
                    Require(normal.Height >= normal.GetPreferredSize(new Size(normal.Width, 0)).Height, "Mode text clipped");
                    Require(experimental.Bottom <= experimental.Parent.ClientSize.Height && normal.Bottom <= normal.Parent.ClientSize.Height, "Radio option clipped by group");
                    Require(open.Height >= open.GetPreferredSize(new Size(open.Width, 0)).Height, "Primary action text clipped");
                    foreach (var c in Descendants(form)) {
                        var panel = c as Panel;
                        if (panel != null && panel.AutoScroll) Require(!panel.HorizontalScroll.Visible, "Horizontal scrolling required");
                        var label = c as Label;
                        if (label != null && label.Visible) Require(label.Height >= label.GetPreferredSize(new Size(label.Width, 0)).Height, "Label text clipped: " + label.Text);
                    }
                    var footerBounds = form.RectangleToClient(open.RectangleToScreen(open.ClientRectangle));
                    Require(footerBounds.Bottom <= form.ClientSize.Height && footerBounds.Left >= 0 && footerBounds.Right <= form.ClientSize.Width, "Primary action clipped");
                    checks.Add(new { width = width, text_percent = 100 + i * 25, primary_visible = true, radio_height = normal.Height });
                    if (i == 4) using (var bitmap = new Bitmap(form.Width, form.Height)) {
                        form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                        bitmap.Save(Path.Combine(Root, ".guild-data", "loading-followup", "ui-200-" + width + ".png"));
                    }
                    if (i < 4) form.ChangeTextSize(1);
                }
            }
            optionsToggle.PerformClick(); Require(!preload.Visible, "Optional tools cannot be collapsed");
            form.ChangeTextSize(0); form.ClientSize = new Size(880, 720); Application.DoEvents();
            foreach (var control in Descendants(form)) { var panel = control as Panel; if (panel != null && panel.AutoScroll) panel.AutoScrollPosition = Point.Empty; }
            using (var bitmap = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size)); bitmap.Save(Path.Combine(Root, ".guild-data", "loading-followup", "launcher-preview.png")); }
            Require(!invoked, "UI audit invoked launch callback prematurely");
            open.Select(); form.PreviewEnter(); Require(invoked, "Enter did not invoke the stub primary action"); form.Close();
        }
        CheckPreparationFlow();
        File.WriteAllText(Path.Combine(Root, ".guild-data", "loading-followup", "ui-accessibility.json"), Json.Serialize(new { passed = true, checks = checks, keyboard = "Native radio arrows and Tab to preload/capture passed; accessible checkbox actions passed", preparation_flow = "Cancel button and window close prevent launch; retry launches exactly once after preparation; errors prevent launch", scope = "WCAG 2.2 / WCAG2ICT targeted checks; full screen-reader and multi-monitor DPI qualification not performed", launcher_started = false }), new UTF8Encoding(false));
    }

    static void CheckPreparationFlow() {
        foreach (string scenario in new[] { "cancel", "close", "error" }) {
            int launches = 0;
            var pending = new TaskCompletionSource<bool>();
            CancellationToken token = CancellationToken.None;
            using (var form = MakePresetWindow(delegate(string value, bool capture) { launches++; },
                delegate(IProgress<string> progress, CancellationToken cancellation) { token = cancellation; return pending.Task; })) {
                form.ShowInTaskbar = false; form.Opacity = 0; form.Show(); Application.DoEvents();
                Button start = null;
                foreach (var control in Descendants(form)) {
                    if (control.Name == "open") start = (Button)control;
                    if (control.Name == "preload") ((CheckBox)control).Checked = true;
                }
                start.PerformClick(); Application.DoEvents();
                Require(launches == 0 && start.AccessibleName == "Cancel launch", "Game start must wait for preparation");
                if (scenario == "error") pending.SetException(new IOException("Simulated read failure"));
                else {
                    if (scenario == "close") form.Close(); else start.PerformClick();
                    Require(token.IsCancellationRequested && !form.IsDisposed, "Cancellation must wait for the reader to stop");
                    pending.SetResult(true);
                }
                var wait = Stopwatch.StartNew();
                while (start.AccessibleName == "Cancel launch" && wait.ElapsedMilliseconds < 2000) { Application.DoEvents(); Thread.Sleep(1); }
                Require(launches == 0 && start.AccessibleName == "Start modded Zenless", "Cancelled or failed preparation must never launch");
                pending = new TaskCompletionSource<bool>();
                start.PerformClick(); Application.DoEvents();
                Require(launches == 0, "Retry launched before preparation completed");
                pending.SetResult(true); wait.Restart();
                while (launches == 0 && wait.ElapsedMilliseconds < 2000) { Application.DoEvents(); Thread.Sleep(1); }
                Require(launches == 1 && form.IsDisposed, "Successful preparation must launch exactly once");
            }
        }
    }

    static string ChoosePreset()
    {
        string choice = null;
        using (var form = MakePresetWindow(delegate(string value, bool capture) { choice = value; RecordCapture = capture; },
            async delegate(IProgress<string> progress, CancellationToken cancellation) {
                LastPreload = await Task.Run(delegate { return ModelPreloader.Prepare(Path.Combine(Profile, "ZZMI", "Mods"), progress, cancellation); });
            }))
            form.ShowDialog();
        return choice;
    }
    static ProcessStartInfo GameStart() {
        var start = new ProcessStartInfo(Path.Combine(Profile, "Resources", "Bin", "XXMI Launcher.exe"), "--nogui --xxmi ZZMI");
        start.WorkingDirectory = Path.GetDirectoryName(start.FileName);
        start.UseShellExecute = false; start.CreateNoWindow = true;
        return start;
    }
    static void CheckFiles()
    {
        Require(File.Exists(Path.Combine(Profile, ".collector-owned-diagnostic-profile")), "The approved diagnostic profile is missing. Keep this application in the XXMI-Launcher repository.");
        Require(Hash(Path.Combine(Profile, "ZZMI", "d3d11.dll")) == DllHash, "The prepared-loading DLL changed. This entry point will not load an unverified replacement.");
        Require(Hash(Path.Combine(Profile, "Resources", "Bin", "XXMI Launcher.exe")) == LauncherHash, "The isolated launcher executable changed.");
        ValidateConfiguration(Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Profile, "XXMI Launcher Config.json"))));
        ValidateIni(File.ReadAllText(Path.Combine(Profile, "ZZMI", "d3dx.ini")));
    }
    static void MustReject(Action operation)
    {
        bool rejected = false;
        try { operation(); } catch (InvalidOperationException) { rejected = true; }
        Require(rejected, "Negative validation test failed.");
    }
    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            Require(args.Length == 0 || (args.Length == 1 && (args[0] == "--check" || args[0] == "--self-test" || args[0] == "--render-preview" || args[0] == "--ui-self-test" || args[0] == "--measure-preload")), "Unsupported arguments.");
            if (args.Length == 1 && args[0] == "--ui-self-test") { Application.EnableVisualStyles(); CheckInterface(); return 0; }
            if (args.Length == 1 && args[0] == "--render-preview") {
                Application.EnableVisualStyles();
                using (var form = MakePresetWindow(delegate(string value, bool capture) {}))
                using (var bitmap = new Bitmap(form.Width, form.Height)) {
                    form.ShowInTaskbar = false; form.Opacity = 0; form.Show(); Application.DoEvents();
                    form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size)); form.Hide();
                    bitmap.Save(Path.Combine(Root, ".guild-data", "loading-followup", "launcher-preview.png"));
                }
                return 0;
            }
            CheckFiles();
            if (args.Length == 1 && args[0] == "--measure-preload") {
                ModelPreloader.SelfTest();
                var measured = ModelPreloader.Prepare(Path.Combine(Profile, "ZZMI", "Mods"), null, CancellationToken.None);
                File.WriteAllText(Path.Combine(Root, ".guild-data", "loading-followup", "model-preload-measured.json"), Json.Serialize(measured), new UTF8Encoding(false));
                return 0;
            }
            if (args.Length == 1)
            {
                if (args[0] == "--self-test")
                {
                    ModelPreloader.SelfTest();
                    var command = GameStart();
                    Require(command.Arguments == "--nogui --xxmi ZZMI" && !command.UseShellExecute && command.CreateNoWindow, "Quick Start command changed");
                    var config = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Profile, "XXMI Launcher Config.json")));
                    Obj(Obj(Obj(config, "Importers"), "ZZMI"), "Importer")["game_launch"] = "MANUAL";
                    MustReject(delegate { ValidateConfiguration(config); });
                    string ini = File.ReadAllText(Path.Combine(Profile, "ZZMI", "d3dx.ini"));
                    foreach (bool prepared in new[] { false, true }) foreach (bool record in new[] { false, true }) {
                        string changed = WithPreset(ini, prepared, record); ValidateIni(changed);
                        Require(ReadIni(changed)["Logging/mod_load_trace"] == (record ? "1" : "0"), "Capture toggle failed.");
                    }
                    MustReject(delegate { ValidateIni(WithPreset(ini, false).Replace("mode = off", "mode = adaptive")); });
                    MustReject(delegate { ValidateIni(ini.Replace("require_admin = false", "require_admin = true")); });
                    MustReject(delegate { ValidateIni(ini + "\n[Loader]\nlaunch = game.exe\n"); });
                    MustReject(delegate { ValidateIni(ini + "\n[ModLoading]\nmode = off\n"); });
                }
                File.WriteAllText(Path.Combine(Profile, "READY-CHECK.json"), Json.Serialize(new { passed = true, checks = args[0], game_launch_mode = "DIRECT", preparation_presets = "baseline and optimized", dll_sha256 = DllHash, game_launched = false, launcher_started = false, elevation_requested = false }), new UTF8Encoding(false));
                return 0;
            }
            using (var identity = WindowsIdentity.GetCurrent())
                Require(new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator),
                    "Open XXMI Prepared Loading.exe normally and approve its Windows prompt. Administrator access is required to attach to your game.");
            foreach (string name in new[] { "XXMI Launcher", "ZenlessZoneZero", "ZenlessZoneZeroBeta" })
                foreach (var process in Process.GetProcessesByName(name))
                    using (process) Require(false, "Close Zenless and XXMI before starting another session.");
            Application.EnableVisualStyles();
            string mode = ChoosePreset();
            if (mode == null) return 0;
            var preloadReport = PreloadModels && LastPreload != null ? LastPreload : new ModelPreloader.Result { enabled = false, stop_reason = "disabled" };
            File.WriteAllText(Path.Combine(Profile, "PRELOAD-RESULT.json"), Json.Serialize(preloadReport), new UTF8Encoding(false));
            string iniPath = Path.Combine(Profile, "ZZMI", "d3dx.ini");
            File.WriteAllText(iniPath, WithPreset(File.ReadAllText(iniPath), mode == "optimized", RecordCapture), new UTF8Encoding(false));
            string ready = Path.Combine(Profile, "capture-ready.json");
            double since = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
            if (RecordCapture) {
            string python = @"C:\Python312\pythonw.exe";
            string observer = Path.Combine(Root, "tools", "PreparedLoading", "capture-speed.py");
            Require(File.Exists(python) && File.Exists(observer), "The local capture runtime is missing.");
            if (File.Exists(ready)) File.Delete(ready);
            var observe = new ProcessStartInfo(python, "\"" + observer + "\" --root \"" + Root.TrimEnd('\\') + "\" --mode " + mode + " --dll-hash " + DllHash + " --since " + since.ToString(System.Globalization.CultureInfo.InvariantCulture) + " --ready \"" + ready + "\"");
            observe.UseShellExecute = false; observe.CreateNoWindow = true; observe.WorkingDirectory = Root;
            using (var helper = Process.Start(observe))
            {
                for (int attempt = 0; attempt < 300 && !File.Exists(ready) && !helper.HasExited; ++attempt) Thread.Sleep(100);
                Require(File.Exists(ready), "Capture setup did not become ready. See captures/loading-speed for details.");
                var state = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(ready));
                Require(Is(state, "ready", true), "Capture setup failed: " + (state.ContainsKey("error") ? state["error"] : "unknown error"));
            }
            }
            var start = GameStart(); // Inherits the already approved token; no second elevation request.
            using (var child = Process.Start(start))
            {
                if (RecordCapture) File.WriteAllText(ready, Json.Serialize(new { ready = true, launcher_pid = child.Id, since = since }), new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(Profile, "LAUNCHER-OPEN.json"), Json.Serialize(new { launcher_pid = child.Id, game_launch_requested = true, game_launch_verified = false, launcher_arguments = start.Arguments, mode = "DIRECT", preset = mode, preload_models = PreloadModels, administrator = true, elevation = "user-approved entry manifest; inherited by launcher" }), new UTF8Encoding(false));
            }
            return 0;
        }
        catch (Exception error)
        {
            if (args.Length == 0) MessageBox.Show(error.Message, "XXMI Prepared Loading", MessageBoxButtons.OK, MessageBoxIcon.Error);
            else File.WriteAllText(Path.Combine(Root, "prepared-launcher-check-error.txt"), error.ToString());
            return 1;
        }
    }
}
