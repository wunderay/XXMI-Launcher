param(
    [Parameter(Mandatory=$true)][string]$Payload,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$WixBin,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The MSI must be built on Windows.' }
$payloadPath = (Resolve-Path -LiteralPath $Payload).Path
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $out | Out-Null
if (-not $WixBin) {
    $candidates = @()
    if ($env:WIX) { $candidates += (Join-Path $env:WIX 'bin'); $candidates += $env:WIX }
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'WiX Toolset v3.14\bin') }
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath (Join-Path $candidate 'candle.exe')) -and
            (Test-Path -LiteralPath (Join-Path $candidate 'light.exe'))) { $WixBin = $candidate; break }
    }
}
if (-not $WixBin) { throw 'Preinstalled WiX 3 candle.exe/light.exe required; supply -WixBin. No tool is downloaded.' }
$candle = (Resolve-Path -LiteralPath (Join-Path $WixBin 'candle.exe')).Path
$light = (Resolve-Path -LiteralPath (Join-Path $WixBin 'light.exe')).Path
$helper = Join-Path $PSScriptRoot 'authoring.py'
$msi = Join-Path $out "XXMI-Launcher-Installer-Online-v$Version.msi"
# Do not leave a stale successful-looking artifact after a failed rebuild.
Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$msi.sha256" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $out 'INSTALLER-BUILD.json') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $out 'INSTALLER-VALIDATION.json') -Force -ErrorAction SilentlyContinue
Start-Transcript -Path (Join-Path $out 'installer-build.log') -Force | Out-Null
try {
    & $Python $helper --payload $payloadPath --version $Version --output-directory $out
    if ($LASTEXITCODE -ne 0) { throw 'Payload validation or original MSI authoring failed.' }
    $source = Join-Path $out 'installer.wxs'
    $object = Join-Path $out 'installer.wixobj'
    & $candle -nologo -arch x64 -ext WixUIExtension -out $object $source 2>&1 |
        Tee-Object -FilePath (Join-Path $out 'candle.log') | Write-Output
    if ($LASTEXITCODE -ne 0) { throw 'WiX compilation failed; see candle.log.' }
    # WiX default ICE validation remains enabled. No -sval or -sice bypasses.
    & $light -nologo -ext WixUIExtension -out $msi $object 2>&1 |
        Tee-Object -FilePath (Join-Path $out 'light.log') | Write-Output
    if ($LASTEXITCODE -ne 0) { throw 'WiX linking/default ICE validation failed; see light.log.' }
    & $Python $helper --payload $payloadPath --version $Version --check-receipt (Join-Path $out 'INSTALLER-AUTHORING.json')
    if ($LASTEXITCODE -ne 0) { throw 'Payload changed during MSI build.' }
    & $Python (Join-Path $PSScriptRoot 'validate_msi.py') --msi $msi --payload $payloadPath --version $Version --output (Join-Path $out 'INSTALLER-VALIDATION.json')
    if ($LASTEXITCODE -ne 0) { throw 'Read-only MSI database/cabinet validation failed.' }
    $receipt = Get-Content -LiteralPath (Join-Path $out 'INSTALLER-AUTHORING.json') -Raw | ConvertFrom-Json
    $build = [ordered]@{
        format = 1
        msi = [IO.Path]::GetFileName($msi)
        sha256 = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant()
        version = $Version
        product_code = $receipt.product_code
        identity = $receipt.identity
        launcher_source = $receipt.launcher_source
        payload_sha256 = $receipt.payload_sha256
        authoring_receipt_sha256 = (Get-FileHash -LiteralPath (Join-Path $out 'INSTALLER-AUTHORING.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        candle = [ordered]@{ path = $candle; version = (Get-Item -LiteralPath $candle).VersionInfo.FileVersion }
        light = [ordered]@{ path = $light; version = (Get-Item -LiteralPath $light).VersionInfo.FileVersion }
        database_validation_sha256 = (Get-FileHash -LiteralPath (Join-Path $out 'INSTALLER-VALIDATION.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        default_ice_validation = $true
        signed = $false
        qualification = 'Development MSI build only; no automatic install, game launch, or runtime qualification.'
    }
    $build | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $out 'INSTALLER-BUILD.json') -Encoding utf8
    ((Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($msi)) |
        Set-Content -LiteralPath "$msi.sha256" -Encoding ascii
    Write-Output "PASS: development MSI built with default ICE validation: $msi"
} catch {
    Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
    throw
} finally {
    Stop-Transcript | Out-Null
}
