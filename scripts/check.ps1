# Run every quality gate. Usage (from the project root):  .\scripts\check.ps1
$ErrorActionPreference = "Continue"
$py = ".\.venv\Scripts\python.exe"
$failed = @()

Write-Host "== ruff (lint)" -ForegroundColor Cyan
& $py -m ruff check src tests scripts; if ($LASTEXITCODE -ne 0) { $failed += "ruff" }

Write-Host "== mypy (types)" -ForegroundColor Cyan
& $py -m mypy; if ($LASTEXITCODE -ne 0) { $failed += "mypy" }

Write-Host "== pytest (unit, integration, e2e; hardware tests deselected)" -ForegroundColor Cyan
& $py -m pytest -q -p no:logging; if ($LASTEXITCODE -ne 0) { $failed += "pytest" }

Write-Host "== knowledge base + OpenAPI export" -ForegroundColor Cyan
& $py -c "from fixmytech.domain.knowledge import load_knowledge_base as l; print(l().version)"; if ($LASTEXITCODE -ne 0) { $failed += "knowledge" }
& $py scripts\export_openapi.py; if ($LASTEXITCODE -ne 0) { $failed += "openapi" }

Write-Host "== pip-audit (dependency vulnerabilities, needs internet)" -ForegroundColor Cyan
& $py -m pip_audit -r requirements.lock.txt --progress-spinner off; if ($LASTEXITCODE -ne 0) { $failed += "pip-audit" }

if ($failed.Count) { Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "All checks passed." -ForegroundColor Green
