# ============================================================
# build_release.ps1 - bundle Flow into a distributable zip.
#
# Usage:
#   pwsh -File release/build_release.ps1            # implicit version 0.1.0
#   pwsh -File release/build_release.ps1 -Version 0.2.0
#
# Output: dist/Flow-v<version>.zip in the project root.
# ============================================================
param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"

# Resolve project root (parent of this script's directory).
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Output path.
$DistDir = Join-Path $ProjectRoot "dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}
$ZipName = "Flow-v$Version.zip"
$ZipPath = Join-Path $DistDir $ZipName
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

# Stage files in a temp folder so the zip layout is exactly what testers see
# after extraction. Excludes anything they don't need.
$Stage = Join-Path $env:TEMP "flow-release-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path $Stage | Out-Null

$Excludes = @(
    ".git", ".github", ".venv", "data", "dist", "release", "tests", "docs",
    ".claude", ".superpowers", "notes", "__pycache__", ".pytest_cache",
    ".sync-config.json", ".llm-config.json", "memory"
)

Get-ChildItem -Path $ProjectRoot -Force | ForEach-Object {
    if ($Excludes -notcontains $_.Name) {
        Copy-Item -Path $_.FullName -Destination $Stage -Recurse -Force
    }
}

# Strip __pycache__ folders that may live deep in copied subtrees.
Get-ChildItem -Path $Stage -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "__pycache__" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Zip with the project files at the root of the archive.
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force

Remove-Item $Stage -Recurse -Force

$SizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host ""
Write-Host "Built $ZipName  ($SizeMB MB)" -ForegroundColor Green
Write-Host "  $ZipPath"
Write-Host ""
Write-Host "Inspect with:  Expand-Archive '$ZipPath' -DestinationPath ./_inspect" -ForegroundColor DarkGray
