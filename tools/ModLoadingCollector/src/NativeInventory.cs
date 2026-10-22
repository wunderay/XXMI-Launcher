using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Management;
using System.Runtime.Serialization;
using System.Security.Cryptography;
using System.Threading;

namespace XxmiCollector
{
    [DataContract]
    internal sealed class InventoryDocument
    {
        [DataMember(Name = "schemaVersion")] public int SchemaVersion = 1;
        [DataMember(Name = "collectedUtc")] public string CollectedUtc;
        [DataMember(Name = "collectorArchitecture")] public string CollectorArchitecture;
        [DataMember(Name = "logicalProcessorCount")] public int LogicalProcessorCount;
        [DataMember(Name = "system")] public List<InventoryProbe> System = new List<InventoryProbe>();
        [DataMember(Name = "cpu")] public List<InventoryProbe> Cpu = new List<InventoryProbe>();
        [DataMember(Name = "gpu")] public List<InventoryProbe> Gpu = new List<InventoryProbe>();
        [DataMember(Name = "storage")] public List<InventoryProbe> Storage = new List<InventoryProbe>();
        [DataMember(Name = "modulesStatus")] public string ModulesStatus;
        [DataMember(Name = "modules")] public List<ModuleInventory> Modules = new List<ModuleInventory>();
        [DataMember(Name = "limitations")] public string Limitations = "WMI inventory describes installed hardware, not the game's active rendering adapter. WMI AdapterRAM is intentionally omitted because it is not reliable VRAM capacity. Game-device VRAM capacity/budget/usage and device creation flags require in-process native evidence. Module inventory is a capture-start snapshot; unavailable/protected processes remain explicit. File SHA256 identifies on-disk bytes, not loaded memory pages.";
    }

    [DataContract]
    internal sealed class InventoryProbe
    {
        [DataMember(Name = "kind")] public string Kind;
        [DataMember(Name = "status")] public string Status;
        [DataMember(Name = "reason")] public string Reason;
        [DataMember(Name = "values")] public List<InventoryValue> Values = new List<InventoryValue>();
    }

    [DataContract]
    internal sealed class InventoryValue
    {
        [DataMember(Name = "name")] public string Name;
        [DataMember(Name = "value")] public string Value;
    }

    [DataContract]
    internal sealed class ModuleInventory
    {
        [DataMember(Name = "name")] public string Name;
        [DataMember(Name = "path")] public string Path;
        [DataMember(Name = "fileVersion")] public string FileVersion;
        [DataMember(Name = "productVersion")] public string ProductVersion;
        [DataMember(Name = "sha256")] public string Sha256;
        [DataMember(Name = "sizeBytes")] public long SizeBytes;
        [DataMember(Name = "status")] public string Status;
        [DataMember(Name = "reason")] public string Reason;
    }

    internal static class NativeInventory
    {
        private const int QueryTimeoutMilliseconds = 3000;

        public static void Collect(Process process, string destination, Action<string> failure)
        {
            var document = new InventoryDocument { CollectedUtc = CaptureSession.Utc(DateTime.UtcNow),
                CollectorArchitecture = Environment.Is64BitProcess ? "x64" : "x86", LogicalProcessorCount = Environment.ProcessorCount };
            document.System.AddRange(Query("os", "Win32_OperatingSystem", new[] { "Caption", "Version", "BuildNumber", "OSArchitecture", "TotalVisibleMemorySize", "FreePhysicalMemory" }));
            document.System.AddRange(Query("computer", "Win32_ComputerSystem", new[] { "Manufacturer", "Model", "TotalPhysicalMemory", "NumberOfLogicalProcessors" }));
            document.Cpu.AddRange(Query("cpu", "Win32_Processor", new[] { "Name", "Manufacturer", "NumberOfCores", "NumberOfLogicalProcessors", "MaxClockSpeed" }));
            document.Gpu.AddRange(Query("gpu", "Win32_VideoController", new[] { "Name", "PNPDeviceID", "DriverVersion", "DriverDate", "VideoProcessor", "Status" }));
            document.Storage.AddRange(Query("disk", "Win32_DiskDrive", new[] { "Model", "InterfaceType", "MediaType", "Size", "Status" }));
            CollectModules(process, document);
            foreach (var group in new[] { document.System, document.Cpu, document.Gpu, document.Storage })
                foreach (var probe in group)
                    if (probe.Status != "ok") failure("Inventory " + probe.Kind + ": " + probe.Status + " " + probe.Reason);
            if (document.ModulesStatus != "ok") failure("Loaded-module inventory: " + document.ModulesStatus);
            JsonFile.Write(destination, document);
        }

        private static List<InventoryProbe> Query(string kind, string className, string[] properties)
        {
            var rows = new List<InventoryProbe>();
            object gate = new object();
            bool finished = false;
            string completionFailure = null;
            // Asynchronous WMI plus a bounded wait keeps a slow/unavailable provider from
            // holding startup indefinitely. Late callbacks cannot mutate the returned list.
            using (var completed = new ManualResetEvent(false))
            using (var searcher = new ManagementObjectSearcher("SELECT " + string.Join(",", properties) + " FROM " + className))
            {
                var observer = new ManagementOperationObserver();
                observer.ObjectReady += (sender, args) =>
                {
                    using (var item = args.NewObject)
                    {
                        lock (gate)
                        {
                            if (finished || rows.Count >= 32) return;
                            var row = new InventoryProbe { Kind = kind, Status = "ok" };
                            foreach (string property in properties)
                            {
                                try
                                {
                                    object value = item[property];
                                    row.Values.Add(new InventoryValue { Name = property, Value = value == null ? null : Convert.ToString(value, CultureInfo.InvariantCulture) });
                                }
                                catch (Exception ex) { row.Status = "partial"; row.Reason = CaptureSession.Describe(ex); }
                            }
                            rows.Add(row);
                        }
                    }
                };
                observer.Completed += (sender, args) =>
                {
                    lock (gate)
                    {
                        if (finished) return;
                        if (args.Status != ManagementStatus.NoError) completionFailure = args.Status.ToString();
                        completed.Set();
                    }
                };
                try
                {
                    searcher.Options = new EnumerationOptions { ReturnImmediately = true, Timeout = TimeSpan.FromMilliseconds(QueryTimeoutMilliseconds), Rewindable = false };
                    searcher.Get(observer);
                    bool signaled = completed.WaitOne(QueryTimeoutMilliseconds);
                    lock (gate)
                    {
                        finished = true;
                        if (!signaled) rows.Add(new InventoryProbe { Kind = kind, Status = "timeout", Reason = "WMI query did not finish within 3 seconds; any returned rows may be incomplete." });
                        else if (completionFailure != null) rows.Add(new InventoryProbe { Kind = kind, Status = "unavailable", Reason = completionFailure });
                        else if (rows.Count == 0) rows.Add(new InventoryProbe { Kind = kind, Status = "no_data", Reason = "WMI query returned no rows." });
                        else if (rows.Count >= 32) rows.Add(new InventoryProbe { Kind = kind, Status = "bounded", Reason = "At most 32 hardware rows retained." });
                    }
                    if (!signaled) try { observer.Cancel(); } catch (ManagementException) { }
                }
                catch (Exception ex)
                {
                    lock (gate) { finished = true; rows.Add(new InventoryProbe { Kind = kind, Status = "unavailable", Reason = CaptureSession.Describe(ex) }); }
                    try { observer.Cancel(); } catch (ManagementException) { }
                }
            }
            return rows;
        }

        private static void CollectModules(Process process, InventoryDocument document)
        {
            try
            {
                process.Refresh();
                int inspected = 0;
                foreach (ProcessModule module in process.Modules)
                {
                    using (module)
                    {
                        if (++inspected > 4096) { document.ModulesStatus = "bounded: module enumeration stopped after 4096 entries"; break; }
                        string name = module.ModuleName;
                        if (!string.Equals(name, "d3d11.dll", StringComparison.OrdinalIgnoreCase) && !string.Equals(name, "dxgi.dll", StringComparison.OrdinalIgnoreCase)) continue;
                        var row = new ModuleInventory { Name = name, Status = "loaded" };
                        document.Modules.Add(row);
                        try
                        {
                            row.Path = module.FileName;
                            var version = FileVersionInfo.GetVersionInfo(row.Path);
                            row.FileVersion = version.FileVersion;
                            row.ProductVersion = version.ProductVersion;
                            var before = new FileInfo(row.Path);
                            long size = before.Length;
                            DateTime modified = before.LastWriteTimeUtc;
                            row.SizeBytes = size;
                            if (size > 256L * 1024 * 1024) { row.Status = "hash_excluded"; row.Reason = "Module exceeds 256 MiB hashing limit."; continue; }
                            using (var stream = new FileStream(row.Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                            using (var hash = SHA256.Create())
                            {
                                // Limit actual read bytes as well as initial metadata.
                                byte[] buffer = new byte[65536];
                                long read = 0;
                                int got;
                                while ((got = stream.Read(buffer, 0, buffer.Length)) > 0)
                                {
                                    read += got;
                                    if (read > 256L * 1024 * 1024) throw new IOException("Module grew beyond hashing limit.");
                                    hash.TransformBlock(buffer, 0, got, buffer, 0);
                                }
                                hash.TransformFinalBlock(new byte[0], 0, 0);
                                var after = new FileInfo(row.Path);
                                if (read != size || after.Length != size || after.LastWriteTimeUtc != modified) throw new IOException("Module file changed while hashing.");
                                row.Sha256 = BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                            }
                        }
                        catch (Exception ex) { row.Status = "partial_or_unavailable"; row.Reason = CaptureSession.Describe(ex); }
                    }
                }
                if (document.ModulesStatus == null) document.ModulesStatus = "ok";
                foreach (string expected in new[] { "d3d11.dll", "dxgi.dll" })
                    if (!document.Modules.Exists(x => string.Equals(x.Name, expected, StringComparison.OrdinalIgnoreCase)))
                        document.Modules.Add(new ModuleInventory { Name = expected, Status = document.ModulesStatus == "ok" ? "not_loaded_at_capture_start" : "unknown", Reason = "Absence is only established when enumeration succeeds; modules loaded later are not observed." });
            }
            catch (Exception ex)
            {
                document.ModulesStatus = "unavailable: " + CaptureSession.Describe(ex);
                foreach (string expected in new[] { "d3d11.dll", "dxgi.dll" })
                    if (!document.Modules.Exists(x => string.Equals(x.Name, expected, StringComparison.OrdinalIgnoreCase)))
                        document.Modules.Add(new ModuleInventory { Name = expected, Status = "unknown", Reason = document.ModulesStatus });
            }
        }
    }
}
