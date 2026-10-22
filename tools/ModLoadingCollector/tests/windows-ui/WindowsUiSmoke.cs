using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;

// Synthetic UI only: no Program.Main, process enumeration, capture, WMI or game access.
internal static class WindowsUiSmoke
{
    private static readonly List<string> failures = new List<string>();
    private static string destination;

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 2) { Console.Error.WriteLine("Usage: WindowsUiSmoke.exe <collector.exe> <evidence-directory>"); return 2; }
        destination = Path.GetFullPath(args[1]); Directory.CreateDirectory(destination);
        try
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT) throw new PlatformNotSupportedException("Requires Windows native WinForms.");
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            Assembly app = Assembly.LoadFrom(Path.GetFullPath(args[0]));
            Run(app, "default", new Size(940, 600), 1f);
            Run(app, "minimum", new Size(640, 480), 1f);
            Run(app, "font-125-simulation", new Size(1240, 650), 1.25f);
            Run(app, "font-150-simulation", new Size(1240, 650), 1.5f);
        }
        catch (Exception ex) { failures.Add(ex.ToString()); }
        foreach (string failure in failures) Console.Error.WriteLine("FAIL: " + failure);
        string summary = failures.Count == 0 ? "PASS: all synthetic Windows UI smoke checks." : "FAIL: " + failures.Count + " checks.";
        Console.WriteLine(summary);
        File.WriteAllLines(Path.Combine(destination, "assertions.txt"), failures.Concat(new[] { summary }));
        return failures.Count == 0 ? 0 : 1;
    }

    private static void Run(Assembly app, string name, Size size, float scale)
    {
        using (var form = (Form)Activator.CreateInstance(app.GetType("XxmiCollector.CaptureForm", true), true))
        {
            try
            {
                // The only Shown handler enumerates processes. Detach it before showing the
                // real native window, so Visible, layout and button interaction are meaningful.
                var eventKey = typeof(Form).GetField("EVENT_SHOWN", BindingFlags.Static | BindingFlags.NonPublic);
                if (eventKey == null) throw new InvalidOperationException("Cannot safely detach Shown; refusing to show the form.");
                var events = (EventHandlerList)typeof(Component).GetProperty("Events", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(form);
                object key = eventKey.GetValue(null); events.RemoveHandler(key, events[key]);
                form.StartPosition = FormStartPosition.Manual; form.Location = new Point(0, 0);
                form.AutoScaleMode = AutoScaleMode.None;
                if (scale != 1f)
                {
                    // Explicit font stress simulation, not a claim of real per-monitor DPI.
                    var fonts = Walk(form).Select(c => new { Control = c, Font = c.Font }).ToArray();
                    foreach (var item in fonts) item.Control.Font = new Font(item.Font.FontFamily, item.Font.Size * scale, item.Font.Style);
                    form.Scale(new SizeF(scale, scale));
                }
                if (name == "minimum") form.Size = size;
                else form.ClientSize = size;
                var combo = (ComboBox)Find(form, "RunningGame");
                Type choiceType = app.GetType("XxmiCollector.ProcessChoice", true);
                object choice = Activator.CreateInstance(choiceType);
                choiceType.GetProperty("Name").SetValue(choice, "Synthetic UI sample (no game access)");
                choiceType.GetProperty("Id").SetValue(choice, 4242);
                combo.Items.Add(choice); combo.SelectedIndex = 0;
                form.Show(); Layout(form);
                Check(!Find(form, "OptionalSettings").Visible && !Find(form, "OptionalEvidence").Visible, name + ": optional sections initially collapsed");
                Snapshot(form, name + "-ready");
                CheckBasic(form, name);
                foreach (Control control in Walk(form)) CheckContrast(control, name);
                var pairs = (Color[,])app.GetType("XxmiCollector.CapturePalette", true)
                    .GetField("ContrastPairs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                Color border = (Color)app.GetType("XxmiCollector.CapturePalette", true)
                    .GetField("Border", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                for (int i = 0; i < pairs.GetLength(0); i++)
                    Check(Contrast(pairs[i, 0], pairs[i, 1]) >= (pairs[i, 0] == border ? 3.0 : 4.5), name + ": explicit paint palette contrast pair " + i);
                ((Button)Find(form, "OptionalSettingsToggle")).PerformClick(); Layout(form);
                Check(Find(form, "OptionalSettings").Visible, name + ": settings toggle opens section");
                Snapshot(form, name + "-settings");
                ((Button)Find(form, "OptionalEvidenceToggle")).PerformClick(); Layout(form);
                Check(Find(form, "OptionalEvidence").Visible, name + ": evidence toggle opens section");
                var scroll = (ScrollableControl)Find(form, "CaptureContent");
                foreach (string target in new[] { "OutputFolder", "PresentMonPath", "RunNotes", "ObservationNote", "Observations" })
                {
                    Control control = Find(form, target); scroll.ScrollControlIntoView(control); Layout(form);
                    Rectangle visible = scroll.RectangleToScreen(scroll.ClientRectangle);
                    Rectangle bounds = control.RectangleToScreen(control.ClientRectangle);
                    Check(visible.IntersectsWith(bounds), name + ": expanded " + target + " reachable by scrolling");
                }
                Snapshot(form, name + "-expanded-scrolled");
                Check(FullyInside(form, Find(form, "CaptureActions")), name + ": footer remains visible with expanded sections");
            }
            catch (Exception ex) { failures.Add(name + ": " + ex); try { Snapshot(form, name + "-error"); } catch { } }
            finally { form.Close(); }
        }
    }

    private static void CheckBasic(Form form, string name)
    {
        Control footer = Find(form, "CaptureActions");
        Control start = ((Control)form.AcceptButton);
        Check(FullyInside(form, start) && FullyInside(footer, start), name + ": start action fully visible");
        Check(FullyInside(form, footer), name + ": footer fully visible");
        var scroll = (ScrollableControl)Find(form, "CaptureContent");
        Check(!scroll.HorizontalScroll.Visible, name + ": basic view has no horizontal scrollbar");
        foreach (Control c in Walk(form).Where(c => c.Visible && (c is Label || c is Button)))
        {
            // Text must fit its own box even if a vertically scrolled section is offscreen.
            int available = Math.Max(1, c.ClientSize.Width - c.Padding.Horizontal);
            TextFormatFlags flags = TextFormatFlags.NoPrefix | TextFormatFlags.WordBreak;
            Size measured = TextRenderer.MeasureText(c.Text, c.Font, new Size(available, int.MaxValue), flags);
            Check(measured.Height <= c.ClientSize.Height - c.Padding.Vertical + 3,
                name + ": text fits height: " + c.Text);
            Rectangle bounds = scroll.RectangleToClient(c.RectangleToScreen(c.ClientRectangle));
            if (!footer.Contains(c)) Check(bounds.Left >= -1 && bounds.Right <= scroll.ClientSize.Width + 1,
                name + ": text stays within content width: " + c.Text);
            else Check(FullyInside(footer, c), name + ": footer button fits: " + c.Text);
        }
    }

    private static void CheckContrast(Control c, string name)
    {
        if (!(c is TextBoxBase || c is ComboBox || c is NumericUpDown || c is Label || c is Button)) return;
        double ratio = Contrast(c.ForeColor, c.BackColor);
        Check(ratio >= 4.5, name + ": text contrast >=4.5 for " + (string.IsNullOrEmpty(c.Name) ? c.Text : c.Name) + " (" + ratio.ToString("F2") + ")");
    }

    private static double Contrast(Color a, Color b)
    {
        double x = Luminance(a), y = Luminance(b); return (Math.Max(x, y) + .05) / (Math.Min(x, y) + .05);
    }
    private static double Luminance(Color c)
    {
        Func<byte, double> linear = value => { double v = value / 255.0; return v <= .04045 ? v / 12.92 : Math.Pow((v + .055) / 1.055, 2.4); };
        return .2126 * linear(c.R) + .7152 * linear(c.G) + .0722 * linear(c.B);
    }
    private static void Check(bool condition, string message) { if (!condition) failures.Add(message); }
    private static bool FullyInside(Control parent, Control child)
    { return parent.RectangleToScreen(parent.ClientRectangle).Contains(child.RectangleToScreen(child.ClientRectangle)); }
    private static Control Find(Control root, string name) { return Walk(root).Single(c => c.Name == name); }
    private static IEnumerable<Control> Walk(Control root)
    { yield return root; foreach (Control child in root.Controls) foreach (Control item in Walk(child)) yield return item; }
    private static void Layout(Form form) { form.PerformLayout(); Application.DoEvents(); form.Update(); }
    private static void Snapshot(Form form, string name)
    {
        using (var bitmap = new Bitmap(form.Width, form.Height))
        {
            form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
            using (Graphics g = Graphics.FromImage(bitmap))
            {
                var stripe = new Rectangle(0, 0, bitmap.Width, 22);
                g.FillRectangle(Brushes.White, stripe);
                g.DrawString("SYNTHETIC UI ONLY | " + name + " | no capture or game access", SystemFonts.CaptionFont, Brushes.Black, stripe);
            }
            bitmap.Save(Path.Combine(destination, name + ".png"), ImageFormat.Png);
        }
    }
}
