using System;
using System.Collections.Generic;
using System.Diagnostics;

namespace XxmiCollector
{
    internal sealed class DetectedGame
    {
        internal ProcessChoice Process { get; set; }
        internal string GameName { get; set; }
    }

    internal sealed class GameProcessHints
    {
        internal string WindowTitle { get; set; }
        internal string ExecutablePath { get; set; }
    }

    internal static class GameDetection
    {
        // Executables from the launcher's model_importers/*_package.py definitions.
        // Wuthering Waves.exe is its branded wrapper, not the rendering process.
        private static readonly Dictionary<string, string> KnownGames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) {
            { "ZenlessZoneZero", "Zenless Zone Zero" },
            { "ZenlessZoneZeroBeta", "Zenless Zone Zero (Beta)" },
            { "GenshinImpact", "Genshin Impact" },
            { "YuanShen", "Genshin Impact" },
            { "StarRail", "Honkai: Star Rail" },
            { "BH3", "Honkai Impact 3rd" },
            { "Endfield", "Arknights: Endfield" }
        };

        internal static List<DetectedGame> Find(IList<ProcessChoice> choices, Func<int, GameProcessHints> hintProvider = null)
        {
            var found = new List<DetectedGame>();
            var seen = new HashSet<int>();
            if (choices == null) return found;
            foreach (var choice in choices)
            {
                if (choice == null || choice.Id <= 0 || String.IsNullOrEmpty(choice.Name)) continue;
                string name = choice.Name;
                if (name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) name = name.Substring(0, name.Length - 4);
                string game;
                if (!KnownGames.TryGetValue(name, out game))
                {
                    if (!String.Equals(name, "Client-Win64-Shipping", StringComparison.OrdinalIgnoreCase)) continue;
                    GameProcessHints hints;
                    try { hints = (hintProvider ?? Probe)(choice.Id); }
                    catch (Exception ex) when (ex is System.ComponentModel.Win32Exception || ex is ArgumentException || ex is InvalidOperationException || ex is NotSupportedException)
                    { continue; }
                    if (!IsWutheringWaves(hints)) continue;
                    game = "Wuthering Waves";
                }
                if (seen.Add(choice.Id)) found.Add(new DetectedGame { Process = choice, GameName = game });
            }
            return found;
        }

        private static bool IsWutheringWaves(GameProcessHints hints)
        {
            if (hints == null) return false;
            string title = (hints.WindowTitle ?? "").Trim();
            if (title.Equals("Wuthering Waves", StringComparison.OrdinalIgnoreCase) ||
                title.StartsWith("Wuthering Waves ", StringComparison.OrdinalIgnoreCase) ||
                title.StartsWith("Wuthering Waves-", StringComparison.OrdinalIgnoreCase)) return true;
            string path = (hints.ExecutablePath ?? "").Replace('\\', '/');
            if (!path.EndsWith("/Client/Binaries/Win64/Client-Win64-Shipping.exe", StringComparison.OrdinalIgnoreCase)) return false;
            foreach (string segment in path.Split('/'))
                if (segment.Equals("Wuthering Waves", StringComparison.OrdinalIgnoreCase) ||
                    segment.Equals("Wuthering Waves Game", StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        private static GameProcessHints Probe(int processId)
        {
            var hints = new GameProcessHints();
            using (var process = Process.GetProcessById(processId))
            {
                try { hints.WindowTitle = process.MainWindowTitle; }
                catch (System.ComponentModel.Win32Exception) { }
                catch (InvalidOperationException) { }
                try { hints.ExecutablePath = process.MainModule == null ? null : process.MainModule.FileName; }
                catch (System.ComponentModel.Win32Exception) { }
                catch (InvalidOperationException) { }
                catch (NotSupportedException) { }
            }
            return hints;
        }
    }
}
