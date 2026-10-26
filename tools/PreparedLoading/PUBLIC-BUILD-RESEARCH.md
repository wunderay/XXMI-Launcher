# Public launcher build/release research

Checked on 2026-10-25 after the user supplied the upstream repository URL.
This research did not build, install, sign or publish anything.

## Upstream findings

The inspected upstream `main` is
[`816c2aadd3e2629331dd1ab8888ba59819105373`](https://github.com/SpectrumQT/XXMI-Launcher/tree/816c2aadd3e2629331dd1ab8888ba59819105373).
Its tree, the 592 commits reachable from it and published version tags did not
reveal an MSI authoring project or launcher build workflow. The public Actions
API lists one dependency-graph workflow and four dependency-graph runs, rather
than an MSI release workflow. This does not rule out a local or private build
process.

The official [v2.4.9 release](https://github.com/SpectrumQT/XXMI-Launcher/releases/tag/v2.4.9)
provides `XXMI-Launcher-Installer-Online-v2.4.9.msi`. Its SHA-256 is
`8e64015bb7b4ecbb08cbd22c0da9d64e66908bd4b77bd21b948a479015f7aeeb`.
The downloaded file matched that published digest. Static byte inspection found
`AI_PACKAGING_TOOLAdvanced Installer 22.5` and `EnhancedUI.exe` inside it.
The installer was not executed. This identifies the packaging tool, not its
original `.aip` authoring project or exact build command.

The old [XXMI-Installer](https://github.com/SpectrumQT/XXMI-Installer/blob/main/README.md)
repository is deprecated and points users to the launcher MSI releases. It is
not evidence of the maintained MSI authoring project.

## Public MSI build reference

[GPMI](https://github.com/tianyiyun128/GPMI) is an independent GPLv3 project built
on the XXMI Launcher shell. At inspected commit
`b470e98622041c4723ffa8cb236c53e850a6657f`, it publishes a real MSI build recipe:

- [GitHub Actions workflow](https://github.com/tianyiyun128/GPMI/blob/b470e98622041c4723ffa8cb236c53e850a6657f/.github/workflows/package-msi.yml)
- [Build/staging command script](https://github.com/tianyiyun128/GPMI/blob/b470e98622041c4723ffa8cb236c53e850a6657f/package-gpmi-msi.cmd)
- [WiX MSI authoring generator](https://github.com/tianyiyun128/GPMI/blob/b470e98622041c4723ffa8cb236c53e850a6657f/scripts/package_msi.py)
- [Published MSI example](https://github.com/tianyiyun128/GPMI/releases/tag/v1.0.2)

The recipe regenerates embedded resources, compiles with Nuitka, stages
`Resources/Bin`, generates WiX authoring and builds an MSI. Its workflow uses
WiX 7 with explicit license-acceptance handling and can publish a release.
Those actions were inspected, not run. This provides a concrete reference for
recreating a fork's MSI build; missing upstream build files are not an absolute
technical barrier to assembling an MSI.

It is not a drop-in replacement for the existing XXMI installed channel:

- GPMI uses per-user installation and `HKCU\Software\GPMI`.
- Microsoft's [XXMI winget manifest](https://github.com/microsoft/winget-pkgs/blob/master/manifests/s/SpectrumQT/XXMILauncher/2.4.9/SpectrumQT.XXMILauncher.installer.yaml)
  records the official MSI as machine-scoped and self-elevating. It records
  UpgradeCode `{5EBB1A78-20AD-47CF-95E8-78BCEDBB4D93}` and v2.4.9 ProductCode
  `{22267CF2-53FE-4ECD-BC33-EBCC8130D217}`.
- The [upstream updater contract](https://github.com/SpectrumQT/XXMI-Launcher/blob/816c2aadd3e2629331dd1ab8888ba59819105373/src/xxmi_launcher/core/packages/launcher_package.py)
  detects `HKLM\SOFTWARE\SpectrumQT\XXMI Launcher` / `Path`, supplies
  `APPDIR` and `CREATE_SHORTCUTS` to Windows Installer, and waits for
  `EnhancedUI.exe` to show a window.

Adapting the public reference requires deliberately preserving or migrating
those installation/update contracts, version/upgrade identity and user data.
Copying GPMI's identifiers, updater/security code or uninstall cleanup would not
establish compatibility. No such replacement MSI authoring has been implemented
or validated by this research.

## Two different signing mechanisms

In [issue #381](https://github.com/SpectrumQT/XXMI-Launcher/issues/381#issuecomment-6223677875),
SpectrumQT discusses the cost/difficulty of Windows code signing after Windows
Code Integrity blocked a file in an official MSI. That concerns Authenticode and
Windows publisher trust.

XXMI's updater separately verifies project ECDSA package signatures. The
[release notes](https://github.com/SpectrumQT/XXMI-Launcher/releases/tag/v2.4.9)
publish `Signature` and `Signature Extra`; the [verification code](https://github.com/SpectrumQT/XXMI-Launcher/blob/816c2aadd3e2629331dd1ab8888ba59819105373/src/xxmi_launcher/core/utils/security.py)
checks project ECDSA signatures against the existing publisher key. A locally built MSI can be Authenticode-unsigned, like
the existing published binaries, while the normal updater still requires its
project package signature. A public build recipe supplies neither the existing
publisher's private key nor authority to publish an accepted upstream update.

## Result

The exact upstream Advanced Installer project was not found publicly. A working
public Nuitka/WiX MSI reference was found, so a reproducible fork build can be
developed without waiting for that project. Official updater delivery still
requires the authorized publisher release/signing process; real-game
qualification remains separate. The normal source integration and its already
recorded automated verification are unchanged by this research.
