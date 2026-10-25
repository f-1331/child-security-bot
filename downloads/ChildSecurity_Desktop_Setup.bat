@echo off
chcp 65001 >nul
title Child Security - Noutbuk uchun dastur
echo ============================================================
echo          Child Security - Oila va Farzand Nazorati
echo           Noutbuk va Windows uchun Desktop Dasturi
echo ============================================================
echo.
echo Ish stoliga (Desktop) Child Security yorlig'i o'rnatilmoqda...
set URL=https://child-security-bot.vercel.app
set DESKTOP=%USERPROFILE%\Desktop\Child Security.url
echo [InternetShortcut] > "%DESKTOP%"
echo URL=%URL% >> "%DESKTOP%"
echo IconIndex=0 >> "%DESKTOP%"
echo IconFile=%SystemRoot%\System32\imageres.dll >> "%DESKTOP%"
echo.
echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!
echo Dastur ishga tushirilmoqda...
start msedge.exe --app="%URL%" 2>nul || start chrome.exe --app="%URL%" 2>nul || start "" "%URL%"
exit
