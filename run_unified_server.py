import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s : %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "unified_server.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("unified_server")


def start_tunnel():
    """Start an HTTPS public tunnel with localtunnel (fixed subdomain child-security-uz) or Cloudflare fallback."""
    # 1. Try localtunnel with fixed subdomain child-security-uz
    logger.info("Starting localtunnel on port 8000 with subdomain 'child-security-uz'...")
    try:
        cmd = ["cmd.exe", "/c", "npx", "--yes", "localtunnel", "--port", "8000", "--subdomain", "child-security-uz"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        url = None
        start = time.time()
        while time.time() - start < 15:
            if proc.poll() is not None:
                logger.warning(f"localtunnel exited early with code {proc.poll()}")
                break
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            m = re.search(r"https://[a-zA-Z0-9-]+\.loca\.lt", line)
            if m:
                url = m.group(0)
                logger.info(f"✅ localtunnel live: {url}")
                with open(BASE_DIR / "tunnel_url.txt", "w", encoding="utf-8") as f:
                    f.write(url)
                return proc, url
        if proc.poll() is None:
            proc.terminate()
    except Exception as e:
        logger.warning(f"localtunnel fixed subdomain failed: {e}")

    # 2. Try random subdomain localtunnel
    logger.info("Attempting random subdomain localtunnel...")
    try:
        cmd = ["cmd.exe", "/c", "npx", "--yes", "localtunnel", "--port", "8000"]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        url = None
        start = time.time()
        while time.time() - start < 15:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            m = re.search(r"https://[a-zA-Z0-9-]+\.loca\.lt", line)
            if m:
                url = m.group(0)
                logger.info(f"✅ localtunnel live: {url}")
                with open(BASE_DIR / "tunnel_url.txt", "w", encoding="utf-8") as f:
                    f.write(url)
                return proc, url
        if proc.poll() is None:
            proc.terminate()
    except Exception as e:
        logger.warning(f"Random localtunnel failed: {e}")

    # 3. Fallback: Cloudflare Tunnel if available
    cf_binary = BASE_DIR / "cloudflared.exe"
    if cf_binary.exists():
        logger.info("Attempting Cloudflare Tunnel fallback...")
        log_path = BASE_DIR / "cloudflared.log"
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

        cmd = [
            str(cf_binary),
            "tunnel",
            "--url", "http://127.0.0.1:8000",
            "--no-autoupdate",
            "--logfile", str(log_path)
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start = time.time()
        while time.time() - start < 15:
            if proc.poll() is not None:
                logger.warning(f"Cloudflare tunnel exited early with code {proc.poll()}")
                break
            if log_path.exists():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        c = f.read()
                    if "status 429" in c:
                        logger.warning("Cloudflare tunnel rate limited (HTTP 429).")
                        proc.terminate()
                        break
                    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", c)
                    if m:
                        url = m.group(0)
                        logger.info(f"✅ Cloudflare Tunnel live: {url}")
                        with open(BASE_DIR / "tunnel_url.txt", "w", encoding="utf-8") as f:
                            f.write(url)
                        return proc, url
                except Exception:
                    pass
            time.sleep(0.5)

        if proc.poll() is None:
            proc.terminate()

    logger.error("❌ All tunnel providers failed.")
    return None, None


def update_vercel_json_and_push(tunnel_url: str):
    """Update vercel.json routes to forward to the active tunnel and git push."""
    vercel_path = BASE_DIR / "vercel.json"
    if not vercel_path.exists():
        logger.warning("vercel.json not found, skipping sync.")
        return

    try:
        with open(vercel_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Replace any previous target url in /api/ and /uploads/
        old_pattern = r'https://[a-zA-Z0-9\.\-]+(?:\.onrender\.com|\.trycloudflare\.com|\.app\.lhr\.life|\.loca\.lt)'
        new_content = re.sub(old_pattern, tunnel_url, content)

        if new_content != content:
            with open(vercel_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info(f"Updated vercel.json with new live tunnel URL: {tunnel_url}")

            # Git commit and push with non-blocking stdin
            subprocess.run(["git", "add", "vercel.json"], cwd=str(BASE_DIR), stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True)
            subprocess.run(["git", "commit", "-m", f"chore(tunnel): route API traffic to {tunnel_url}"], cwd=str(BASE_DIR), stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(BASE_DIR), stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
            subprocess.run(["git", "push", "origin", "main"], cwd=str(BASE_DIR), stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=True)
            logger.info("✅ Pushed new tunnel route to GitHub for instant Vercel sync!")
        else:
            logger.info("vercel.json is already up to date.")
    except Exception as e:
        logger.warning(f"Could not automatically sync vercel.json: {e}")


async def monitor_tunnel(proc):
    """Keep tunnel alive and continuously drain stdout so pipe buffers never overflow."""
    while True:
        if proc.poll() is not None:
            logger.warning(f"Tunnel process exited with code {proc.poll()}. Restarting tunnel in 5s...")
            await asyncio.sleep(5)
            new_proc, new_url = start_tunnel()
            if new_proc and new_url:
                proc = new_proc
                os.environ["BACKEND_URL"] = new_url
                update_vercel_json_and_push(new_url)
                logger.info(f"✅ Tunnel restarted successfully: {new_url}")
            else:
                logger.error("Could not restart tunnel. Retrying in 10s...")
                await asyncio.sleep(10)
                continue
        if proc.stdout:
            try:
                line = await asyncio.to_thread(proc.stdout.readline)
                if not line:
                    await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(5)


async def main_unified():
    tunnel_proc, tunnel_url = start_tunnel()
    if not tunnel_url:
        logger.error("Cannot proceed without an active tunnel.")
        sys.exit(1)

    # Set BACKEND_URL in environment for local process
    os.environ["BACKEND_URL"] = tunnel_url

    # Update vercel.json so child-security-bot.vercel.app proxies to local server
    update_vercel_json_and_push(tunnel_url)

    # Start monitor task for tunnel
    tunnel_task = asyncio.create_task(monitor_tunnel(tunnel_proc))

    # Also start HA failback watchdog task in background
    try:
        from render_failback_watchdog import run_ha_watchdog
        watchdog_task = asyncio.create_task(run_ha_watchdog())
        logger.info("✅ HA Failover Watchdog attached to unified event loop.")
    except Exception as e:
        logger.warning(f"Could not attach HA watchdog: {e}")
        watchdog_task = None

    # Supervisor loop for main application
    while True:
        try:
            import main
            logger.info("🚀 Launching main application (FastAPI + Telegram Bots + Scheduler)...")
            await main.main()
        except asyncio.CancelledError:
            logger.info("Unified server cancelled.")
            break
        except Exception as e:
            logger.error(f"Main application raised error: {e}. Restarting in 5s...", exc_info=True)
            await asyncio.sleep(5)

    if tunnel_proc and tunnel_proc.poll() is None:
        tunnel_proc.terminate()
    tunnel_task.cancel()
    if watchdog_task:
        watchdog_task.cancel()
    logger.info("Unified server shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main_unified())
    except KeyboardInterrupt:
        logger.info("Terminated by user.")
