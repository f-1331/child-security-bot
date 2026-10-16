@echo off
title Child Security Bot Server
cd /d "%~dp0"
echo ========================================================
echo Starting Child Security Bot ^& Mini App Server...
echo ========================================================
"C:\Users\hp\AppData\Local\Programs\Python\Python311\python.exe" main.py
pause
