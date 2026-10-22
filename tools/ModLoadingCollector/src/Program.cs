using System;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("XXMI Capture")]
[assembly: AssemblyDescription("On-demand local diagnostics for XXMI mod loading")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]

namespace XxmiCollector
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.ThreadException += (sender, e) => MessageBox.Show(
                e.Exception.Message, "XXMI Capture", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Application.Run(new CaptureForm());
        }
    }
}
