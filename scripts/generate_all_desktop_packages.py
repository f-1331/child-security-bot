import os
import zipfile
from pathlib import Path

base_dir = Path(__file__).resolve().parent.parent
downloads_dir = base_dir / "downloads"
webapp_downloads_dir = base_dir / "webapp" / "downloads"
downloads_dir.mkdir(parents=True, exist_ok=True)
webapp_downloads_dir.mkdir(parents=True, exist_ok=True)

# 1. WINDOWS SETUP BAT CONTENT (Foolproof, clean .url without trailing spaces, Edge/Chrome detection, fallback start)
windows_bat_content = """@echo off
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
set "DESK_PATH=%USERPROFILE%\\Desktop"
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" 2^>nul`) do (
    if exist "%%d" set "DESK_PATH=%%d"
)

:: 2. Ish stoliga toza Internet yorlig'ini (.url) yaratish (bo'sh joylarsiz)
(
echo [InternetShortcut]
echo URL=%URL%
echo IconIndex=0
echo IconFile=%SystemRoot%\\System32\\imageres.dll
) > "%DESK_PATH%\\Child Security.url"

:: Zaxira nusxalar
if exist "%USERPROFILE%\\Desktop" (
    copy /y "%DESK_PATH%\\Child Security.url" "%USERPROFILE%\\Desktop\\Child Security.url" >nul 2>&1
)
if exist "%USERPROFILE%\\OneDrive\\Desktop" (
    copy /y "%DESK_PATH%\\Child Security.url" "%USERPROFILE%\\OneDrive\\Desktop\\Child Security.url" >nul 2>&1
)

echo.
echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!
echo Dastur ishga tushirilmoqda...
echo.

:: 3. Microsoft Edge yoki Google Chrome orqali alohida toza ilova oynasida ochish
set "EDGE_X86=%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"
set "EDGE_64=%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"
set "CHROME_64=%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe"
set "CHROME_X86=%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe"
set "CHROME_USER=%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe"

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
"""

windows_url_content = """[InternetShortcut]
URL=https://child-security-bot.vercel.app
IconIndex=0
IconFile=C:\\Windows\\System32\\imageres.dll
"""

windows_readme_content = """Child Security - Windows Desktop O'rnatish Qo'llanmasi
======================================================
1. "Child Security.url" faylini ustiga 2 marta bosing - sayt darhol ochiladi!
2. Yoki "ChildSecurity_Desktop_Setup.bat" faylini ishga tushiring - u ish stolingizga (Desktop) doimiy yorliq yaratib beradi.
3. Agar Windows SmartScreen ogohlantirsa:
   "Подробнее" (Batafsil) -> "Выполнить в любом случае" (Baribir bajarish) tugmasini bosing.
"""

# 2. LINUX SETUP SH AND DESKTOP ENTRY CONTENT
linux_sh_content = """#!/bin/bash
clear
echo "============================================================"
echo "         Child Security - Oila va Farzand Nazorati"
echo "             Linux (Ubuntu / Debian / Fedora / Mint)"
echo "============================================================"
echo ""
echo "Linux ish stoliga (Desktop) Child Security o'rnatilmoqda..."

URL="https://child-security-bot.vercel.app"
DESK_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$DESK_DIR" "$APPS_DIR"

cat << 'EOF' > "$DESK_DIR/ChildSecurity.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Child Security
Comment=Bolalar xavfsizligi va ota-ona nazorati ilovasi
Exec=sh -c "google-chrome --app=https://child-security-bot.vercel.app 2>/dev/null || chromium --app=https://child-security-bot.vercel.app 2>/dev/null || chromium-browser --app=https://child-security-bot.vercel.app 2>/dev/null || xdg-open https://child-security-bot.vercel.app"
Icon=security-high
Terminal=false
Categories=Utility;Security;Network;
StartupNotify=true
EOF

chmod +x "$DESK_DIR/ChildSecurity.desktop"
cp "$DESK_DIR/ChildSecurity.desktop" "$APPS_DIR/ChildSecurity.desktop" 2>/dev/null
gio set "$DESK_DIR/ChildSecurity.desktop" metadata::trusted true 2>/dev/null || true

echo ""
echo "[OK] Child Security Linux ish stolingizga o'rnatildi!"
echo "Ilova ishga tushirilmoqda..."
echo ""

google-chrome --app="$URL" 2>/dev/null || \
chromium --app="$URL" 2>/dev/null || \
chromium-browser --app="$URL" 2>/dev/null || \
xdg-open "$URL" 2>/dev/null || \
firefox "$URL" 2>/dev/null

exit 0
"""

linux_desktop_content = """[Desktop Entry]
Version=1.0
Type=Application
Name=Child Security
Comment=Bolalar xavfsizligi va ota-ona nazorati ilovasi
Exec=sh -c "google-chrome --app=https://child-security-bot.vercel.app 2>/dev/null || chromium --app=https://child-security-bot.vercel.app 2>/dev/null || chromium-browser --app=https://child-security-bot.vercel.app 2>/dev/null || xdg-open https://child-security-bot.vercel.app"
Icon=security-high
Terminal=false
Categories=Utility;Security;Network;
StartupNotify=true
"""

linux_readme_content = """Child Security - Linux O'rnatish Qo'llanmasi
=============================================
1. Ushbu papkada terminalni oching.
2. Quyidagi buyruqni bering:
   chmod +x ChildSecurity_Linux_Setup.sh
   ./ChildSecurity_Linux_Setup.sh
3. Ish stolingizda "Child Security" paydo bo'ladi!
"""

# Write all to both downloads and webapp/downloads
for folder in [downloads_dir, webapp_downloads_dir]:
    # Windows BAT
    with open(folder / "ChildSecurity_Desktop_Setup.bat", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(windows_bat_content)
    
    # Windows ZIP package
    win_zip_path = folder / "ChildSecurity_Windows_Setup.zip"
    with zipfile.ZipFile(win_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ChildSecurity_Desktop_Setup.bat", windows_bat_content)
        zf.writestr("Child Security.url", windows_url_content)
        zf.writestr("Qo'llanma.txt", windows_readme_content)
    
    # Linux SH
    with open(folder / "ChildSecurity_Linux_Setup.sh", "w", encoding="utf-8", newline="\n") as f:
        f.write(linux_sh_content)
    
    # Linux Desktop Entry
    with open(folder / "ChildSecurity.desktop", "w", encoding="utf-8", newline="\n") as f:
        f.write(linux_desktop_content)
        
    # Linux ZIP package
    lin_zip_path = folder / "ChildSecurity_Linux_Setup.zip"
    with zipfile.ZipFile(lin_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zinfo_sh = zipfile.ZipInfo("ChildSecurity_Linux_Setup.sh")
        zinfo_sh.external_attr = 0o755 << 16
        zf.writestr(zinfo_sh, linux_sh_content)
        
        zinfo_desk = zipfile.ZipInfo("ChildSecurity.desktop")
        zinfo_desk.external_attr = 0o755 << 16
        zf.writestr(zinfo_desk, linux_desktop_content)
        
        zf.writestr("README.txt", linux_readme_content)

print("Generated all Windows and Linux packages successfully!")
