# Full-plan completion status — work in progress

The full plan remains in progress. Source reviews and Windows fixtures found
safety and test-harness defects; the remaining owner-thread cleanup fix and
prepared-runtime activation proof are being completed. No new DLL has been
deployed or signed. The earlier account-limit interruption has been resolved.

## Written and saved

- Generation-owned native registries, reader leases spanning Present pre/post,
  fresh-generation legacy reload, bounded migration and initializer replay.
- Actual detached production parser/linker and current include discovery;
  dependency-aware parsed replay and deterministic clean-build comparisons.
- General candidate worker/preparation/publication pipeline and an isolated
  test-only DLL host for production parser and prepared-runtime fixtures.
- Selected working sets, observed co-use priorities and explicit rescan APIs.
- Content-identified shader cache with verified include closure, actual compiler
  identity, bounded ownership and standard-handler compatibility fixtures.
- Continuous native tracing with capture-specific loss accounting and graceful
  stopping of in-flight scopes; offline qualification tooling.
- Authenticated development-package selection, pinning, settings and rollback UI.

## Mandatory remaining work

1. Complete owner-thread final cleanup for SINGLETHREADED legacy generations.
   Device/thread guards already prevent foreign context calls, but their GPU
   children must also be released on their recorded owner thread.
2. Pass the exact-candidate prepared-runtime DLL fixture with the same pass-through
   device/context used by production. Wrapped fixture Map/Unmap hooks invalidated
   staged runtime revisions; migration and source fences remain intact.
3. Verify the resulting source-linked archive and retain its exact hashes.
4. Obtain paired real-game candidate evidence, independent visual/timing review,
   and authorized publisher signing before release qualification. Never launch
   Zenless or trigger UAC; the user launches it manually.

Latest Windows evidence: [run 40144454108](https://github.com/wunderay/XXMI-Launcher/actions/runs/40144454108).
All 71 launcher tests passed, including real DPAPI, protected staging/recovery,
Toolhelp and Unicode rename cases. Standalone native suites passed, including
continuous trace DLL export, WARP resource creation, actual production parsing,
and eight standard shader include equivalence cases. Both DLL builds passed.
The prepared-runtime activation fixture remains failing and is not qualified.

## Fixed and independently reviewed

- Manifest metadata copies, assignment and destruction retain their own budget;
  compiler/IR, pool and parameter expansion are admitted before allocation.
  Charged IR cache reuse requires the same budget domain.
- Full command/expression qualification includes null copies, nested shader
  arguments, indirect draws and resource-property/readback side effects.
- Retirement completion checks slot identity; nonblocking publication and atomic
  snapshots avoid the legacy-writer/render-lock inversion.
- Queued CPU reservations cover buffer padding and encoded WIC/DDS staging.
  Codec-internal memory remains conservatively estimated.
- Windows staging retains protected handles through rename and recovery;
  process enumeration fails closed and includes configured game aliases.
- Pinned packages match the exact authenticated outer archive, including Manifest.
- New native implementation formatting follows the original tab indentation and
  multiline control flow. Formatting-only passes preserve C++ tokens.

The exact candidate is recorded in `native-candidate.json`; subsequent fixes
remain separate until committed and tested. Final Windows Python gate: 71 passed.
Portable ownership, generation and refresh regression gates passed. Independent
Runtime and metadata reviews found and rechecked the fixes above; independent
launcher review reproduced and rechecked archive-pin and process-alias failures.

## Existing evidence

Compatibility preflight: https://github.com/wunderay/XXMI-Launcher/actions/runs/40129119066
The early ownership/parser refactor built the production DLL. Selected-policy
and previous native suites passed. New fixture failures were identified:
constant-condition warning, unsupported macro-include test syntax, and fixture
project configuration mapping. The next run built the DLL/parser but exposed a Runtime activation timeout and
two strict fixture warnings. The reviewed candidate fixes the warnings and
preserves bounded failure diagnostics; a passing revised run is still required.

The previously verified development archive in `development-builds/` belongs to
an earlier, limited implementation. It is not evidence for the new general
pipeline. Collector 0.1.6 real-game results are recorded as a reported baseline;
the actual local evidence/source files have not been transferred here.
