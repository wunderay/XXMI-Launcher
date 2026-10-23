# XXMI Capture

A portable Windows desktop collector for repeatable mod-loading investigations.
It produces an offline HTML report, raw CSV/JSON evidence, optional screenshots
and attachments, and a ZIP you can review and share. Captures run only when you
start them. The app uses Windows Forms and .NET Framework 4.8 on Windows 10/11 x64;
the prebuilt executable needs no Python, launcher integration, or .NET SDK.

Read `QUICKSTART.txt` for the guided workflow. Launch the game; a single supported
running game is detected automatically. Click **Start capture**. The default capture lasts 60 seconds.
Settings and evidence controls are collapsed initially; screenshots are optional.
Expand the optional settings to change the duration, scenario, baseline/candidate
label, save folder, native trace folder or PresentMon path. Record baseline and
candidate with identical game/mod settings. Finish early or let the timer finish,
then open the report or saved ZIP. **Open folder** exposes retained partial data
even when collection fails.

Game detection checks the launcher's known executable names for Zenless Zone
Zero (including beta), Genshin Impact (including YuanShen), Honkai: Star Rail,
Honkai Impact 3rd and Arknights: Endfield. Wuthering Waves uses a generic Unreal
executable, so its window title or branded installation path must corroborate
its identity; the branded launcher wrapper is excluded. Multiple detected games
require a choice. **Change game** exposes manual selection when detection is
uncertain. Idle detection refreshes periodically and never changes an active
capture's target.

The desktop UI uses a light background, dark text, readable input controls and
explicit action states. Optional sections scroll on smaller screens while the
main capture and result actions remain available. Windows high-contrast colors
are respected when that mode is active.

## Available evidence

| Evidence | Source and boundary |
|---|---|
| OS, CPU, RAM, GPU/driver, storage inventory | Bounded WMI probes; missing/protected data is explicit. Installed GPUs do not identify the game's active adapter. |
| Loaded `d3d11.dll` / `dxgi.dll` paths, versions and SHA-256 | Capture-start module enumeration and on-disk file snapshot. This does not hash the process's loaded memory pages. |
| Process CPU, working set, private memory and IO | 500 ms observations. CPU is normalized over all logical processors. IO includes cached/non-disk process IO. |
| Native loading-phase durations | Existing diagnostic DLL exports. Only changed/new top-level exports whose filename identifies the selected PID are copied. |
| Frame-time percentiles | Optional user-selected PresentMon executable; explicit supported millisecond fields only. Permission/incompatible-tool failures remain unavailable. |
| Event markers and screenshots | Approximate operator observations and visible game-window captures; correctness and exact first-visible-frame latency still require review. |

The app does not inject into a game, install or replace DLLs, edit INIs, change
trust settings, elevate itself, download tools, or upload data. A process's GPU
budget, GPU residency, active device flags, visual correctness and exact
first-eligible/first-correct-frame latency remain unmeasured here.

## Permission failures

Capture uses a held limited-query process handle. Protected or elevated games
can deny some measurements; those fields remain unavailable while other evidence
continues. If even limited access is denied, process lifetime and telemetry remain
unknown rather than falsely reporting an exit. Such a capture ends by its timer
or **Finish & save**. Module snapshots are accepted only while the original
process lifetime can be verified. No elevation or game security changes occur.

The default save folder is `<XXMI-Launcher repo>\captures`, excluded from Git.
The app also creates a local self-ignore file there if none exists, so captures
stay ignored when the clone has not yet received the root ignore rule.
Extract the portable app anywhere inside your local clone; it finds the repo by
walking upward from the app directory, then from the working directory. If neither
location is inside the repo, **Start capture** asks you to select the repo folder.
It does not fall back to Documents or AppData. **Open folder** reveals each capture.
Optional settings still let you select a different save folder. If a write fails, the error
names its operation and path; choose a writable folder and retry. **Copy error
details** copies the exception and native error code locally for troubleshooting.
Review copied details before sharing, since they include local paths.

## Native phase capture

Native timings require the diagnostic DLL implemented separately under
`DirectX11/ModLoading` in the native XXMI-Libs-Package source. They are unavailable
with an unmodified release DLL. Install a development build only through the
existing isolated, authenticated development-package workflow; this collector
does not automate that step.

Select the folder containing that diagnostic DLL. During a collector capture,
set `mod_load_trace = 1` and `mod_load_trace_commands = 0` in its existing
`[Logging]` section and use the normal F10 reload. Perform a short scenario, set
`mod_load_trace = 0`, then F10 again to export. If the collector's timer already
stopped, click **Refresh exported traces** to collect the later export and rebuild
the report/ZIP. Later-exported events can extend outside the process-sampling
window; the report preserves their source timestamps instead of claiming exact
alignment. Trace snapshot checks are best-effort consistency checks, not proof
against noncooperating writers.

The native buffer holds 8192 events. The report retains dropped-event counters,
incomplete-export warnings and sample sizes. Counters are cumulative: the report
does not sum them across exports. Present CPU scopes are not displayed frame
times, and nested phase timings must not be added as independent work.

## Optional PresentMon

Select an existing trusted PresentMon executable. It must advertise
`--process_id`, `--output_file`, `--timed` and `--terminate_after_timed` in `--help`.
The app validates these flags, captures for the selected PID, records tool
identity/arguments/status, and manages only its own child process. Some Windows
setups restrict ETW access; the app records the failure and continues collecting
other evidence. It does not automatically elevate.

Early stop can leave a partial CSV tail; this is reflected in the report.
Supported CSV frame-time fields are `MsBetweenPresents` and `FrameTime` in
milliseconds, preferring the former when both exist. The report requires you to
qualify the intended swapchain and does not silently treat any CPU sample as a
frame. Use paired runs and enough valid samples before interpreting p99.

## Local outputs and bounds

Each run has a unique folder under `<XXMI-Launcher repo>\captures` or your selected
output folder. It contains `session.json`, `inventory.json`, `samples.csv`,
`markers.csv`, `report.html`, `analysis.json`, copied native traces and selected
evidence. PresentMon adds its CSV, metadata and bounded diagnostics. A sibling ZIP
contains generated files and manifest-recorded attachments/traces; untracked
files and linked paths are excluded. A failed archive update retains the prior
ZIP. Raw capture files remain available when a report cannot be completed.

Native snapshots are limited to 128 files, 64 MiB per file and 256 MiB total.
Attachments are limited to 16 successful files, 32 MiB per file and 128 MiB total.
The report bounds CSV/JSON input and identifies rejected/incomplete data. Review
the bundle before sharing: local account paths, notes and selected configurations
may be present. A screenshot can show obscured or black content; inspect it.

## Build and verification

From a .NET SDK environment, run `Build.cmd` on Windows, or:

```sh
dotnet build tools/ModLoadingCollector/ModLoadingCollector.csproj -c Release
```

This restores only the Microsoft .NET Framework reference-assembly package.
Runtime references are built into .NET Framework 4.8. The executable and its
`.exe.config` are under `bin/Release/net48`. The project references the launcher's
existing window icon, so build from this repository. Keep the license and source
with redistributed builds.

Portable logic tests use a .NET 10 SDK; they do not require Windows:

```sh
dotnet run --project tools/ModLoadingCollector/tests/report/ReportTests.csproj -- work/report-tests
dotnet run --project tools/ModLoadingCollector/tests/backend/BackendTests.csproj -- work/backend-tests
dotnet run --project tools/ModLoadingCollector/tests/archive/ArchiveTests.csproj -- work/archive-tests
dotnet run --project tools/ModLoadingCollector/tests/detection/DetectionTests.csproj
```

The PresentMon lifecycle harness additionally requires Linux and Python 3; see
`tests/presentmon/README.md`. It uses fake child processes, not real ETW.

The Windows x64 executable builds with zero warnings or errors. Portable tests
cover parsing, percentiles, export completeness, stable snapshots, PID filtering,
stopping, attachments, archive bounds, owned child processes and automatic game
detection (18 assertions).

[Native Windows UI smoke checks passed](https://github.com/wunderay/XXMI-Launcher/actions/runs/39856694366)
for the source at `93838a0d8631d10eb4d0f305e23d4dfba2d0a3b8`. The synthetic-only
harness checks single/multiple/no-game selection, manual override, selection
stability, contrast values, default/minimum layouts, and keyboard access to
expanded fields. Font/geometry simulations at 125% and 150% passed; they do not
establish actual monitor DPI behavior. See `tests/windows-ui/README.md`.

Live game capture, WMI/module permissions, actual monitor DPI/high-contrast
settings, screenshot behavior and real PresentMon capture remain local
qualification tasks. These checks do not pass the plan's Windows/D3D11/ZZMI
baseline gate or establish a performance improvement.
