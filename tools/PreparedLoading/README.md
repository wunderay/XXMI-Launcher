# Prepared loading development build

This package is experimental. Phase 0 measurements were waived. Synthetic
Windows fixtures do not establish game compatibility, visual correctness or
first-frame performance. See `native/docs/mod-loading/prepared-loading.md`
inside `corresponding-source.zip` for implemented paths and limitations.

`BUILD-INFO.json` identifies the pinned upstream base, exact source patch, DLL,
compiler and Windows SDK. `corresponding-source.zip` contains the native source
used by this build, including its third-party notices. The source patch is also
included separately. `SHA256SUMS` is published alongside the development zip.

The build script disables deployment events. This archive has no installer and
does not modify a game, an active DLL or launcher trust settings. Its capability
metadata is deliberately unsigned: launcher controls remain unavailable until
the existing authorized package signer signs the exact metadata and includes
it in a supported development package. Do not weaken package verification to
enable this build.

For developers, use a VS2022 x64 developer shell with a Windows SDK:

```powershell
git -C <clean-native-checkout> apply --check <path-to-native.patch>
git -C <clean-native-checkout> apply <path-to-native.patch>
./build-native.ps1 -NativeSource <clean-native-checkout> -OutputDirectory <isolated-build-folder>
```

The clean native checkout must be at
`a7847eae82aaac32175c9a7d199673d5c9323bcd` with its version tags fetched.
Python 3.8 or newer is required for packaging and the Windows status fixture.
Never run `CopyToGames.bat` as part of this workflow. To roll back a supported
test installation, stop the game, select Off, and restore the previous signed
package using the launcher's supported update/backup process. Derived status
and preparation caches are expendable.
