# Implementation and normal release status

## First-visit coverage update, 2026-10-27

The user authorized longer startup preparation while retaining current memory
limits. The current preset allows up to 120 seconds, stopping early when idle.
Retained texture keys now have an independent 4,096-entry bound; queue depth stays
32. DDS/BUF/IB reservations use the verified handle size on a worker before
allocation, keeping the 3 GiB CPU and 2 GiB GPU caps and all source fences.

Standalone WARP now prepares/reuses 256 of 256 tiny textures within the same
64 MiB test budget; the prior 128-key ceiling left half unprepared. CPU tests fill
a 16-byte cache with four four-byte pinned sources and reject the fifth without
eviction. Production-path reuse, mutation isolation, descriptor rejection and zero
speculative uploads over 100 later Presents still pass. Both 92-unit builds,
native/GPU fixtures, collector checks and the updated launcher's UI tests pass.

No game or UAC was invoked. No-perceptible-loading and first-visit/revisit parity
remain NOT PROVEN in Zenless. Unsupported and over-budget assets still fall back
to on-demand loading. Original texture quality is unchanged. The earlier sections
below describe superseded candidates; the withheld coverage expansion was
subsequently authorized and shipped in this update.

## GPU retention follow-up, 2026-10-27

Startup uploads now retain qualified objects immediately after cache insertion,
before the next allocation can evict them. Completion callbacks are bounded by
the existing queue and checked against the live source/config/device generation.
Texture alias metadata retains its previous queue-size-times-four bound: the
coverage expansion was withheld because it can add startup work. CPU/GPU budgets,
texture quality and post-startup upload suppression remain unchanged. The added oversized-profile fixture exercises the actual
runtime, while the standalone probe covers texture capacity and cancellation.
Game-level speedup and no-regression qualification across startup, first use,
F10 and frame pacing still require a user-run capture.
The existing status writer also publishes allocation-free first-use hit/miss
counters once per second, retaining session history across source invalidation.
These cover file-backed resources, not shader bundles or displayed frame timing.

## GPU startup preparation and completed redesign, 2026-10-27

GPU preparation now defaults on. The native DLL prepares private objects in the
actual rendering device during a bounded startup pass, then stops speculative
uploads during ordinary Presents. CPU/GPU caps are 3/2 GiB with DXGI headroom and
a cooperative 30-second deadline. Original texture quality is preserved.
The production-path WARP fixture proves distinct same-file objects, exact first-use
reuse with file reads blocked, mutation isolation, descriptor mismatch fallback,
and zero additional speculative creations over 100 later Presents. Both final
92-unit builds, native fixtures, nine collector tests and entry/UI checks passed.
The delegated ZZZ redesign is complete, including first-open layout and 15 zoom/
reflow cases down to 320 pixels. No game or UAC was invoked. Zenless stutter
reduction remains NOT PROVEN. Current release README and receipts give details.
All sections below describe historical candidates.

## Direct Quick Start and bounded model preloading, 2026-10-27

The existing root EXE now starts stock XXMI with --nogui --xxmi ZZMI from its
own Start button. The extra launcher window/click is removed. Default-on model
file-cache warming uses four readers before game launch, bounded by 4 GiB,
8 GiB available RAM headroom and a cooperative 30-second deadline. Background
CPU preparation is off while preloading. No textures or GPU resources are altered.
Final entry/configuration/preload/UI-cancellation tests and seven collector tests
passed without starting stock XXMI, Zenless or elevation. Read-only actual-file
measurements are preserved in the ZIP; differing cache state/work prevents a
speedup claim. In-game stutter improvement remains NOT PROVEN. Current instructions
and exact identities are in development-builds/prepared-loading/README.md and
LOCAL-LAUNCHER.json. Earlier sections are historical.

## Quality-preserving shader reuse and completed comparison, 2026-10-27

The completed previous-candidate captures are complete/lossless but establish no
overall speedup: F10 3.823 -> 3.979 s and CPU Present p95 38.19 -> 40.89 ms.
Different texture-load counts limit aggregate comparisons. GAME-RESULTS.json in
development-builds/prepared-loading retains the findings and report identities.

The new candidate connects verified shader reuse to ordinary startup/F10, records
DDS read and device-creation calls separately, and counts shader-cache hits.
Textures retain original quality. The redesigned selector defaults to Standard
(shader reuse on, CPU prefetch off), labels CPU preparation experimental, and makes
the automatic 180-second collector optional. Older captures are preserved.

All native fixtures and six collector tests passed. Eight synthetic warm shader
lookups took 30.788 ms versus 123.979 ms for compilation, with exact bytecode and
include/macro invalidation checks. WARP DDS pixel readback matched. The current
entry passed unelevated validation; no game or stock launcher was opened and no
UAC requested. New candidate game compatibility and total speedup remain NOT PROVEN.
Source: c442d746282339651a4ecb4b2a111f43daadc30b + native-speed.patch +
native-loading.patch. See the current development README/BUILD-INFO for exact
hashes and instructions. Earlier sections below are historical.


## Bounded CPU preparation and automatic comparison, 2026-10-26

This update supersedes the off-only mitigation described below. The existing
root EXE now offers Baseline (preparation off) and Optimized (bounded CPU cache),
uses the same new DLL in both, and starts a passive observer before opening XXMI.
The user presses Start. Native tracing stops after 180 seconds and the observer
collects loaded-DLL identity, native timings and CPU Present intervals into
captures/loading-speed. Five collector tests and entry validation passed.

Native source is c442d746282339651a4ecb4b2a111f43daadc30b plus native-speed.patch;
see development-builds/prepared-loading/BUILD-INFO.json for exact source and DLL
hashes. Fixes cover unrelated sibling deletion invalidation, speculative cache
churn, retired generations, faster verified snapshots, CPU-only setup work and
timed tracing. All 92 translation units were rebuilt for production and fixture.
Four capability tests and 21 native fixture targets passed; tests do not prove
game compatibility or speed. The equal-work real-buffer benchmark measured
32.47% less CPU snapshot-processing time; startup/F10/game speedup remains
NOT PROVEN. The user agreed to perform the two matching runs.

The agent did not launch Zenless, open the launcher GUI or request elevation.
The normal installation remains unchanged. Existing profile and development ZIP
were updated in place, with no extra mod-library copy. Current instructions are
in development-builds/prepared-loading/README.md. Everything below is historical
context and does not describe the current off-only/current-source state.

Prepared loading is integrated into the existing launcher's ordinary library
update, settings and recovery paths. PR #2 is ready for code review, not a draft.
The native implementation and automated Windows checks pass for the exact
candidate below. **The development MSI download is withdrawn after a reported
Defender `Trojan:Script/Wacatac.B!ml` detection.** See the
[antivirus investigation](../../development-builds/installer/1202392c4aea/SECURITY-STATUS.md).
Prior functional/integrity checks did not establish antivirus clearance.

## Real-game regression report and local mitigation, 2026-10-26

The user reports no speedup and slower startup, first-use loading and F10 reload.
The final runtime snapshot from PID 178408 reports 2,313 assets, zero CPU/GPU-ready
assets and a generic compatibility reason. It is a final snapshot, not a session
history; the exact invalidation cause is unresolved. Source review found that
the active prefetch loop cycles the file list without adaptive priorities, while
the preset disables the general fast configuration path and permits GPU preparation
throughout gameplay. These are unnecessary work and cache-thrashing risks, not a
measured allocation of the user's slowdown.

The runnable local profile and setup now keep experimental preparation off;
Direct launch and the approved elevation-on-open behavior are preserved. The
entry was rebuilt and passed validation-only self-tests without launching the
game or requesting UAC. Acceleration remains incomplete and unqualified. See
`development-builds/prepared-loading/LOCAL-LAUNCHER.json` for retained evidence.
The development DLL archive below is unchanged and must not be read as a proven
performance improvement for this user's mods.

## Local runnable diagnostic launcher, 2026-10-26

`XXMI Prepared Loading.exe` now opens the existing user-approved diagnostic copy.
Its existing unsafe-mode consent is retained. The new native DLL is pinned by
SHA-256, the INI is configured, and the launcher uses Direct game launch.
At the user's request, pressing Start now runs XXMI's usual injection/game launch
sequence; opening the entry application alone still only opens the launcher. No new
publisher signature or normal-launcher authentication exception was introduced.
The normal installation's config, INI and DLL hashes matched before/after setup
and after a GUI smoke test. Eleven changed files in the existing copied mod set
were refreshed (under 1 MiB); no additional full mod-library copy was made.

The entry's validation tests reject Manual mode, native automatic-launch
commands, native auto-elevation settings and duplicate preparation settings.
With explicit user approval, the entry manifest now requests administrator access
when the user opens it; the stock launcher inherits that token. The actual GUI
reached Ready and closed before this manifest change. The final EXE passed
validation-only self-tests unelevated, and its embedded manifest was inspected
as data. Elevated GUI startup remains untested. Start was not pressed, the game
was not launched and no UAC was requested by the agent. Actual injection,
rendering and game performance remain unqualified. The previous statement that
nothing was deployed now applies to the normal installation; the approved isolated
copy has been updated. Receipt: `development-builds/prepared-loading/LOCAL-LAUNCHER.json`.

## Local latency fixes and development build, 2026-10-26

Current native source: `c442d746282339651a4ecb4b2a111f43daadc30b`;
tree `30e935aa00088be3f8493c143f8c6fa70ec59e6e`.
The earlier review below describes the previous candidate. This revision fixes:

- Canonical long filenames and directories are eligible even when Windows assigns
  8.3 aliases. Existing notifications expand to long names; unresolved removed or
  renamed entries force full reconciliation. Pending OS notifications reject
  stale cache hits. Reparse points, hard links and short input spellings retain
  the conservative fallback.
- The ordinary texture cache-hit path consumes supported worker-decoded pixels.
  Unsupported flags, formats or resizing requirements retain the exact WIC path.
  This creates a private texture; it does not share mutable GPU objects.
- One indivisible resource may exceed the per-pump upload byte target during an
  explicitly enabled loading window. GPU capacity and process-memory admission
  remain enforced; a second resource waits for another pump. A D3D creation call
  cannot be interrupted by the millisecond target, so this is not a hard frame-time
  guarantee and does not enable loading windows automatically.
- Rejected/oversized sources are remembered for the current source generation,
  with a 4,096-entry bound, instead of repeatedly reading them as the queue cycles.
  File notifications and periodic reconciliation permit fresh attempts.

Validation: all 21 standalone/native fixture targets completed (the trace worker
and host form one integration check), plus four native capability CLI cases.
The actual DLL test covered parser/linker, worker publication, WARP texture pixel
readback, first use with source reads denied, and last-good recovery. The prepared
buffer first-use sample was **0.001100 ms**, with zero new resource creations.
This is one synthetic sample, not game performance, a percentile or a cold-load
measurement. The CPU-only decoded path still needs GPU texture creation.

No game or installer was launched, no UAC was requested, and no installed DLL or
security setting was changed. The resulting package is unsigned development
material; normal authenticated installation remains unavailable without publisher
signing. Large mod sets still exceed finite RAM/VRAM budgets, and unsupported
command/resource combinations retain compatible loading. Instant loading for all
mods and actual-game compatibility remain **NOT PROVEN**.

`build-native-direct.py` reproduces the direct MSVC build with an existing compiler
and SDK, without installing tools or deploying files. `native.patch` is stored as
exact bytes so Windows line-ending conversion cannot invalidate its SHA-256.
The local build and test receipt is in `integration-evidence/verification.json`.

Cleanup remains blocked: automatic approval review rejected removal of the owned
`.preparation-data` scratch directory with only "blocked by policy". Approximately
546.1 MiB of compiler, source and fixture intermediates remain together there.
The final package and source bundle were verified before cleanup was attempted.

## Local code and installation review, 2026-10-26

Review scope: launcher branch `codex/installer-antivirus-fix` at `6203b1d`,
native patch SHA-256 `f6dcc8255fdf3e4c198dc1e519f583d52e248306e73c9163a3fb87be4fdefaff`.
No game or installer was launched, and no native candidate was deployed.

The code implements real cache hits and precreation: `CustomResource::Substantiate`
can return a prepared GPU resource; buffer and texture loaders can consume cached
source bytes; workers can decode supported images and restore decoded disk entries.
That establishes a plausible mechanism, not a measured game speedup.

The local installation still has stock `d3d11.dll` version `1.2.2.0`, without a
preparation-capability file beside it. Launcher defaults leave preparation and
individual features off. The MSI alone does not deliver an authenticated new
native package, so installing it alone cannot activate these optimizations.

Concrete performance limitations found in the native code:

- **The installed mod paths cannot use the prepared snapshot cache.**
  `SnapshotIO.cpp::WatchablePath` rejects any distinct short-name spelling,
  including ancestor aliases (`native.patch`, lines 15885-15897). Read-only
  `GetShortPathNameW` inventory found aliases for all 2,138 buffer/image files
  under the local Mods directory. This is a filesystem inventory, not a count of
  active INI references. `PreparationScheduler::Worker` discards unwatchable
  snapshots (lines 10819-10831), so these files would retain legacy loading.
  Supporting file-identity-aware notification invalidation is needed; simply
  removing the guard would permit stale resource reuse.
- **CPU-only preparation does not eliminate texture decoding.** The worker
  decodes eligible images (lines 10801-10808), but the ordinary texture cache-hit
  path calls the encoded-memory WIC loader again (lines 645-656). Decoded pixels
  are consumed by the GPU-preparation path. When that path is disabled or misses,
  worker decoding can add work without removing the draw-time decode.
- **GPU preparation has restrictive admission and may still stall a frame.**
  The default upload budget is 16 MiB; a single resource whose estimate exceeds
  the budget is discarded from the queue rather than spread across frames
  (lines 10340-10346). GPU creation runs from the Present hook (lines 3084-3087),
  and its 2 ms default is checked between calls, not inside a driver call
  (lines 10348-10382). It needs an explicit loading window and does not establish
  a hard frame-time bound. Whole-configuration command compatibility can also
  disable resource sharing (lines 12658-12681 and 13545-13564).

The original 0.1.6 capture is present locally and its report hash still matches
the forwarded receipt. It is one baseline, not paired candidate evidence.
No actual-game improvement percentage is established. The attempted Python
prepared-loading test run stopped during import because this interpreter lacks
`cryptography`; no passing test result is claimed for this review.

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
