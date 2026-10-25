The Windows workflow builds `XXMI-Launcher-development-win-x64.zip`. Download the
artifact and extract the complete archive into a writable folder. Open the
`XXMI Launcher` folder and double-click **XXMI Launcher.exe**. Keep `_internal`,
`Themes`, and `Locale` beside it. Running the extracted launcher requires neither
Python nor PowerShell nor developer tools. Do not run the executable inside the ZIP.

This is an unsigned development launcher. Windows may display its usual publisher
warning. It does not include a prepared-loading native DLL or authorize one. The
existing signed capability and qualification gates still control prepared-loading
features; unsigned native development controls intentionally remain unavailable.
Packaging does not establish real game compatibility or performance.

The executable has development version `0.0.0.0`. The launcher's existing
`update_available()` check disables launcher self-update for version `0.0.0`,
preserving this development code. This does not alter native package signatures,
capability checks, or ordinary importer package behavior. Normal launcher startup
can access the network and write its configuration, just as source startup does.

To rebuild, use Windows x64 with Python 3.12 x64 and run from the repository root:

```powershell
./tools/PreparedLoading/build-launcher.ps1 -OutputDirectory ./work/launcher-package
```

The helper creates a private venv, installs the application requirements without
the unused Nuitka compiler, and pins PyInstaller 6.20.0 plus hooks-contrib 2026.5.
PyInstaller was chosen for this development artifact because the application
already supports it and a directory bundle avoids a full Nuitka C compilation and
single-file extraction overhead. This is a repeatable build recipe, not a claim
that PE files or ZIP timestamps are bit-for-bit reproducible.

`LAUNCHER-BUILD.json` inside the bundle records installed build dependencies, source
commit/local-change status, requirements digest, and packaged file hashes.
`LAUNCHER-PACKAGING.json` beside the ZIP records its SHA-256 and smoke-test outcome.
`LAUNCHER-SELF-TEST.json` preserves the frozen test receipt or failure traceback,
including when the windowed executable has no standard error stream.
The build directory contains a private environment and is not a distributable.

Before reporting success, packaging extracts the exact ZIP into a new temporary
path containing spaces and runs its executable with `--self-test`, a 120-second
timeout, and only Windows System32 on PATH. The test verifies bundled files and
development PE version, imports the real application/GUI and PyAV dependencies,
loads localization, decodes an image, and creates then destroys hidden CTk/Tkhtml
widgets with the shipped theme. It never constructs `Application`; audit hooks
reject network connections and child processes. The extracted bundle must remain
unchanged. This test does not exercise a normal interactive session or a game.
The packaging helper supplies a controlled result path outside the extracted
bundle; this is the only additional file permitted in the smoke directory.
