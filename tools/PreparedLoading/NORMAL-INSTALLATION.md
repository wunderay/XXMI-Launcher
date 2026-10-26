# Normal launcher integration and release handoff

Prepared loading is configured inside the existing launcher's Importer settings.
Users install and update XXMI libraries through the normal launcher controls;
they do not select a development ZIP, paste signatures or edit an INI file.
Unsupported or unauthenticated libraries keep preparation disabled and explain
why. The existing package publisher key and signature checks remain in force.

## Installation, update and recovery

The normal XXMI package path snapshots its authenticated prior package before
download replaces the manifest. A durable protected update record is written
before mutation. Deployment and recovery require the configured game processes
to be stopped and use protected staging handles. Interrupted operations retain
the recovery barrier until they are repaired.

The ordinary library recovery action restores the previous authenticated package
and managed preparation settings. It preserves Mods, nvapi and unrelated INI
content. A failed first installation offers Repair through the existing updater
when no previous version exists. Replayed preattempt bytes preserve original
files; they never grant publisher trust or permit loading unauthenticated code.

Legacy development pins retain their recovery path. They are not the primary
installation interface.

## Native package producer

The native repository's existing `zip-release.yml` tag workflow now generates
`PreparationCapabilities.json` from the actual built `d3d11.dll` and includes it
beside the root DLLs in `XXMI-PACKAGE-<tag>.zip`. The shared generator is
`tools/preparation-capabilities.py`; the synthetic native build uses it too.
Metadata remains explicitly at `release_stage: development` while actual-game
qualification is incomplete.

The authorized native release owner must use the existing private release
process to sign the exact capability-file bytes, add its signature to
`Manifest.json`, sign the required DLLs, and authenticate the final outer ZIP
through the existing release signature. The public workflow does not possess
that signing authority. Adding an unsigned marker does not enable the feature.
The metadata must bind the exact deployed DLL hash.

## Launcher installation payload

`build-launcher.ps1 -OutputDirectory <task-folder> [-Version X.Y.Z]` produces
`install-payload/` with the established layout:

```text
Resources/Bin/XXMI Launcher.exe
Resources/Bin/_internal/...
Themes/...
Locale/...
LICENSE
LAUNCHER-BUILD.json
```

The application uses its original frozen root selection. There is no custom
top-level launcher-root override and no additional user ZIP produced by this
helper. This is an isolated Windows test/release-owner payload, not a replacement
MSI installer or a published release. The maintained release build may continue
using its established compiler and MSI authoring project.

The release owner supplies the actual product version. The default `0.0.0`
preserves the existing development self-update guard. Nonzero versions follow
the existing installed-launcher version/update behavior. Generated PE resources
are kept under the task build directory, without changing source files.

The staging smoke test copies the exact payload to a separate path containing
spaces and runs its executable with only Windows System32 on PATH. It verifies
the PE version, every packaged file, bundled native dependencies, localization,
images and hidden CTk/Tkhtml widgets. It does not start Application, access the
network, invoke Windows Installer, launch a game or request elevation. Additional
Windows tests construct the actual Importer settings and normal update buttons.

## Remaining release prerequisites

The documented supported installed channel is the regular launcher MSI. The
public launcher source does not contain its maintained MSI authoring/build
project, and `SpectrumQT/XXMI-Installer` is deprecated. The existing normal
updater already selects the MSI asset for an MSI installation and authenticates
downloads before installation.

The [original MSI project](../Installer/README.md) builds a development installer
from the standard payload with XXMI's existing scope, registry and upgrade
identity. It does not use GPMI packaging code or the unpublished upstream `.aip`.
The workflow compiles with default ICE validation, inspects the real MSI without
installing it, and tests install/upgrade/repair/uninstall using isolated inert
fixtures. [Windows qualification passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40268228471)
for producer `1202392c4aea7a682da81e63b8e41b6c98fbcc07`; the
[single development MSI and receipts](../../development-builds/installer/1202392c4aea/README.md)
are available. The real MSI has not been installed by this work.

Official delivery still requires the release owner to build a compatible MSI,
include the normal launcher payload and publish the authenticated native package
described above. Windows Authenticode and project updater ECDSA signatures are
separate; the original build project supplies neither the existing publisher
private key nor authority to publish an accepted upstream update.

Real-game performance, complete first-eligible-frame appearance, recovery
observations and independent acceptance remain separate qualification gates.
The user launches Zenless manually. Automated fixture results do not establish
that those gates have passed. No real MSI deployment, game launch, UAC, signing,
release publication or merge is performed by this work.
