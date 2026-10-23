# Windows process permissions smoke test

Run on Windows x64 with .NET Framework 4.8. The project links the production
`CaptureSession.cs`; its minimal `NativeInventory` stub writes JSON and performs
no WMI, module enumeration, or game access.

```powershell
dotnet build tools/ModLoadingCollector/tests/windows-permissions/WindowsPermissionsSmoke.csproj -c Release
& tools/ModLoadingCollector/tests/windows-permissions/bin/Release/net48/WindowsPermissionsSmoke.exe work/permissions-smoke
if ($LASTEXITCODE -ne 0) { throw "Windows permissions smoke failed" }
```

The executable starts only copies of itself. It retains a cleanup handle before
applying a process DACL using `SetKernelObjectSecurity`. Explicit deny ACEs for
the current user reproduce access denied without elevation, privilege changes,
injection, or access to any actual game. DACLs disappear with those disposable
children. Cleanup uses controlled stdin, bounded waits, and the retained handle
as a termination fallback, including assertion failures.

Assertions cover:

- Denied full-query/synchronize rights make the original .NET Framework
  `Process.HasExited` call fail with Win32 error 5, while limited query and
  `GetProcessTimes` succeed. Production capture stays recording and emits CPU
  data. Memory counters may succeed with limited query on modern Windows;
  unavailable counters must be empty with explicit memory diagnostics.
- Capture retains the same original process handle across samples. The owned
  original exits with code 259; its native exit FILETIME changes from zero to
  nonzero, and capture stops with a `process_exited` marker while another child
  remains alive. The manifest still identifies the original PID.
- Denying both query rights leaves all metrics empty and lifetime explicitly
  unknown. Capture emits no false exit marker and stops by configured duration
  or explicit `StopAsync`, leaving the child alive.

`assertions.txt`, console errors with managed source stacks, and per-scenario
capture files provide failure evidence. A failed DACL or legacy-access precondition
fails the test; it is never silently skipped. Build success on Linux checks
compilation only. Runtime assertions require Windows and are not evidence about
any particular game's security policy. The identity scenario verifies retained
handles and an unrelated live successor; it does not force OS PID reuse.
