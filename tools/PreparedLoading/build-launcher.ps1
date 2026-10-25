param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$Python = 'python',
    [string]$Version = '0.0.0'
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The launcher must be built on Windows x64.' }
$out = [IO.Path]::GetFullPath($OutputDirectory)
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$build = Join-Path $out 'build'
New-Item -ItemType Directory -Force $build | Out-Null
$previousVersionResource = $env:XXMI_LAUNCHER_VERSION_RESOURCE
Start-Transcript -Path (Join-Path $build 'launcher-build.log') -Force | Out-Null
try {
    & $Python -c 'import struct, sys; assert sys.version_info[:2] == (3, 12) and struct.calcsize("P") == 8, "Python 3.12 x64 required"'
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported build interpreter.' }
    $versionResource = Join-Path $build 'launcher-version.txt'
    & $Python (Join-Path $PSScriptRoot 'package-launcher.py') --version $Version --version-resource $versionResource
    if ($LASTEXITCODE -ne 0) { throw 'Invalid launcher version or resource generation failed.' }
    $env:XXMI_LAUNCHER_VERSION_RESOURCE = $versionResource
    $venv = Join-Path $build 'venv'
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Packaging environment creation failed.' }
    $buildPython = Join-Path $venv 'Scripts\python.exe'
    # The application dependency pins are unchanged. Nuitka is an unused alternative
    # compiler, so it is omitted only from this private build environment.
    $requirements = Join-Path $build 'launcher-requirements.txt'
    Get-Content -LiteralPath (Join-Path $root 'requirements.txt') |
        Where-Object { $_ -notmatch '^Nuitka==' } |
        Set-Content -LiteralPath $requirements -Encoding utf8
    & $buildPython -m pip install --disable-pip-version-check -r $requirements 'PyInstaller==6.20.0' 'pyinstaller-hooks-contrib==2026.5'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned launcher dependency installation failed.' }
    $env:PYTHONHASHSEED = '0'
    & $buildPython -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'pyinstaller') `
        (Join-Path $PSScriptRoot 'launcher.spec')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher freezing failed.' }
    & $buildPython (Join-Path $PSScriptRoot 'package-launcher.py') --root $root --output $out --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Launcher installation staging or frozen smoke test failed.' }
    Write-Output "PASS: standard installation payload and staged executable smoke test in $out"
} finally {
    $env:XXMI_LAUNCHER_VERSION_RESOURCE = $previousVersionResource
    Stop-Transcript | Out-Null
}
