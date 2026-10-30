#!/bin/bash
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

google-chrome --app="$URL" 2>/dev/null || chromium --app="$URL" 2>/dev/null || chromium-browser --app="$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || firefox "$URL" 2>/dev/null

exit 0
