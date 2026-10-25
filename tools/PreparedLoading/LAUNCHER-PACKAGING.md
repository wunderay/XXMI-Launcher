The Windows build stages `install-payload/` for the launcher's existing installer
and release pipeline. Its layout matches the supported installation layout:

```
install-payload/
    Resources/Bin/XXMI Launcher.exe
    Resources/Bin/_internal/
    Themes/
    Locale/
    LICENSE
    LAUNCHER-BUILD.json
```

The executable uses the launcher's original frozen root resolution. Its root is
three directories above the executable, so existing configuration, Themes,
Locale, logs and updater paths resolve from the installation directory. There is
no alternate top-level executable or custom root override.

This public repository does not contain the maintained MSI authoring and signing
workflow. `install-payload/` is a release-owner handoff for that pipeline; this
helper does not create an MSI, sign or publish a release, or replace an installed
launcher. The deprecated `XXMI-Installer` is not used. CI artifact download may
wrap this directory for transport; that is not a new public user ZIP delivery.
The existing supported portable release naming is
`XXMI-Launcher-Portable-vX.X.X.zip`, with this same installation tree. Release
owners control any supported portable packaging and version assignment.

By default this is an unsigned development payload with PE version `0.0.0.0`. The existing
`update_available()` check disables launcher self-update at `0.0.0`. No launcher
update behavior or package trust is changed. The build contains no prepared-loading
native DLL and does not establish real game compatibility or performance.
Normal application startup can access the network and write its configuration.

To rebuild, use Windows x64 with Python 3.12 x64 from the repository root:

```powershell
./tools/PreparedLoading/build-launcher.ps1 -OutputDirectory ./work/launcher-package
```

Release pipelines may supply `-Version 1.2.3` using their assigned version. The
input must have three numeric components, each between 0 and 65535, without
leading zeroes. The helper generates `build/launcher-version.txt` for the PE
resource and records that version in the payload manifest; the frozen test
checks they agree. The repository template is unchanged and is the `0.0.0`
fallback for direct debug builds using the spec. A positive version uses the
existing normal launcher update behavior. Assigning a version does not sign,
publish, or authorize a release.

The helper creates a private venv, installs the application requirements without
the unused Nuitka compiler, and uses the previously verified PyInstaller 6.20.0
plus hooks-contrib 2026.5 compiler pins. This recipe does not claim bit-for-bit
reproducible PE files. The `build/` directory retains the environment, transcript
(`launcher-build.log`), PyInstaller warnings and dependency diagnostics. It is not
part of the installation payload.

`LAUNCHER-BUILD.json` in the payload records build dependencies, source commit,
tracked/untracked source input status, requirements digest, and the SHA-256 of
each payload file except the manifest itself. Input status includes application,
assets, license, build helpers, distribution tests and CI workflow.
`LAUNCHER-PACKAGING.json` beside the payload records its path, executable path,
manifest digest and smoke-test outcome. `LAUNCHER-SELF-TEST.json` preserves the
frozen test receipt or failure traceback even for the windowed executable.

Packaging copies the exact staged tree into a temporary installation path with
spaces and runs `Resources/Bin/XXMI Launcher.exe --self-test` with a 120-second
timeout and only Windows System32 on PATH. The self-test verifies all manifest
hashes and the manifest PE version, imports application/GUI/PyAV dependencies,
loads localization, decodes an image, and creates then destroys hidden CTk/Tkhtml
widgets with the shipped theme. It never constructs `Application`; audit hooks
reject network and child processes. The tree must remain unchanged, and a
controlled result file is the only additional file permitted beside it.
This smoke test requires Windows CI and does not exercise a normal interactive
session or a game. Linux layout tests alone cannot establish Windows runtime
success.
