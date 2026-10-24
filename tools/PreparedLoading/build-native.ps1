param(
    [Parameter(Mandatory=$true)][string]$NativeSource,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$native = (Resolve-Path -LiteralPath $NativeSource).Path
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $out | Out-Null
$bin = Join-Path $out 'bin'
$tests = Join-Path $out 'tests'
New-Item -ItemType Directory -Force $bin, $tests | Out-Null
$mod = Join-Path $native 'DirectX11\ModLoading'
$script:TestFailures = @()

function Run-NativeTest {
    param([string]$Name, [string[]]$Sources, [string[]]$Libraries = @(), [string]$PythonRunner = '', [string]$Standard = 'c++14', [string[]]$Definitions = @(), [switch]$CompileOnly, [switch]$SharedLibrary)
    $dir = Join-Path $tests $Name
    New-Item -ItemType Directory -Force $dir | Out-Null
    Push-Location $dir
    try {
        $arguments = @('/nologo', "/std:$Standard", '/MT', '/EHsc', '/W4', '/WX', '/UNDEBUG',
            '/D_CRT_SECURE_NO_WARNINGS', '/DUNICODE', '/D_UNICODE',
            '/DNOMINMAX', '/DWIN32_LEAN_AND_MEAN', "/I$mod",
            "/I$(Join-Path $native 'DirectXTK\Inc')")
        $arguments += $Sources | ForEach-Object { Join-Path $native $_ }
        $arguments += $Definitions | ForEach-Object { "/D$_" }
        if ($SharedLibrary) {
            $arguments += '/LD'
            $arguments += "/Fe:$Name.dll"
        } else {
            $arguments += "/Fe:$Name.exe"
        }
        if ($Libraries.Count) { $arguments += '/link'; $arguments += $Libraries }
        & cl @arguments 2>&1 | Tee-Object -FilePath build.log
        if ($LASTEXITCODE -ne 0) { throw "$Name compilation failed ($LASTEXITCODE)" }
        if ($CompileOnly -or $SharedLibrary) { return }
        if ($PythonRunner) {
            & python (Join-Path $native $PythonRunner) (Join-Path $dir "$Name.exe") 2>&1 | Tee-Object -FilePath run.log
        } else {
            & (Join-Path $dir "$Name.exe") 2>&1 | Tee-Object -FilePath run.log
        }
        if ($LASTEXITCODE -ne 0) { throw "$Name failed ($LASTEXITCODE)" }
    } catch {
        $script:TestFailures += "$Name`: $($_.Exception.Message)"
        Write-Output "FAIL: $Name`: $($_.Exception.Message)"
    } finally { Pop-Location }
}

Run-NativeTest -Name 'cpu' -Sources @(
    'tests\preparation_cpu\main.cpp',
    'tests\preparation_cpu\disk.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp',
    'DirectX11\ModLoading\CpuAssetCache.cpp',
    'DirectX11\ModLoading\DiskAssetCache.cpp',
    'DirectX11\ModLoading\AssetCatalog.cpp',
    'DirectX11\ModLoading\PreparationScheduler.cpp'
) -Libraries @('ole32.lib', 'windowscodecs.lib')
Run-NativeTest -Name 'config' -Sources @(
    'tests\preparation_generations\main.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp',
    'DirectX11\ModLoading\ConfigCompiler.cpp',
    'DirectX11\ModLoading\IncrementalConfigCache.cpp',
    'DirectX11\ModLoading\GenerationManager.cpp'
) -Libraries @('ole32.lib', 'windowscodecs.lib')
Run-NativeTest -Name 'refresh' -Sources @(
    'tests\preparation_refresh\main.cpp',
    'DirectX11\ModLoading\ResourceRefresh.cpp',
    'DirectX11\ModLoading\RefreshCpuBudget.cpp',
    'DirectX11\ModLoading\ResourceRefreshClosure.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp'
) -Libraries @('ole32.lib', 'windowscodecs.lib') `
  -PythonRunner 'tests\preparation_refresh\run_windows_fixture.py'
Run-NativeTest -Name 'changes' -Sources @(
    'tests\preparation_changes\change_reconciler_test.cpp',
    'DirectX11\ModLoading\ChangeReconciler.cpp',
    'DirectX11\ModLoading\ChangeMonitor.cpp'
)
Run-NativeTest -Name 'windows_changes' -Sources @(
    'tests\preparation_changes\change_monitor_windows_test.cpp',
    'DirectX11\ModLoading\ChangeReconciler.cpp',
    'DirectX11\ModLoading\ChangeMonitor.cpp'
)
Run-NativeTest -Name 'gpu_policy' -Sources @(
    'tests\preparation_gpu\test_gpu_policy.cpp',
    'DirectX11\ModLoading\DeviceResourceCache.cpp',
    'DirectX11\ModLoading\MemoryBudgetController.cpp'
) -Libraries @('d3d11.lib', 'dxgi.lib')
Run-NativeTest -Name 'runtime_policy' -Sources @('tests\preparation_runtime\main.cpp')
Run-NativeTest -Name 'ownership' -Sources @('tests\preparation_ownership\main.cpp') -Standard 'c++17'
Run-NativeTest -Name 'working_set' -Sources @(
    'tests\preparation_working_set\working_set_test.cpp',
    'DirectX11\ModLoading\WorkingSetPolicy.cpp'
)
Run-NativeTest -Name 'load_trace' -Sources @(
    'tests\mod_loading\load_trace_test.cpp',
    'DirectX11\ModLoading\LoadTrace.cpp'
)
Run-NativeTest -Name 'load_trace_streaming' -Sources @(
    'tests\mod_loading\load_trace_streaming_test.cpp',
    'DirectX11\ModLoading\LoadTrace.cpp',
    'DirectX11\ModLoading\TraceControl.cpp'
)
Run-NativeTest -Name 'trace_worker' -Sources @(
    'tests\mod_loading\load_trace_streaming_windows_fixture.cpp',
    'DirectX11\ModLoading\LoadTrace.cpp',
    'DirectX11\ModLoading\TraceControl.cpp'
) -Definitions @('LOAD_TRACE_FIXTURE_DLL') -SharedLibrary
Run-NativeTest -Name 'trace_host' -Sources @(
    'tests\mod_loading\load_trace_streaming_windows_fixture.cpp'
) -CompileOnly
$traceDll = Join-Path $tests 'trace_worker\trace_worker.dll'
$traceHost = Join-Path $tests 'trace_host\trace_host.exe'
if ((Test-Path -LiteralPath $traceDll) -and (Test-Path -LiteralPath $traceHost)) {
    & $traceHost $traceDll 2>&1 | Tee-Object -FilePath (Join-Path $out 'trace-integration.log')
    if ($LASTEXITCODE -ne 0) { $script:TestFailures += 'Native streaming trace DLL fixture failed.' }
}
Run-NativeTest -Name 'standard_include_probe' -Sources @('tests\preparation_shader_cache\standard_probe.cpp') `
    -Libraries @('d3dcompiler.lib') -PythonRunner 'tests\preparation_shader_cache\run_windows_fixture.py'
Run-NativeTest -Name 'shader_identity' -Sources @(
    'tests\preparation_shader_cache\windows.cpp',
    'DirectX11\ModLoading\ShaderSourceCache.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp',
    'DirectX11\ModLoading\RefreshCpuBudget.cpp'
) -Libraries @('d3dcompiler.lib', 'ole32.lib', 'windowscodecs.lib') `
    -PythonRunner 'tests\preparation_shader_cache\run_windows_fixture.py'
Run-NativeTest -Name 'refresh_device' -Sources @(
    'tests\preparation_refresh\test_refresh_device.cpp',
    'DirectX11\ModLoading\ResourceRefresh.cpp',
    'DirectX11\ModLoading\RefreshCpuBudget.cpp',
    'DirectX11\ModLoading\ResourceRefreshOverlay.cpp',
    'DirectX11\ModLoading\ResourceRefreshClosure.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp'
) -Libraries @('d3d11.lib', 'dxgi.lib', 'ole32.lib', 'windowscodecs.lib')
Run-NativeTest -Name 'status_serialization' -Sources @(
    'tests\preparation_status\status_serialization_test.cpp',
    'DirectX11\ModLoading\PreparationStatus.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp'
) -Libraries @('ole32.lib', 'windowscodecs.lib', 'propsys.lib')
Run-NativeTest -Name 'windows_status' -Sources @(
    'tests\preparation_status\status_publisher_windows_test.cpp',
    'DirectX11\ModLoading\PreparationStatus.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp'
) -Libraries @('ole32.lib', 'windowscodecs.lib', 'propsys.lib') `
  -PythonRunner 'tests\preparation_status\run_windows_fixture.py'

# Suppress deployment/copy events in every project configuration.
$buildArguments = @((Join-Path $native 'StereovisionHacks.sln'), '/m', '/t:Build',
    '/p:Configuration=Zip Release', '/p:Platform=x64', "/p:OutDir=$bin\",
    '/p:PostBuildEventUseInBuild=false', '/p:PreBuildEventUseInBuild=false', '/v:minimal')
& msbuild @buildArguments 2>&1 | Tee-Object -FilePath (Join-Path $out 'native-build.log')
if ($LASTEXITCODE -ne 0) { $script:TestFailures += "Native DLL compilation failed ($LASTEXITCODE)" }

$toolkit = Get-ChildItem -LiteralPath $bin -Recurse -File -Filter 'DirectXTK.lib' | Select-Object -First 1
if ($toolkit) {
Run-NativeTest -Name 'gpu_device' -Sources @(
    'tests\preparation_gpu\test_gpu_device.cpp',
    'DirectX11\ModLoading\SnapshotIO.cpp',
    'DirectX11\ModLoading\DeviceResourceCache.cpp',
    'DirectX11\ModLoading\MemoryBudgetController.cpp',
    'DirectX11\ModLoading\PreparationPlanner.cpp',
    'DirectX11\ModLoading\LoadTrace.cpp'
) -Libraries @($toolkit.FullName, 'd3d11.lib', 'dxgi.lib', 'd3dcompiler.lib',
    'windowscodecs.lib', 'ole32.lib')
} else { $script:TestFailures += 'Built DirectXTK static library was not found.' }

# The production DLL is built without test exports. A separate DLL from the
# same source enables parser fixtures only inside an owned isolated process.
if (Test-Path -LiteralPath (Join-Path $bin 'd3d11.dll')) {
    $fixtureBin = Join-Path $out 'native-fixture\bin'
    New-Item -ItemType Directory -Force $fixtureBin | Out-Null
    Get-ChildItem -LiteralPath $bin -Filter '*.lib' | Copy-Item -Destination $fixtureBin
    $fixtureArgs = @((Join-Path $native 'StereovisionHacks.sln'), '/m', '/t:Build',
        '/p:Configuration=Zip Release', '/p:Platform=x64',
        "/p:OutDir=$fixtureBin\", '/p:ModLoadingTestHooks=true',
        '/p:PostBuildEventUseInBuild=false', '/p:PreBuildEventUseInBuild=false', '/v:minimal')
    & msbuild @fixtureArgs 2>&1 | Tee-Object -FilePath (Join-Path $out 'native-fixture-build.log')
    if ($LASTEXITCODE -ne 0) {
        $script:TestFailures += 'Native parser fixture DLL compilation failed.'
    } else {
        Run-NativeTest -Name 'native_config_host' -Sources @('tests\preparation_native_integration\host.cpp') -CompileOnly
        $hostExe = Join-Path $tests 'native_config_host\native_config_host.exe'
        if (Test-Path -LiteralPath $hostExe) {
            & python (Join-Path $native 'tests\preparation_native_integration\run_windows_fixture.py') `
                $hostExe (Join-Path $fixtureBin 'd3d11.dll') 2>&1 | Tee-Object -FilePath (Join-Path $out 'native-integration.log')
            if ($LASTEXITCODE -ne 0) { $script:TestFailures += 'Production native parser fixture failed.' }
        }
    }
}
if ($script:TestFailures.Count) {
    $script:TestFailures | Set-Content -LiteralPath (Join-Path $out 'failures.txt')
    throw ($script:TestFailures -join '; ')
}

$dll = Join-Path $bin 'd3d11.dll'
if (!(Test-Path -LiteralPath $dll -PathType Leaf)) { throw 'Expected d3d11.dll missing.' }
$exports = & dumpbin /nologo /exports $dll
if ($LASTEXITCODE -ne 0) { throw 'Production DLL export inspection failed.' }
$exports | Set-Content -LiteralPath (Join-Path $out 'production-exports.txt')
if ($exports -match 'XXMI_Test') { throw 'Test-only exports were found in the production DLL.' }
$digest = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLowerInvariant()
$capabilities = [ordered]@{
    schema_version = 1
    mod_loading_version = 2
    dll_sha256 = $digest
    modes = @('off', 'adaptive', 'prepared_profile')
    features = @('cpu_cache_enabled', 'gpu_cache_enabled', 'adaptive_learning',
        'transactional_activation', 'general_fast_refresh', 'full_rescan_key', 'profile_members', 'profile_name')
    package_version = '0.2.0-development'
    release_stage = 'development'
}
[IO.File]::WriteAllText((Join-Path $out 'PreparationCapabilities.json'),
    ($capabilities | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
# Only the authorized package signer may add the exact-byte ECDSA signature.
$provenance = [ordered]@{
    native_base = 'a7847eae82aaac32175c9a7d199673d5c9323bcd'
    patch_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'native.patch') -Algorithm SHA256).Hash.ToLowerInvariant()
    dll_sha256 = $digest
    launcher_source = (& git -C (Join-Path $PSScriptRoot '..\..') rev-parse HEAD)
    workflow_run = "$env:GITHUB_SERVER_URL/$env:GITHUB_REPOSITORY/actions/runs/$env:GITHUB_RUN_ID"
    configuration = 'Zip Release|x64'
    compiler = $env:VCToolsVersion
    windows_sdk = $env:WindowsSDKVersion
    deployment_events = 'disabled'
    qualification = 'Synthetic Windows tests only; real game compatibility and performance unqualified.'
    capability_signature = 'not signed; launcher capability gate remains closed'
}
$candidate = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'native-candidate.json') -Raw | ConvertFrom-Json
if ($candidate.patch_sha256 -ne $provenance.patch_sha256 -or $candidate.native_base -ne $provenance.native_base) {
    throw 'Native candidate identity does not match the source patch.'
}
$provenance.native_source = $candidate.native_parent
$provenance.native_tree = $candidate.native_tree
[IO.File]::WriteAllText((Join-Path $out 'BUILD-INFO.json'),
    ($provenance | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
& python (Join-Path $PSScriptRoot 'package-development.py') --native $native --build $out
if ($LASTEXITCODE -ne 0) { throw 'Development packaging failed.' }
Write-Output "PASS: native DLL and synthetic fixtures; SHA256=$digest"
