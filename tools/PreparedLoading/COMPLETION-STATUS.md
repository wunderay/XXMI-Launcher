# Full-plan completion status — work in progress

The implementation is not complete or release-qualified. Coding/review agents
hit the account usage limit while the final fixes were in progress. No new DLL
has been deployed, signed, or published as a verified replacement.

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

1. Finish the native metadata ownership fixes. In particular,
   `ConfigSourceManifest` must not release its budget before owned storage, and
   metadata copies/assignment must not duplicate allocations under one lease.
   Recheck semantic graph admission and all related ownership lifetimes.
2. Finish Windows package staging and idle-process safety. Review reproduced
   mutable staged files being committed after verification and process discovery
   treating AccessDenied as absence. These are blockers, not accepted limits.
3. Complete independent review of the connected general Runtime and final fixes.
4. Run the revised full Windows integration suite, including actual parser and
   prepared-runtime DLL fixtures, shader standard-handler equivalence, streaming
   trace fixture, and real Windows DPAPI/protected-file lifecycle tests.
5. Verify final source-linked package provenance and authenticated package
   update/rollback behavior. Keep the existing publisher trust checks.
6. Obtain paired real-game candidate evidence, independent visual/timing review,
   and authorized publisher signing before release qualification. Never launch
   Zenless or trigger UAC; the user launches it manually.

## Existing evidence

Compatibility preflight: https://github.com/wunderay/XXMI-Launcher/actions/runs/40129119066
The early ownership/parser refactor built the production DLL. Selected-policy
and previous native suites passed. New fixture failures were identified:
constant-condition warning, unsupported macro-include test syntax, and fixture
project configuration mapping. Fixes are saved but the revised integration run
and connected Runtime review are still required.

The previously verified development archive in `development-builds/` belongs to
an earlier, limited implementation. It is not evidence for the new general
pipeline. Collector 0.1.6 real-game results are recorded as a reported baseline;
the actual local evidence/source files have not been transferred here.
