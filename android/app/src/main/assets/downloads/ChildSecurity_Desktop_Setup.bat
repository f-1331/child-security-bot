@echo off
chcp 65001 >nul
title Child Security - Oila va Farzand Nazorati
cls
echo ============================================================
echo          Child Security - Oila va Farzand Nazorati
echo           Noutbuk va Windows uchun Desktop Dasturi
echo ============================================================
echo.
echo Ish stoliga (Desktop) dastur yorlig'i o'rnatilmoqda...

set "URL=https://child-security-bot.vercel.app"

:: 1. Haqiqiy Desktop manzilini aniqlash (OneDrive yoki oddiy Desktop)
set "DESK_PATH=%USERPROFILE%\Desktop"
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" 2^>nul`) do (
    if exist "%%d" set "DESK_PATH=%%d"
)

:: 2. Ish stoliga toza Internet yorlig'ini (.url) yaratish (bo'sh joylarsiz)
(
echo [InternetShortcut]
echo URL=%URL%
echo IconIndex=0
echo IconFile=%SystemRoot%\System32\imageres.dll
) > "%DESK_PATH%\Child Security.url"

:: Zaxira nusxalar
if exist "%USERPROFILE%\Desktop" (
    copy /y "%DESK_PATH%\Child Security.url" "%USERPROFILE%\Desktop\Child Security.url" >nul 2>&1
)
if exist "%USERPROFILE%\OneDrive\Desktop" (
    copy /y "%DESK_PATH%\Child Security.url" "%USERPROFILE%\OneDrive\Desktop\Child Security.url" >nul 2>&1
)

echo.
echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!
echo Dastur ishga tushirilmoqda...
echo.

:: 3. Microsoft Edge yoki Google Chrome orqali alohida toza ilova oynasida ochish
set "EDGE_X86=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
set "EDGE_64=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
set "CHROME_64=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "CHROME_X86=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
set "CHROME_USER=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if exist "%EDGE_X86%" (
    start "" "%EDGE_X86%" --app="%URL%"
    exit /b 0
)
if exist "%EDGE_64%" (
    start "" "%EDGE_64%" --app="%URL%"
    exit /b 0
)
if exist "%CHROME_64%" (
    start "" "%CHROME_64%" --app="%URL%"
    exit /b 0
)
if exist "%CHROME_X86%" (
    start "" "%CHROME_X86%" --app="%URL%"
    exit /b 0
)
if exist "%CHROME_USER%" (
    start "" "%CHROME_USER%" --app="%URL%"
    exit /b 0
)

:: Zaxira: Standart brauzerda ochish
start "" "%URL%"
exit /b 0
