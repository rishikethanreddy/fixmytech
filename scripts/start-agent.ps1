# Starts the FixMyTech local agent for the hosted site (or a local one).
# Usage:  .\scripts\start-agent.ps1                      (uses the address saved in scripts\agent-url.txt)
#         .\scripts\start-agent.ps1 -Url https://my-site.web.app
param([string]$Url)

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$saved = Join-Path $PSScriptRoot "agent-url.txt"
if (-not (Test-Path $py)) { Write-Error "Virtual environment missing. See README 'Setup'."; exit 1 }
if (-not $Url -and (Test-Path $saved)) { $Url = (Get-Content $saved -Raw).Trim() }
if (-not $Url) { $Url = "http://127.0.0.1:8000" }
Write-Host "Starting the FixMyTech agent for $Url ... keep this window open while you scan. Close it to stop."
& $py -m fixmytech.agent --web-url $Url
