import asyncio
import os
import logging
import html
from typing import Optional
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

NIGHTLY_STATE_FILE = os.path.join(settings.BASE_DIR, "nightly_report_state.txt")
HOURLY_STATE_FILE = os.path.join(settings.BASE_DIR, "hourly_report_state.txt")


def _get_last_nightly_sent_date() -> Optional[str]:
    try:
        if os.path.exists(NIGHTLY_STATE_FILE):
            with open(NIGHTLY_STATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def _save_last_nightly_sent_date(date_str: str) -> None:
    try:
        with open(NIGHTLY_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception:
        pass


def _get_last_hourly_sent_slot() -> Optional[str]:
    try:
        if os.path.exists(HOURLY_STATE_FILE):
            with open(HOURLY_STATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def _save_last_hourly_sent_slot(slot_str: str) -> None:
    try:
        with open(HOURLY_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(slot_str)
    except Exception:
        pass


async def dispatch_hourly_location_reports(bot: Bot, session, hour_str: str):
    """
    Every 1 hour (during daytime 07:00 - 21:00), sends an update to all parents
    with their child's current location, safe zone status, and battery level.
    """
    from sqlalchemy import select
    from db.models import User, LocationLog
    parents_res = await session.execute(
        select(User).where(User.role == "parent")
    )
    parents = list(parents_res.scalars().all())
    if not parents:
        return

    for p in parents:
        children = await crud.get_children_for_parent(session, p.id)
        if not children:
            continue

        for ch in children:
            try:
                latest_loc = await crud.get_latest_location(session, ch.id)
                c_name = html.escape(ch.full_name or f"Farzand #{ch.id}")

                if not latest_loc:
                    continue

                lat = latest_loc.latitude
                lon = latest_loc.longitude
                maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                batt_level = latest_loc.battery_level if latest_loc.battery_level is not None else "Noma'lum"
                charging_icon = "⚡" if latest_loc.is_charging else ""

                now_utc = datetime.now(timezone.utc)

                # Speed change and movement analysis over the last hour
                hour_ago = now_utc - timedelta(minutes=65)
                h_res = await session.execute(
                    select(LocationLog)
                    .where(
                        LocationLog.child_id == ch.id,
                        LocationLog.recorded_at >= hour_ago
                    )
                    .order_by(LocationLog.recorded_at.asc())
                )
                hour_logs = list(h_res.scalars().all())

                max_hour_spd = 0.0
                fastest_time_str = None
                for hl in hour_logs:
                    if hl.speed is not None and hl.speed > max_hour_spd and hl.speed < 160.0:
                        max_hour_spd = hl.speed
                        if hl.recorded_at:
                            dt = hl.recorded_at if hl.recorded_at.tzinfo else hl.recorded_at.replace(tzinfo=timezone.utc)
                            fastest_time_str = dt.astimezone(TASHKENT_TZ).strftime("%H:%M")

                cur_spd = latest_loc.speed if (latest_loc and latest_loc.speed is not None) else 0.0

                if max_hour_spd >= 25.0:
                    movement_line = (
                        f"🚗 <b>Tezlik o'zgarishi:</b> Soat {fastest_time_str} da <b>{max_hour_spd:.0f} km/soat</b> tezlikda harakatlandi (Transportda)\n"
                        f"📍 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat" + (" (To'xtagan / Harakatsiz)" if cur_spd < 2 else "")
                    )
                elif max_hour_spd >= 7.0:
                    movement_line = (
                        f"🛴 <b>Tezlik o'zgarishi:</b> Soat {fastest_time_str} da <b>{max_hour_spd:.0f} km/soat</b> tezlikda harakatlandi (Velosiped / Samokat)\n"
                        f"📍 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat" + (" (To'xtagan / Harakatsiz)" if cur_spd < 2 else "")
                    )
                elif max_hour_spd >= 2.0:
                    movement_line = (
                        f"🚶‍♂️ <b>Tezlik o'zgarishi:</b> Soat {fastest_time_str} da <b>{max_hour_spd:.0f} km/soat</b> tezlikda harakatlandi (Piyoda)\n"
                        f"📍 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat" + (" (To'xtagan / Harakatsiz)" if cur_spd < 2 else "")
                    )
                else:
                    movement_line = "🛑 <b>Harakat:</b> Soat davomida harakatsiz / bir joyda turibdi (0 km/soat)"

                # Check if currently inside any active geofence
                current_zone_name = None
                geofences = await crud.get_geofences_for_child(session, ch.id)
                for gf in geofences:
                    if gf.is_active:
                        dist = crud.haversine_distance(lat, lon, gf.latitude, gf.longitude)
                        if dist <= gf.radius_meters:
                            current_zone_name = gf.name
                            break

                if current_zone_name:
                    loc_desc = f"🏰 <b>{html.escape(current_zone_name)}</b> hududida"
                else:
                    loc_desc = f"<code>{lat:.5f}, {lon:.5f}</code>"

                # Calculate last seen text
                rec_dt = latest_loc.recorded_at
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                diff_sec = max(0, int((now_utc - rec_dt).total_seconds()))
                if diff_sec < 180:
                    time_ago = "🟢 Jonli / Hozirgina"
                elif diff_sec < 3600:
                    time_ago = f"🟡 {diff_sec // 60} daqiqa oldin"
                else:
                    time_ago = f"⚪ {diff_sec // 3600} soat oldin"

                msg = (
                    f"⏰ <b>SOATLIK XAVFSIZLIK XABARNOMASI ({hour_str}:00)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Farzand:</b> {c_name}\n"
                    f"📍 <b>Joylashuv:</b> {loc_desc}\n"
                    f"🔋 <b>Batareya:</b> {batt_level}% {charging_icon}\n"
                    f"🕒 <b>Oxirgi signal:</b> {time_ago}\n"
                    f"{movement_line}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🗺 <a href=\"{maps_url}\">Xaritada ochish (Google Maps)</a>"
                )

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🗺 Jonli xaritada ko'rish",
                            web_app=WebAppInfo(url=f"{settings.WEBAPP_BASE_URL}?role=parent&parent_id={p.id}&child_id={ch.id}")
                        )
                    ],
                    [
                        InlineKeyboardButton(text="🔔 Signal chalish", callback_data=f"siren_child_{ch.id}")
                    ]
                ])

                await bot.send_message(chat_id=p.id, text=msg, reply_markup=kb, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send hourly location for child {ch.id} to parent {p.id}: {e}")


async def run_nightly_and_walksafe_worker(bot: Bot):
    """
    Background worker loop:
    1. At 22:00 every night (Tashkent time), sends daily safety report to all parents.
    2. Every 1 hour (07:00 - 21:00), sends hourly child location updates to parents.
    3. Checks active Walk Safe sessions and alerts parents if deadline expired without completion.
    """
    logger.info("Nightly report, hourly location, and Walk Safe background scheduler activated.")
    last_sent_date = _get_last_nightly_sent_date()
    last_hourly_slot = _get_last_hourly_sent_slot()
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

            # 1. 22:00 Nightly Safety Report (Guaranteed at 22:00+ Tashkent time, exactly once per day)
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

                                # Discipline Status Check (Demo child gets bonus demo coins; real children are rewarded by parents)
                                tamper_events = telemetry.child_daily_tamper_log.get(ch.id, [])
                                today_tamper = [ev for ev in tamper_events if ev.get("time", "").startswith(today_str)]

                                if len(today_tamper) == 0:
                                    if ch.id == 2002:
                                        await crud.add_coins_to_user(session, ch.id, 50)
                                        discipline_line = "🎖️ <b>Kun Qahramoni (Demo):</b> Bugun hech qanday qoidabuzarlik qilinmadi (+50 tanga demo bonusi berildi)! 🌟\n"
                                    else:
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
                _save_last_nightly_sent_date(today_str)
                logger.info(f"Nightly safety reports dispatched for {today_str}.")

            # 1.5 Hourly Child Location Updates (Sent every 1 hour at EXACTLY :00 between 07:00 and 21:00 Tashkent time)
            current_hour_slot = now_tz.strftime("%Y-%m-%d-%H")
            if 7 <= now_tz.hour <= 21:
                # User requirement: If SOS is NOT active, only send at exact minute :00!
                if now_tz.minute == 0:
                    if last_hourly_slot != current_hour_slot:
                        logger.info(f"Triggering hourly location updates for {current_hour_slot} (Exact time: {now_tz.hour}:00)...")
                        async with AsyncSessionLocal() as session:
                            await dispatch_hourly_location_reports(bot, session, f"{now_tz.hour:02d}")
                            await session.commit()
                        last_hourly_slot = current_hour_slot
                        _save_last_hourly_sent_slot(current_hour_slot)
                else:
                    # If not minute 0, mark current hour slot handled so it never fires late/out-of-schedule
                    if last_hourly_slot != current_hour_slot:
                        last_hourly_slot = current_hour_slot
                        _save_last_hourly_sent_slot(current_hour_slot)

            # 1.6 Active SOS 20-Second Recurring Broadcast (When SOS is active, alerts parents every 20s)
            async with AsyncSessionLocal() as session:
                from sqlalchemy import select
                from db.models import User
                from bot.keyboards import get_sos_alert_parent_keyboard
                active_sos_res = await session.execute(
                    select(User).where(User.is_sos_active == True)
                )
                active_sos_children = list(active_sos_res.scalars().all())
                now_utc = datetime.now(timezone.utc)
                for ch in active_sos_children:
                    last_sos_time = telemetry.last_sos_alert_map.get(ch.id)
                    # Check if at least 20 seconds passed since last SOS broadcast
                    if not last_sos_time or (now_utc - last_sos_time) >= timedelta(seconds=20):
                        telemetry.last_sos_alert_map[ch.id] = now_utc
                        parents = await crud.get_parents_for_child(session, ch.id)
                        if parents:
                            latest_loc = await crud.get_latest_location(session, ch.id)
                            lat = latest_loc.latitude if latest_loc else None
                            lon = latest_loc.longitude if latest_loc else None
                            batt = f"{latest_loc.battery_level}%" if (latest_loc and latest_loc.battery_level is not None) else "84%"
                            cur_spd = latest_loc.speed if (latest_loc and latest_loc.speed is not None) else 0.0
                            if cur_spd >= 30.0:
                                speed_line = f"🚗 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat (Avtomobil / Transportda)"
                            elif cur_spd >= 6.0:
                                speed_line = f"🏃 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat (Yugurmoqda)"
                            elif cur_spd >= 1.5:
                                speed_line = f"🚶 <b>Hozirgi tezlik:</b> {cur_spd:.0f} km/soat (Piyoda yurmoqda)"
                            else:
                                speed_line = "🛑 <b>Hozirgi tezlik:</b> 0 km/soat (Harakatsiz / To'xtab turibdi)"

                            maps_url = f"https://www.google.com/maps/?q={lat},{lon}" if (lat and lon) else "Mavjud emas"
                            coord_str = f"{lat:.6f}, {lon:.6f}" if (lat and lon) else "Mavjud emas"
                            c_name = html.escape(ch.full_name or "Farzand")
                            now_time_str = datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")

                            sos_alert_text = (
                                f"🚨 <b>SOS! Farzandingiz {c_name} xavfda! (Batareya: {batt})</b>\n\n"
                                f"🚀 {speed_line}\n"
                                f"Joylashuv: {maps_url}\n"
                                f"Koordinata: <code>{coord_str}</code>\n"
                                f"🕒 Vaqt: <b>{now_time_str}</b>\n\n"
                                f"⚠️ <i>Xavf holati o'chirilmaguncha har 20 sekundda jo'natilmoqda!</i>"
                            )
                            for p in parents:
                                try:
                                    await bot.send_message(
                                        chat_id=p.id,
                                        text=sos_alert_text,
                                        reply_markup=get_sos_alert_parent_keyboard(ch.id, lat, lon),
                                        parse_mode="HTML"
                                    )
                                    if lat and lon:
                                        await bot.send_location(chat_id=p.id, latitude=lat, longitude=lon)
                                except Exception as e:
                                    logger.error(f"Failed to send 20s recurring SOS for child {ch.id} to parent {p.id}: {e}")

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

        await asyncio.sleep(5)
