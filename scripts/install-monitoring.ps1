param()
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot 'monitoring\runtime'
$assets = Get-Content -LiteralPath (Join-Path $repoRoot 'monitoring\assets.json') -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
foreach ($name in @('prometheus', 'windows_exporter')) {
    $asset = $assets.$name
    $download = Join-Path $runtimeRoot ([IO.Path]::GetFileName($asset.url))
    if (-not (Test-Path -LiteralPath $download) -or (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $asset.sha256) {
        Write-Host "Downloading $name $($asset.version) from its official GitHub release..."
        Invoke-WebRequest -Uri $asset.url -OutFile $download
    }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $asset.sha256) {
        throw "SHA256 mismatch for $name; refusing to execute"
    }
    if ($name -eq 'prometheus') {
        Expand-Archive -LiteralPath $download -DestinationPath $runtimeRoot -Force
    }
}
Write-Host 'Portable binaries installed under monitoring/runtime. No Windows service or firewall rule was installed.'
