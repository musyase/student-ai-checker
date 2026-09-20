param(
  [string]$Target = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$bundleDir = Join-Path $projectRoot "src-tauri\binaries"

if (-not (Test-Path $venvPython)) {
  throw "Не найдено виртуальное окружение: $venvPython"
}

& $venvPython -m PyInstaller --noconfirm --clean --onefile `
  --name student-ai-checker-backend `
  --paths (Join-Path $projectRoot "src") `
  --add-data "$(Join-Path $projectRoot 'web');web" `
  --add-data "$(Join-Path $projectRoot 'scripts');scripts" `
  --add-data "$(Join-Path $projectRoot 'examples');examples" `
  (Join-Path $projectRoot "scripts\desktop_backend.py")

New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
$binary = Join-Path $projectRoot "dist\student-ai-checker-backend.exe"
if (-not (Test-Path $binary)) {
  throw "PyInstaller не создал $binary"
}
$targetName = if ($Target) { "student-ai-checker-backend-$Target.exe" } else { "student-ai-checker-backend-x86_64-pc-windows-msvc.exe" }
Copy-Item $binary (Join-Path $bundleDir $targetName) -Force
Write-Host "Backend sidecar готов: $bundleDir\$targetName"
