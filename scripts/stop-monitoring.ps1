param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot 'monitoring\runtime\processes.json'
if (Test-Path -LiteralPath $pidFile) {
    foreach ($row in @(Get-Content -LiteralPath $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json)) {
        $running = Get-Process -Id $row.pid -ErrorAction SilentlyContinue
        if ($running -and $running.Path -eq $row.path -and [math]::Abs($running.StartTime.ToUniversalTime().Ticks - [long]$row.started) -lt 10000000) {
            Stop-Process -Id $row.pid
            Write-Host "Stopped $($row.name)"
        }
    }
    Remove-Item -LiteralPath $pidFile
}
