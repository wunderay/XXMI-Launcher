# Implementation and normal release status

Prepared loading is integrated into the existing launcher's ordinary library
update, settings and recovery paths. PR #2 is ready for code review, not a draft.
The native implementation and automated Windows checks pass for the exact
candidate below. **The development MSI download is withdrawn after a reported
Defender `Trojan:Script/Wacatac.B!ml` detection.** See the
[antivirus investigation](../../development-builds/installer/1202392c4aea/SECURITY-STATUS.md).
Prior functional/integrity checks did not establish antivirus clearance.

**Official updater delivery still requires the authorized publisher release
and package-signing process.** An [original MSI project](../Installer/README.md)
now authors the standard launcher payload without using GPMI's packaging code.
It preserves the existing installation identity and adds standard MSI updater
window handling and numeric version comparisons. Windows compilation, read-only
payload inspection and isolated inert-fixture lifecycle tests passed. The
[development MSI evidence](../../development-builds/installer/1202392c4aea/README.md)
is retained with its download recommendation withdrawn. No normal release/deployment has occurred, and actual-game
qualification remains open. The earlier [build research](PUBLIC-BUILD-RESEARCH.md)
is historical context, not the implementation recipe.

## Current normal user flow

- Install and update XXMI libraries through the existing launcher controls.
- Configure preparation inside Importer settings when the authenticated library
  advertises support. Unsupported or unsigned support remains disabled with a
  reason; users do not select a development ZIP or paste metadata.
- Use library recovery for the previous authenticated installation, or the
  ordinary Repair action after an interrupted first install.

Normal updates snapshot the old authenticated package before download changes
its manifest. Durable barriers, fixed-root validation, protected staging and
idle-process checks cover partial writes and recovery. Rollback preserves Mods,
nvapi and unrelated INI edits. The existing publisher trust checks remain intact.

The launcher payload uses `Resources/Bin/XXMI Launcher.exe` with the original
root calculation, Themes and Locale. It supports a release-owner-supplied
version. The build helper stages a payload for maintained installer/release
integration and does not publish another standalone launcher archive. The native
tag-package producer includes capability metadata beside the DLLs; metadata
remains unsigned/development until the existing release owner signs it.

## Implemented

| Plan area | Implemented behavior |
| --- | --- |
| CPU preparation | Bounded immutable snapshots, decoding, source/decoded caches, cancellation and disk cache. |
| GPU preparation | Real-device resources, views, shader/state bundles, device epochs and process-budget admission. |
| Working sets | Exact selected IDs/dependency closure, observed usage/co-use priorities, bounded adaptive history. |
| Source changes | Notifications, current include discovery, stable verification, overflow recovery and explicit rescan. |
| Detached configuration | Production parser/linker, generation-owned registries and reader leases spanning pre/post work. |
| Publication | Complete preparation, current source/device fences, bounded persistent migration/initializers, safe retirement. |
| Incremental/cache work | Per-file IR reuse, budget-domain identity, deterministic full semantic linking and sealed shader include caches. |
| Launcher/recovery | Authenticated archive pinning, independent feature controls, protected staging, process checks and rollback. |
| Evidence | Continuous loss-aware tracing, activation durations, offline evidence gate and explicit visual/timing attestation. |

Unsupported mutable/pool/initialization/dynamic-expression cases retain the safe
previous generation or use compatibility behavior. Readiness does not establish
physical residency, rendered correctness or a hardware performance target.
New native code follows the original repository's tab indentation and readable
multiline control flow; formatting-only passes preserved C++ tokens.

## Exact candidate and completed verification

- Launcher MSI producer: `1202392c4aea7a682da81e63b8e41b6c98fbcc07`.
- Normal runtime verification source: `9cc5505f3777050b75797474448e2ba153876a48`.
- Native source: `58a87cbce73c8888c0df88cd6fa175fff2efbb16`.
- Native tree: `9da30f68f7c6b71668b04c6557d198674352dc2a`.
- Native base: `a7847eae82aaac32175c9a7d199673d5c9323bcd`.
- [Full Windows run passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40267680474).
- [Original MSI run passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40268228471):
  41 passed, two platform skips; inert install/upgrade/repair/uninstall;
  real MSI default ICE and exact embedded hashes for all 1,801 files.

The subsequent producer changes affect installer validation/tests only; launcher
runtime source is unchanged from the full Windows run. The MSI and receipts
were downloaded and their hashes checked again. The real MSI was never installed.

All three jobs passed at that source:

- **101 Windows lifecycle/qualification tests**, including real protected-file
  commits and subprocess termination/restart during single-DLL deployment and
  first-install repair. Publisher verification is explicitly mocked in the
  installation contract fixtures; separate Windows tests exercise actual DPAPI.
- **9 Windows distribution/GUI tests**: real Importer settings, validated mode
  application, unsigned-support refusal, standard Update events, repeated
  scrollable-frame cleanup, original installation layout and version resources.
- Native CPU/cache, configuration/IR/budget, notification, working-set, shader,
  GPU/WARP, ownership, status and loss-aware trace suites; production parser and
  complete worker/preparation/publication/first-use/migration/last-good fixtures;
  plus all **4 native capability-generator tests**.

The staged standard-layout EXE passed its offline hidden GUI/import/assets and
integrity smoke with developer tools removed from PATH. This does not start the
normal Application, invoke Windows Installer, launch a game or request elevation.

Downloaded artifacts were checked again: **1,800 installation payload files**,
clean source-input status and manifest digest; **2,159 tracked native files** plus
its generated version header; native DLL/patch/source/archive/commit/tree
identities. The artifact transport preserves hidden files so the downloaded
payload matches the tested tree. Native Git history verifies against the pinned
public base. The compact [verification record](integration-evidence/verification.json)
binds the source, job/artifact IDs and hashes; its source-only bundle is a
maintainer handoff, not a deployable library package.

Separate source review covered ordinary update/rollback, crash barriers,
first-install repair, subset transaction replay, package roots, staging, build
layout and Tk callback ownership. This does not authenticate a human reviewer
or establish actual-game visual/release acceptance. Earlier Astraea findings
remain fixed: genuine persisted ShaderRegex cache identity/format, and reviewer
acceptance bound to the complete paired evidence manifest.

## Remaining release gates

1. Use the authorized publisher release/package-signing process for official
   updater delivery. Resolve the reported antivirus detection before restoring
   development MSI delivery. Prior qualification covered the functional and
   integrity checks described above. The native metadata, DLLs
   and outer package must be authenticated through the existing signer before
   the launcher accepts support. No signing key or substitute trust path is
   created by this work.
2. Transfer and inspect original collector 0.1.6 baseline files. The forwarded
   paths and hashes remain a reported receipt, not transferred evidence.
3. Collect pinned paired actual-game scenarios and the plan's memory, recovery,
   performance and complete first-eligible-frame evidence; obtain independent
   human acceptance and release qualification. The user launches Zenless
   manually; screenshots remain optional supporting evidence.

Only isolated inert fixture MSI installations are authorized in disposable CI;
the real MSI is inspected without installation. No active DLL deployment, game launch, UAC, signing, release
publication or merge was performed. Prior standalone portable development
archives are retained as historical evidence and are not the current normal
release delivery. The original Phase 0 patch remains unchanged.

See [normal installation/release handoff](NORMAL-INSTALLATION.md) and
[the qualification protocol](qualification/README.md).
