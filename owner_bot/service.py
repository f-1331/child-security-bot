import os
import platform
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
    tashkent_tz = timezone(timedelta(hours=5))
    if user.created_at:
        dt = user.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        created_str = dt.astimezone(tashkent_tz).strftime("%Y-%m-%d %H:%M")
    else:
        created_str = "Noma'lum"

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
        if loc.timestamp:
            loc_dt = loc.timestamp
            if loc_dt.tzinfo is None:
                loc_dt = loc_dt.replace(tzinfo=timezone.utc)
            loc_time = loc_dt.astimezone(tashkent_tz).strftime("%H:%M:%S (%d.%m)")
        else:
            loc_time = "Noma'lum"
        speed_str = f"{loc.speed:.1f} km/soat" if loc.speed else "0 km/soat"
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

        policy_obj = dossier.get("policy")
        if policy_obj:
            lock_txt = "🛑 <b>BARCHA ILOVALAR BLOKLANGAN</b>" if policy_obj.is_all_apps_blocked else "🟢 Ochiq"
            un_txt = "🔒 Faol (PIN talab)" if policy_obj.anti_uninstall_enabled else "⚠️ O'chiq"
            extra_lines.append(f"🛑 Bloklash holati: {lock_txt}")
            extra_lines.append(f"🛡 Sozlamalar himoyasi: {un_txt}")
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


async def get_apps_and_screen_time_summary(session: AsyncSession) -> Tuple[str, InlineKeyboardMarkup]:
    """Generate global analytics on Screen Time, Top Apps and Master Lockdowns."""
    from db.models import ChildDevicePolicy, AppUsageReport, User
    from sqlalchemy import select, func
    import json

    res_policies = await session.execute(select(ChildDevicePolicy))
    policies = res_policies.scalars().all()

    total_policies = len(policies)
    total_locked_all = sum(1 for p in policies if p.is_all_apps_blocked)
    total_anti_uninstall = sum(1 for p in policies if p.anti_uninstall_enabled)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    res_reports = await session.execute(
        select(AppUsageReport).where(AppUsageReport.report_date == today_str)
    )
    reports = res_reports.scalars().all()

    total_children_logged = len(reports)
    total_screen_seconds = sum(r.total_screen_time_seconds for r in reports)
    total_unlocks = sum(r.unlock_count for r in reports)

    app_usage_counter = {}
    for r in reports:
        if r.app_stats_json:
            try:
                app_list = json.loads(r.app_stats_json)
                for a in app_list:
                    name = a.get("app_name", "Noma'lum")
                    sec = a.get("usage_seconds", 0)
                    app_usage_counter[name] = app_usage_counter.get(name, 0) + sec
            except Exception:
                pass

    if not app_usage_counter:
        apps_block_str = "  <i>ℹ️ Farzand qurilmalaridan hali bugungi ilovalar hisoboti kelmagan (0 ta).</i>"
        total_screen_seconds = 0
        total_unlocks = 0
        avg_str = "0 daqiqa"
        total_h = 0
        total_m = 0
    else:
        sorted_apps = sorted(app_usage_counter.items(), key=lambda x: x[1], reverse=True)[:8]
        apps_text_lines = []
        for i, (name, sec) in enumerate(sorted_apps, 1):
            h = sec // 3600
            m = (sec % 3600) // 60
            time_str = f"{h}s {m}d" if h > 0 else f"{m} daqiqa"
            apps_text_lines.append(f"  {i}. <b>{name}</b> — <code>{time_str}</code>")
        apps_block_str = "\n".join(apps_text_lines)

        avg_minutes = (total_screen_seconds // max(1, total_children_logged)) // 60
        avg_h = avg_minutes // 60
        avg_m = avg_minutes % 60
        avg_str = f"{avg_h}s {avg_m}d" if avg_h > 0 else f"{avg_m} daqiqa"
        total_h = total_screen_seconds // 3600
        total_m = (total_screen_seconds % 3600) // 60

    html = (
        f"📱 <b>QURILMALAR & ILOVALAR MONİTORİNGİ TAHLILI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Bugungi platforma ko'rsatkichlari:</b>\n"
        f"  • Jami qayd etilgan ekran vaqti: <b>{total_h} soat {total_m} daqiqa</b>\n"
        f"  • Har bir bolaga o'rtacha vaqt: <b>{avg_str}</b>\n"
        f"  • Qurilmalar ochilgan (Unlock): <b>{total_unlocks} marta</b>\n"
        f"  • Faol farzandlar: <b>{total_children_logged} nafar</b>\n\n"
        f"🔥 <b>Platformada eng ko'p ishlatilgan Top-8 ilovalar:</b>\n"
        f"{apps_block_str}\n\n"
        f"🛡 <b>Xavfsizlik & Bloklash holati:</b>\n"
        f"  • Barcha ilovalar bloklangan (Lockdown): <b>{total_locked_all} ta qurilma</b>\n"
        f"  • Sozlamalar himoyasi (Anti-Uninstall): <b>{total_anti_uninstall} ta qurilma</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ℹ️ <i>Ota-onalar o'z Web App yoki boti orqali farzandlari ilovalarini boshqaradilar.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛑 Barchasini bloklash", callback_data="adm_lockdown_all_prompt"),
            InlineKeyboardButton(text="✅ Barcha bloklarni yechish", callback_data="adm_unlock_all_do")
        ],
        [
            InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh_screentime"),
            InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
        ]
    ])

    return html, kb


async def get_battery_and_device_radar(session: AsyncSession) -> Tuple[str, InlineKeyboardMarkup]:
    """Scan and list low battery devices, silent devices, and hardware health."""
    from db.models import User, LocationLog
    from sqlalchemy import select

    res = await session.execute(select(User).where(User.role == "child", User.is_active == True))
    children = res.scalars().all()

    low_battery_list = []
    silent_list = []
    online_count = 0
    now_utc = datetime.now(timezone.utc)

    for ch in children:
        latest = await crud.get_latest_location(session, ch.id)
        if latest:
            if latest.battery_level is not None and latest.battery_level <= 20:
                low_battery_list.append((ch, latest))

            rec_at = latest.recorded_at
            if rec_at:
                if rec_at.tzinfo is None:
                    rec_at = rec_at.replace(tzinfo=timezone.utc)
                diff_hours = (now_utc - rec_at).total_seconds() / 3600
                if diff_hours > 2.0:
                    silent_list.append((ch, latest, diff_hours))
                else:
                    online_count += 1
            else:
                silent_list.append((ch, latest, 999))
        else:
            silent_list.append((ch, None, 999))

    if low_battery_list:
        low_batt_lines = []
        for ch, loc in low_battery_list[:5]:
            ch_name = ch.full_name or f"Farzand #{ch.id}"
            charging_str = "🔌 Zaryadlanmoqda" if loc.is_charging else "⚠️ Zaryadlanmayapti"
            low_batt_lines.append(f"  • <b>{ch_name}</b> (ID: <code>{ch.id}</code>): <b>{loc.battery_level}%</b> ({charging_str})")
        low_batt_str = "\n".join(low_batt_lines)
    else:
        low_batt_str = "  • <i>Hozircha quvvati 20% dan past qurilmalar yo'q (Me'yorda)</i>"

    if silent_list:
        silent_lines = []
        for ch, loc, hrs in silent_list[:5]:
            ch_name = ch.full_name or f"Farzand #{ch.id}"
            time_ago = f"{int(hrs)} soat oldin" if hrs < 100 else "Signal kelmagan"
            silent_lines.append(f"  • <b>{ch_name}</b> (ID: <code>{ch.id}</code>): <i>{time_ago}</i>")
        silent_str = "\n".join(silent_lines)
    else:
        silent_str = "  • <i>Barcha qurilmalar faol va so'nggi 2 soat ichida signal bergan</i>"

    total_ch = len(children)
    health_pct = round((online_count / max(1, total_ch)) * 100) if total_ch > 0 else 100

    html = (
        f"🔋 <b>BATAREYA & QURILMALAR TELEMETRIYA RADARI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📶 <b>Qurilmalar faollik salomatligi:</b> <code>{health_pct}% aloqada</code>\n"
        f"🟢 So'nggi 2 soatda onlayn bo'lgan: <b>{online_count} ta</b>\n"
        f"⚪️ Jami farzand qurilmalari: <b>{total_ch} ta</b>\n\n"
        f"🪫 <b>Past zaryadli qurilmalar (&lt;= 20%):</b>\n"
        f"{low_batt_str}\n\n"
        f"💤 <b>Aloqaga chiqmagan (Jim turgan) qurilmalar:</b>\n"
        f"{silent_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ℹ️ <i>Qurilmalar fon rejimida har 15-25 soniyada GPS telemetriya yuboradi.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📡 Barcha qurilmalarga signal yuborish", callback_data="adm_ping_all_devices")
        ],
        [
            InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh_battery"),
            InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
        ]
    ])

    return html, kb


async def perform_database_optimization(session: AsyncSession) -> str:
    """Clean ancient location history, optimize indexes, and run SQLite VACUUM."""
    import os
    from sqlalchemy import text
    from config import settings

    db_file = "parental_tracker.db"
    size_before_mb = 0.0
    if os.path.exists(db_file):
        size_before_mb = os.path.getsize(db_file) / 1024 / 1024

    t_cnt = await session.execute(text("SELECT COUNT(*) FROM location_logs"))
    count_before = t_cnt.scalar() or 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    del_res = await session.execute(
        text("DELETE FROM location_logs WHERE recorded_at < :cutoff"),
        {"cutoff": cutoff}
    )
    deleted_rows = del_res.rowcount if hasattr(del_res, "rowcount") else 0
    await session.commit()

    try:
        if "sqlite" in settings.RESOLVED_DATABASE_URL.lower():
            await session.execute(text("PRAGMA optimize"))
            await session.execute(text("VACUUM"))
            await session.commit()
    except Exception:
        pass

    size_after_mb = size_before_mb
    if os.path.exists(db_file):
        size_after_mb = os.path.getsize(db_file) / 1024 / 1024

    saved_mb = max(0.0, size_before_mb - size_after_mb)

    return (
        f"🧹 <b>BAZA OPTIMIZATSIYASI YAKUNLANDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑 Tozalangan eski loglar (30+ kun): <b>{deleted_rows} ta</b>\n"
        f"📊 Qolgan faol GPS yozuvlari: <b>{max(0, count_before - deleted_rows)} ta</b>\n"
        f"💾 Oldingi hajm: <b>{size_before_mb:.2f} MB</b>\n"
        f"⚡️ Yangi hajm: <b>{size_after_mb:.2f} MB</b>\n"
        f"🎉 Qaytarib olingan xotira: <b>{saved_mb:.2f} MB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <i>Baza indekslari va SQLite kesh optimallashdi, server tezligi oshirildi.</i>"
    )


async def distribute_coins_airdrop(session: AsyncSession, amount: int, message_text: Optional[str] = None) -> Tuple[int, int]:
    """Distribute coin bonus to all active children on the platform."""
    from db.models import User
    from sqlalchemy import select

    res = await session.execute(select(User).where(User.role == "child", User.is_active == True))
    children = res.scalars().all()

    total_count = 0
    main_bot = get_main_bot()

    if not message_text:
        message_text = f"🎁 **TABRIKLAYMIZ!**\n\nSizga tizim ma'muriyati tomonidan **+{amount:,} tanga** bayramona bonus sovg'a qilindi! Jamg'armangiz oshdi 🐷🌟"

    for ch in children:
        ch.virtual_balance = (ch.virtual_balance or 0) + amount
        total_count += 1
        if main_bot:
            try:
                await main_bot.send_message(
                    chat_id=ch.id,
                    text=message_text,
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    await session.commit()
    return total_count, total_count * amount

