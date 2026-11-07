import os
import shutil

def sync_assets():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(base_dir, "webapp")
    dst_dir = os.path.join(base_dir, "android", "app", "src", "main", "assets")

    if not os.path.exists(dst_dir):
        os.makedirs(dst_dir, exist_ok=True)

    synced_count = 0
    total_bytes = 0

    # Skip heavy desktop download bundles to keep APK lean (< 15MB)
    ignore_extensions = {".zip", ".bat", ".exe", ".command", ".sh", ".url", ".webloc"}
    ignore_dirs = {"downloads"}

    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        rel_path = os.path.relpath(root, src_dir)
        target_root = os.path.join(dst_dir, rel_path) if rel_path != "." else dst_dir
        os.makedirs(target_root, exist_ok=True)

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ignore_extensions:
                continue

            src_file = os.path.join(root, f)
            dst_file = os.path.join(target_root, f)

            # Copy if missing or modified
            if not os.path.exists(dst_file) or os.path.getmtime(src_file) != os.path.getmtime(dst_file) or os.path.getsize(src_file) != os.path.getsize(dst_file):
                shutil.copy2(src_file, dst_file)
                sz = os.path.getsize(dst_file)
                total_bytes += sz
                synced_count += 1
                print(f"Synced: {os.path.relpath(dst_file, base_dir)} ({sz:,} bytes)")

    print(f"\n[Asset Sync Complete] {synced_count} files synced ({total_bytes:,} bytes).")

if __name__ == "__main__":
    sync_assets()
