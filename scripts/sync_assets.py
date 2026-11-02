import shutil
import os

def sync_assets():
    src_dir = "webapp"
    dst_dir = os.path.join("android", "app", "src", "main", "assets")
    
    for filename in ["app.js", "index.html", "style.css"]:
        s = os.path.join(src_dir, filename)
        d = os.path.join(dst_dir, filename)
        if os.path.exists(s):
            shutil.copy2(s, d)
            print(f"Synced {s} -> {d} ({os.path.getsize(d)} bytes)")

if __name__ == "__main__":
    sync_assets()
