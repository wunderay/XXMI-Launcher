# Candidate portable source baseline

Read-only inspection: 2026-10-28. No source build, patch application, Git mutation, game launch or native deployment was performed by this packaging task. This is a candidate reconstruction recipe requiring Q00 verification, not proof that the complete chain applies cleanly.

## Smallest existing reconstruction route

1. Existing tracked `tools/PreparedLoading/integration-evidence/native-history.bundle` contains source history through native parent `c442d746282339651a4ecb4b2a111f43daadc30b`, tree `30e935aa00088be3f8493c143f8c6fa70ec59e6e`, over public base `a7847eae82aaac32175c9a7d199673d5c9323bcd`. It is modified relative to the current launcher HEAD: copy the current exact source bundle into the narrowly selected input baseline rather than inheriting the older committed bytes. Current size is 380030 bytes; SHA256 `8855d980d1081f2b1cfc8a461b630d804f3905db06c01ca810811fa86801b63f`. A clean native checkout containing the base plus the verified bundle is one route. Alternative: exact public base plus the existing aggregate `native.patch`; verify equivalence to the bundled parent before using this alternative. Do not apply aggregate native.patch again on top of c442d746.
2. Apply the incremental source patches in order: speed, loading, GPU startup, GPU retention. All four exist locally but are currently untracked in the launcher repository. Bind their exact bytes into the authorized portable input commit/artifact; existing checkout presence alone is insufficient for remote execution.
3. Include exact `version.generated.h` from the current ignored native source, verified against its recorded source identity; a clean rebuild cannot depend on an untracked host-only generated header. The inspected header is 213 bytes, SHA256 `8c111be0b0e76b274a50d98de19ef0a1c24ad26732c10079b6d1c8c46ba16689`; relocate it to `tools/PreparedLoading/baseline/version.generated.h` in the portable input baseline rather than publishing ignored scratch.
4. Include existing build/fixture/collector/qualification helpers with their required imports/configuration. At minimum inspect `build-native-direct.py`, `build-native.ps1`, `capture-speed.py`, `test_capture_speed.py`, `validate-speed.py`, `qualify.py` and `qualification/` contracts. The direct builder and newer capture helpers are currently untracked; bind only the helpers actually required. The direct builder hardcodes four compile workers: make its resource limit explicit before using it under the one-adventurer host budget. Do not invoke legacy packaging helpers' raw Git subprocesses; use the required git_safe launcher or its supported Python API.
5. Treat current `development-builds/prepared-loading/BUILD-INFO.json`, `LOCAL-LAUNCHER.json`, `COMPLETION-STATUS.md` and source manifest as provenance, not a source substitute. The native source digest in that record is `7bc377b215fedea252bfafc7f7aebcfe48c83186858cf353583d5ce80bd723bd`; its digest algorithm uses the sorted `native/` file names from the existing development ZIP and concatenates `relative_name + NUL + file_sha256 + LF`, then SHA256. Reconstruct and compare those inputs; do not equate this digest with a Git tree hash.

## Inspected exact patch hashes (SHA256)

| File | SHA256 |
| --- | --- |
| native.patch | `07898d8f53ba71439d70e5de9327cc5d90f3210a26eb13eb517d452ee7156fa7` |
| native-speed.patch | `b7d1cf2c1380d9ffde325c367569924e3e6113f69c6e068368ab808fae578bb4` |
| native-loading.patch | `85ade7906e6c7e0255602153273db359f0f3c69594c99c08ff4a946c71697d27` |
| native-gpu-startup.patch | `d9932a38d9e5dcc66a082317c3c2ca8007fe20923c7073f6631c794a776ee4a6` |
| native-gpu-retention.patch | `2a249ff34ae23a21dfd5ac23c8535d6d13ed7f4223cab4db455837f49db6dfd2` |

The inspected development record names DLL `523a9834a410d67ca5ea1d9de2efb8b4a6f70442d1c4235dcdcaf233e9d708b8`, MSVC 14.44.35207 x64 and SDK 10.0.17763.0. This task did not rehash or load that DLL, rerun the old build or validate compiler availability. Every new candidate needs a new exact receipt; historical assertions do not transfer.

## Exclusions and honest remaining gates

Do not blanket-stage the dirty launcher branch, installer/antivirus status reports, generated MSI/EXE/ZIP files, local toolchain, full mods, captures with private local paths, installed profile, ignored scratch or unrelated source. Include only explicitly selected source/provenance required to reconstruct the candidate; retain third-party licensing/notices.

Source must be reconstructible in a clean authorized worker environment and registered through the supported Guild repository/test path before implementation dispatch. Native Windows/WARP checks require a qualified native executor; a generic Linux image cannot establish them. Existing synthetic success cannot establish first-visit/revisit game parity, displayed-frame correctness or normal signed release readiness.
