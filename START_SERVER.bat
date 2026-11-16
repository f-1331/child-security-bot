@echo off
title Child Security Bot - Unified Server & Render Watchdog
cd /d "%~dp0"
echo =======================================================
echo   Child Security System - Local Server + Auto Failback
echo =======================================================
start "Render Auto-Watchdog" /min python render_failback_watchdog.py
python run_unified_server.py
pause
