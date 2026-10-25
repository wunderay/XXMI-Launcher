# Release qualification status

No importer or game version is release-qualified by this tooling. The supplied 0.1.6 diagnostic baseline is recorded only as a reported, unaccessed receipt. The checked-in example and report are synthetic contract fixtures and explicitly fail qualification.

External gates remain: transfer and inspect original baseline files; collect pinned paired actual-game candidate evidence (at least 100 independent warm activations per claimed scenario); independently attest complete first-eligible-frame appearance; retain actual failure/rollback observations and full validation matrix evidence; obtain authenticated independent acceptance and authorized publisher signing outside this offline tool.

The reviewed native candidate `4599c694c23c7dfe0a62e40a2878b07985cdc521` passed the
[full native Windows job](https://github.com/wunderay/XXMI-Launcher/actions/runs/40180466047/job/121130237883),
including the production parser/runtime/WARP fixtures and ShaderRegex regression.
The parallel launcher job passed all 77 lifecycle/qualification tests. Both
Astraea findings are addressed: persisted ShaderRegex identity/layout is restored,
and reviewer acceptance binds the complete paired evidence.

The portable launcher at `cef37d86a1dafe40c9c807e0d1c704833d105083` passed its
[extracted Windows EXE smoke](https://github.com/wunderay/XXMI-Launcher/actions/runs/40181635989).
This packaging-only run reuses the unchanged native/lifecycle test inputs; skipped
jobs are not represented as freshly executed. Verified archives are in
`development-builds/prepared-loading/4599c694c23c` and
`development-builds/launcher/cef37d86a1da`. The launcher does not include or
authorize a native DLL. These checks do not change actual-game qualification.
