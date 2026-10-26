using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;

namespace XxmiCollector
{
    internal static class SessionArchive
    {
        private const long FileLimit = 64L * 1024 * 1024;
        private const long TotalLimit = 512L * 1024 * 1024;
        private static readonly string[] GeneratedFiles = {
            "session.json", "inventory.json", "samples.csv", "markers.csv", "analysis.json",
            "report.html", "presentmon.json", "presentmon.log", "frame-times.csv", "provenance.json", "imported-provenance.json"
        };

        internal static string Create(string sessionDirectory)
        {
            string root = Path.GetFullPath(sessionDirectory);
            RejectLink(root);
            string manifestPath = Path.Combine(root, "session.json");
            RejectLink(manifestPath);
            if (new FileInfo(manifestPath).Length > 2 * 1024 * 1024)
                throw new IOException("Session manifest exceeds the 2 MiB archive limit.");
            ArchiveManifest manifest;
            using (var input = new FileStream(manifestPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                var serializer = new DataContractJsonSerializer(typeof(ArchiveManifest),
                    new DataContractJsonSerializerSettings { MaxItemsInObjectGraph = 2048 });
                manifest = (ArchiveManifest)serializer.ReadObject(input);
            }
            if (manifest == null) throw new IOException("The session manifest is missing.");
            var entries = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string name in GeneratedFiles)
                if (File.Exists(Path.Combine(root, name))) entries.Add(name);
            AddRecorded(entries, manifest.Traces, "native-traces");
            AddRecorded(entries, manifest.Evidence, "evidence");
            if (entries.Count > 200) throw new IOException("Archive exceeds the 200-file limit.");

            string destination = root + ".zip";
            string temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                long total = 0;
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                using (var archive = new ZipArchive(output, ZipArchiveMode.Create))
                {
                    foreach (string relative in entries.OrderBy(x => x, StringComparer.Ordinal))
                    {
                        string source = root;
                        foreach (string segment in relative.Split('/'))
                        {
                            source = Path.Combine(source, segment);
                            RejectLink(source);
                        }
                        var info = new FileInfo(source);
                        long size = info.Length;
                        DateTime modified = info.LastWriteTimeUtc;
                        if (size > FileLimit || size > TotalLimit - total)
                            throw new IOException("Archive input exceeds its file or total size limit: " + relative);
                        // FileShare.Read prevents concurrent mutation on Windows while copying.
                        using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
                        using (var entry = archive.CreateEntry(relative, CompressionLevel.Optimal).Open())
                        {
                            byte[] buffer = new byte[65536];
                            long read = 0; int got;
                            while ((got = input.Read(buffer, 0, buffer.Length)) != 0)
                            {
                                read += got;
                                if (read > size || read > FileLimit || read > TotalLimit - total)
                                    throw new IOException("Archive input changed or exceeded limits: " + relative);
                                entry.Write(buffer, 0, got);
                            }
                            info.Refresh();
                            if (read != size || info.Length != size || info.LastWriteTimeUtc != modified)
                                throw new IOException("Archive input changed while reading: " + relative);
                            total += read;
                        }
                    }
                }
                if (File.Exists(destination))
                {
                    RejectLink(destination);
                    File.Replace(temporary, destination, null);
                }
                else File.Move(temporary, destination);
                return destination;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        private static void AddRecorded(HashSet<string> entries, List<ArchiveFile> records, string directory)
        {
            if (records == null) return;
            if (records.Count > 160) throw new IOException("Recorded evidence count exceeds archive limits.");
            foreach (var record in records)
            {
                if (record == null || record.Status != "copied") continue;
                string relative = (record.Path ?? "").Replace('\\', '/');
                string[] parts = relative.Split('/');
                if (parts.Length != 2 || parts[0] != directory || String.IsNullOrEmpty(parts[1]) ||
                    parts[1] == "." || parts[1] == ".." || parts[1].EndsWith(".", StringComparison.Ordinal) ||
                    parts[1].EndsWith(" ", StringComparison.Ordinal) ||
                    parts[1].Any(c => c < 32 || ":<>\"|?*".IndexOf(c) >= 0))
                    throw new IOException("The manifest contains an invalid archive evidence path.");
                entries.Add(relative);
            }
        }

        private static void RejectLink(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Linked files or folders are excluded from the archive: " + path);
        }

        [DataContract] private sealed class ArchiveManifest
        {
            [DataMember(Name = "traces")] public List<ArchiveFile> Traces { get; set; }
            [DataMember(Name = "evidence")] public List<ArchiveFile> Evidence { get; set; }
        }
        [DataContract] private sealed class ArchiveFile
        {
            [DataMember(Name = "status")] public string Status { get; set; }
            [DataMember(Name = "path")] public string Path { get; set; }
        }
    }
}
