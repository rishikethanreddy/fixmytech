@echo off
rem Double-click to start the FixMyTech agent. The address it serves is in scripts\agent-url.txt.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start-agent.ps1"
pause
