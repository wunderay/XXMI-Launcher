# Full-plan implementation and qualification status

The source implementation, independent safety review and automated Windows
validation are complete for this development candidate. The full plan remains
**release-unqualified** until real-game performance/visual evidence and authorized
publisher signing are complete. No game was launched, no UAC was triggered,
and no DLL was installed or deployed.

## Exact candidate

- Native commit: `626775cad8a2f3a2e1dc1056c437ffa60bd78899`.
- Native tree: `a7320f0a76ee0be0f3153d731d81532ed49d418f`.
- Native base: `a7847eae82aaac32175c9a7d199673d5c9323bcd`.
- DLL SHA-256: `26ae27a7019ac6aec64c7560071e8e4d8d97738c2794c29398519353235b8fbe`.
- Development ZIP SHA-256: `0f831c0b84475acfb6cf791dd98514ed802d29efc975dce1f36eb8f89292bc93`.
- [Windows verification run](https://github.com/wunderay/XXMI-Launcher/actions/runs/40146131131).
- [Archive, build record, hashes and native Git bundle](../../development-builds/prepared-loading/626775cad8a2/).

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

- All **71 Windows launcher tests passed**, including actual DPAPI, protected
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
- The downloaded artifact matched every one of **2,152 tracked native source
  files**, plus the generated version header. DLL, patch, source archive,
  commit/tree and workflow identities matched. The native Git bundle verifies
  against the pinned public base.

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
