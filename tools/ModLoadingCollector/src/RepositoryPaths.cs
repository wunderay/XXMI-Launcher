using System;
using System.IO;

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

    private static bool IsLauncherRepository(string directory)
    {
        return File.Exists(Path.Combine(directory, "src", "xxmi_launcher", "__init__.py"))
            && File.Exists(Path.Combine(directory, "Themes", "Default", "window-icon.ico"));
    }
}
