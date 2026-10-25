import urllib.request
import sys
import time

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

urls = [
    # Android APKs
    ("Android Parent APK (Vercel)", "https://child-security-bot.vercel.app/download/parent-apk"),
    ("Android Child APK (Vercel)", "https://child-security-bot.vercel.app/download/child-apk"),
    
    # iOS (iPhone / iPad) Profile
    ("iOS MobileConfig (Vercel route)", "https://child-security-bot.vercel.app/download/ios-profile"),
    ("iOS MobileConfig static (Vercel static)", "https://child-security-bot.vercel.app/downloads/ChildSecurity.mobileconfig"),
    
    # Windows Desktop
    ("Windows BAT setup (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-windows"),
    ("Windows ZIP package (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-windows-zip"),
    
    # Apple Mac Desktop
    ("Mac Setup ZIP (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-mac"),
    
    # Linux Desktop
    ("Linux Setup ZIP (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-linux"),
    ("Linux Setup SH static", "https://child-security-bot.vercel.app/downloads/ChildSecurity_Linux_Setup.sh"),
    ("Linux Desktop static", "https://child-security-bot.vercel.app/downloads/ChildSecurity.desktop"),

    # Direct Render backend endpoints
    ("Render Parent APK", "https://child-security-site.onrender.com/download/parent-apk"),
    ("Render Child APK", "https://child-security-site.onrender.com/download/child-apk"),
    ("Render iOS MobileConfig", "https://child-security-site.onrender.com/download/ios-profile"),
    ("Render Windows BAT", "https://child-security-site.onrender.com/download/desktop-windows"),
]

print("=== CHECKING ALL PLATFORMS (WINDOWS, MAC, LINUX, IOS, ANDROID) ===")
all_passed = True
for name, u in urls:
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        res = urllib.request.urlopen(req, timeout=20)
        content_type = res.headers.get("Content-Type", "")
        content_length = res.headers.get("Content-Length", "unknown")
        print(f"✅ [PASS] {name}\n    URL: {u}\n    Status: {res.status} | Length: {content_length} bytes | Type: {content_type}\n")
    except Exception as e:
        all_passed = False
        print(f"❌ [FAIL] {name}\n    URL: {u}\n    Error: {e}\n")

if all_passed:
    print("🎉 ALL DOWNLOAD CHANNELS ACROSS WINDOWS, MAC, LINUX, IOS, ANDROID ARE 100% OPERATIONAL!")
else:
    print("⚠️ SOME PLATFORMS NEED ATTENTION!")
