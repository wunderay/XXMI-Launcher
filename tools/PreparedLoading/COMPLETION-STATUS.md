# Full-plan implementation and qualification status

The source implementation, review remediation and automated Windows checks pass
for this development candidate. PR #2 is ready for code review, not a draft. The full plan remains
**release-unqualified** until real-game performance/visual evidence and authorized
publisher signing are complete. No game was launched, no UAC was triggered,
and no DLL was installed or deployed.

## Exact candidate

- Native commit: `4599c694c23c7dfe0a62e40a2878b07985cdc521`.
- Native tree: `2c35a0dc33f5be79d515ccabeea8c11862c7b79a`.
- Native base: `a7847eae82aaac32175c9a7d199673d5c9323bcd`.
- DLL SHA-256: `c9c9fbf5e3e3f0e5e2c35b3b836542c6d0303aeed240e0c6a2d8c6ace8ccc2a2`.
- Development ZIP SHA-256: `515ce80659a302b92cfafb53050d64f9b46d402f26aa3aa9f23b94bf48c29243`.
- [Native Windows verification job](https://github.com/wunderay/XXMI-Launcher/actions/runs/40180466047/job/121130237883).
- [Archive, build record, hashes and native Git bundle](../../development-builds/prepared-loading/4599c694c23c/).

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

## Completed verification

- All **77 Windows launcher tests passed**, including actual DPAPI, protected
  handle staging/recovery, Toolhelp and variable-length Unicode rename cases.
- Native CPU, configuration, memory, notification, working-set, tracing, GPU and
  status suites passed. Actual SDK shader checks cover DEBUG cache retention,
  executable/reflection equivalence, exact optimized DXBC and eight standard
  include cases. Independently compiled DEBUG PDB metadata is nondeterministic;
  only comparison copies are stripped, while cached bytes remain exact.
- The production parser and prepared-runtime DLL fixtures passed: include
  discovery, clean-build equivalence, bounded numeric expansion, full worker/GPU
  preparation, latest persistent migration, publication, first use and last-good
  retention. SINGLETHREADED WARP checks include a real buffer, foreign-call
  rejection, worker pin release and final destruction on its owner thread.
- Continuous trace DLL export passed with 100,000 events and graceful stopping.
  Test exports were checked absent from the packaged production DLL.
- Independent Runtime, metadata and launcher reviews reproduced, fixed and
  rechecked the ownership, source/retirement, expression, memory and package
  provenance defects found during implementation.
- The downloaded artifact matched every one of **2,156 tracked native source
  files**, plus the generated version header. DLL, patch, source archive,
  commit/tree and workflow identities matched. The native Git bundle verifies
  against the pinned public base.

## Astraea review remediation

- P1: restored the original 16-byte version 1 ShaderRegex cache layout with a
  separately persisted regex hash. The loader compares that field with the
  current configuration before NO_MATCH, group linking or cached bytecode use.
  The shared production validator rejects stale hashes, malformed candidate
  layouts (including hash 0/1 aliases), invalid lengths and count overflow.
  This cache path remains validated when prepared loading is off.
- P2: acceptance now requires the canonical SHA-256 of the complete evidence
  manifest excluding only its top-level review reference. This binds both full
  run/capture records, paired conditions, scenarios, recovery and release
  coverage. The reported baseline 10-to-12 ms rewrite with a fixed 12 ms candidate
  rejects the unchanged review; fresh-review controls pass.
- A separate source reviewer found no P1/P2 issues in the remediations. The
  production regex-validator test passes on Linux and MSVC; all 20 qualification
  tests pass within the 77-test Windows suite. This is source/test review,
  not independent human visual or release acceptance.

## Runnable launcher

[Download and run instructions](../../development-builds/launcher/cef37d86a1da/)
provide a complete portable launcher: extract, open the folder and double-click
`XXMI Launcher.exe`. Python, PowerShell and build tools are not runtime
requirements. Shortcuts use the executable's actual directory, supporting both
this layout and the original installed layout.

[The final Windows EXE build/smoke passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/40181635989)
at source `cef37d86a1dafe40c9c807e0d1c704833d105083`. It tests the exact
extracted EXE with developer tools removed from PATH: imports, hidden CTk/Tkhtml,
native runtimes, themes, localization, image decoding, PE version and integrity.
The downloaded ZIP SHA-256 is
`afc9156ac0b94fa0880ef306c60c1b0a30ff8946f17cbe78830a9b1e743c9637`;
all 1,801 packaged file hashes and the clean source-input record were verified.

The native and 77-test Windows jobs passed at `b3b5a7b8bea6c249ae7b63cdc6feed5fa8263434`
in run 40180466047. Later packaging-only runs reuse those unchanged native and
prepared-lifecycle/qualification inputs. The final shortcut-path change was
separately reviewed and included in the final EXE build. The final packaging-only
run explicitly skips those reused jobs; it is not a fresh execution of all tests.

The launcher is unsigned and does not include or authorize a native DLL.
Experimental prepared-loading controls retain the authorized signed-package
gate. The packaging smoke never constructs the normal Application or launches
a game; normal user startup can access the network and save configuration.

## Remaining release gates

1. Import the actual collector 0.1.6 raw report/source files. The forwarded local
   paths and hashes are a reported baseline receipt, not transferred evidence.
2. Collect paired candidate/baseline real-game scenarios, resource and memory
   evidence, preparation cost and first-correct-frame observations across the
   plan's validation matrix. The user launches Zenless manually; screenshots
   remain optional supporting evidence.
3. Obtain independent visual/timing acceptance and authorized publisher signing
   through the existing trust path. Capability metadata stays unsigned and the
   launcher gate stays closed until then. No importer/version is yet qualified.

See [the evidence protocol](qualification/README.md). The older development ZIP
in the parent directory belongs to the previous limited implementation and is
separate from this candidate. The original Phase 0 patch remains unchanged.
