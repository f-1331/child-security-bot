#!/bin/bash
clear
echo "============================================================"
echo "         Child Security - Oila va Farzand Nazorati"
echo "             Apple Mac & macOS Desktop Dasturi"
echo "============================================================"
echo ""
echo "Ish stoliga (Desktop) Child Security dasturi o'rnatilmoqda..."

URL="https://child-security-bot.vercel.app"
DESKTOP_DIR="$HOME/Desktop"

# 1. macOS .webloc internet yorlig'i yaratish
cat << 'EOF' > "$DESKTOP_DIR/Child Security.webloc"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>URL</key>
    <string>https://child-security-bot.vercel.app</string>
</dict>
</plist>
EOF

# 2. osacompile orqali macOS mustaqil ilovasi (.app) yaratish
if command -v osacompile >/dev/null 2>&1; then
    osacompile -o "$DESKTOP_DIR/Child Security.app" -e 'do shell script "open -a \"Google Chrome\" --args --app=https://child-security-bot.vercel.app || open -a \"Microsoft Edge\" --args --app=https://child-security-bot.vercel.app || open https://child-security-bot.vercel.app"' 2>/dev/null
fi

echo ""
echo "[OK] Child Security Mac ish stolingizga muvaffaqiyatli o'rnatildi!"
echo "Ilova ishga tushirilmoqda..."
echo ""

# 3. Brauzer orqali to'liq ekranli dastur rejimida ochish
open -a "Google Chrome" --args --app="$URL" 2>/dev/null || \
open -a "Microsoft Edge" --args --app="$URL" 2>/dev/null || \
open "$URL"

exit 0
