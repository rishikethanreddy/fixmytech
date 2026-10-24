# Start the local development stack in three windows:
#   1. MOCK FlipValue server   http://127.0.0.1:8090   (development only, fake valuations)
#   2. FixMyTech backend + UI  http://127.0.0.1:$Port
#   3. Local diagnostic agent  http://127.0.0.1:8765   (opens the pairing link in your browser)
#
# Usage:  .\scripts\run-dev.ps1            (port 8000)
#         .\scripts\run-dev.ps1 -Port 8010 (if 8000 is busy)
#         .\scripts\run-dev.ps1 -Mock      (also start the MOCK FlipValue: fake numbers, development only)
#
# Without -Mock, no resale numbers are shown until a real FlipValue API key is added on the Settings page.
param([int]$Port = 8000, [switch]$Mock)

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Virtual environment missing. See README 'Setup'."; exit 1 }

if (-not $Mock) {
  $fv = "`$env:FLIPVALUE_MODE='disabled';"
} else {
  Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command",
    "`$env:MOCK_FLIPVALUE_API_KEY='mock-dev-key'; & '$py' -m fixmytech.integrations.flipvalue.mock_server"
  $fv = "`$env:FLIPVALUE_MODE='mock'; `$env:FLIPVALUE_BASE_URL='http://127.0.0.1:8090'; `$env:FLIPVALUE_API_KEY='mock-dev-key'; `$env:FIXMYTECH_ALLOW_MOCK_VALUATION='true';"
}
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command",
  "$fv & '$py' -m fixmytech.server --port $Port"
Start-Sleep -Seconds 3
Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command",
  "& '$py' -m fixmytech.agent --web-url http://127.0.0.1:$Port"
Write-Host "Started. Web app: http://127.0.0.1:$Port  (close the windows to stop)"
