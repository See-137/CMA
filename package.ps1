$src = $PSScriptRoot
$out = Join-Path (Split-Path $src) "CMA-v0.1.zip"
$exclude = '\.git\\|\.venv\\|\.ruff_cache\\|\.claude\\|node_modules\\|\\dist\\|__pycache__\\|\\data\\'

if (Test-Path $out) { Remove-Item $out }

# Create a temp staging folder with the right name
$staging = Join-Path $env:TEMP "CMA-package-staging"
$stageDir = Join-Path $staging "CMA"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

# Copy only clean files
$files = Get-ChildItem $src -Recurse -File | Where-Object {
    $_.FullName -notmatch $exclude -and
    $_.Name -ne 'package-lock.json' -and
    $_.Extension -ne '.pyc' -and
    $_.Name -ne 'package.ps1'
}

foreach ($f in $files) {
    $rel = $f.FullName.Substring($src.Length + 1)
    $dest = Join-Path $stageDir $rel
    $destDir = Split-Path $dest
    if (!(Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item $f.FullName $dest
}

# Zip the staging folder — extract gives CMA\ at the top level
Compress-Archive -Path $stageDir -DestinationPath $out -Force

# Cleanup
Remove-Item $staging -Recurse -Force

$size = [math]::Round((Get-Item $out).Length / 1KB)
Write-Host "Created: $out ($size KB, $($files.Count) files)"
Write-Host "Extract gives: CMA\install.bat, CMA\cma.bat, etc."
