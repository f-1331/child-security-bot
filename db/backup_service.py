import os
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile
from config import settings

logger = logging.getLogger(__name__)

DB_PATH = Path(settings.BASE_DIR) / "parental_tracker.db"
BACKUP_DIR = Path(settings.BASE_DIR) / "backups"


def create_local_db_backup() -> Optional[Path]:
    """Create a local atomic backup of the SQLite database."""
    if not DB_PATH.exists():
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUP_DIR / f"parental_tracker_{timestamp}.db"
        shutil.copy2(DB_PATH, backup_file)
        # Keep only the latest 5 backups locally to save space
        backups = sorted(BACKUP_DIR.glob("parental_tracker_*.db"), key=os.path.getmtime)
        while len(backups) > 5:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except Exception:
                pass
        return backup_file
    except Exception as e:
        logger.error(f"Failed to create local DB backup: {e}")
        return None


async def send_db_backup_to_chat(bot: Bot, chat_id: int) -> bool:
    """Send database snapshot directly as a Telegram document."""
    if not DB_PATH.exists():
        await bot.send_message(chat_id=chat_id, text="⚠️ Baza fayli hali mavjud emas.")
        return False
    try:
        backup_file = create_local_db_backup()
        file_to_send = backup_file if backup_file and backup_file.exists() else DB_PATH
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Read stats from DB
        import sqlite3
        stats_str = ""
        try:
            conn = sqlite3.connect(str(file_to_send))
            cursor = conn.cursor()
            users_count = cursor.execute("SELECT count(*) FROM users").fetchone()[0]
            parents_count = cursor.execute("SELECT count(*) FROM users WHERE role='parent'").fetchone()[0]
            children_count = cursor.execute("SELECT count(*) FROM users WHERE role='child'").fetchone()[0]
            links_count = cursor.execute("SELECT count(*) FROM parent_child_links").fetchone()[0]
            conn.close()
            stats_str = (
                f"\n📊 <b>Baza ko'rsatkichlari:</b>\n"
                f"👥 Jami foydalanuvchilar: {users_count}\n"
                f"👨‍👩‍👧 Ota-onalar: {parents_count} | Farzandlar: {children_count}\n"
                f"🔗 Oila bog'lanishlari: {links_count} ta"
            )
        except Exception:
            pass

        doc = FSInputFile(path=str(file_to_send), filename=f"parental_tracker_{datetime.now().strftime('%Y%m%d')}.db")
        caption = (
            f"💾 <b>MA'LUMOTLAR BAZASI ZAXIRASI (BACKUP)</b>\n"
            f"📅 <b>Vaqt:</b> {now_str}\n"
            f"{stats_str}\n\n"
            f"🛡 <i>Ushbu fayl orqali barcha ma'lumotlar saqlab qolinadi.</i>"
        )
        await bot.send_document(chat_id=chat_id, document=doc, caption=caption, parse_mode="HTML")
        return True
    except Exception as e:
        logger.error(f"Failed to send DB backup to chat {chat_id}: {e}")
        await bot.send_message(chat_id=chat_id, text=f"❌ Zaxirani yuborishda xatolik: {e}")
        return False
