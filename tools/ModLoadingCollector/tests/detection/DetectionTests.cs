using System;
using System.Collections.Generic;
using XxmiCollector;

namespace XxmiCollector
{
    // Same two-property input contract as the Windows inventory; no process probes run.
    public sealed class ProcessChoice { public int Id { get; set; } public string Name { get; set; } }
}

internal static class DetectionTests
{
    private static int checks;
    private static void Check(bool condition, string name) { checks++; if (!condition) throw new Exception(name); }
    private static ProcessChoice Process(int id, string name) { return new ProcessChoice { Id = id, Name = name }; }
    private static void Main()
    {
        int probes = 0;
        Func<int, GameProcessHints> unused = id => { probes++; return null; };
        foreach (string name in new[] { "ZenlessZoneZero", "ZenlessZoneZeroBeta.exe", "genshinimpact.EXE", "YuanShen", "StarRail", "BH3", "Endfield" })
        {
            var found = GameDetection.Find(new[] { Process(100, name) }, unused);
            Check(found.Count == 1 && found[0].Process.Id == 100 && !String.IsNullOrEmpty(found[0].GameName), "branded alias: " + name);
        }
        Check(probes == 0, "branded process detection needs no extra process access");
        Check(GameDetection.Find(new[] { Process(1, "GenshinImpactLauncher"), Process(2, "HoYoPlay"), Process(3, "Wuthering Waves"), Process(4, "SomeStarRailTool") }, unused).Count == 0, "launchers and substrings excluded");
        var generic = new[] { Process(10, "Client-Win64-Shipping") };
        Check(GameDetection.Find(generic, id => new GameProcessHints()).Count == 0, "generic UE executable alone excluded");
        Check(GameDetection.Find(generic, id => new GameProcessHints { ExecutablePath = @"C:\Games\Other\Client\Binaries\Win64\Client-Win64-Shipping.exe" }).Count == 0, "generic directory layout alone excluded");
        Check(GameDetection.Find(generic, id => new GameProcessHints { WindowTitle = "Wuthering Waves" }).Count == 1, "Wuthering window title corroborates generic executable");
        Check(GameDetection.Find(generic, id => new GameProcessHints { ExecutablePath = @"C:\Games\Wuthering Waves Game\Client\Binaries\Win64\Client-Win64-Shipping.exe" }).Count == 1, "branded expected executable path corroborates generic executable");
        Check(GameDetection.Find(generic, id => new GameProcessHints { ExecutablePath = @"C:\Games\Wuthering Waves\Other\Client-Win64-Shipping.exe" }).Count == 0, "branded folder with unexpected structure excluded");
        Check(GameDetection.Find(generic, id => { throw new System.ComponentModel.Win32Exception(5); }).Count == 0, "protected ambiguous process remains manual");
        var multiple = GameDetection.Find(new[] { Process(1, "StarRail"), Process(2, "ZenlessZoneZero"), Process(1, "StarRail") }, unused);
        Check(multiple.Count == 2, "all distinct game instances retained without arbitrary winner");
        Check(GameDetection.Find(null, unused).Count == 0, "absent process snapshot safe");
        Check(GameDetection.Find(new[] { Process(0, "StarRail"), null, Process(20, null) }, unused).Count == 0, "invalid candidates ignored");
        Console.WriteLine("Game detection checks passed: " + checks);
    }
}
