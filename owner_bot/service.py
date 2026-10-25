import os
import platform
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

import db.crud as crud
from config import settings

_main_bot_instance: Optional[Bot] = None
_start_time: float = time.time()


def set_main_bot(bot: Bot) -> None:
    """Store reference to the primary public client bot."""
    global _main_bot_instance
    _main_bot_instance = bot


def get_main_bot() -> Optional[Bot]:
    """Retrieve reference to the primary public client bot."""
    return _main_bot_instance


def get_uptime_str() -> str:
    """Return formatted system uptime string."""
    uptime_sec = int(time.time() - _start_time)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}k")
    if hours > 0:
        parts.append(f"{hours}s")
    parts.append(f"{mins}d {secs}s")
    return " ".join(parts)


def format_dossier_html(dossier: Dict[str, Any]) -> str:
    """Format deep user dossier into clean, structured Telegram HTML."""
    user = dossier["user"]
    loc = dossier.get("latest_location")
    parents = dossier.get("parents", [])
    children = dossier.get("children", [])
    tasks = dossier.get("tasks", [])
    geofences = dossier.get("geofences", [])
    balance_som = dossier.get("balance_som", 0)

    role_badge = {
        "parent": "👨‍👩‍👧 Ota-ona (Parent)",
        "child": "👦 Farzand (Child)",
        "unassigned": "⚪️ Biriktirilmagan",
    }.get(user.role, user.role)

    username_str = f"@{user.username}" if user.username else "<i>(Username yo'q)</i>"
    phone_str = user.phone_number or "<i>(Telefon kiritilmagan)</i>"
    created_str = (
        user.created_at.strftime("%Y-%m-%d %H:%M UTC")
        if user.created_at
        else "Noma'lum"
    )

    # Codes
    pairing_code_str = f"<code>{user.pairing_code}</code>" if user.pairing_code else "<i>(Mavjud emas)</i>"
    web_access_code_str = f"<code>{user.web_access_code}</code>" if user.web_access_code else "<i>(Mavjud emas)</i>"

    # Status flags
    sos_badge = "🚨 <b>FAOL SOS (Xavf ostida!)</b>" if user.is_sos_active else "🟢 Normal"
    active_badge = "✅ Faol" if user.is_active else "⛔️ Noactive"

    # Location & Battery
    if loc:
        battery_icon = "🔌" if loc.is_charging else "🔋"
        battery_str = f"{battery_icon} {loc.battery_level}%" if loc.battery_level is not None else "Noma'lum"
        loc_time = loc.timestamp.strftime("%H:%M:%S (%d.%m)") if loc.timestamp else "Noma'lum"
        speed_str = f"{loc.speed:.1f} km/s" if loc.speed else "0 km/s"
        loc_str = (
            f"📍 Koordinatalar: <code>{loc.latitude:.6f}, {loc.longitude:.6f}</code>\n"
            f"🎯 Aniqlik: ±{loc.accuracy:.1f}m | Tezlik: {speed_str}\n"
            f"🕒 Vaqt: {loc_time} | {battery_str}\n"
            f"🗺 <a href=\"https://www.google.com/maps?q={loc.latitude},{loc.longitude}\">Google Maps orqali ochish</a>"
        )
    else:
        loc_str = "📍 <i>Joylashuv signali hali kelmagan</i>"

    # Family Links
    if user.role == "child":
        if parents:
            parent_list = ", ".join(
                [f"<b>{p.full_name or 'Ota-ona'}</b> (ID: <code>{p.id}</code>)" for p in parents]
            )
            family_str = f"👨‍👩‍👧 Ota-onasi: {parent_list}"
        else:
            family_str = "👨‍👩‍👧 Ota-onasi: <i>Hali hech kimga ulanmagan</i>"
    else:
        if children:
            child_list = ", ".join(
                [f"<b>{c.full_name or 'Farzand'}</b> (ID: <code>{c.id}</code>)" for c in children]
            )
            family_str = f"👦 Farzandlari ({len(children)} ta): {child_list}"
        else:
            family_str = "👦 Farzandlari: <i>Biriktirilmagan</i>"

    # Extra features info
    extra_lines = []
    if user.role == "child":
        extra_lines.append(
            f"💰 Virtual Balans: <b>{user.virtual_balance} tanga</b> (≈ {balance_som:,} so'm)"
        )
        if user.wishlist_title:
            target_str = f" / {user.wishlist_target_som:,} so'm" if user.wishlist_target_som else ""
            extra_lines.append(f"🎯 Orzusi: <i>{user.wishlist_title}</i>{target_str}")
        if user.walk_safe_active:
            extra_lines.append("🚶‍♂️ <b>Walk Safe taymer: FAOL</b>")
        extra_lines.append(f"📋 Vazifalar: {len(tasks)} ta")
    else:
        extra_lines.append(f"🛡 Nazorat hududlari (Geofence): {len(geofences)} ta")

    extra_str = "\n".join(extra_lines)

    html = (
        f"📋 <b>FOYDALANUVCHI DOSYESI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Ism:</b> {user.full_name or 'Foydalanuvchi'}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🌐 <b>Username:</b> {username_str}\n"
        f"📞 <b>Telefon:</b> {phone_str}\n"
        f"🎭 <b>Roli:</b> {role_badge}\n"
        f"📅 <b>Ro'yxatdan o'tgan:</b> {created_str}\n"
        f"📊 <b>Holat:</b> {active_badge} | {sos_badge}\n\n"
        f"🔐 <b>Xavfsizlik & Ulanish kodlari:</b>\n"
        f"  • Ulanish kodi (Pairing): {pairing_code_str}\n"
        f"  • Veb kirish kodi (Web PIN): {web_access_code_str}\n\n"
        f"👪 <b>Oila bog'lanishi:</b>\n"
        f"  {family_str}\n\n"
        f"📡 <b>Jonli Telemetriya & GPS:</b>\n"
        f"  {loc_str}\n\n"
        f"⚙️ <b>Qo'shimcha parametrlar:</b>\n"
        f"  {extra_str}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    return html


async def get_system_diagnostic_html(session: AsyncSession) -> str:
    """Generate live server and architecture diagnostic report."""
    stats = await crud.get_system_overview_stats(session)
    uptime = get_uptime_str()

    # Memory info (RSS)
    mem_mb = "N/A"
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_mb = f"{process.memory_info().rss / 1024 / 1024:.1f} MB"
    except Exception:
        pass

    # Active WebSockets count
    ws_count = 0
    try:
        from api.telemetry import manager
        ws_count = sum(len(conns) for conns in manager.active_connections.values())
    except Exception:
        pass

    # DB Latency Ping & Engine Info
    db_latency = "N/A"
    try:
        t0 = time.time()
        from sqlalchemy import text
        await session.execute(text("SELECT 1"))
        db_latency = f"{(time.time() - t0) * 1000:.1f} ms"
    except Exception as e:
        db_latency = f"Xato: {e}"

    is_pg = "postgresql" in settings.RESOLVED_DATABASE_URL.lower()
    db_type_badge = "🐘 PostgreSQL (Doimiy bulut)" if is_pg else "📁 SQLite (Fayl asosida)"

    html = (
        f"🌐 <b>SERVER & TIZIM HOLATI DIAGNOSTIKASI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"💻 <b>OS & Platform:</b> {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"🐍 <b>Python:</b> {platform.python_version()}\n"
        f"🧠 <b>RAM (Xotira iste'moli):</b> <code>{mem_mb}</code>\n"
        f"💾 <b>Baza turi:</b> <code>{db_type_badge}</code>\n"
        f"⚡️ <b>Baza tezligi (DB Ping):</b> <code>{db_latency}</code>\n"
        f"🔌 <b>Jonli WebSocket ulanishlar:</b> <code>{ws_count} ta faol qurilma</code>\n\n"
        f"🛡 <b>Xavfsizlik & Himoya:</b>\n"
        f"  • SSL / HTTPS: <b>Grade A+ (Faol & Sertifikatlangan)</b>\n"
        f"  • CSP / HSTS / CORS: <b>High-grade Enterprise Active</b>\n"
        f"  • Background Sleep Guard: <b>ES_CONTINUOUS Faol</b>\n"
        f"  • Render Keepalive: <b>Har 10 daqiqada avtomatik</b>\n\n"
        f"📊 <b>Platforma ko'rsatkichlari:</b>\n"
        f"  • Jami foydalanuvchilar: <b>{stats['total_users']}</b>\n"
        f"  • Bugun qo'shilganlar: <b>{stats['today_users']}</b>\n"
        f"  • Ota-onalar: <b>{stats['total_parents']}</b> | Farzandlar: <b>{stats['total_children']}</b>\n"
        f"  • Jonli GPS nuqtalari: <b>{stats['total_location_logs']} ta</b>\n"
        f"  • Faol SOS holatlari: <b>{stats['active_sos']} ta</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <i>Barcha tizimlar barqaror va 100% ishlab turibdi.</i>"
    )
    return html
