using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using XxmiCollector;

internal static class ArchiveTests
{
    private static int checks;
    private static void Check(bool condition, string name) { checks++; if (!condition) throw new Exception(name); }
    private static bool Refused(Action action) { try { action(); return false; } catch (Exception e) when (e is IOException || e is System.Runtime.Serialization.SerializationException) { return true; } }
    private static string Digest(string path) { using (var h = SHA256.Create()) return Convert.ToBase64String(h.ComputeHash(File.ReadAllBytes(path))); }
    private static void Main(string[] args)
    {
        string root = Path.GetFullPath(args.Length == 0 ? "work/archive-tests" : args[0]);
        Directory.CreateDirectory(root);
        string session = Path.Combine(root, "session-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(session);
        string evidence = Path.Combine(session, "evidence"); Directory.CreateDirectory(evidence);
        string native = Path.Combine(session, "native-traces"); Directory.CreateDirectory(native);
        File.WriteAllText(Path.Combine(session, "samples.csv"), "elapsed_ms\n0\n");
        File.WriteAllText(Path.Combine(session, "report.html"), "<p>report</p>");
        File.WriteAllText(Path.Combine(session, "imported-provenance.json"), "{}");
        File.WriteAllText(Path.Combine(session, "untracked.txt"), "must not export");
        File.WriteAllText(Path.Combine(evidence, "known.txt"), "evidence");
        File.WriteAllText(Path.Combine(evidence, "unknown.txt"), "must not export");
        File.WriteAllText(Path.Combine(native, "known.csv"), "trace");
        string manifest = "{\"traces\":[{\"status\":\"copied\",\"path\":\"native-traces/known.csv\"}],\"evidence\":[{\"status\":\"copied\",\"path\":\"evidence/known.txt\"}]}";
        string manifestPath = Path.Combine(session, "session.json"); File.WriteAllText(manifestPath, manifest);
        string zip = SessionArchive.Create(session);
        using (var archive = ZipFile.OpenRead(zip))
        {
            var names = archive.Entries.Select(x => x.FullName).ToArray();
            Check(names.Length == 6, "bounded allowlist only");
            Check(names.Contains("imported-provenance.json"), "source provenance included");
            Check(names.Contains("evidence/known.txt") && names.Contains("native-traces/known.csv"), "recorded evidence included");
            Check(!names.Any(x => x.Contains("unknown") || x.Contains("untracked")), "untracked files excluded");
        }
        File.WriteAllText(Path.Combine(session, "report.html"), "<p>updated</p>"); SessionArchive.Create(session);
        using (var archive = ZipFile.OpenRead(zip))
        using (var reader = new StreamReader(archive.GetEntry("report.html").Open())) Check(reader.ReadToEnd().Contains("updated"), "old archive replaced");
        string prior = Digest(zip);
        File.WriteAllText(manifestPath, "{\"evidence\":[{\"status\":\"copied\",\"path\":\"evidence/../outside.txt\"}]}");
        Check(Refused(() => SessionArchive.Create(session)), "traversal refused"); Check(Digest(zip) == prior, "old ZIP retained on invalid manifest");
        File.WriteAllText(manifestPath, manifest);
        string outside = Path.Combine(root, "outside.txt"); File.WriteAllText(outside, "outside session");
        string known = Path.Combine(evidence, "known.txt"); File.Delete(known); File.CreateSymbolicLink(known, outside);
        Check(Refused(() => SessionArchive.Create(session)), "linked evidence refused"); Check(Digest(zip) == prior, "old ZIP retained on linked evidence");
        File.Delete(known); File.WriteAllText(known, "evidence");
        using (var sparse = new FileStream(known, FileMode.Open, FileAccess.Write)) sparse.SetLength(64L * 1024 * 1024 + 1);
        Check(Refused(() => SessionArchive.Create(session)), "oversize refused"); Check(Digest(zip) == prior, "old ZIP retained on size rejection");
        Check(!Directory.EnumerateFiles(root, "*.tmp").Any(), "failed archive temporary removed");
        File.WriteAllText(known, "evidence"); File.WriteAllText(manifestPath, "not json");
        Check(Refused(() => SessionArchive.Create(session)), "invalid JSON refused");
        Console.WriteLine("Archive checks passed: " + checks);
    }
}
