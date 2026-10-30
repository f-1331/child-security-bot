import asyncio
import logging
import html
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import settings
from db.database import AsyncSessionLocal
import db.crud as crud
from api import telemetry

logger = logging.getLogger("scheduler")

# Robust local Tashkent timezone (UTC+5) that never fails even on systems without tzdata
TASHKENT_TZ = timezone(timedelta(hours=5))


async def run_nightly_and_walksafe_worker(bot: Bot):
    """
    Background worker loop:
    1. At 22:00 every night (Tashkent time), sends daily safety report & discipline bonus to all parents.
    2. Checks active Walk Safe sessions and alerts parents if deadline expired without completion.
    """
    logger.info("Nightly report and Walk Safe background scheduler activated.")
    last_sent_date = None
    alerted_expired_walk_safes = set()

    # Determine timezone with absolute priority to Tashkent UTC+5
    tz = TASHKENT_TZ
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(settings.TIMEZONE)
        except Exception:
            tz = TASHKENT_TZ

    while True:
        try:
            now_tz = datetime.now(tz)
            today_str = now_tz.strftime("%Y-%m-%d")

            # 1. 22:00 Nightly Safety Report (Triggers at or after 22:00 once per day)
            if now_tz.hour >= 22 and last_sent_date != today_str:
                logger.info(f"Triggering 22:00 nightly safety reports for {today_str} (Local Tashkent time: {now_tz.strftime('%H:%M:%S')})...")
                async with AsyncSessionLocal() as session:
                    from sqlalchemy import select
                    from db.models import User
                    parents_res = await session.execute(
                        select(User).where(User.role == "parent")
                    )
                    parents = list(parents_res.scalars().all())

                    for p in parents:
                        children = await crud.get_children_for_parent(session, p.id)
                        if not children:
                            continue
                        for ch in children:
                            try:
                                rep = await crud.generate_daily_safety_report(session, ch.id)
                                c_name = html.escape(rep["child_name"])
                                zones_list = rep["visited_zones"]
                                if zones_list:
                                    zones_str = " ➔ ".join([
                                        f"{html.escape(z['name'])} ({z.get('points_str', z.get('points_inside', 1))} marta)"
                                        if z.get('points_inside', 0) > 1 else html.escape(z["name"])
                                        for z in zones_list
                                    ])
                                    est_time = sum(z["est_minutes"] for z in zones_list)
                                    hours = est_time // 60
                                    mins = est_time % 60
                                    time_str = f"{hours} soat {mins} daqiqa" if hours > 0 else f"{mins} daqiqa"
                                else:
                                    zones_str = "Hali qayd etilmagan"
                                    time_str = "0 daqiqa"

                                # Only include wishlist if configured by parent/child
                                wishlist = rep.get("wishlist")
                                if wishlist and wishlist.get("title") and wishlist.get("target_som", 0) > 0:
                                    w_title = html.escape(wishlist.get("title"))
                                    w_prog = wishlist.get("progress_percent", 0)
                                    w_cur = wishlist.get("current_som", 0)
                                    w_tgt = wishlist.get("target_som", 0)
                                    wishlist_line = f"🎯 <b>Orzular qutisi ({w_title}):</b> {w_prog}% ({w_cur:,} / {w_tgt:,} so'm)\n"
                                else:
                                    wishlist_line = ""

                                dist_str = rep.get("distance_formatted") or f"{rep.get('total_distance_km', 0)} km"
                                batt_str = f"{rep['battery_level']}%" if rep['battery_level'] is not None else "Noma'lum"
                                spd_str = f"{rep['max_speed_kmh']} km/soat" if rep['max_speed_kmh'] > 0 else "Piyoda"

                                # Discipline Status Check (Informational only - coins are awarded only by parents)
                                tamper_events = telemetry.child_daily_tamper_log.get(ch.id, [])
                                today_tamper = [ev for ev in tamper_events if ev.get("time", "").startswith(today_str)]

                                if len(today_tamper) == 0:
                                    discipline_line = "🎖️ <b>Kun Qahramoni:</b> Bugun hech qanday qoidabuzarlik qilinmadi! 🌟\n"
                                else:
                                    discipline_line = f"⚠️ <b>Intizom holati:</b> Bugun {len(today_tamper)} ta qoidabuzarlik qayd etildi\n"

                                report_msg = (
                                    f"🌙 <b>KUNLIK XAVFSIZLIK HISOBOTI (22:00)</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"👤 <b>Farzand:</b> {c_name}\n\n"
                                    f"📍 <b>Tashrif buyurilgan joylar:</b>\n"
                                    f"   └ {zones_str}\n\n"
                                    f"⏱ <b>Xavfsiz joylarda bo'lgan vaqti:</b> {time_str}\n"
                                    f"🚶‍♂️ <b>Kuzatilgan masofa:</b> {dist_str} ({rep['points_count']} ta signal)\n"
                                    f"🚀 <b>Maksimal tezlik:</b> {spd_str}\n"
                                    f"🔋 <b>Batareya quvvati:</b> {batt_str}\n\n"
                                    f"✅ <b>Bajarilgan topshiriqlar:</b> {rep['completed_tasks_count']} ta (+{rep['earned_som']:,} so'm)\n"
                                    f"{wishlist_line}"
                                    f"{discipline_line}"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"✨ <i>Farzandingiz xavfsizligi va kunlik intizomi to'liq nazoratda! Xayrli tun!</i>"
                                )
                                report_kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="🗺 Xaritada marshrutni ko'rish",
                                            web_app=WebAppInfo(url=f"{settings.WEBAPP_BASE_URL}?role=parent&parent_id={p.id}&child_id={ch.id}")
                                        )
                                    ],
                                    [
                                        InlineKeyboardButton(text="🔔 Baland signal chalish", callback_data=f"siren_child_{ch.id}"),
                                        InlineKeyboardButton(text="🎁 +100 Tanga mukofot", callback_data=f"night_reward_{ch.id}_100")
                                    ],
                                    [
                                        InlineKeyboardButton(text="🛌 Tungi uyqu rejimiga olish", callback_data=f"night_bedtime_{ch.id}")
                                    ]
                                ])
                                await bot.send_message(chat_id=p.id, text=report_msg, reply_markup=report_kb, parse_mode="HTML")
                            except Exception as ch_err:
                                logger.error(f"Failed to dispatch nightly report for child {ch.id} to parent {p.id}: {ch_err}")

                    await session.commit()

                last_sent_date = today_str
                logger.info(f"Nightly safety reports dispatched for {today_str}.")

            # 2. Walk Safe Deadline Monitoring
            async with AsyncSessionLocal() as session:
                from sqlalchemy import select
                from db.models import User
                now_utc = datetime.now(timezone.utc)
                active_ws_res = await session.execute(
                    select(User).where(
                        User.walk_safe_active == True,
                        User.walk_safe_deadline != None
                    )
                )
                active_ws_children = list(active_ws_res.scalars().all())
                for ch in active_ws_children:
                    if ch.walk_safe_deadline:
                        ch_deadline = ch.walk_safe_deadline
                        if ch_deadline.tzinfo is None:
                            ch_deadline = ch_deadline.replace(tzinfo=timezone.utc)
                        if ch_deadline < now_utc:
                            ws_key = f"{ch.id}_{ch.walk_safe_started_at}"
                            if ws_key not in alerted_expired_walk_safes:
                                parents = await crud.get_parents_for_child(session, ch.id)
                                ch_name_esc = html.escape(ch.full_name or f"Farzand #{ch.id}")
                                dest_esc = html.escape(ch.walk_safe_destination or "Uyga qaytish")
                                latest_loc = await crud.get_latest_location(session, ch.id)
                                maps_url = f"https://www.google.com/maps/search/?api=1&query={latest_loc.latitude},{latest_loc.longitude}" if latest_loc else None

                                warn_msg = (
                                    f"🚨 <b>DIQQAT: XAVFSIZ YO'L VAQTI TUGADI!</b>\n\n"
                                    f"Farzandingiz <b>{ch_name_esc}</b> «Xavfsiz yo'l» (<b>{dest_esc}</b>) rejimida belgilangan vaqt ichida yetib borganini tasdiqlamadi!\n\n"
                                    + (f"📍 Oxirgi joylashuvi: <a href=\"{maps_url}\">Google Maps orqali ko'rish</a>\n\n" if maps_url else "")
                                    + f"Iltimos, farzandingiz bilan zudlik bilan bog'laning!"
                                )
                                for p in parents:
                                    try:
                                        await bot.send_message(chat_id=p.id, text=warn_msg, parse_mode="HTML")
                                        if latest_loc:
                                            await bot.send_location(chat_id=p.id, latitude=latest_loc.latitude, longitude=latest_loc.longitude)
                                    except Exception as e:
                                        logger.error(f"Failed to alert parent {p.id} of Walk Safe timeout: {e}")
                                alerted_expired_walk_safes.add(ws_key)
        except Exception as err:
            logger.error(f"Scheduler worker error: {err}")

        await asyncio.sleep(15)
