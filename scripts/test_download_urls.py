import urllib.request
import sys
import time

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

urls = [
    # Android APKs
    ("Android Parent APK (Vercel redirect)", "https://child-security-bot.vercel.app/download/parent-apk"),
    ("Android Child APK (Vercel redirect)", "https://child-security-bot.vercel.app/download/child-apk"),
    ("Android APK generic (Vercel redirect)", "https://child-security-bot.vercel.app/download/apk"),
    
    # iOS (iPhone / iPad) Profile
    ("iOS MobileConfig (Vercel route)", "https://child-security-bot.vercel.app/download/ios-profile"),
    ("iOS MobileConfig static (Vercel static)", "https://child-security-bot.vercel.app/downloads/ChildSecurity.mobileconfig"),
    
    # Windows Desktop
    ("Windows BAT setup (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-windows"),
    ("Windows BAT setup static (Vercel static)", "https://child-security-bot.vercel.app/downloads/ChildSecurity_Desktop_Setup.bat"),
    
    # Apple Mac Desktop
    ("Mac Setup ZIP (Vercel route)", "https://child-security-bot.vercel.app/download/desktop-mac"),
    ("Mac Setup ZIP static (Vercel static)", "https://child-security-bot.vercel.app/downloads/ChildSecurity_Mac_Setup.zip"),
    ("Mac Setup Command static (Vercel static)", "https://child-security-bot.vercel.app/downloads/ChildSecurity_Mac_Setup.command"),

    # Direct Render backend endpoints
    ("Render Parent APK", "https://child-security-site.onrender.com/download/parent-apk"),
    ("Render Child APK", "https://child-security-site.onrender.com/download/child-apk"),
    ("Render iOS MobileConfig", "https://child-security-site.onrender.com/download/ios-profile"),
    ("Render Windows BAT", "https://child-security-site.onrender.com/download/desktop-windows"),
]

print("=== CHECKING ALL DOWNLOAD CHANNELS ACROSS ALL PLATFORMS ===")
all_passed = True
for name, u in urls:
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)'})
        res = urllib.request.urlopen(req, timeout=20)
        content_type = res.headers.get("Content-Type", "")
        content_length = res.headers.get("Content-Length", "unknown")
        print(f"✅ [PASS] {name}\n    URL: {u}\n    Status: {res.status} | Length: {content_length} bytes | Type: {content_type}\n")
    except Exception as e:
        all_passed = False
        print(f"❌ [FAIL] {name}\n    URL: {u}\n    Error: {e}\n")

if all_passed:
    print("🎉 ALL DOWNLOAD PLATFORMS ARE 100% OPERATIONAL!")
else:
    print("⚠️ SOME PLATFORMS NEED ATTENTION!")
