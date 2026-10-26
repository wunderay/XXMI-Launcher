# Prepared loading development candidate

Native source: `4599c694c23c7dfe0a62e40a2878b07985cdc521`. Native tree: `2c35a0dc33f5be79d515ccabeea8c11862c7b79a`.

The development ZIP contains the production DLL, exact source archive, patch,
build scripts and licenses. Its capability metadata is unsigned; the existing
publisher trust gate remains closed. It has no installer.

Windows fixture job: https://github.com/wunderay/XXMI-Launcher/actions/runs/40180466047/job/121130237883. This is synthetic validation, not
real-game performance or visual qualification. Paired game captures, independent
visual/timing acceptance and authorized publisher signing remain required.
The original older development ZIP remains a separate limited implementation.

`native-history.bundle` preserves the exact native commits. In a clone of
`SpectrumQT/XXMI-Libs-Package` containing `a7847eae82aaac32175c9a7d199673d5c9323bcd`, run:

```sh
git bundle verify /path/to/native-history.bundle
git fetch /path/to/native-history.bundle codex/prepared-mod-loading
git checkout --detach FETCH_HEAD
```

The resulting HEAD must equal the native source above. For a source-only build,
use the ZIP's corresponding source and exact generated version header as
described in its README. `SHA256SUMS` covers the ZIP, build record and Git bundle;
`verification.json` binds their identities to the DLL and workflow run.

This candidate fixes the persisted ShaderRegex cache identity/layout regression
in `626775cad8a2`. Prefer this candidate for further development; it remains
unsigned and game/release-unqualified. For the runnable launcher, see
[the portable EXE package](../../launcher/cef37d86a1da/).
