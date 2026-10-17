import logging
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import AsyncSessionLocal
import db.crud as crud
from bot.geofence import evaluate_geofences_and_notify, haversine_distance
from bot.keyboards import (
    get_role_selection_keyboard,
    get_parent_main_keyboard,
    get_child_main_keyboard,
    get_pairing_share_keyboard,
    get_child_details_keyboard,
    get_location_actions_keyboard,
    get_child_sos_call_keyboard,
    get_sos_alert_parent_keyboard,
    get_task_approval_keyboard,
    get_child_task_action_keyboard,
    get_kerakli_joylar_menu_keyboard,
    get_radius_selection_keyboard,
    get_settings_keyboard,
    get_logout_step1_keyboard,
    get_logout_step2_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


# FSM States
class ChildLinkState(StatesGroup):
    waiting_for_code = State()


class LogoutState(StatesGroup):
    waiting_for_confirm = State()


class KerakliJoyAddState(StatesGroup):
    waiting_for_name = State()
    waiting_for_location = State()
    waiting_for_radius = State()


class TaskCreateState(StatesGroup):
    waiting_for_child = State()
    waiting_for_title = State()
    waiting_for_reward = State()


class PhoneSaveState(StatesGroup):
    waiting_for_phone = State()


# ---------------------------------------------------------
# /start & Onboarding Handlers
# ---------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    """Handle /start command, deep-linking and role selection."""
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    username = message.from_user.username

    async with AsyncSessionLocal() as session:
        user, _ = await crud.get_or_create_user(
            session=session,
            user_id=user_id,
            full_name=full_name,
            username=username
        )

        # Check if deep-link pairing code was passed: /start pair_123456
        args = command.args
        if args and args.startswith("pair_"):
            pairing_code = args.replace("pair_", "").strip()
            parent = await crud.get_parent_by_pairing_code(session, pairing_code)
            if parent:
                await crud.update_user_role(session, user_id, "child")
                await crud.link_parent_child(session, parent.id, user_id, nickname=full_name)
                await session.commit()

                # Notify child
                await message.answer(
                    f"🎉 **Tabriklaymiz!**\n\n"
                    f"Siz **{parent.full_name or 'Ota-onangiz'}** hisobiga muvaffaqiyatli ulandingiz.\n"
                    f"Quyidagi xavfsizlik menyusidan foydalanishingiz mumkin:",
                    reply_markup=get_child_main_keyboard(),
                    parse_mode="Markdown"
                )

                # Notify parent
                try:
                    await bot.send_message(
                        chat_id=parent.id,
                        text=(
                            f"🎉 **Yangi farzand ulandi!**\n\n"
                            f"👤 Ismi: **{full_name}**\n"
                            f"🆔 ID: `{user_id}`\n\n"
                            f"Endi farzandingizning joylashuvini bir bosishda ko'rishingiz mumkin."
                        ),
                        reply_markup=get_parent_main_keyboard(parent.id),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify parent about new child link: {e}")
                return

        # If user is already assigned a role
        if user.role == "parent":
            children = await crud.get_children_for_parent(session, user.id)
            await message.answer(
                f"Assalomu alaykum, **{full_name}**!\n\n"
                f"👨‍👩‍👧 Siz **Ota-ona** rejimidasiz.\n"
                f"Ulangan farzandlar soni: **{len(children)} nafar**.\n\n"
                f"Farzandingiz qayerdaligini bilish uchun pastdagi **'📍 Farzandning hozirgi joylashuvi'** tugmasini bosing.",
                reply_markup=get_parent_main_keyboard(user.id),
                parse_mode="Markdown"
            )
            return

        elif user.role == "child":
            parents = await crud.get_parents_for_child(session, user.id)
            parent_names = ", ".join([p.full_name or str(p.id) for p in parents]) if parents else "Ulangan ota-ona yo'q"
            await message.answer(
                f"Salom, **{full_name}**!\n\n"
                f"👦 Siz **Farzand** rejimidasiz.\n"
                f"Ulangan ota-ona: **{parent_names}**.\n\n"
                f"Har qanday favqulodda vaziyatda darhol **'🚨 SOS'** tugmasini bosing!",
                reply_markup=get_child_main_keyboard(),
                parse_mode="Markdown"
            )
            return

        # If user has no role yet, ask for role selection
        await message.answer(
            f"Assalomu alaykum, **{full_name}**!\n\n"
            f"🌟 **Smart Bolalar Nazorati & Joylashuv Tizimi**ga xush kelibsiz!\n\n"
            f"Iltimos, tizimdan foydalanish uchun o'z rolingizni tanlang:",
            reply_markup=get_role_selection_keyboard(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("set_role:"))
async def on_role_selected(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle role selection callback."""
    role = callback.data.split(":")[1]
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        await crud.update_user_role(session, user_id, role)

        if role == "parent":
            code = await crud.generate_pairing_code(session, user_id)
            await session.commit()

            bot_info = await bot.get_me()
            await callback.message.delete()
            await callback.message.answer(
                f"👨‍👩‍👧 **Ota-ona hisobi faollashtirildi!**\n\n"
                f"Farzandingizni ulash uchun unga quyidagi 6 xonali ulanish kodini bering:\n\n"
                f"🔑 Ulanish kodi: ` {code} `\n\n"
                f"Yoki pastdagi tugma orqali to'g'ridan-to'g'ri havolani farzandingizga yuboring:",
                reply_markup=get_pairing_share_keyboard(bot_info.username, code),
                parse_mode="Markdown"
            )
            await callback.message.answer(
                "Asosiy boshqaruv menyusi faollashtirildi:",
                reply_markup=get_parent_main_keyboard(user_id)
            )

        elif role == "child":
            await session.commit()
            await callback.message.delete()
            await state.set_state(ChildLinkState.waiting_for_code)
            await callback.message.answer(
                "👦 **Farzand hisobi tanlandi!**\n\n"
                "Iltimos, ota-onangiz bergan **6 xonali ulanish kodi**ni kiriting (masalan: `123456`):\n\n"
                "*(Agar adashib bosgan bo'lsangiz, bekor qilish uchun /chiqish deb yozing)*",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )

    await callback.answer()


@router.message(ChildLinkState.waiting_for_code)
async def on_child_code_entered(message: Message, state: FSMContext, bot: Bot):
    """Process pairing code entered manually by child."""
    code = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    full_name = message.from_user.full_name

    if code.lower() in ["/cancel", "/chiqish", "/logout", "bekor", "cancel"]:
        await state.clear()
        async with AsyncSessionLocal() as session:
            await crud.reset_user_role(session, user_id)
            await session.commit()
        await message.answer(
            "Amal bekor qilindi. O'z rolingizni qaytadan tanlashingiz mumkin:",
            reply_markup=get_role_selection_keyboard()
        )
        return

    async with AsyncSessionLocal() as session:
        parent = await crud.get_parent_by_pairing_code(session, code)
        if not parent:
            await message.answer(
                "❌ **Kiritilgan kod noto'g'ri yoki mavjud emas!**\n\n"
                "Iltimos, ota-onangizdan kodni qayta so'rab, tekshirib kiriting:",
                parse_mode="Markdown"
            )
            return

        # Link child to parent
        await crud.link_parent_child(session, parent.id, user_id, nickname=full_name)
        await session.commit()
        await state.clear()

        await message.answer(
            f"🎉 **Ajoyib! Ota-onangizga ({parent.full_name or 'Ota-ona'}) muvaffaqiyatli ulandingiz!**\n\n"
            f"Quyidagi xavfsizlik menyusi faollashtirildi:",
            reply_markup=get_child_main_keyboard(),
            parse_mode="Markdown"
        )

        try:
            await bot.send_message(
                chat_id=parent.id,
                text=(
                    f"🎉 **Yangi farzand muvaffaqiyatli ulandi!**\n\n"
                    f"👤 Farzand: **{full_name}**\n"
                    f"🆔 Telegram ID: `{user_id}`\n\n"
                    f"Endi farzandingizning joriy joylashuvini bir tugma bilan ko'ra olasiz."
                ),
                reply_markup=get_parent_main_keyboard(parent.id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about code link: {e}")


# ---------------------------------------------------------
# Native Telegram Real-Time Location Dispatcher
# ---------------------------------------------------------

@router.message(F.text == "📍 Farzandning hozirgi joylashuvi")
async def on_request_current_location(message: Message, bot: Bot):
    """Parent requests the live location pin of their child directly in Telegram."""
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer(
                "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                "Farzand ulash uchun **'➕ Farzand ulash (Kodi)'** tugmasini bosing.",
                parse_mode="Markdown"
            )
            return

        # If parent has 1 child, immediately locate them
        if len(children) == 1:
            child = children[0]
            await dispatch_child_location_to_parent(bot, message.chat.id, child, session)
        else:
            # If multiple children, show selection buttons
            buttons = [
                [InlineKeyboardButton(text=f"📍 {c.full_name}", callback_data=f"locate_child:{c.id}")]
                for c in children
            ]
            await message.answer(
                "Qaysi farzandingizning joylashuvini ko'rmoqchisiz?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )


@router.callback_query(F.data.startswith("locate_child:"))
async def on_locate_child_callback(callback: CallbackQuery, bot: Bot):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if not child:
            await callback.answer("Farzand topilmadi.", show_alert=True)
            return

        await dispatch_child_location_to_parent(bot, callback.message.chat.id, child, session)
    await callback.answer()


async def dispatch_child_location_to_parent(bot: Bot, chat_id: int, child, session: AsyncSession):
    """Send child's native Telegram location pin, Kerakli joy status, battery, and address info."""
    latest_loc = await crud.get_latest_location(session, child.id)

    if not latest_loc:
        # Prompt child to send location
        try:
            await bot.send_message(
                chat_id=child.id,
                text="🔔 **Ota-onangiz joriy joylashuvingizni so'radi!**\n\nIltimos, pastdagi **'📍 Jonli joylashuvimni yuborish'** tugmasini bosing.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warn(f"Failed to ping child for location: {e}")

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"📍 **{child.full_name}** hali birorta ham joylashuv yubormagan.\n\n"
                f"Farzandingiz telefoniga joylashuvni yangilash haqida eslatma yuborildi."
            ),
            parse_mode="Markdown"
        )
        return

    # Check which Kerakli joy the child is currently inside
    fences = await crud.get_active_geofences_for_child(session, child.id)
    current_zone = None
    min_dist = float("inf")

    for fence in fences:
        dist = haversine_distance(latest_loc.latitude, latest_loc.longitude, fence.latitude, fence.longitude)
        if dist <= fence.radius_meters and dist < min_dist:
            min_dist = dist
            current_zone = fence.name

    if current_zone:
        zone_str = f"✅ **'{current_zone}'** hududida (markazdan {int(min_dist)}m)"
    else:
        zone_str = "📍 Kerakli joylar chegarasidan tashqarida (yo'lda)"

    battery_str = f"{latest_loc.battery_level}%" if latest_loc.battery_level is not None else "Noma'lum"
    charging_icon = "⚡ (Quvvatlanmoqda)" if latest_loc.is_charging else ""
    time_str = latest_loc.recorded_at.strftime("%H:%M:%S") if latest_loc.recorded_at else "Hozirgina"

    # 1. Send native Telegram Map Location Pin!
    try:
        await bot.send_location(
            chat_id=chat_id,
            latitude=latest_loc.latitude,
            longitude=latest_loc.longitude
        )
    except Exception as e:
        logger.error(f"Failed to send native location pin: {e}")

    # 2. Send detailed status card
    card_text = (
        f"👤 Farzand: **{child.full_name}**\n"
        f"🕒 Oxirgi signal: **{time_str}**\n"
        f"🔋 Batareya: **{battery_str}** {charging_icon}\n"
        f"📍 Joylashuv: {zone_str}\n"
        f"📌 Koordinata: `{latest_loc.latitude:.5f}, {latest_loc.longitude:.5f}`"
    )

    await bot.send_message(
        chat_id=chat_id,
        text=card_text,
        reply_markup=get_location_actions_keyboard(latest_loc.latitude, latest_loc.longitude, child.id),
        parse_mode="Markdown"
    )

    # Ping child's bot that parent saw location
    try:
        await bot.send_message(
            chat_id=child.id,
            text="🔔 Ota-onangiz joriy joylashuvingizni ko'rdi. Joyingiz o'zgarganda '📍 Jonli joylashuvimni yuborish' tugmasini bosishni unutmang.",
            parse_mode="Markdown"
        )
    except Exception:
        pass


# ---------------------------------------------------------
# Kerakli Joylar (Safe Zones) Management
# ---------------------------------------------------------

@router.message(F.text == "📍 Kerakli joylar")
async def on_kerakli_joylar_menu(message: Message):
    """Parent opens Kerakli joylar menu."""
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        fences = await crud.get_geofences_for_parent(session, parent_id)
        text = (
            f"📍 **Kerakli joylar (Maktab, Uy, O'quv markazi va h.k.)**\n\n"
            f"Ro'yxatdagi joylar soni: **{len(fences)} ta**.\n\n"
            f"Farzandingiz ushbu joylarga yetib borganda yoki chiqib ketganda sizga darhol xabar keladi.\n\n"
            f"💡 _Yangi joy qo'shish uchun pastdagi '➕ Yangi kerakli joy qo'shish' tugmasini bosing yoki shunchaki Telegram orqali ushbu chatga lokatsiya yuboring._"
        )
        await message.answer(
            text,
            reply_markup=get_kerakli_joylar_menu_keyboard(fences),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "add_new_geofence")
async def on_add_new_kerakli_joy_click(callback: CallbackQuery, state: FSMContext):
    """Start Kerakli joy creation flow."""
    await state.set_state(KerakliJoyAddState.waiting_for_name)
    await callback.message.answer(
        "🏷 **Yangi kerakli joy nomini kiriting:**\n(Masalan: _'Maktab №45'_, _'Uyimiz'_, _'Ingliz tili kursi'_)",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(KerakliJoyAddState.waiting_for_name)
async def on_kerakli_joy_name_entered(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(KerakliJoyAddState.waiting_for_location)

    await message.answer(
        f"📍 **'{name}' joylashuvini yuboring:**\n\n"
        f"Buning uchun Telegram'dagi qisqich (📎) tugmasini bosing va **Joylashuv (Location)** yuboring.",
        parse_mode="Markdown"
    )


@router.message(KerakliJoyAddState.waiting_for_location, F.location)
async def on_kerakli_joy_location_received(message: Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude
    await state.update_data(lat=lat, lon=lon)
    await state.set_state(KerakliJoyAddState.waiting_for_radius)

    await message.answer(
        "📏 **Ushbu joy radiusi (qamrovi)ni tanlang:**",
        reply_markup=get_radius_selection_keyboard()
    )


@router.callback_query(KerakliJoyAddState.waiting_for_radius, F.data.startswith("radius:"))
async def on_kerakli_joy_radius_selected(callback: CallbackQuery, state: FSMContext):
    radius = float(callback.data.split(":")[1])
    data = await state.get_data()
    name = data["name"]
    lat = data["lat"]
    lon = data["lon"]
    parent_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        await crud.create_geofence(
            session=session,
            parent_id=parent_id,
            name=name,
            latitude=lat,
            longitude=lon,
            radius_meters=radius
        )
        await session.commit()

    await state.clear()
    await callback.message.edit_text(
        f"✅ **Yangi kerakli joy muvaffaqiyatli saqlandi!**\n\n"
        f"📍 Nomi: **{name}**\n"
        f"📏 Radiusi: **{int(radius)} metr**\n"
        f"📌 Koordinatalar: `{lat:.5f}, {lon:.5f}`\n\n"
        f"Farzandingiz bu joyga yetib kelganda yoki chiqqanda sizga avtomatik bildirishnoma boradi.",
        parse_mode="Markdown"
    )
    await callback.answer("Saqlandi!")


@router.callback_query(F.data.startswith("delete_fence:"))
async def on_delete_fence(callback: CallbackQuery):
    fence_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        success = await crud.delete_geofence(session, fence_id, parent_id)
        await session.commit()

    if success:
        await callback.answer("Kerakli joy o'chirildi!", show_alert=True)
        await callback.message.delete()
    else:
        await callback.answer("O'chirishda xatolik yuz berdi.", show_alert=True)


# ---------------------------------------------------------
# Battery & Phone Status Check ("🔋 Batareya va Holat")
# ---------------------------------------------------------

@router.message(F.text == "🔋 Batareya va Holat")
async def on_battery_status_pressed(message: Message):
    """Parent checks battery levels and phone profiles of children."""
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer("Sizga hali farzand ulanmagan.")
            return

        text = "🔋 **Farzandlar telefonining holati:**\n\n"

        for ch in children:
            latest = await crud.get_latest_location(session, ch.id)
            if latest and latest.battery_level is not None:
                lvl = latest.battery_level
                charging_icon = "⚡ Quvvatlanmoqda" if latest.is_charging else "Quvvatlanmayapti"
                
                if lvl <= 20:
                    status_emoji = "🪫 XAVFLI (Kam)"
                elif lvl <= 50:
                    status_emoji = "🟡 O'rtacha"
                else:
                    status_emoji = "🟢 Yetarli"

                sound_mode = "Oddiy"
                if latest.sound_mode == "vibrate":
                    sound_mode = "Vibratsiya"
                elif latest.sound_mode == "silent":
                    sound_mode = "Ovozsiz"

                time_str = latest.recorded_at.strftime("%H:%M:%S")

                text += (
                    f"👤 **{ch.full_name}**\n"
                    f"• Batareya: **{lvl}%** ({status_emoji})\n"
                    f"• Holat: {charging_icon}\n"
                    f"• Ovoz rejimi: **{sound_mode}**\n"
                    f"• Oxirgi signal: **{time_str}**\n\n"
                )
            else:
                text += f"👤 **{ch.full_name}** — Telemetriya ma'lumotlari hali olinmagan\n\n"

        await message.answer(text, parse_mode="Markdown")


# ---------------------------------------------------------
# Daily Route Summary ("📊 Bugungi hisobot")
# ---------------------------------------------------------

@router.message(F.text == "📊 Bugungi hisobot")
async def on_daily_summary_pressed(message: Message):
    """Parent checks summary of movements today."""
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer("Sizga hali farzand ulanmagan.")
            return

        text = "📊 **Bugungi harakat hisoboti:**\n\n"

        for ch in children:
            logs = await crud.get_location_history_today(session, ch.id, limit=200)
            text += f"👤 **{ch.full_name}:**\n"
            text += f"• Qayd etilgan nuqtalar: **{len(logs)} ta**\n"
            if logs:
                first = logs[0]
                last = logs[-1]
                text += f"• Birinchi signal: {first.recorded_at.strftime('%H:%M')}\n"
                text += f"• Oxirgi signal: {last.recorded_at.strftime('%H:%M')}\n"
                if last.battery_level is not None:
                    text += f"• Hozirgi batareya: **{last.battery_level}%**\n"
            else:
                text += "• Bugun hali harakat qayd etilmagan.\n"
            text += "\n"

        await message.answer(text, parse_mode="Markdown")


# ---------------------------------------------------------
# Emergency SOS & Calling
# ---------------------------------------------------------

@router.message(F.text == "🚨 SOS (Xavf tugmasi)")
async def on_child_sos_pressed(message: Message, bot: Bot):
    """Child activates SOS emergency."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if not child or child.role != "child":
            await message.answer("Ushbu tugma farzand hisobi uchun mo'ljallangan.")
            return

        await crud.set_child_sos(session, child_id, True)
        parents = await crud.get_parents_for_child(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)
        await session.commit()

        if not parents:
            await message.answer("⚠️ Siz hali ota-ona hisobiga ulanmagansiz!")
            return

        battery_info = f"{latest_loc.battery_level}%" if latest_loc and latest_loc.battery_level else "Noma'lum"
        loc_coords = f"{latest_loc.latitude}, {latest_loc.longitude}" if latest_loc else "Mavjud emas"
        maps_link = (
            f"https://www.google.com/maps/search/?api=1&query={latest_loc.latitude},{latest_loc.longitude}"
            if latest_loc else "Joylashuv hali yozilmagan"
        )

        parent_alert = (
            f"🚨🚨🚨 **FAVQULODDA VAZIYAT (SOS)!** 🚨🚨🚨\n\n"
            f"Farzandingiz **{child_name}** xavf tugmasini bosdi!\n\n"
            f"🕒 Vaqt: **{now_str}**\n"
            f"🔋 Batareya: **{battery_info}**\n"
            f"📍 Koordinata: `{loc_coords}`\n"
            f"🗺 Xaritada: [Google Maps orqali ochish]({maps_link})\n\n"
            f"⚠️ **DIQQAT:** Farzandingiz bilan zudlik bilan bog'laning!"
        )

        parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None

        for parent in parents:
            try:
                await bot.send_message(
                    chat_id=parent.id,
                    text=parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, latest_loc.latitude if latest_loc else None, latest_loc.longitude if latest_loc else None),
                    parse_mode="Markdown"
                )
                if latest_loc:
                    await bot.send_location(
                        chat_id=parent.id,
                        latitude=latest_loc.latitude,
                        longitude=latest_loc.longitude
                    )
            except Exception as e:
                logger.error(f"Failed to send SOS to parent {parent.id}: {e}")

        # Send immediate emergency response to child with Direct Call button
        await message.answer(
            "🚨 **SOS SIGNALI OTA-ONANGIZGA YUBORILDI!**\n\n"
            "Iltimos, xotirjam bo'ling. Ota-onangizga xabar va joriy joylashuvingiz yetkazildi.\n\n"
            "📞 **Darhol ota-onangizga qo'ng'iroq qiling** yoki ovozli xabar (golos) yuboring — biz uni yetkazamiz!",
            reply_markup=get_child_sos_call_keyboard(parent_phone),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "child_cancel_sos")
async def on_child_cancel_sos(callback: CallbackQuery, bot: Bot):
    """Child reports the danger is cleared."""
    child_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        await crud.set_child_sos(session, child_id, False)
        parents = await crud.get_parents_for_child(session, child_id)
        await session.commit()

        for p in parents:
            try:
                await bot.send_message(
                    chat_id=p.id,
                    text=f"✅ Farzandingiz ({callback.from_user.full_name}) xavf bartaraf bo'lganini xabar qildi.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    await callback.message.edit_text("✅ Xavfsizlik tiklandi va ota-onangizga xabar berildi.")
    await callback.answer()


@router.message(F.text == "📞 Ota-onamga qo'ng'iroq")
async def on_child_call_parent_pressed(message: Message):
    """Child wants to call parent directly."""
    child_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents:
            await message.answer("Sizga hali ota-ona ulanmagan.")
            return

        text = "📞 **Ota-onangiz bilan bog'lanish:**\n\n"
        buttons = []
        for p in parents:
            text += f"👤 **{p.full_name}**"
            if p.phone_number:
                text += f": `{p.phone_number}`\n"
                clean_phone = p.phone_number.replace(" ", "").replace("-", "")
                buttons.append([InlineKeyboardButton(text=f"📞 {p.full_name}ga qo'ng'iroq qilish", url=f"tel:{clean_phone}")])
            else:
                text += f" (@{p.username or 'yoq'})\n"

        if not buttons:
            text += "\n💡 _Ota-onangiz o'z telefon raqamini botga kiritmagan. Telegram orqali yozishingiz mumkin._"

        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
            parse_mode="Markdown"
        )


@router.message(F.text == "📞 Oila kontaktlari")
async def on_family_contacts_menu(message: Message, state: FSMContext):
    """Parent configures phone number for direct emergency calling."""
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, parent_id)
        phone = user.phone_number if user and user.phone_number else "Kiritilmagan"

        await state.set_state(PhoneSaveState.waiting_for_phone)
        await message.answer(
            f"📞 **Oila Favqulodda Kontaktlari**\n\n"
            f"Hozirgi telefon raqamingiz: **{phone}**\n\n"
            f"Farzandingiz SOS tugmasini bosganda to'g'ridan-to'g'ri bir tugma bilan sizga qo'ng'iroq qilishi uchun telefon raqamingizni kiriting (masalan: `+998901234567`):",
            parse_mode="Markdown"
        )


@router.message(PhoneSaveState.waiting_for_phone)
async def on_phone_entered(message: Message, state: FSMContext):
    phone = message.text.strip()
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, parent_id)
        if user:
            user.phone_number = phone
            await session.commit()

    await state.clear()
    await message.answer(
        f"✅ **Telefon raqamingiz saqlandi:** `{phone}`\n\n"
        f"Endi farzandingiz SOS xavf tugmasini bosganida darhol sizga bir bosishda qo'ng'iroq qila oladi.",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------
# Child Native Location Handling (Location Button or Live)
# ---------------------------------------------------------

@router.message(F.location)
@router.edited_message(F.location)
async def on_location_received(message: Message, bot: Bot):
    """Handle incoming location from child."""
    user_id = message.from_user.id
    location = message.location
    if not location:
        return

    lat = location.latitude
    lon = location.longitude
    accuracy = getattr(location, "horizontal_accuracy", None)
    live_period = getattr(location, "live_period", None)
    source = "telegram_live" if live_period else "telegram_pin"

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user:
            return

        # If user is parent and sent location -> Ask to add as Kerakli joy!
        if user.role == "parent":
            caption = message.caption or "Yangi kerakli joy"
            await crud.create_geofence(
                session=session,
                parent_id=user_id,
                name=caption,
                latitude=lat,
                longitude=lon,
                radius_meters=200.0
            )
            await session.commit()
            await message.answer(
                f"✅ **Yangi kerakli joy avtomatik qo'shildi!**\n\n"
                f"📍 Nomi: **{caption}**\n"
                f"📏 Radiusi: **200 metr**\n"
                f"📌 Koordinata: `{lat:.5f}, {lon:.5f}`\n\n"
                f"Farzandingiz ushbu manzilga yetib borganda sizga darhol xabar keladi.",
                parse_mode="Markdown"
            )
            return

        if user.role != "child":
            return

        # Save Location Log
        await crud.log_location(
            session=session,
            child_id=user_id,
            latitude=lat,
            longitude=lon,
            accuracy=accuracy,
            source=source
        )

        # Check Kerakli joylar entry/exit
        await evaluate_geofences_and_notify(
            bot=bot,
            session=session,
            child=user,
            lat=lat,
            lon=lon
        )

        # If child in SOS, forward to parents
        if user.is_sos_active:
            parents = await crud.get_parents_for_child(session, user_id)
            for p in parents:
                try:
                    await bot.send_location(chat_id=p.id, latitude=lat, longitude=lon)
                except Exception:
                    pass

        await session.commit()

    if not live_period:
        await message.answer("📍 Joylashuvingiz qabul qilindi va tizimda yangilandi!", parse_mode="Markdown")


# ---------------------------------------------------------
# Check-in Handler ("✅ Yetib keldim")
# ---------------------------------------------------------

@router.message(F.text.startswith("✅ Yetib keldim"))
async def on_checkin_pressed(message: Message, bot: Bot):
    """Child checks in."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name
    now_str = datetime.now().strftime("%H:%M")

    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)

        if not parents:
            await message.answer("Siz hali biron bir ota-onaga ulanmagansiz.")
            return

        # Find which Kerakli joy
        place_info = ""
        if latest_loc:
            fences = await crud.get_active_geofences_for_child(session, child_id)
            for f in fences:
                dist = haversine_distance(latest_loc.latitude, latest_loc.longitude, f.latitude, f.longitude)
                if dist <= f.radius_meters:
                    place_info = f"\n📍 Manzil: **'{f.name}'**"
                    break

        for parent in parents:
            try:
                checkin_text = (
                    f"✅ **YETIB KELDIM (Check-in)!**\n\n"
                    f"Farzandingiz **{child_name}** manzilga eson-omon yetib kelganini xabar qildi.{place_info}\n"
                    f"🕒 Vaqt: **{now_str}**"
                )
                await bot.send_message(
                    chat_id=parent.id,
                    text=checkin_text,
                    parse_mode="Markdown"
                )
                if latest_loc:
                    await bot.send_location(
                        chat_id=parent.id,
                        latitude=latest_loc.latitude,
                        longitude=latest_loc.longitude
                    )
            except Exception as e:
                logger.error(f"Failed to send checkin to parent {parent.id}: {e}")

        await message.answer(
            f"🌟 **Rahmat!** Ota-onangizga soat {now_str} da manzilga eson-omon yetib borganingiz bildirildi.",
            parse_mode="Markdown"
        )


# ---------------------------------------------------------
# Child Media Forwarding (Voice & Photo in Emergency)
# ---------------------------------------------------------

@router.message(F.voice | F.audio | F.photo)
async def on_child_media_received(message: Message, bot: Bot):
    """Forward voice notes and photos from child to linked parents."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name

    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if not child or child.role != "child":
            return

        parents = await crud.get_parents_for_child(session, child_id)
        if not parents:
            return

        for parent in parents:
            try:
                if message.voice:
                    await bot.send_voice(
                        chat_id=parent.id,
                        voice=message.voice.file_id,
                        caption=f"🎙 **Farzandingizdan ({child_name}) ovozli xabar!**",
                        parse_mode="Markdown"
                    )
                elif message.photo:
                    await bot.send_photo(
                        chat_id=parent.id,
                        photo=message.photo[-1].file_id,
                        caption=f"📷 **Farzandingizdan ({child_name}) yuborilgan surat!**",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Failed to forward child media: {e}")

        await message.answer("✅ Xabaringiz ota-onangizga yetkazildi.")


# ---------------------------------------------------------
# Tasks & Gamification
# ---------------------------------------------------------

@router.message(F.text == "📋 Vazifalarim va Tangalar")
async def on_child_tasks_pressed(message: Message):
    """Child views their assigned tasks and current coin balance."""
    child_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        tasks = await crud.get_tasks_for_child(session, child_id)

        balance = child.virtual_balance if child else 0
        text = f"💰 **Sizning balansingiz:** `{balance:,}` so'm / tanga\n\n"

        pending_tasks = [t for t in tasks if t.status == "pending"]
        completed_tasks = [t for t in tasks if t.status == "completed_by_child"]
        approved_tasks = [t for t in tasks if t.status == "approved"]

        if not tasks:
            text += "Hozircha sizga berilgan vazifalar yo'q. Yaxshi dam oling! 🎮"
            await message.answer(text, parse_mode="Markdown")
            return

        text += f"📌 **Bajarilishi kerak bo'lgan vazifalar ({len(pending_tasks)} ta):**\n"
        for t in pending_tasks:
            text += f"▫️ **{t.title}** — Mukofot: `+{t.reward_coins}` tanga\n"
            if t.description:
                text += f"   _{t.description}_\n"

        if completed_tasks:
            text += f"\n⏳ **Tekshiruvdagi vazifalar ({len(completed_tasks)} ta):**\n"
            for t in completed_tasks:
                text += f"▫️ {t.title} (+{t.reward_coins} tanga) — _ota-ona tasdiqlashini kutmoqda_\n"

        if approved_tasks:
            text += f"\n🏆 **Bajarilgan vazifalar ({len(approved_tasks)} ta):**\n"
            for t in approved_tasks[:3]:
                text += f"✅ {t.title} (+{t.reward_coins} tanga berildi)\n"

        await message.answer(text, parse_mode="Markdown")

        for t in pending_tasks:
            await message.answer(
                f"🎯 **Vazifa:** {t.title}\n💰 Mukofot: {t.reward_coins} tanga",
                reply_markup=get_child_task_action_keyboard(t.id),
                parse_mode="Markdown"
            )


@router.callback_query(F.data.startswith("complete_task:"))
async def on_complete_task_clicked(callback: CallbackQuery, bot: Bot):
    """Child submits task for parent approval."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.submit_task_completed(session, task_id)
        if not task:
            await callback.answer("Vazifa topilmadi yoki allaqachon bajarilgan.", show_alert=True)
            return

        child = await crud.get_user(session, task.child_id)
        child_name = child.full_name if child else "Farzandingiz"

        approval_text = (
            f"🔔 **Vazifa bajarildi deb bildirildi!**\n\n"
            f"👤 Farzand: **{child_name}**\n"
            f"📌 Vazifa: **{task.title}**\n"
            f"💰 Mukofot: **{task.reward_coins:,}** tanga\n\n"
            f"Vazifa to'g'ri bajarilgan bo'lsa, tasdiqlang:"
        )

        try:
            await bot.send_message(
                chat_id=task.parent_id,
                text=approval_text,
                reply_markup=get_task_approval_keyboard(task.id, task.reward_coins),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about task completion: {e}")

        await session.commit()

    await callback.message.edit_text("⏳ Vazifa ota-onangizga tekshirish uchun yuborildi!")
    await callback.answer("Yuborildi!")


@router.callback_query(F.data.startswith("approve_task:"))
async def on_approve_task(callback: CallbackQuery, bot: Bot):
    """Parent approves task and rewards coins."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.approve_task(session, task_id)
        if not task:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return

        child = await crud.get_user(session, task.child_id)
        child_name = child.full_name if child else "Farzand"
        new_balance = child.virtual_balance if child else 0

        try:
            await bot.send_message(
                chat_id=task.child_id,
                text=(
                    f"🎉🎉 **TABRIKLAYMIZ!** 🎉🎉\n\n"
                    f"Ota-onangiz **'{task.title}'** vazifangizni tasdiqladi!\n"
                    f"💰 Balansingizga: **+{task.reward_coins:,} tanga** qo'shildi!\n"
                    f"Hozirgi umumiy balansingiz: **{new_balance:,} tanga**."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of approved task: {e}")

        await session.commit()

    await callback.message.edit_text(
        f"✅ **Vazifa tasdiqlandi!**\n\n"
        f"Farzandingiz hisobiga **+{task.reward_coins:,} tanga** muvaffaqiyatli o'tkazildi."
    )
    await callback.answer("Tasdiqlandi!")


@router.callback_query(F.data.startswith("reject_task:"))
async def on_reject_task(callback: CallbackQuery, bot: Bot):
    """Parent rejects task."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.reject_task(session, task_id)
        if task:
            try:
                await bot.send_message(
                    chat_id=task.child_id,
                    text=f"❌ **'{task.title}'** vazifasi ota-onangiz tomonidan rad etildi. Iltimos, vazifani to'liq bajarib, qayta urinib ko'ring.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify child of rejected task: {e}")
        await session.commit()

    await callback.message.edit_text("❌ Vazifa rad etildi.")
    await callback.answer("Rad etildi!")


@router.message(F.text == "📋 Vazifalar va Tangalar")
async def on_parent_tasks_menu(message: Message):
    """Parent views tasks and option to assign new tasks."""
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer("Sizga hali hech qanday farzand ulanmagan.")
            return

        tasks = await crud.get_tasks_for_parent(session, parent_id)
        text = f"📋 **Vazifalar boshqaruvi**\n\nJami yaratilgan vazifalar: {len(tasks)} ta.\n\n"

        for child in children:
            child_tasks = [t for t in tasks if t.child_id == child.id and t.status in ["pending", "completed_by_child"]]
            text += f"👤 **{child.full_name}** (Balans: {child.virtual_balance:,} tanga)\n"
            if child_tasks:
                for t in child_tasks:
                    status_text = "⏳ Tekshiruvda" if t.status == "completed_by_child" else "▫️ Bajarilmoqda"
                    text += f"  - {t.title} ({t.reward_coins:,} tanga) [{status_text}]\n"
            else:
                text += "  - Faol vazifalar yo'q\n"
            text += "\n"

        buttons = []
        for child in children:
            buttons.append([
                InlineKeyboardButton(
                    text=f"➕ {child.full_name}ga vazifa qo'shish",
                    callback_data=f"create_task:{child.id}"
                )
            ])

        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("create_task:"))
async def on_start_create_task(callback: CallbackQuery, state: FSMContext):
    """Start task creation FSM."""
    child_id = int(callback.data.split(":")[1])
    await state.update_data(child_id=child_id)
    await state.set_state(TaskCreateState.waiting_for_title)

    await callback.message.answer(
        "📝 **Yangi vazifa nomini kiriting:**\n\n(Masalan: _'Matematika darslarini qilish'_, _'Xonani tartibga keltirish'_, _'Uyga vaqtida kelish'_)",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(TaskCreateState.waiting_for_title)
async def on_task_title_entered(message: Message, state: FSMContext):
    title = message.text.strip()
    await state.update_data(title=title)
    await state.set_state(TaskCreateState.waiting_for_reward)

    await message.answer(
        f"💰 **Ushbu vazifa uchun mukofot miqdorini tanga / so'mda kiriting:**\n(Masalan: `5000` yoki `10000`):",
        parse_mode="Markdown"
    )


@router.message(TaskCreateState.waiting_for_reward)
async def on_task_reward_entered(message: Message, state: FSMContext, bot: Bot):
    try:
        reward = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("Iltimos, faqat butun son kiriting (masalan: `5000`):")
        return

    data = await state.get_data()
    child_id = data["child_id"]
    title = data["title"]
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        await crud.create_task(
            session=session,
            parent_id=parent_id,
            child_id=child_id,
            title=title,
            reward_coins=reward
        )
        await session.commit()

        try:
            await bot.send_message(
                chat_id=child_id,
                text=(
                    f"🎯 **SIZGA YANGI VAZIFA BERILDI!**\n\n"
                    f"📌 Vazifa: **{title}**\n"
                    f"💰 Mukofot: **+{reward:,} tanga**!\n\n"
                    f"Vazifani bajargach, '📋 Vazifalarim' bo'limidan 'Bajarildi' deb yuboring!"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child: {e}")

    await state.clear()
    await message.answer(
        f"✅ **Vazifa muvaffaqiyatli saqlandi va farzandga yuborildi!**\n\n"
        f"📌 Nomi: **{title}**\n"
        f"💰 Mukofot: **{reward:,} tanga**",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------
# Parent Children List & Pairing Code
# ---------------------------------------------------------

@router.message(F.text == "👶 Farzandlarim")
async def on_list_children_pressed(message: Message):
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer(
                "Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                "Farzand ulash uchun **'➕ Farzand ulash (Kodi)'** tugmasini bosing.",
                parse_mode="Markdown"
            )
            return

        text = f"👶 **Ulangan farzandlar ro'yxati ({len(children)} nafar):**\n\n"
        buttons = []

        for ch in children:
            latest_loc = await crud.get_latest_location(session, ch.id)
            battery = f"{latest_loc.battery_level}%" if latest_loc and latest_loc.battery_level is not None else "Noma'lum"
            charging = "🔌 (Quvvatlanmoqda)" if latest_loc and latest_loc.is_charging else ""
            status = "🚨 SOS FAVQULODDA!" if ch.is_sos_active else "🟢 Faol"

            text += (
                f"👤 **{ch.full_name or 'Farzand'}** (@{ch.username or 'yoq'})\n"
                f"• Holat: **{status}**\n"
                f"• Batareya: **{battery}** {charging}\n"
                f"• Balans: **{ch.virtual_balance:,} tanga**\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 {ch.full_name} boshqaruvi",
                    callback_data=f"child_view:{ch.id}"
                )
            ])

        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("child_view:"))
async def on_child_view_details(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)

        if not child:
            await callback.answer("Farzand topilmadi.", show_alert=True)
            return

        battery = f"{latest_loc.battery_level}%" if latest_loc and latest_loc.battery_level is not None else "Noma'lum"
        time_str = latest_loc.recorded_at.strftime("%H:%M:%S") if latest_loc and latest_loc.recorded_at else "Hali yangilanmagan"

        text = (
            f"👤 **Farzand:** {child.full_name}\n"
            f"🆔 ID: `{child.id}`\n"
            f"🔋 Batareya: **{battery}**\n"
            f"🕒 Oxirgi signal: **{time_str}**\n"
            f"💰 Tangalar balansi: **{child.virtual_balance:,} tanga**\n"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_child_details_keyboard(child_id),
            parse_mode="Markdown"
        )
    await callback.answer()


@router.message(F.text == "➕ Farzand ulash (Kodi)")
async def on_show_pairing_code(message: Message, bot: Bot):
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        code = await crud.generate_pairing_code(session, parent_id)
        await session.commit()

        bot_info = await bot.get_me()
        await message.answer(
            f"🔑 **Sizning ulanish kodingiz:** ` {code} `\n\n"
            f"Ushbu kodni farzandingiz telefonida botga yuboring yoki quyidagi ulashish tugmasini bosing:",
            reply_markup=get_pairing_share_keyboard(bot_info.username, code),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("resolve_sos:"))
async def on_resolve_sos(callback: CallbackQuery, bot: Bot):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await crud.set_child_sos(session, child_id, False)
        child = await crud.get_user(session, child_id)
        await session.commit()

        if child:
            try:
                await bot.send_message(
                    chat_id=child.id,
                    text="✅ Ota-onangiz SOS xavf signalini to'xtatdi. Siz bilan hammasi joyida ekanligidan xursandmiz!",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    await callback.message.edit_text("✅ SOS signali bekor qilindi.")
    await callback.answer()


@router.message(F.text == "ℹ️ Yordam")
async def on_help_pressed(message: Message):
    text = (
        f"📖 **Qo'llanma va Yordam**\n\n"
        f"**Smart Bolalar Nazorati Tizimi** farzandingiz xavfsizligini Telegram orqali to'g'ridan-to'g'ri ta'minlaydi:\n\n"
        f"1. **📍 Hozirgi joylashuv:** '📍 Farzandning hozirgi joylashuvi' tugmasini bosishingiz bilan farzandingizning aniq geolokatsiyasi va koordinatasi Telegram xaritasi sifatida chatga tashlanadi.\n"
        f"2. **📍 Kerakli joylar:** Maktab, uy yoki kurs manzillarini kiriting. Bola yetib kelganda yoki chiqqanda bot avtomatik xabar beradi. Istalgan lokatsiyani chatga tashlash orqali ham yangi joy qo'sha olasiz.\n"
        f"3. **🚨 SOS va Tezkor Qo'ng'iroq:** Bola xavf tugmasini bossa, darhol lokatsiya yuboriladi va bolaning ekranida ota-onaga 1-bosishda qo'ng'iroq qilish tugmasi chiqadi.\n"
        f"4. **🪫 Batareya Nazorati:** Telefon quvvati **20%** dan tushib ketsa, ota-onaga avtomatik ogohlantirish keladi.\n"
        f"5. **📋 Vazifalar va Tangalar:** Farzandingizga rag'batlantiruvchi vazifalar belgilang."
    )
    await message.answer(text, parse_mode="Markdown")


# ---------------------------------------------------------
# Settings & 2-Step Logout (Role Reset) Handlers
# ---------------------------------------------------------

@router.message(F.text == "⚙️ Sozlamalar / Chiqish")
@router.message(Command("logout", "chiqish", "reset_role", "sozlamalar"))
async def on_settings_logout_pressed(message: Message, state: FSMContext):
    """Show settings menu with option to logout/change role."""
    await state.clear()
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role == "unassigned":
            await message.answer(
                "Siz hali biron-bir rol tanlamagansiz. Iltimos, o'z rolingizni tanlang:",
                reply_markup=get_role_selection_keyboard()
            )
            return

        role_display = "👨‍👩‍👧 Ota-ona" if user.role == "parent" else "👦 Farzand"

        text = (
            f"⚙️ **SOZLAMALAR VA HISOB BOSHQARUVI**\n\n"
            f"👤 **Foydalanuvchi:** {user.full_name or message.from_user.full_name}\n"
            f"🆔 **Telegram ID:** `{user.id}`\n"
            f"🎭 **Hozirgi rol:** **{role_display}**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 **Rolni almashtirish / Hisobdan chiqish:**\n"
            f"Agar siz adashib boshqa rol tanlab qo'ygan bo'lsangiz (masalan, farzand adashib 'Ota-ona' nomidan kirgan bo'lsa yoki aksincha), "
            f"quyidagi tugma orqali hisobdan chiqib, qaytadan to'g'ri rolni tanlashingiz mumkin.\n\n"
            f"🛡 *Tasodifan yoki bolalar o'ynab chiqib ketmasligi uchun tizim 2-darajali xavfsizlik tasdiqlashini so'raydi.*"
        )
        await message.answer(text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")


@router.callback_query(F.data == "logout_start")
async def on_logout_start(callback: CallbackQuery, state: FSMContext):
    """Level 1 confirmation for account logout."""
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        role_display = "👨‍👩‍👧 Ota-ona" if (user and user.role == "parent") else "👦 Farzand"

    text = (
        f"⚠️ **1-BOSQICH: HISOBDAN CHIQISHNI TASDIQLASH (1/2)**\n\n"
        f"Siz hozirda: **{role_display}** hisobidasiz.\n\n"
        f"Agar rolingizni adashib tanlagan bo'lsangiz, hisobdan chiqib to'g'ri rolni tanlashingiz mumkin.\n\n"
        f"❗️ **Ogohlantirish:** Chiqsangiz, joriy rolingiz va barcha ulanishlar bekor qilinadi.\n\n"
        f"Chiqishni davom ettirishni xohlaysizmi?"
    )
    await callback.message.edit_text(text, reply_markup=get_logout_step1_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "logout_step1")
async def on_logout_step1(callback: CallbackQuery, state: FSMContext):
    """Level 2 final confirmation with random security code."""
    import random
    confirm_code = "".join(random.choices("0123456789", k=4))
    await state.set_state(LogoutState.waiting_for_confirm)
    await state.update_data(confirm_code=confirm_code)

    text = (
        f"🔒 **2-DARAJALI YAKUNIY TASDIQLASH (2/2)**\n\n"
        f"Tasodifiy bosilish yoki bolalar o'ynab chiqib ketishining oldini olish uchun xavfsizlik kodi:\n\n"
        f"🔑 Tasdiqlash kodi: **{confirm_code}**\n\n"
        f"⚠️ Haqiqatan ham hozirgi hisobdan butunlay chiqib, yangi rol tanlamoqchimisiz?\n\n"
        f"Yakunlash uchun pastdagi qizil tasdiqlash tugmasini bosing yoki ushbu kodni (`{confirm_code}`) xabar sifatida yozib yuboring:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_logout_step2_keyboard(confirm_code),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("logout_step2:"))
async def on_logout_step2(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Process Level 2 button press."""
    provided_code = callback.data.split(":")[1]
    state_data = await state.get_data()
    expected_code = state_data.get("confirm_code")

    if expected_code and provided_code != expected_code:
        await callback.answer("❌ Tasdiqlash kodi eskirgan yoki noto'g'ri!", show_alert=True)
        return

    await perform_account_logout(callback.from_user.id, callback.from_user.full_name, callback.message, state, bot)
    await callback.answer()


@router.message(LogoutState.waiting_for_confirm)
async def on_logout_code_entered(message: Message, state: FSMContext, bot: Bot):
    """Process Level 2 manual code entry."""
    user_code = message.text.strip()
    state_data = await state.get_data()
    expected_code = state_data.get("confirm_code")

    if expected_code and user_code == expected_code:
        await perform_account_logout(message.from_user.id, message.from_user.full_name, message, state, bot)
    elif user_code.lower() in ["bekor", "cancel", "/cancel", "/start"]:
        await state.clear()
        async with AsyncSessionLocal() as session:
            user = await crud.get_user(session, message.from_user.id)
            keyboard = get_parent_main_keyboard(message.from_user.id) if (user and user.role == "parent") else get_child_main_keyboard()
        await message.answer("✅ Chiqish bekor qilindi. O'z hisobingizdasiz.", reply_markup=keyboard)
    else:
        await message.answer(
            f"❌ **Noto'g'ri tasdiqlash kodi!**\n\n"
            f"Chiqish uchun to'g'ri kodni kiriting (`{expected_code}`) yoki bekor qilish uchun 'bekor' deb yozing.",
            reply_markup=get_logout_step2_keyboard(expected_code or "1234"),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "logout_cancel")
async def on_logout_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel logout process."""
    await state.clear()
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        role = user.role if user else "unassigned"

    keyboard = get_parent_main_keyboard(user_id) if role == "parent" else get_child_main_keyboard()

    try:
        await callback.message.edit_text(
            "✅ **Chiqish bekor qilindi.** Siz o'z hisobingizda xavfsiz qoldingiz.",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await callback.answer("Amal bekor qilindi.")


async def perform_account_logout(user_id: int, full_name: str, message_obj, state: FSMContext, bot: Bot):
    """Execute complete reset of user role and notify linked users."""
    await state.clear()
    async with AsyncSessionLocal() as session:
        success, notified_ids = await crud.reset_user_role(session, user_id)
        await session.commit()

        # Notify linked parents/children
        for partner_id in notified_ids:
            try:
                await bot.send_message(
                    chat_id=partner_id,
                    text=(
                        f"ℹ️ **Hisob yangilanishi:**\n\n"
                        f"Bog'langan foydalanuvchi (**{full_name}**) o'z hisobidan chiqdi va rolni qayta tanlash holatiga o'tdi.\n"
                        f"Zarur bo'lsa, qaytadan 6 xonali ulanish kodi orqali bog'lanishingiz mumkin."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Could not notify partner {partner_id} of logout: {e}")

    notice_text = (
        f"✅ **Hisobdan muvaffaqiyatli chiqildi!**\n\n"
        f"Sizning avvalgi rolingiz to'liq bekor qilindi.\n"
        f"Endi quyidagi tugmalar orqali to'g'ri rolingizni tanlashingiz mumkin:"
    )

    if isinstance(message_obj, Message):
        await message_obj.answer(
            notice_text,
            reply_markup=get_role_selection_keyboard(),
            parse_mode="Markdown"
        )
    else:
        try:
            await message_obj.delete()
        except Exception:
            pass
        await bot.send_message(
            chat_id=user_id,
            text=notice_text,
            reply_markup=get_role_selection_keyboard(),
            parse_mode="Markdown"
        )
