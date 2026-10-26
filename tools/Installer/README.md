# Original XXMI Launcher installer

This project authors its own WiX 3 MSI from the standard launcher payload. It
does not use GPMI packaging code or the unpublished Advanced Installer project.
The output is one development MSI, not a portable launcher ZIP. Windows signing
and the existing XXMI updater package signatures remain separate release steps.

On Windows with Python 3.12 and WiX 3.14 already available:

```powershell
./tools/PreparedLoading/build-launcher.ps1 -OutputDirectory ./work/launcher-package -Version 2.4.10
./tools/Installer/build-msi.ps1 -Payload ./work/launcher-package/install-payload -Version 2.4.10 -OutputDirectory ./work/installer -Python ./work/launcher-package/build/venv/Scripts/python.exe
```

The builder validates the clean source, exact payload hashes and numeric version,
generates original authoring, compiles with default ICE validation, then inspects
the MSI database and embedded cabinet without installing or running the payload.
Build, authoring and validation receipts accompany the MSI and its SHA-256.
No build tool is downloaded by the MSI helper.

The installer preserves XXMI's UpgradeCode, machine registration and writable
application layout. It honors `APPDIR` and `CREATE_SHORTCUTS`, uses the registered
installation directory on upgrade, and removes only installer-owned files and
empty directories. Configuration, Mods and unrelated files are retained. It
does not launch the launcher or a game after installation.

The `Original launcher MSI` workflow builds the real MSI but never installs it.
Separate inert fixtures use unique product/registry identities to test install,
upgrade, repair and uninstall on an already privileged disposable Windows runner.
These tests cannot request elevation and are disabled for ordinary local runs.
The updater tests cover standard MSI windows, legacy installer descendants, fast
completion, failure and numerical version ordering.

Development builds are unsigned and identify XXMI Launcher contributors as the
manufacturer. They are not an official SpectrumQT release, and do not bypass
publisher verification for native library updates. Real-game visual/performance
qualification is independent of installer qualification.
