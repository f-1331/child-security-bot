import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s : %(message)s"
)
logger = logging.getLogger("render_watchdog")

BASE_DIR = Path(__file__).resolve().parent
RENDER_HEALTH_URL = "https://child-security-site.onrender.com/health"
OWNER_TELEGRAM_ID = 6707839718
CHECK_INTERVAL_SECONDS = 300  # Check every 5 minutes

def is_vercel_pointing_to_render() -> bool:
    vercel_path = BASE_DIR / "vercel.json"
    if not vercel_path.exists():
        return False
    with open(vercel_path, "r", encoding="utf-8") as f:
        content = f.read()
    return "child-security-site.onrender.com" in content

def switch_vercel_to_render():
    vercel_path = BASE_DIR / "vercel.json"
    with open(vercel_path, "r", encoding="utf-8") as f:
        content = f.read()

    old_pattern = r'https://[a-zA-Z0-9\.\-]+\.(?:onrender\.com|trycloudflare\.com)'
    new_content = re.sub(old_pattern, "https://child-security-site.onrender.com", content)

    with open(vercel_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    logger.info("Updated vercel.json back to Render URL.")

    # Git commit and push
    subprocess.run(["git", "add", "vercel.json"], cwd=str(BASE_DIR), check=True)
    subprocess.run(["git", "commit", "-m", "chore(render): automatically restored Render backend route"], cwd=str(BASE_DIR), check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(BASE_DIR), check=True)
    logger.info("✅ Pushed restored Render route to GitHub for instant Vercel switch!")


async def notify_owner_telegram():
    from config import settings
    from aiogram import Bot
    try:
        bot = Bot(token=settings.BOT_TOKEN)
        text = (
            "🎉 <b>Render serveri muvaffaqiyatli tiklandi!</b>\n\n"
            "Cloudflare tuneli o'rniga Vercel qaytadan rasmiy <code>https://child-security-site.onrender.com</code> serveriga ulandi.\n"
            "Barcha xizmatlar (SOS, GPS, WebApp, Botlar) bulutda 24/7 uzluksiz ishlamoqda!"
        )
        await bot.send_message(chat_id=OWNER_TELEGRAM_ID, text=text, parse_mode="HTML")
        await bot.session.close()
        logger.info("Telegram notification sent to owner about Render restoration.")
    except Exception as e:
        logger.warning(f"Could not send Telegram notification to owner: {e}")


async def check_render_health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(RENDER_HEALTH_URL)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") in ("healthy", "ok"):
                    return True
    except Exception as e:
        logger.debug(f"Render health check failed ({RENDER_HEALTH_URL}): {e}")
    return False


async def run_watchdog():
    logger.info(f"Render Auto-Failback Watchdog started. Checking {RENDER_HEALTH_URL} every {CHECK_INTERVAL_SECONDS}s ...")
    while True:
        try:
            is_up = await check_render_health()
            if is_up:
                logger.info("🎉 RENDER IS UP AND HEALTHY!")
                if not is_vercel_pointing_to_render():
                    logger.info("Switching vercel.json back to Render...")
                    switch_vercel_to_render()
                    await notify_owner_telegram()
                    marker = BASE_DIR / "RENDER_RESTORED.txt"
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(f"Render restored at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    logger.info("Auto-failback to Render completed successfully!")
                else:
                    logger.info("Vercel is already configured to point to Render.")
            else:
                logger.info(f"Render is currently not ready yet. Retrying in {CHECK_INTERVAL_SECONDS}s ...")
        except Exception as e:
            logger.error(f"Error in watchdog iteration: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_watchdog())
