import urllib.request

urls = [
    'https://child-security-bot.vercel.app/download/parent-apk',
    'https://child-security-bot.vercel.app/download/child-apk',
    'https://child-security-bot.vercel.app/download/ios-profile',
    'https://child-security-bot.vercel.app/download/desktop-windows',
    'https://child-security-bot.vercel.app/downloads/ChildSecurity.mobileconfig',
    'https://child-security-bot.vercel.app/downloads/ChildSecurity_Desktop_Setup.bat',
    'https://child-security-site.onrender.com/download/parent-apk',
    'https://child-security-site.onrender.com/download/child-apk',
    'https://child-security-site.onrender.com/download/ios-profile',
    'https://child-security-site.onrender.com/download/desktop-windows',
]

for u in urls:
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req, timeout=15)
        print(f"SUCCESS {res.status}: {u} -> length: {res.headers.get('Content-Length')}, type: {res.headers.get('Content-Type')}")
    except Exception as e:
        print(f"FAILED: {u} -> {e}")
