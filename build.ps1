# Build banner app: PyInstaller onedir + NSIS installer
# Usage (PowerShell 7 recommended, 5.1 works too):
#   pwsh -NoProfile -File build.ps1 [-SkipExe]
#   powershell -ExecutionPolicy Bypass -File build.ps1 [-SkipExe]
# The script is ASCII-only on purpose: Windows PowerShell 5.1 reads non-BOM
# UTF-8 scripts as ANSI, which breaks on non-ASCII text.
param(
    [switch]$SkipExe
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host ("==> Shell: PowerShell {0}" -f $PSVersionTable.PSVersion)

$p315 = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.15-64\python.exe"
$makensis = Join-Path $root "tools\nsis\nsis-3.10\makensis.exe"

if (-not (Test-Path $p315)) { throw "Python 3.15 not found: $p315" }
if (-not $SkipExe) {
    Write-Host "==> PyInstaller build"
    & $p315 -m PyInstaller Banner.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
}
if (-not (Test-Path (Join-Path $root "dist\Banner\Banner.exe"))) {
    throw "Missing dist\Banner\Banner.exe; run without -SkipExe first"
}

if (-not (Test-Path $makensis)) { throw "makensis not found: $makensis" }

# NSIS needs a UTF-8 BOM for Unicode scripts (Chinese UI text); repair if an
# editor saved it without BOM.
$nsi = Join-Path $root "installer.nsi"
$bytes = [IO.File]::ReadAllBytes($nsi)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
if (-not $hasBom) {
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    [IO.File]::WriteAllText($nsi, $text, (New-Object Text.UTF8Encoding($true)))
    Write-Host "==> Added UTF-8 BOM to installer.nsi"
}

New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\installer") | Out-Null
Write-Host "==> NSIS installer"
& $makensis /V2 "/DPROJECTDIR=$root" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }

Get-ChildItem (Join-Path $root "dist\installer") | ForEach-Object {
    Write-Host ("Output: {0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB))
}
