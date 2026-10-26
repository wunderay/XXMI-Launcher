using System.Drawing;
using System.Windows.Forms;

namespace XxmiCollector
{
    internal static class CapturePalette
    {
        private static readonly bool HighContrast = SystemInformation.HighContrast;
        internal static readonly Color Canvas = HighContrast ? SystemColors.Window : Color.FromArgb(243, 246, 250);
        internal static readonly Color Surface = HighContrast ? SystemColors.Window : Color.White;
        internal static readonly Color Text = HighContrast ? SystemColors.WindowText : Color.FromArgb(24, 36, 48);
        internal static readonly Color SecondaryText = HighContrast ? SystemColors.WindowText : Color.FromArgb(65, 80, 96);
        internal static readonly Color Primary = HighContrast ? SystemColors.Highlight : Color.FromArgb(0, 84, 80);
        internal static readonly Color OnPrimary = HighContrast ? SystemColors.HighlightText : Color.White;
        internal static readonly Color Border = HighContrast ? SystemColors.WindowText : Color.FromArgb(106, 122, 137);
        internal static readonly Color DisabledSurface = HighContrast ? SystemColors.Control : Color.FromArgb(229, 235, 241);
        internal static readonly Color DisabledText = HighContrast ? SystemColors.ControlText : Color.FromArgb(65, 80, 96);

        // All ordinary text and button-state pairs are explicit for contrast review.
        internal static readonly Color[,] ContrastPairs = {
            { Text, Canvas }, { Text, Surface }, { SecondaryText, Canvas }, { SecondaryText, Surface },
            { OnPrimary, Primary }, { DisabledText, DisabledSurface }, { Border, Surface }, { Border, Canvas }
        };
    }
}
