# Original XXMI Launcher development MSI

[Download the single installer](XXMI-Launcher-Installer-Online-v2.4.10.msi).
Run it on Windows x64, then open the installed **XXMI Launcher** shortcut.
No portable ZIP, build tools or separate capture program are needed.

This is an unsigned contributor development build, version **2.4.10**, from
source `1202392c4aea7a682da81e63b8e41b6c98fbcc07`. It uses original WiX authoring
and the normal XXMI installation layout, registry and upgrade identity. It does
not launch the launcher or a game after installation. Native prepared-loading
support still requires authenticated packages from the existing publisher.

MSI size: **54,475,945 bytes**. SHA-256:

```text
d6076f7200521a661b6f2d34d1759ab86a953dc7a2fb0e61343249d27e558781
```

[Installer CI passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40268228471):
41 tests passed, with two platform-specific skips. Inert fixtures exercised
install, upgrade, repair and uninstall, retaining configuration, Mods, security
files and custom theme/locale files. The real MSI passed default ICE validation
and read-only database/cabinet inspection of all **1,801 file hashes**. Its staged
EXE passed the offline import, hidden GUI, assets and installation-layout smoke.

Default ICE completed with warnings: ICE39 for redundant explicit file compression,
ICE91 for the retained machine registration/per-user application directory design,
ICE60 for five files without language metadata, and ICE61 for intentional
same-version upgrades. No validation checks were suppressed. The CI logs retain
these diagnostics; validation is not claimed to be warning-free.

[Normal Windows checks passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40267680474):
101 lifecycle/qualification tests, nine GUI/distribution tests and all native
suites. That run uses runtime source `9cc5505f3777050b75797474448e2ba153876a48`;
subsequent changes affect installer validation/tests only.

The downloaded artifact archive, MSI and receipt hashes were checked again.
See [verification.json](verification.json), the build/validation receipts and
[SHA256SUMS](SHA256SUMS). Windows checkout CRLF bytes explain the authoring-source
hash recorded in the build receipt; those bytes were independently matched.

The real MSI has not been installed by this work. Fixtures do not establish
upgrade behavior from every official installer version, actual-game visual or
performance qualification, or official publisher release acceptance. No game
launch, UAC, active DLL deployment, signing or merge was performed. Automatic
updates retain the existing publisher channel and require a numerically newer
version; this development build does not create a separate update channel.
