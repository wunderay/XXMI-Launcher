# Windows PowerShell 5.1 process-wrapper regression only: never load the diagnostic entry point.
param([string]$OutputDirectory = 'work/defender-regression')
$ErrorActionPreference = 'Stop'
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $out | Out-Null
$report = [ordered]@{ passed = $false; powershell_version = [string]$PSVersionTable.PSVersion; cases = @(); error = $null }
try {
    if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) {
        throw 'Regression must run in Windows PowerShell 5.1.'
    }
    $tokens = $null
    $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot 'scan-defender.ps1'), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw ($parseErrors.Message -join '; ') }
    $functions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Scanner'
    }, $false))
    if ($functions.Count -ne 1) { throw 'Expected exactly one Invoke-Scanner function.' }
    # Evaluate only this function definition. The downloader/scanner main body is not evaluated.
    . ([scriptblock]::Create($functions[0].Extent.Text))
    $cmd = Join-Path $env:SystemRoot 'System32/cmd.exe'
    foreach ($code in @(0, 2)) {
        $result = Invoke-Scanner $cmd @('/d', '/c', 'exit', [string]$code) "exit-$code" 10
        $report.cases += $result
        if ($null -eq $result.exit_code -or $result.exit_code -isnot [int] -or
            $result.exit_code -ne $code -or $result.timed_out) {
            throw "Expected explicit numeric exit code $code with no timeout."
        }
    }
    $output = Invoke-Scanner $cmd @('/d', '/c', '"echo scanner-test-stdout & echo scanner-test-stderr 1>&2 & exit /b 0"') 'output-flush' 10
    $report.cases += $output
    if ($output.exit_code -ne 0 -or $output.timed_out -or
        (Get-Content -LiteralPath (Join-Path $out $output.stdout_file) -Raw) -notmatch 'scanner-test-stdout' -or
        (Get-Content -LiteralPath (Join-Path $out $output.stderr_file) -Raw) -notmatch 'scanner-test-stderr') {
        throw 'Both redirected streams must flush before the wrapper returns.'
    }
    $powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $timeout = Invoke-Scanner $powershell @('-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '"Start-Sleep -Seconds 30"') 'owned-timeout' 1
    $elapsed.Stop()
    $report.cases += $timeout
    if (-not $timeout.timed_out -or $null -eq $timeout.exit_code -or $timeout.exit_code -isnot [int] -or
        $elapsed.Elapsed.TotalSeconds -ge 15) {
        throw 'Owned sleep process must time out, terminate, and return an explicit numeric exit code.'
    }
    $report.passed = $true
} catch {
    $report.error = $_.Exception.Message
} finally {
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $out 'regression.json') -Encoding utf8
    Write-Output "Defender process-wrapper regression passed: $($report.passed)"
    if ($report.error) { Write-Output $report.error }
}
if (-not $report.passed) { exit 1 }
