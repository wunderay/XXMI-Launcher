using System;
using System.IO;
using System.Text;

internal static class RepositoryPaths
{
    internal static string FindCaptureDirectory(params string[] startingDirectories)
    {
        if (startingDirectories == null)
            return null;

        foreach (string startingDirectory in startingDirectories)
        {
            if (string.IsNullOrWhiteSpace(startingDirectory))
                continue;

            try
            {
                string fullPath = Path.GetFullPath(startingDirectory);
                DirectoryInfo directory = new DirectoryInfo(fullPath);

                while (directory != null)
                {
                    if (IsLauncherRepository(directory.FullName))
                        return Path.Combine(directory.FullName, "captures");

                    directory = directory.Parent;
                }
            }
            catch (ArgumentException)
            {
                // Ignore malformed candidates and continue with the next one.
            }
            catch (IOException)
            {
                // Ignore candidates that cannot be inspected.
            }
            catch (UnauthorizedAccessException)
            {
                // Ignore candidates that cannot be inspected.
            }
            catch (NotSupportedException)
            {
                // Ignore candidates that cannot be represented as paths.
            }
        }

        return null;
    }

    internal static void EnsureCaptureDirectoryIgnored(string directory)
    {
        if (string.IsNullOrWhiteSpace(directory))
            return;

        string fullPath;
        try
        {
            fullPath = Path.GetFullPath(directory);
        }
        catch (ArgumentException)
        {
            return;
        }
        catch (NotSupportedException)
        {
            return;
        }

        DirectoryInfo captures = new DirectoryInfo(fullPath);
        DirectoryInfo repository = captures.Parent;
        if (!string.Equals(captures.Name, "captures", StringComparison.OrdinalIgnoreCase)
            || repository == null
            || !IsLauncherRepository(repository.FullName))
            return;

        Directory.CreateDirectory(captures.FullName);
        string ignorePath = Path.Combine(captures.FullName, ".gitignore");
        if (File.Exists(ignorePath))
            return;

        FileStream stream;
        try
        {
            stream = new FileStream(ignorePath, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        }
        catch (IOException)
        {
            if (File.Exists(ignorePath))
                return;
            throw;
        }

        try
        {
            using (stream)
            using (StreamWriter writer = new StreamWriter(stream, new UTF8Encoding(false)))
                writer.Write("*\n");
        }
        catch
        {
            try
            {
                File.Delete(ignorePath);
            }
            catch
            {
                // Preserve the original write or disposal failure.
            }
            throw;
        }
    }

    private static bool IsLauncherRepository(string directory)
    {
        return File.Exists(Path.Combine(directory, "src", "xxmi_launcher", "__init__.py"))
            && File.Exists(Path.Combine(directory, "Themes", "Default", "window-icon.ico"));
    }
}
