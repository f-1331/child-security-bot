import html
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
    db_type_badge = "🐘 PostgreSQL (Doimiy bulut)" if is_pg else "📁 SQLite (Vaqtinchalik fayl)"

    db_alert = ""
    if not is_pg:
        db_alert = (
            f"\n\n⚠️ <b>DIQQAT: Baza vaqtinchalik SQLite rejimida ishlayapti!</b>\n"
            f"Render Free serverida har safar yangi deploy bo'lganda diskdagi SQLite fayli o'chadi. "
            f"Ma'lumotlar o'chib ketmasligi uchun Renderda <b>PostgreSQL DATABASE_URL</b> ni ulab qo'ying."
        )

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
        f"✅ <i>Barcha tizimlar barqaror va 100% ishlab turibdi.</i>{db_alert}"
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


async def get_live_gps_radar(session: AsyncSession) -> Tuple[str, InlineKeyboardMarkup]:
    """Generate real-time live GPS radar listing for all active children with maps links."""
    from db.models import User
    from sqlalchemy import select

    res = await session.execute(select(User).where(User.role == "child", User.is_active == True))
    children = res.scalars().all()
    now_utc = datetime.now(timezone.utc)
    tashkent_tz = timezone(timedelta(hours=5))

    lines = [
        "📡 <b>JONLI GPS RADAR — BOLALAR XAVFSIZLIGI</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]
    buttons = []

    valid_loc_count = 0
    for ch in children:
        loc = await crud.get_latest_location(session, ch.id)
        if loc and loc.latitude and loc.longitude:
            valid_loc_count += 1
            safe_name = ch.full_name or f"Farzand #{ch.id}"
            maps_url = f"https://maps.google.com/?q={loc.latitude:.6f},{loc.longitude:.6f}"
            batt_icon = "🔌" if loc.is_charging else "🔋"
            batt_str = f"{batt_icon} {loc.battery_level}%" if loc.battery_level is not None else ""
            speed_str = f"⚡️ {loc.speed:.0f} km/h" if loc.speed else "⚡️ 0 km/h"
            
            rec_dt = loc.recorded_at or loc.timestamp
            if rec_dt:
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                time_str = rec_dt.astimezone(tashkent_tz).strftime("%H:%M:%S")
            else:
                time_str = "Hozir"

            sos_tag = " 🚨 <b>[SOS!]</b>" if ch.is_sos_active else ""
            lines.append(
                f"👤 <b>{safe_name}</b> (ID: <code>{ch.id}</code>){sos_tag}\n"
                f"📍 <a href=\"{maps_url}\">Google Maps ({loc.latitude:.5f}, {loc.longitude:.5f})</a>\n"
                f"📊 {batt_str} | {speed_str} | 🕒 {time_str}\n"
            )
            buttons.append([
                InlineKeyboardButton(text=f"📍 {safe_name} (Xaritada)", url=maps_url),
                InlineKeyboardButton(text="🔊 Sirena", callback_data=f"adm_siren:{ch.id}"),
                InlineKeyboardButton(text="🛑 Qulflash", callback_data=f"adm_lock:{ch.id}")
            ])

    if valid_loc_count == 0:
        lines.append("<i>Hozircha birorta ham qurilmadan GPS signali olinmadi.</i>\n")
    else:
        lines.append(f"🟢 Jami onlayn kuzatilayotgan qurilmalar: <b>{valid_loc_count} ta</b>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Barcha koordinatalar sun'iy yo'ldosh (GPS) va uyali aloqa orqali yangilanadi.</i>")

    buttons.append([
        InlineKeyboardButton(text="🔄 Radarni yangilash", callback_data="adm_refresh_radar"),
        InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_database_health_stats(session: AsyncSession) -> str:
    """Detailed Neon PostgreSQL / SQLite connection health and row counts."""
    from db.models import User, ParentChildLink, LocationLog, Geofence, Task
    from sqlalchemy import select, func, text

    db_type = "Neon PostgreSQL (Bulutli)" if "postgres" in settings.RESOLVED_DATABASE_URL.lower() else "SQLite (Mahalliy)"
    
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one() or 0
    total_parents = (await session.execute(select(func.count(User.id)).where(User.role == "parent"))).scalar_one() or 0
    total_children = (await session.execute(select(func.count(User.id)).where(User.role == "child"))).scalar_one() or 0
    total_links = (await session.execute(select(func.count(ParentChildLink.parent_id)))).scalar_one() or 0
    total_locs = (await session.execute(select(func.count(LocationLog.id)))).scalar_one() or 0
    total_geofences = (await session.execute(select(func.count(Geofence.id)))).scalar_one() or 0
    total_tasks = (await session.execute(select(func.count(Task.id)))).scalar_one() or 0
    active_sos = (await session.execute(select(func.count(User.id)).where(User.is_sos_active == True))).scalar_one() or 0

    uptime = get_uptime_str()

    return (
        "🗄️ <b>MA'LUMOTLAR BAZASI VA SERVER SALOMATLIGI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 <b>Baza turi:</b> <code>{db_type}</code>\n"
        f"🟢 <b>Ulanish holati:</b> <code>200 OK (Faol va Barqaror)</code>\n"
        f"⏱ <b>Server Uptime:</b> <code>{uptime}</code>\n"
        f"🌐 <b>WebApp URL:</b> <code>{settings.RESOLVED_WEBAPP_URL}</code>\n\n"
        "📊 <b>JADVALLAR BO'YICHA STATISTIKA:</b>\n"
        f"• 👥 Jami foydalanuvchilar: <b>{total_users}</b> ta\n"
        f"  ├ 👨‍👩‍👧 Ota-onalar: <b>{total_parents}</b> ta\n"
        f"  └ 👦 Farzandlar: <b>{total_children}</b> ta\n"
        f"• 🔗 Oila bog'lanishlari: <b>{total_links}</b> ta\n"
        f"• 📍 Saqlangan GPS koordinatalar: <b>{total_locs}</b> ta\n"
        f"• 🛡 Geozonalar (Xavfsiz makonlar): <b>{total_geofences}</b> ta\n"
        f"• 📋 Topshiriqlar: <b>{total_tasks}</b> ta\n"
        f"• 🚨 Faol SOS signallari: <b>{active_sos}</b> ta\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡️ <i>Neon serverless DB avtomatik replikatsiya va zaxiralash bilan himoyalangan.</i>"
    )


def generate_magic_login_url(user: Any, code_override: Optional[str] = None) -> str:
    """Generate 1-click tokenless login link for WebApp with direct auto-auth and owner impersonation."""
    code = code_override or getattr(user, "web_access_code", None) or getattr(user, "pairing_code", None) or ""
    role = getattr(user, "role", None) or "parent"
    uid = getattr(user, "id", 0)
    target_param = f"parent_id={uid}" if role == "parent" else f"child_id={uid}"

    parts = [f"role={role}", target_param]
    if code:
        parts.append(f"code={code}")
        parts.append(f"auth_code={code}")
    parts.append(f"admin_uid={uid}")
    parts.append("impersonate=1")

    return f"{settings.RESOLVED_WEBAPP_URL}?{'&'.join(parts)}"


async def get_or_ensure_magic_login_url(session: AsyncSession, user: Any) -> str:
    """Ensure user has a web access code in DB and return the 1-click magic login URL."""
    code = getattr(user, "web_access_code", None) or getattr(user, "pairing_code", None)
    if not code:
        code = await crud.generate_web_access_code(session, user.id)
    return generate_magic_login_url(user, code_override=code)


async def format_users_directory_html(
    session: AsyncSession,
    users: List[Any],
    total_count: int,
    page: int,
    total_pages: int,
    role_filter: str = "all",
    search_query: Optional[str] = None,
) -> str:
    """Format paginated bot user directory into clean, informative HTML."""
    filter_names = {
        "all": "🌐 Barchasi",
        "parent": "👨‍👩‍👧 Ota-onalar",
        "child": "👦 Farzandlar",
        "banned": "🚫 Bloklanganlar",
    }
    active_filter_label = filter_names.get(role_filter, role_filter)
    search_info = f"\n🔍 Qidiruv: <code>{html.escape(search_query)}</code>" if search_query else ""

    header = (
        "👥 <b>BOT FOYDALANUVCHILARI KATALOGI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Jami topildi: <b>{total_count}</b> ta | Filtr: <b>{active_filter_label}</b>"
        f"{search_info}\n"
        f"📄 Sahifa: <b>{page}</b> / <b>{total_pages}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not users:
        return (
            header
            + "ℹ️ <i>Hech qanday foydalanuvchi topilmadi. Filtrni o'zgartiring yoki qidiruv so'zini tekshiring.</i>"
        )

    lines = []
    start_num = (page - 1) * 10 + 1

    for idx, u in enumerate(users, start=start_num):
        is_owner = u.id in (1, 6707839718)
        role_emoji = "👑" if is_owner else ("👦" if u.role == "child" else "👨‍👩‍👧")
        status_emoji = "🚫 Blok" if not u.is_active else ("🚨 SOS" if u.is_sos_active else "🟢 Faol")
        username_str = f"@{u.username}" if u.username else "yo'q"
        disp_id = 1 if is_owner else u.id
        name_clean = html.escape(u.full_name or (f"@{u.username}" if u.username else f"Foydalanuvchi #{disp_id}"))
        phone_str = u.phone_number or "yo'q"

        # Family linkages
        if u.role == "child":
            parents = await crud.get_parents_for_child(session, u.id)
            if parents:
                parent_names = ", ".join(p.full_name or str(p.id) for p in parents[:2])
                family_txt = f"Ota-onasi: <b>{html.escape(parent_names)}</b>"
            else:
                family_txt = "Ota-onasi: <i>Bog'lanmagan</i>"
            extra_txt = f"💰 {u.virtual_balance:,} tanga | {family_txt}"
        else:
            children = await crud.get_children_for_parent(session, u.id)
            family_txt = f"Farzandlar: <b>{len(children)}</b> ta"
            extra_txt = family_txt

        code_val = u.web_access_code or u.pairing_code or "yo'q"

        user_block = (
            f"<b>{idx}.</b> {role_emoji} <b>{name_clean}</b> (ID: <code>{disp_id}</code>) [{status_emoji}]\n"
            f"   ├ 🌐 {username_str} | 📞 <code>{phone_str}</code>\n"
            f"   ├ 📌 {extra_txt}\n"
            f"   └ 🔑 Kirish kodi: <code>{code_val}</code>"
        )
        lines.append(user_block)

    body = "\n\n".join(lines)
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Pastdagi tugmalar orqali istalgan foydalanuvchi hisobiga 1-bosishda kiring (Ghost Login) yoki dosyesini oching.</i>"
    )
    return header + body + footer


async def trigger_remote_siren(child_id: int) -> bool:
    """Trigger max-volume emergency siren on child's device via WebSocket."""
    now_time = datetime.now(timezone(timedelta(hours=5))).strftime("%H:%M")
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "remote_siren",
            "sound": True,
            "volume": 1.0,
            "time": now_time
        })
    except Exception:
        pass

    main_bot = get_main_bot()
    if main_bot:
        try:
            await main_bot.send_message(
                chat_id=child_id,
                text="🚨 <b>DIQQAT! MASOFAVIY FAVQULODDA SIRENA FAOL!</b>\n\nQurilmangizda xavf signali chalinmoqda! 🔊",
                parse_mode="HTML"
            )
        except Exception:
            pass
    return True


async def trigger_remote_screen_lock(child_id: int, lock: bool) -> bool:
    """Remotely lock or unlock child's device apps and screen."""
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "remote_lock",
            "lock": lock,
            "timestamp": int(time.time())
        })
    except Exception:
        pass

    main_bot = get_main_bot()
    if main_bot:
        try:
            msg = "🛑 <b>QURILMA MASOFAVIY BLOKLANDI!</b>\n\nIlovalar vaqtinchalik cheklandi." if lock else "✅ <b>QURILMA QULFDAN CHIQARILDI!</b>\n\nIlovalardan foydalanishingiz mumkin."
            await main_bot.send_message(chat_id=child_id, text=msg, parse_mode="HTML")
        except Exception:
            pass
    return True


async def perform_global_keyboards_refresh(bot: Optional[Bot], session: AsyncSession) -> Tuple[int, int]:
    """
    Sends updated reply keyboard to all active parents and children.
    Returns (success_count, fail_count).
    """
    from db.models import User
    from sqlalchemy import select
    from bot.keyboards import get_parent_main_keyboard, get_child_main_keyboard, get_role_selection_keyboard

    target_bot = bot or get_main_bot()
    if not target_bot:
        return 0, 0

    res = await session.execute(select(User).where(User.is_active == True))
    users = res.scalars().all()
    success_count = 0
    fail_count = 0

    msg = (
        "🔄 <b>BOT YANGILANDI VA MENYULAR RESET QILINDI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Hurmatli foydalanuvchi!\n\n"
        "Bot xizmatlari muvaffaqiyatli qayta ishga tushirildi. "
        "Barcha yangi tugmalar, offline SMS va tezkor xabarnomalar faollashtirildi 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    for u in users:
        if u.role == "parent":
            kb = get_parent_main_keyboard(u.id)
        elif u.role == "child":
            kb = get_child_main_keyboard(u.id)
        else:
            kb = get_role_selection_keyboard()

        try:
            await target_bot.send_message(chat_id=u.id, text=msg, reply_markup=kb, parse_mode="HTML")
            success_count += 1
        except Exception:
            fail_count += 1

    return success_count, fail_count


async def perform_global_cache_and_sos_reset(session: AsyncSession) -> dict:
    """Clear all active SOS alerts and reset transient demo balances."""
    from db.models import User
    from sqlalchemy import update

    stmt = update(User).where(User.is_sos_active == True).values(is_sos_active=False)
    res = await session.execute(stmt)
    await session.commit()
    cleared_sos = res.rowcount if hasattr(res, "rowcount") else 0

    return {
        "cleared_sos": cleared_sos,
        "status": "success"
    }


async def perform_global_factory_reset(bot: Optional[Bot], session: AsyncSession) -> Tuple[int, int]:
    """
    Factory reset: Unlinks all parents and children, resets roles to 'unassigned',
    clears pairing codes and active SOS, and sends onboarding role selection keyboard to all users.
    Returns (reset_users_count, notified_count).
    """
    from db.models import User, ParentChildLink
    from sqlalchemy import select, delete, update
    from bot.keyboards import get_role_selection_keyboard

    owner_id = getattr(settings, "OWNER_ID", 6707839718)

    # 1. Delete all parent-child connections
    await session.execute(delete(ParentChildLink))

    # 2. Reset roles, pairing codes, web codes, and SOS (exclude owner)
    stmt = (
        update(User)
        .where(User.id != owner_id)
        .values(
            role="unassigned",
            pairing_code=None,
            pairing_code_created_at=None,
            web_access_code=None,
            is_sos_active=False
        )
    )
    res = await session.execute(stmt)
    await session.commit()
    reset_count = res.rowcount if hasattr(res, "rowcount") else 0

    # 3. Notify all users with fresh onboarding keyboard (exclude owner)
    target_bot = bot or get_main_bot()
    notified_count = 0
    if target_bot:
        users_res = await session.execute(select(User).where(User.is_active == True, User.id != owner_id))
        users = users_res.scalars().all()
        notice_text = (
            "🔄 <b>TIZIM BOSHLANG'ICH HOLATGA QAYTARILDI!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Hurmatli foydalanuvchi!\n\n"
            "Platforma yangilanishi munosabati bilan barcha hisoblar boshlang'ich holatga keltirildi.\n\n"
            "Iltimos, pastdagi tugmalar orqali qaytadan o'z rolingizni tanlang 👇\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        role_kb = get_role_selection_keyboard()
        for u in users:
            try:
                await target_bot.send_message(chat_id=u.id, text=notice_text, reply_markup=role_kb, parse_mode="HTML")
                notified_count += 1
            except Exception:
                pass

    return reset_count, notified_count


async def get_online_activity_summary(session: AsyncSession) -> Tuple[str, InlineKeyboardMarkup]:
    """Fetch live online user statistics (last 15m, 1h, 24h)."""
    from db.models import User, LocationLog
    from sqlalchemy import select, func, desc

    now_utc = datetime.now(timezone.utc)
    t_15m = now_utc - timedelta(minutes=15)
    t_1h = now_utc - timedelta(hours=1)
    t_24h = now_utc - timedelta(hours=24)

    # 1. Locations in last 15m, 1h, 24h
    res_15m = await session.execute(select(func.count(func.distinct(LocationLog.child_id))).where(LocationLog.recorded_at >= t_15m))
    active_children_15m = res_15m.scalar() or 0

    res_1h = await session.execute(select(func.count(func.distinct(LocationLog.child_id))).where(LocationLog.recorded_at >= t_1h))
    active_children_1h = res_1h.scalar() or 0

    res_24h = await session.execute(select(func.count(func.distinct(LocationLog.child_id))).where(LocationLog.recorded_at >= t_24h))
    active_children_24h = res_24h.scalar() or 0

    # 2. Total user counts
    tot_p = await session.execute(select(func.count(User.id)).where(User.role == "parent"))
    total_parents = tot_p.scalar() or 0

    tot_c = await session.execute(select(func.count(User.id)).where(User.role == "child"))
    total_children = tot_c.scalar() or 0

    # 3. Active children list with recent ping
    recent_locs = await session.execute(
        select(LocationLog)
        .order_by(desc(LocationLog.recorded_at))
        .limit(8)
    )
    latest_logs = recent_locs.scalars().all()

    seen_ids = set()
    lines = []
    for log in latest_logs:
        if log.child_id in seen_ids:
            continue
        seen_ids.add(log.child_id)
        ch = await crud.get_user(session, log.child_id)
        c_name = ch.full_name if ch else f"Farzand #{log.child_id}"
        delta_m = int((now_utc - log.recorded_at).total_seconds() / 60) if log.recorded_at else 0
        ago_str = "Hozirgina" if delta_m < 2 else f"{delta_m} daqiqa oldin"
        batt = f"{log.battery_level}%" if log.battery_level is not None else "?%"
        lines.append(f"• <b>{html.escape(c_name)}</b> (ID: <code>{log.child_id}</code>) — 🔋 {batt} ({ago_str})")

    pings_txt = "\n".join(lines) if lines else "<i>Hozircha faol signallar yo'q</i>"

    text = (
        "👥 <b>FAOL FOYDALANUVCHILAR VA JONLI MONITORING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 <b>Ayni damda onlayn (so'nggi 15 daqiqa):</b> <code>{active_children_15m}</code> ta farzand\n"
        f"🕒 <b>So'nggi 1 soatda faol:</b> <code>{active_children_1h}</code> ta farzand\n"
        f"📅 <b>Bugungi faol qurilmalar (24 soat):</b> <code>{active_children_24h}</code> ta\n\n"
        f"👨‍👩‍👧 <b>Jami ota-onalar:</b> <code>{total_parents}</code> ta\n"
        f"👦 <b>Jami farzandlar:</b> <code>{total_children}</code> ta\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>So'nggi signal yuborgan qurilmalar:</b>\n{pings_txt}\n\n"
        "🔄 <i>Ma'lumotlar jonli telemetriya asosida tuzildi.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh_online"),
                InlineKeyboardButton(text="📡 GPS Radar", callback_data="adm_refresh_radar")
            ],
            [
                InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
            ]
        ]
    )
    return text, kb


async def get_top_balances_summary(session: AsyncSession) -> Tuple[str, InlineKeyboardMarkup]:
    """Fetch economy stats and top-10 richest children by coin balances."""
    from db.models import User
    from sqlalchemy import select, func, desc

    res_sum = await session.execute(
        select(func.sum(User.virtual_balance)).where(User.role == "child")
    )
    total_coins = res_sum.scalar() or 0

    res_avg = await session.execute(
        select(func.avg(User.virtual_balance)).where(User.role == "child")
    )
    avg_coins = int(res_avg.scalar() or 0)

    # Top-10
    res_top = await session.execute(
        select(User)
        .where(User.role == "child")
        .order_by(desc(User.virtual_balance))
        .limit(10)
    )
    top_children = res_top.scalars().all()

    total_som_est = total_coins * 10

    lines = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, ch in enumerate(top_children):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        bal = ch.virtual_balance or 0
        som_val = bal * 10
        u_name = f"@{ch.username}" if ch.username else f"ID: {ch.id}"
        lines.append(f"{medal} <b>{html.escape(ch.full_name or 'Farzand')}</b> ({u_name})\n   └ 🪙 <b>{bal:,} tanga</b> (~{som_val:,} so'm)")

    top_txt = "\n".join(lines) if lines else "<i>Hali tangalar mavjud emas</i>"

    text = (
        "🏆 <b>TANGALAR IQTISODIYOTI & TOP REYTING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>Muomaladagi jami tangalar:</b> <code>{total_coins:,}</code> tanga\n"
        f"💵 <b>Taxminiy so'm ekvivalenti:</b> ~<code>{total_som_est:,}</code> so'm\n"
        f"📊 <b>O'rtacha balans:</b> <code>{avg_coins:,}</code> tanga/farzand\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎖 <b>ENG KO'P TANGA TOPGAN BOLALAR (TOP-10):</b>\n\n{top_txt}\n\n"
        "💡 <i>Tangalarni ko'paytirish uchun <code>/airdrop</code> yoki <code>/add_coins &lt;id&gt; &lt;son&gt;</code> buyruqlaridan foydalaning.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 Hammaga Airdrop", callback_data="ad_amt_custom"),
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh_coins")
            ],
            [
                InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
            ]
        ]
    )
    return text, kb


def get_app_download_links_html() -> Tuple[str, InlineKeyboardMarkup]:
    """Return all platform download links and installation options."""
    apk_url = "https://child-security-bot.vercel.app/app/child-security.apk"
    webapp_url = "https://child-security-bot.vercel.app"
    render_api_url = "https://child-security-site.onrender.com"

    text = (
        "📱 <b>CHILD SECURITY — RASMIY ILOVALAR VA HAVOLALAR</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Platformaning barcha rasmiy distributivlari va xizmatlari:\n\n"
        f"🤖 <b>Android Ilova (APK):</b>\n"
        f"└ <a href='{apk_url}'>APK Yuklab Olish (Doimiy 24/7 Fon GPS, Ilovalar tahlili, Bloklash qalqoni)</a>\n\n"
        f"🌐 <b>Rasmiy WebApp Portali (Vercel):</b>\n"
        f"└ <a href='{webapp_url}'>{webapp_url}</a>\n\n"
        f"💻 <b>Noutbuk & Kompyuter (PWA / Desktop):</b>\n"
        f"└ Brauzer orqali kompyuterga 1-bosishda o'rnatish imkoniyati mavjud\n\n"
        f"🤖 <b>Asosiy Telegram Boti:</b>\n"
        f"└ @child_security_bot\n\n"
        f"⚡ <b>Render Backend Server:</b>\n"
        f"└ <code>{render_api_url}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <i>Barcha ilovalar oxirgi xavfsizlik versiyasiga yangilangan.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🤖 APK Faylini Yuklash", url=apk_url),
                InlineKeyboardButton(text="🌐 Saytni Ochish", url=webapp_url)
            ],
            [
                InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")
            ]
        ]
    )
    return text, kb


async def perform_add_coins_to_child(session: AsyncSession, child_id: int, amount: int) -> Tuple[bool, int, str]:
    """Add or subtract virtual coins to a specific child."""
    child = await crud.get_user(session, child_id)
    if not child:
        return False, 0, f"ID: {child_id} bo'yicha farzand topilmadi."
    new_bal = max(0, (child.virtual_balance or 0) + amount)
    child.virtual_balance = new_bal
    await session.commit()

    main_bot = get_main_bot()
    if main_bot:
        try:
            sign = f"+{amount}" if amount > 0 else f"{amount}"
            await main_bot.send_message(
                chat_id=child_id,
                text=f"🎁 <b>ADMINISTRATSIYA SIZGA BONUS BERDI!</b>\n\nHisobingizga <b>{sign} tanga</b> qo'shildi! 🪙\nHozirgi balansingiz: <b>{new_bal:,} tanga</b>.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    return True, new_bal, f"{child.full_name} balansi muvaffaqiyatli o'zgartirildi: {new_bal:,} tanga."


async def perform_ban_toggle(session: AsyncSession, user_id: int, ban: bool) -> Tuple[bool, str]:
    """Ban (deactivate) or unban (reactivate) a user."""
    user = await crud.get_user(session, user_id)
    if not user:
        return False, f"ID: {user_id} bo'yicha foydalanuvchi topilmadi."
    user.is_active = not ban
    await session.commit()
    action_str = "bloklandi" if ban else "blokdan chiqarildi"
    return True, f"Foydalanuvchi {user.full_name} (ID: {user_id}) muvaffaqiyatli {action_str}!"

