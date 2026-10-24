# Full-plan completion status — work in progress

The source implementation and independent safety reviews are complete for the
current candidate. Windows integration and real-game release qualification remain
required. No new DLL has been deployed or signed. The earlier account-limit
interruption has been resolved; work resumed on the saved candidate.

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

1. Pass the exact-candidate Windows integration suite: actual parser and
   prepared-runtime DLL fixtures, standard shader include equivalence, streaming
   trace DLL, and real DPAPI/protected-file/process lifecycle tests.
2. Verify the resulting source-linked archive and retain its exact hashes.
3. Obtain paired real-game candidate evidence, independent visual/timing review,
   and authorized publisher signing before release qualification. Never launch
   Zenless or trigger UAC; the user launches it manually.

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

Current native source: `361da74afee5e56027ad5202a773cb63ac918191`.
Final local Python gate: 70 tests, 66 passed, four Windows-only skipped.
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
