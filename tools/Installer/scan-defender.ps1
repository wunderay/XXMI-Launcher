# Diagnostic only: never install, execute, extract, rebuild, or publish the suspect MSI.
# https://learn.microsoft.com/en-us/defender-endpoint/command-line-arguments-microsoft-defender-antivirus
# -DisableRemediation (custom scans only) ignores file exclusions, scans archives,
# applies no actions, and reports detections in command output, not event logs/UI.
# Exit 2 can mean either an unremediated threat OR a scan error; it is not a verdict.
# https://learn.microsoft.com/en-us/powershell/module/defender/get-mpthreatdetection
param([string]$OutputDirectory = 'work/defender-diagnostic')
$ErrorActionPreference = 'Stop'
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $out | Out-Null
$target = Join-Path $out 'original-installer.msi'
$report = [ordered]@{
    format = 1
    started_utc = [DateTime]::UtcNow.ToString('o')
    artifact_url = 'https://raw.githubusercontent.com/wunderay/XXMI-Launcher/f4e0880ed38eb338de6caf8d474fcc666489d5a4/development-builds/installer/1202392c4aea/XXMI-Launcher-Installer-Online-v2.4.10.msi'
    expected_sha256 = 'd6076f7200521a661b6f2d34d1759ab86a953dc7a2fb0e61343249d27e558781'
    actual_sha256 = $null
    postscan_sha256 = $null
    file_exists_after_scan = $null
    download_blocked_or_failed = $false
    outcome = 'not_scanned'
    stage = 'preflight'
    error = $null
    status_before = $null
    status_after = $null
    status_at_scan = $null
    cloud_settings_before = $null
    cloud_settings_after = $null
    detections_before = @()
    detections_after = @()
    new_detections = @()
    threat_names_from_output = @()
    scanner = $null
    signature_update = $null
    scan = $null
    limits = @(
        'Diagnostic custom scan only; no safety or false-positive conclusion.',
        'DisableRemediation does not disable real-time protection; download/hash can be blocked or quarantined.',
        'Custom-scan detections may be absent from Get-MpThreatDetection; raw scanner output is retained.',
        'Before/after history may include unrelated runner activity; inspect resources before attribution.',
        'No MSI execution, payload extraction, manual third-party sample submission, or Defender configuration changes.',
        'Defender cloud protection/sample submission follows existing runner settings; these are queried, never changed.'
    )
}

function Get-DetectorStatus {
    Get-MpComputerStatus -ErrorAction Stop | Select-Object AMServiceEnabled, AMRunningMode,
        AntivirusEnabled, RealTimeProtectionEnabled, AMEngineVersion, AMProductVersion,
        AntivirusSignatureVersion, AntivirusSignatureLastUpdated, AntivirusSignatureAge,
        DefenderSignaturesOutOfDate
}

function Get-CloudSettings {
    try {
        $settings = Get-MpPreference -ErrorAction Stop | Select-Object MAPSReporting,
            SubmitSamplesConsent, DisableBlockAtFirstSeen, CloudBlockLevel, CloudExtendedTimeout
        [pscustomobject]@{ available = $true; settings = $settings; error = $null }
    } catch {
        [pscustomobject]@{ available = $false; settings = $null; error = $_.Exception.Message }
    }
}

function Get-Detections {
    $names = @{}
    foreach ($threat in @(Get-MpThreat -ErrorAction Stop)) {
        $names[[string]$threat.ThreatID] = $threat.ThreatName
    }
    @(Get-MpThreatDetection -ErrorAction Stop | ForEach-Object {
        [pscustomobject]@{
            DetectionID = [string]$_.DetectionID
            ThreatID = [string]$_.ThreatID
            ThreatName = $names[[string]$_.ThreatID]
            InitialDetectionTime = $_.InitialDetectionTime
            LastThreatStatusChangeTime = $_.LastThreatStatusChangeTime
            ActionSuccess = $_.ActionSuccess
            Resources = @($_.Resources)
        }
    })
}

function Invoke-Scanner([string]$Executable, [string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds) {
    $stdout = Join-Path $out "$Name.stdout.log"
    $stderr = Join-Path $out "$Name.stderr.log"
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -NoNewWindow `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
    if ($timedOut) {
        $process.Kill()
        $process.WaitForExit()
    }
    $process.Refresh()
    [pscustomobject]@{
        arguments = $Arguments
        exit_code = $process.ExitCode
        timed_out = $timedOut
        stdout_file = [IO.Path]::GetFileName($stdout)
        stderr_file = [IO.Path]::GetFileName($stderr)
    }
}

$historyAvailable = $false
try {
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') {
        throw 'This diagnostic requires an ephemeral GitHub-hosted Windows runner.'
    }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Runner must already be elevated. This script never requests UAC.'
    }
    if ((Get-Service WinDefend -ErrorAction Stop).Status -ne 'Running') {
        throw 'Defender service is unavailable; it will not be started or enabled.'
    }
    $report.status_before = Get-DetectorStatus
    $report.cloud_settings_before = Get-CloudSettings
    if (-not $report.status_before.AMServiceEnabled -or -not $report.status_before.AntivirusEnabled) {
        throw 'Defender antivirus is unavailable.'
    }
    $report.detections_before = @(Get-Detections)
    $historyAvailable = $true
    $platform = Join-Path $env:ProgramData 'Microsoft/Windows Defender/Platform'
    $candidates = @(Get-ChildItem -LiteralPath $platform -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+(-\d+)?$' } |
        Sort-Object { [version](($_.Name -split '-')[0]) } -Descending |
        ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' })
    $candidates += Join-Path $env:ProgramFiles 'Windows Defender/MpCmdRun.exe'
    $scanner = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $scanner) { throw 'Preinstalled MpCmdRun.exe is unavailable.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $scanner
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw 'MpCmdRun.exe does not have a valid Microsoft signature.'
    }
    $report.scanner = [ordered]@{
        path = $scanner
        version = (Get-Item -LiteralPath $scanner).VersionInfo.FileVersion
        sha256 = (Get-FileHash -LiteralPath $scanner -Algorithm SHA256).Hash.ToLowerInvariant()
        signature_status = [string]$signature.Status
        signer = $signature.SignerCertificate.Subject
    }
    $report.stage = 'signature_update'
    $report.signature_update = Invoke-Scanner $scanner @('-SignatureUpdate') 'signature-update' 180
    if ($report.signature_update.timed_out -or $report.signature_update.exit_code -ne 0) {
        throw 'Defender signature update did not complete successfully.'
    }
    $status = Get-DetectorStatus
    if (-not $status.AntivirusEnabled -or -not $status.AMServiceEnabled -or
        -not $status.AntivirusSignatureVersion -or $status.AntivirusSignatureVersion -eq '0.0.0.0' -or
        -not $status.AntivirusSignatureLastUpdated -or $status.DefenderSignaturesOutOfDate -or
        ([DateTime]::UtcNow - ([DateTime]$status.AntivirusSignatureLastUpdated).ToUniversalTime()).TotalHours -gt 48) {
        throw 'Current Defender signatures are unavailable (maximum permitted age: 48 hours).'
    }
    $report.stage = 'download'
    if (Test-Path -LiteralPath $target) { throw 'Target already exists; use a fresh diagnostic directory.' }
    Invoke-WebRequest -UseBasicParsing -Uri $report.artifact_url -OutFile $target -TimeoutSec 180
    $report.stage = 'hash'
    $report.actual_sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($report.actual_sha256 -ne $report.expected_sha256) { throw 'Downloaded MSI SHA-256 does not match the exact original artifact.' }
    $report.stage = 'scan'
    $report.status_at_scan = $status
    $report.scan = Invoke-Scanner $scanner @('-Scan', '-ScanType', '3', '-File', ('"' + $target + '"'), '-DisableRemediation') 'scan' 600
    $scanText = (Get-Content -LiteralPath (Join-Path $out 'scan.stdout.log') -Raw) + "`n" +
        (Get-Content -LiteralPath (Join-Path $out 'scan.stderr.log') -Raw)
    # Windows-hosted runners use English scanner output. Unknown/localized output fails closed.
    $report.threat_names_from_output = @([regex]::Matches($scanText, '(?i)\b(?:Trojan|Virus|Worm|Backdoor|Exploit|HackTool|PUA|PWS|Ransom|SoftwareBundler|Adware|Spyware|Behavior|BrowserModifier|Misleading|RemoteAccess|SettingsModifier|Spammer|Spoofer|Tool):[A-Za-z0-9_./!+-]+') |
        ForEach-Object { $_.Value } | Sort-Object -Unique)
    $explicitDetection = $report.threat_names_from_output.Count -gt 0 -or
        $scanText -match '(?im)^\s*Threat(?:\s+Name)?\s*[:=]\s*\S' -or
        $scanText -match '(?im)^.*\b(?:threat|malware)\b.*\b(?:found|detected)\b' -and
        $scanText -notmatch '(?im)^.*\bno (?:threats?|malware)\b'
    if ($explicitDetection) {
        $report.outcome = 'detection'
    } elseif ($report.scan.timed_out -or $report.scan.exit_code -ne 0) {
        $report.outcome = 'inconclusive'
        $report.error = 'Scanner timed out or returned a nonzero code; exit 2 also includes scan errors.'
    } elseif ($scanText -match '(?im)^\s*Scan (?:finished|completed)\.?\s*$' -and
              $scanText -notmatch '(?i)\b(error|failed|failure)\b') {
        $report.outcome = 'scan_completed'
    } else {
        $report.outcome = 'inconclusive'
        $report.error = 'Scanner exit 0 lacked an unambiguous completion signal.'
    }
} catch {
    $report.error = $_.Exception.Message
    if ($report.stage -in @('download', 'hash') -and -not $report.actual_sha256) {
        $report.download_blocked_or_failed = $true
    }
    if ($report.outcome -ne 'detection') { $report.outcome = 'not_scanned' }
} finally {
    if ($historyAvailable) {
        try {
            $report.status_after = Get-DetectorStatus
            $report.cloud_settings_after = Get-CloudSettings
            $report.detections_after = @(Get-Detections)
            $beforeIds = @($report.detections_before | ForEach-Object { $_.DetectionID })
            $report.new_detections = @($report.detections_after | Where-Object { $_.DetectionID -notin $beforeIds })
            $matching = @($report.new_detections | Where-Object {
                @($_.Resources | Where-Object { $_.IndexOf($target, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
            })
            if ($matching.Count -gt 0) { $report.outcome = 'detection' }
            if ($report.outcome -eq 'scan_completed') {
                if (-not $report.status_after.AMServiceEnabled -or -not $report.status_after.AntivirusEnabled) {
                    throw 'Defender antivirus was unavailable after the scan.'
                }
                $report.file_exists_after_scan = Test-Path -LiteralPath $target -PathType Leaf
                if (-not $report.file_exists_after_scan) { throw 'Exact MSI is unavailable after the scan.' }
                $report.postscan_sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($report.postscan_sha256 -ne $report.expected_sha256) { throw 'MSI changed during the scan.' }
                $report.outcome = 'no_detection'
            }
        } catch {
            $report.error = "$($report.error) Final Defender/history/artifact verification failed: $($_.Exception.Message)".Trim()
            if ($report.outcome -ne 'detection') { $report.outcome = 'inconclusive' }
        }
    }
    $report.finished_utc = [DateTime]::UtcNow.ToString('o')
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $out 'report.json') -Encoding utf8
    Write-Output "Defender diagnostic: $($report.outcome); stage: $($report.stage); SHA-256: $($report.actual_sha256)"
    if ($report.error) { Write-Output $report.error }
}
if ($report.outcome -ne 'no_detection') { exit 1 }
