# Prepared loading development candidate

Superseded by [4599c694c23c](../4599c694c23c/), which fixes a persisted
ShaderRegex cache regression. This older archive is retained unchanged.

Native source: `626775cad8a2f3a2e1dc1056c437ffa60bd78899`. Native tree: `a7320f0a76ee0be0f3153d731d81532ed49d418f`.

The development ZIP contains the production DLL, exact source archive, patch,
build scripts and licenses. Its capability metadata is unsigned; the existing
publisher trust gate remains closed. It has no installer.

Windows fixture run: https://github.com/wunderay/XXMI-Launcher/actions/runs/40146131131. This is synthetic validation, not
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
