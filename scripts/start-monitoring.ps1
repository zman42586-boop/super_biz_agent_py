param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot 'monitoring\runtime'
$assets = Get-Content -LiteralPath (Join-Path $repoRoot 'monitoring\assets.json') -Raw | ConvertFrom-Json
$exporterPath = Join-Path $runtimeRoot "windows_exporter-$($assets.windows_exporter.version)-amd64.exe"
$promPath = Join-Path $runtimeRoot "prometheus-$($assets.prometheus.version).windows-amd64\prometheus.exe"
$promtoolPath = Join-Path (Split-Path -Parent $promPath) 'promtool.exe'
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $exporterPath) -or -not (Test-Path -LiteralPath $promPath)) {
    throw 'Run scripts/install-monitoring.ps1 first.'
}
$pidFile = Join-Path $runtimeRoot 'processes.json'
if (Test-Path -LiteralPath $pidFile) {
    $old = @(Get-Content -LiteralPath $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json)
    $liveCount = 0
    foreach ($row in $old) {
        $running = Get-Process -Id $row.pid -ErrorAction SilentlyContinue
        if ($running -and $running.Path -eq $row.path -and [math]::Abs($running.StartTime.ToUniversalTime().Ticks - [long]$row.started) -lt 10000000) { $liveCount++ }
    }
    if ($liveCount -eq 2 -and $old.Count -eq 2) { Write-Host 'Managed windows_exporter and Prometheus are already running'; exit 0 }
    if ($liveCount -gt 0) { throw 'Partial monitoring stack detected; run stop-monitoring.ps1 first.' }
}
Push-Location $repoRoot
try {
    & $pythonPath scripts/configure_monitoring.py
    if ($LASTEXITCODE -ne 0) { throw 'Monitoring configuration failed' }
    & $promtoolPath check config (Join-Path $runtimeRoot 'prometheus.yml')
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Prometheus configuration' }
    foreach ($port in @(Get-Content -LiteralPath (Join-Path $runtimeRoot 'ports.json') -Raw | ConvertFrom-Json)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is occupied; refusing to start duplicate monitoring" }
    }
    $processes = @()
    try {
        $definitions = @(
            @{name='windows-exporter'; path=$exporterPath; args=@('--collectors.enabled=cpu,memory,os,system','--web.listen-address=127.0.0.1:9182')},
            @{name='prometheus'; path=$promPath; args=@('--config.file=monitoring/runtime/prometheus.yml','--storage.tsdb.path=monitoring/runtime/data','--storage.tsdb.retention.time=15d','--web.listen-address=127.0.0.1:9090')}
        )
        foreach ($item in $definitions) {
            $process = Start-Process -FilePath $item.path -ArgumentList $item.args -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot "$($item.name).out.log") -RedirectStandardError (Join-Path $runtimeRoot "$($item.name).err.log")
            $processes += @{pid=$process.Id; path=$item.path; name=$item.name; started=$process.StartTime.ToUniversalTime().Ticks.ToString()}
            Start-Sleep -Milliseconds 500
            if ($process.HasExited) { throw "$($item.name) exited; inspect monitoring/runtime logs" }
        }
        $processes | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
    } catch {
        foreach ($row in $processes) { Stop-Process -Id $row.pid -ErrorAction SilentlyContinue }
        throw
    }
    Write-Host 'Prometheus http://127.0.0.1:9090 ; windows_exporter :9182'
} finally { Pop-Location }
