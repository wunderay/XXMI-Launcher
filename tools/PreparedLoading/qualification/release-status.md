# Release qualification status

No importer or game version is release-qualified by this tooling. The reported
collector 0.1.6 baseline has not supplied its original raw local files here.
Synthetic examples explicitly fail actual-game qualification.

Normal launcher source integration is implemented: existing Update/settings,
authenticated last-good recovery and interrupted-install Repair, with no manual
development ZIP workflow. The normal native producer includes capability
metadata, and the launcher staging uses the established installation layout.

The [full Windows run](https://github.com/wunderay/XXMI-Launcher/actions/runs/40231123585)
passed at launcher `c7749bfc00e47bdebf10d0d95d045e413ea2473d` and native
`58a87cbce73c8888c0df88cd6fa175fff2efbb16`: 101 lifecycle/qualification tests,
9 distribution/real-GUI tests, 4 capability CLI tests and the complete native
synthetic/parser/runtime/WARP suites. Downloaded source and payload artifacts
were verified. These are automated development checks, not game acceptance.

The maintained MSI project/build workflow and authorized publisher signing
remain unavailable. No normal installed release is published, no new publisher
trust is added, and unsigned metadata does not activate preparation. Original
baseline inspection, paired actual-game scenarios, independent visual/timing
acceptance, real recovery observations, full matrix evidence and authorized
release signing remain external gates. See `../NORMAL-INSTALLATION.md`.
