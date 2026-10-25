# Normal integration verification

The verification record binds the reviewed normal-launcher integration to its
successful Windows run and downloaded artifact checks. CI artifacts are build
and test handoff material; no separate end-user launcher ZIP is published here.
The installation payload preserves the established `Resources/Bin` layout.

The source-only `native-history.bundle` preserves the native branch through
`58a87cbce73c8888c0df88cd6fa175fff2efbb16`, based on public upstream
`a7847eae82aaac32175c9a7d199673d5c9323bcd`. Verify/fetch it in a clone
containing that base; `native-candidate.json` and `native.patch` bind the same
commit/tree. The bundle is source history, not a deployable package.

Publisher signing, the maintained MSI release workflow and actual-game
qualification remain external requirements. See `../NORMAL-INSTALLATION.md`.
