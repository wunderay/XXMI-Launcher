# Portable XXMI Launcher development build

1. Download `XXMI-Launcher-development-win-x64.zip`.
2. Extract the complete archive into a writable folder.
3. Open the `XXMI Launcher` folder and double-click **XXMI Launcher.exe**.

Keep `_internal`, `Themes` and `Locale` beside the executable. Python, PowerShell
and build tools are not required to run it. Do not run it from inside the ZIP.

This is an unsigned development launcher. It contains the prepared-loading UI
and package/recovery fixes, but does not include or authorize a native DLL.
Experimental prepared-loading controls still require an authorized signed native
package. Real-game performance/visual qualification remains pending.

Source: [`cef37d86a1dafe40c9c807e0d1c704833d105083`](https://github.com/wunderay/XXMI-Launcher/tree/cef37d86a1dafe40c9c807e0d1c704833d105083).
[Windows EXE build and extracted startup smoke test](https://github.com/wunderay/XXMI-Launcher/actions/runs/40181635989).

The extracted EXE passed offline hidden GUI, import, Tcl/Tkhtml, native-runtime,
localization, image, version and integrity checks with developer tools removed
from PATH. This did not construct the normal Application or launch a game.
All 1,801 packaged file hashes, ZIP digest, exact source commit and clean build
input status were checked again after download. Normal startup can access the
network and save configuration, following the existing launcher behavior.

`LAUNCHER-BUILD.json` records source, dependencies and every packaged file digest;
`LAUNCHER-PACKAGING.json` and `LAUNCHER-SELF-TEST.json` retain the CI receipts.
`verification.json` records the downloaded artifact check. `SHA256SUMS` covers
this directory's archive and metadata. See the source build instructions in
`tools/PreparedLoading/LAUNCHER-PACKAGING.md`.
