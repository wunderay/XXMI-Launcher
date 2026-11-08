# Windows UI smoke

This small .NET Framework 4.8 x64 executable renders the actual WinForms collector on Windows. It checks default and minimum client sizes, plus 125% and 150% **font/geometry stress simulations** in a client area that fits a 1280×720 display. These simulations do not establish real per-monitor DPI behavior.

It loads `CaptureForm` directly without calling the collector entry point. Before showing the native form, it removes its `Shown` event handlers so process enumeration and idle polling never run. Synthetic supported-game process items exercise the real automatic selection path, including no-game and multiple-game cases. Screenshots are explicitly annotated as synthetic. The observation-note check briefly applies the form's recording control state without creating a session or starting a timer, then restores it in `finally`. It never clicks Start, enumerates games, invokes WMI, starts PresentMon, captures a game screen, uploads, or requests elevation.

Setup fields are focused in the ready state. The observation-note check temporarily applies the product's recording UI state, without creating a session or starting a capture timer, and restores the ready state in `finally`. Its screenshot is named `synthetic-recording-note`.

Build and run from the repository root on Windows:

```powershell
dotnet build tools/ModLoadingCollector/ModLoadingCollector.csproj -c Release
dotnet build tools/ModLoadingCollector/tests/windows-ui/WindowsUiSmoke.csproj -c Release
& tools/ModLoadingCollector/tests/windows-ui/bin/Release/net48/WindowsUiSmoke.exe tools/ModLoadingCollector/bin/Release/net48/XXMI.Capture.exe work/ui-smoke
```

Evidence includes actual `DrawToBitmap` screenshots of the collapsed ready view, expanded settings, and expanded evidence scrolled into view, with a synthetic-only annotation. `assertions.txt` records failures and the result. Assertions cover initial optional visibility, footer and Start visibility, text bounds, horizontal overflow, text/native-input color contrast, toggle behavior, and keyboard-focus access to expanded controls through scrolling. Scroll assertions retain focus and verify visibility after layout, which allows native WinForms focus restoration to run. It also verifies that a synthetic permission failure shows the operation/folder and exposes a reachable error-copy action retaining the native error code (without touching the clipboard). This is a layout smoke test, not a capture backend test or accessibility audit. Disabled native controls and actual high-contrast/DPI settings still need manual inspection.

The GitHub workflow runs with read-only repository permissions, builds the application and harness, then uploads evidence even when assertions fail. Linux can cross-compile the harness but cannot execute these native UI checks.
