import os
import base64
from pathlib import Path

def generate():
    base_dir = Path(__file__).resolve().parent.parent
    icon_path = base_dir / "webapp" / "apple-touch-icon.png"
    
    with open(icon_path, "rb") as f:
        icon_b64 = base64.b64encode(f.read()).decode("ascii")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>FullScreen</key>
            <true/>
            <key>Icon</key>
            <data>
{icon_b64}
            </data>
            <key>IsRemovable</key>
            <true/>
            <key>Label</key>
            <string>Child Security</string>
            <key>PayloadDescription</key>
            <string>Child Security — Bolalar xavfsizligi va ota-ona nazorati rasmiy ilovasi</string>
            <key>PayloadDisplayName</key>
            <string>Child Security</string>
            <key>PayloadIdentifier</key>
            <string>com.childsecurity.app.webclip</string>
            <key>PayloadType</key>
            <string>com.apple.webClip.managed</string>
            <key>PayloadUUID</key>
            <string>4e9f3b8b-18a6-4b82-9f3b-63f25c798001</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
            <key>Precomposed</key>
            <true/>
            <key>URL</key>
            <string>https://child-security-bot.vercel.app/?source=ios-profile</string>
        </dict>
    </array>
    <key>PayloadDisplayName</key>
    <string>Child Security Ilovasi</string>
    <key>PayloadIdentifier</key>
    <string>com.childsecurity.app.profile</string>
    <key>PayloadOrganization</key>
    <string>Child Security</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>3a7e2b6a-07a5-4f71-8e2a-52e14b687002</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>
"""
    downloads_dir = base_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    out_file = downloads_dir / "ChildSecurity.mobileconfig"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    
    # Also copy to webapp/downloads/ for static serving if needed
    webapp_downloads = base_dir / "webapp" / "downloads"
    webapp_downloads.mkdir(parents=True, exist_ok=True)
    with open(webapp_downloads / "ChildSecurity.mobileconfig", "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Successfully generated {out_file} ({len(content)} bytes)")

if __name__ == "__main__":
    generate()
