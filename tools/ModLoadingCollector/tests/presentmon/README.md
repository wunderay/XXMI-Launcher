# PresentMon lifecycle checks

Run from the repository root:

```sh
dotnet run --project tools/ModLoadingCollector/tests/presentmon/PresentMonTests.csproj
```

Testing prerequisites: Linux, .NET 10 SDK, `/usr/bin/python3`, and `chmod` on PATH.
The harness compiles the production wrapper using C# 7.3 and drives fake Python
processes. It does not require, download, or execute PresentMon. These are test
requirements only; the collector targets Windows/.NET Framework 4.8.

The 22 assertions cover Windows argument quoting, natural timed completion,
bounded stdout/stderr, early owned-process termination and partial metadata,
missing executables, incompatible help, help timeout, and an unsettled-monitor
stop timeout, missing exit confirmation, and faulted-monitor retry rejection
(deterministic reflection after child exit), plus live probe handle retention
and disposal after confirmed exit. Runtime is about sixteen
seconds. Fake executables and captures are written below `work/presentmon-tests/`
in a unique directory per run. They may be removed after inspecting results.

These checks do not qualify Windows process behavior, .NET Framework 4.8 runtime
compatibility, elevation requirements, or real PresentMon CSV output.
