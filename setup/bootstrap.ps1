# First-run setup for Windows (called by ytd.cmd). No admin rights needed.
#   1. Find Python 3.10+ on this PC, or download a portable Python
#      (python-build-standalone, checksum-verified) into the runtime folder.
#   2. Run setup\bootstrap.py with it (yt-dlp, FFmpeg, Deno - all inside this folder).
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Runtime
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest much faster
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$GitHubApi = if ($env:YTD_GITHUB_API) { $env:YTD_GITHUB_API } else { 'https://api.github.com' }
$PySeries  = '3.12'

function Say([string]$m) { Write-Host "==> $m" }
function Die([string]$m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

function Test-Python([string]$Exe, [string[]]$Extra = @()) {
    try {
        & $Exe @Extra -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    $portable = Join-Path $Runtime 'python\python.exe'
    if ((Test-Path $portable) -and (Test-Python $portable)) { return $portable }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '-3.13', '-3.12', '-3.11', '-3.10', '-3') {
            if (Test-Python 'py' @($v)) {
                $p = (& py $v -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
                if ($p) { return $p.Trim() }
            }
        }
    }
    foreach ($name in 'python', 'python3') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        # Skip the Microsoft Store alias stub in WindowsApps.
        if ($cmd -and $cmd.Source -notlike '*\WindowsApps\*' -and (Test-Python $cmd.Source)) { return $cmd.Source }
    }
    return $null
}

function Resolve-BasePython([string]$Exe) {
    # A Python inside a virtual environment is replaced by the real installation behind it.
    $code = 'import sys; b = getattr(sys, "_base_executable", ""); print(b if sys.prefix != sys.base_prefix and b else sys.executable)'
    try {
        $base = (& $Exe -c $code 2>$null | Select-Object -First 1)
        if ($base -and (Test-Path $base.Trim())) { return $base.Trim() }
    } catch { }
    return $Exe
}

function Get-PortablePython {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    $triple = if ($arch -eq 'ARM64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
    Say "Downloading a portable Python $PySeries for $triple (one time, about 30 MB)"
    try {
        $release = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'ytd-setup' } `
            -Uri "$GitHubApi/repos/astral-sh/python-build-standalone/releases/latest"
    } catch { Die "Could not reach GitHub to download Python: $($_.Exception.Message)" }

    $pattern = '^cpython-' + [regex]::Escape($PySeries) + '\.(\d+)\+\d+-' + [regex]::Escape($triple) + '-install_only_stripped\.tar\.gz$'
    $asset = $release.assets | Where-Object { $_.name -match $pattern } |
        Sort-Object { [int]([regex]::Match($_.name, $pattern).Groups[1].Value) } -Descending |
        Select-Object -First 1
    if (-not $asset) { Die "No portable Python build found for $triple." }
    $sums = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
    if (-not $sums) { Die 'The Python release has no SHA256SUMS file; refusing to continue.' }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ytd-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $archive = Join-Path $tmp 'python.tar.gz'
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $archive
        $sumText = (Invoke-WebRequest -UseBasicParsing -Uri $sums.browser_download_url).Content
        if ($sumText -is [byte[]]) { $sumText = [Text.Encoding]::UTF8.GetString($sumText) }
        $expected = $null
        foreach ($line in ($sumText -split "`n")) {
            $parts = $line.Trim() -split '\s+'
            if ($parts.Count -ge 2 -and $parts[-1].TrimStart('*') -eq $asset.name) { $expected = $parts[0]; break }
        }
        if (-not $expected) { Die "No checksum published for $($asset.name)." }
        $actual = (Get-FileHash -Algorithm SHA256 -Path $archive).Hash
        if ($actual -ne $expected.ToUpper()) { Die 'Python download failed checksum verification.' }
        Write-Host "    OK checksum verified: $($asset.name)"
        if (Test-Path (Join-Path $Runtime 'python')) { Remove-Item -Recurse -Force (Join-Path $Runtime 'python') }
        New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
        # tar.exe ships with Windows 10 (1803+) and 11.
        & tar.exe -xzf $archive -C $Runtime
        if ($LASTEXITCODE -ne 0) { Die 'Extracting Python failed.' }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    $py = Join-Path $Runtime 'python\python.exe'
    if (-not (Test-Path $py)) { Die 'Portable Python is incomplete.' }
    return $py
}

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
$Python = $null
# YTD_FORCE_PORTABLE_PYTHON=1 skips any installed Python (used by CI to test the download path).
if ($env:YTD_FORCE_PORTABLE_PYTHON -ne '1') { $Python = Find-Python }
if ($Python) { $Python = Resolve-BasePython $Python }
if (-not $Python) { $Python = Get-PortablePython }
Write-Host "    OK Python: $Python"
& $Python (Join-Path $Root 'setup\bootstrap.py') --root $Root --runtime $Runtime
exit $LASTEXITCODE
