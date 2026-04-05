# Removes all files under bekrin-back/media/ (uploads, PDF renders, exam pages).
# Directories are kept. media/ is gitignored.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$media = Join-Path $here "..\media"
if (-not (Test-Path $media)) {
    Write-Host "No media folder at $media"
    exit 0
}
Get-ChildItem -Path $media -Recurse -File -ErrorAction SilentlyContinue | Remove-Item -Force
Write-Host "Cleaned files under $media"
