using System;
using System.IO;

internal static class PathTests
{
    private static int assertions;

    private static int Main(string[] args)
    {
        if (args.Length != 1 || string.IsNullOrWhiteSpace(args[0]))
        {
            Console.Error.WriteLine("Pass a task-owned scratch directory as the only argument.");
            return 2;
        }

        string scratch = Path.GetFullPath(args[0]);
        if (Directory.Exists(scratch))
        {
            Console.Error.WriteLine("Scratch directory must not already exist: " + scratch);
            return 2;
        }

        Directory.CreateDirectory(scratch);
        try
        {
            Run(scratch);
            Console.WriteLine("Passed " + assertions + " path assertions.");
            return 0;
        }
        finally
        {
            Directory.Delete(scratch, true);
        }
    }

    private static void Run(string scratch)
    {
        string repo = Path.Combine(scratch, "repo");
        Marker(repo, "src/xxmi_launcher/__init__.py");
        Marker(repo, "Themes/Default/window-icon.ico");
        string nested = Path.Combine(repo, "tools", "collector", "bin");
        Directory.CreateDirectory(nested);
        Equal(Path.Combine(repo, "captures"), RepositoryPaths.FindCaptureDirectory(nested), "nested repository walk");
        Equal(Path.Combine(repo, "captures"), RepositoryPaths.FindCaptureDirectory(Path.Combine(repo, "missing", "child")), "nonexisting child walk");

        string first = Path.Combine(scratch, "first");
        string second = Path.Combine(scratch, "second");
        Marker(first, "src/xxmi_launcher/__init__.py");
        Marker(first, "Themes/Default/window-icon.ico");
        Marker(second, "src/xxmi_launcher/__init__.py");
        Marker(second, "Themes/Default/window-icon.ico");
        Equal(Path.Combine(first, "captures"), RepositoryPaths.FindCaptureDirectory(Path.Combine(first, "sub"), Path.Combine(second, "sub")), "candidate order");

        string missingInit = Path.Combine(scratch, "missing-init");
        Marker(missingInit, "Themes/Default/window-icon.ico");
        Equal(null, RepositoryPaths.FindCaptureDirectory(missingInit), "missing launcher marker");
        string missingIcon = Path.Combine(scratch, "missing-icon");
        Marker(missingIcon, "src/xxmi_launcher/__init__.py");
        Equal(null, RepositoryPaths.FindCaptureDirectory(missingIcon), "missing icon marker");

        string packageSource = Path.Combine(scratch, "package", "Source");
        Marker(packageSource, "Themes/Default/window-icon.ico");
        Equal(null, RepositoryPaths.FindCaptureDirectory(packageSource), "package Source is not repository");

        Equal(null, RepositoryPaths.FindCaptureDirectory(null), "null params array");
        Equal(null, RepositoryPaths.FindCaptureDirectory(new string[0]), "empty candidates");
        Equal(null, RepositoryPaths.FindCaptureDirectory(null, "", "   ", "\0"), "invalid and empty candidates");
        Equal(null, RepositoryPaths.FindCaptureDirectory(Path.Combine(scratch, "ordinary")), "ordinary directory");
    }

    private static void Marker(string root, string relativePath)
    {
        string path = Path.Combine(root, relativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, "fixture");
    }

    private static void Equal(string expected, string actual, string name)
    {
        assertions++;
        if (!string.Equals(expected, actual, StringComparison.Ordinal))
            throw new Exception(name + ": expected '" + expected + "', got '" + actual + "'.");
    }
}
