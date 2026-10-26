# Experimental prepared-loading development artifact

The zip contains the native x64 DLL, exact corresponding source and generated
version header, source patch, licenses, build scripts and provenance. Its DLL
SHA256 is `017d45d5dcc6097b614ff88ef206f1fa7c550a8dc40aa2964886da22d49c0ad0`.
See `SHA256SUMS` for the zip hash.

[Windows build and synthetic test results](https://github.com/wunderay/XXMI-Launcher/actions/runs/40062715561)
passed for launcher source `c06e3df50bf78d0143d21850d2a86256300b0542` and
native source `6548844a37c2cc6a302231aeddee8ec29c4a1cc5` on baseline
`a7847eae82aaac32175c9a7d199673d5c9323bcd`.
The returned archive was checked against its DLL/patch/source digests and all
2,129 tracked native files, applying Git's documented text normalization.

The package is unsigned and has no installer. Capability controls remain closed
until the existing authorized package signer supplies authenticated metadata.
Nothing is deployed to a game or active library by this workflow. Phase 0 was
waived. Real-game rendering/performance and other importers are unqualified;
arbitrary INI edits still use F10. Windows paths with distinct 8.3 aliases use
compatibility behavior, and the actual short-alias fixture was skipped because
the test volume did not provide aliases. The synthetic refresh fixture exercises
production refresh primitives with WARP, rather than full game/parser integration.
