import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime
from aiogram import Bot
from config import settings
from db.database import AsyncSessionLocal
import db.crud as crud
from api.webapp_api import get_owner_bot
from bot.scheduler import TASHKENT_TZ, dispatch_hourly_location_reports
import html
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

async def test_live_delivery():
    main_bot = Bot(token=settings.BOT_TOKEN)
    owner_bot = Bot(token=settings.OWNER_BOT_TOKEN)

    print("Fetching child 997185613 report...")
    async with AsyncSessionLocal() as session:
        rep = await crud.generate_daily_safety_report(session, 997185613)
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

        dist_str = rep.get("distance_formatted") or f"{rep.get('total_distance_km', 0)} km"
        batt_str = f"{rep['battery_level']}%" if rep['battery_level'] is not None else "Noma'lum"
        spd_str = f"{rep['max_speed_kmh']} km/soat" if rep['max_speed_kmh'] > 0 else "Piyoda"

        report_msg = (
            f"🌙 <b>KUNLIK XAVFSIZLIK HISOBOTI (Soat 00:00)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Farzand:</b> {c_name}\n\n"
            f"📍 <b>Tashrif buyurilgan joylar:</b>\n"
            f"   └ {zones_str}\n\n"
            f"⏱ <b>Xavfsiz joylarda bo'lgan vaqti:</b> {time_str}\n"
            f"🚶‍♂️ <b>Kuzatilgan masofa:</b> {dist_str} ({rep['points_count']} ta signal)\n"
            f"🚀 <b>Maksimal tezlik:</b> {spd_str}\n"
            f"🔋 <b>Batareya quvvati:</b> {batt_str}\n\n"
            f"✅ <b>Bajarilgan topshiriqlar:</b> {rep['completed_tasks_count']} ta (+{rep['earned_som']:,} so'm)\n"
            f"🎖️ <b>Kun Qahramoni:</b> Bugun hech qanday qoidabuzarlik qilinmadi! 🌟\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✨ <i>Farzandingiz xavfsizligi va kunlik intizomi to'liq nazoratda! Xayrli tun!</i>"
        )

        report_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗺 Xaritada marshrutni ko'rish",
                    web_app=WebAppInfo(url=f"{settings.WEBAPP_BASE_URL}?role=parent&parent_id=6707839718&child_id=997185613")
                )
            ],
            [
                InlineKeyboardButton(text="🔔 Baland signal chalish", callback_data="siren_child_997185613"),
                InlineKeyboardButton(text="🎁 +100 Tanga mukofot", callback_data="night_reward_997185613_100")
            ],
            [
                InlineKeyboardButton(text="🛌 Tungi uyqu rejimiga olish", callback_data="night_bedtime_997185613")
            ]
        ])

        print("Sending report to 6707839718 via Main Bot...")
        res_main = await main_bot.send_message(
            chat_id=6707839718,
            text=report_msg,
            reply_markup=report_kb,
            parse_mode="HTML"
        )
        print("Main Bot sent successfully! Msg ID:", res_main.message_id)

        print("Sending report to 6707839718 via Owner Bot...")
        res_owner = await owner_bot.send_message(
            chat_id=6707839718,
            text=report_msg,
            reply_markup=report_kb,
            parse_mode="HTML"
        )
        print("Owner Bot sent successfully! Msg ID:", res_owner.message_id)

    await main_bot.session.close()
    await owner_bot.session.close()

if __name__ == "__main__":
    asyncio.run(test_live_delivery())
