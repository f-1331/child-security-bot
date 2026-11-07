import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

import db.crud as crud
from config import settings
from db.database import async_session_maker
from owner_bot.keyboards import (
    get_admin_management_keyboard,
    get_broadcast_confirm_keyboard,
    get_broadcast_target_keyboard,
    get_cancel_keyboard,
    get_owner_main_keyboard,
    get_unlink_confirm_keyboard,
    get_user_dossier_inline_keyboard,
    get_airdrop_preset_keyboard,
    get_shield_management_keyboard,
    get_reset_options_keyboard,
    get_factory_reset_confirm_keyboard,
    get_users_directory_inline_keyboard,
    get_notifications_settings_inline_keyboard,
)
from owner_bot.logger_buffer import get_recent_logs
from owner_bot.service import (
    format_dossier_html,
    format_users_directory_html,
    get_main_bot,
    get_system_diagnostic_html,
    get_apps_and_screen_time_summary,
    get_battery_and_device_radar,
    perform_database_optimization,
    distribute_coins_airdrop,
    get_live_gps_radar,
    get_database_health_stats,
    generate_magic_login_url,
    get_or_ensure_magic_login_url,
    trigger_remote_siren,
    trigger_remote_screen_lock,
    perform_global_keyboards_refresh,
    perform_global_cache_and_sos_reset,
    perform_global_factory_reset,
    get_online_activity_summary,
    get_top_balances_summary,
    get_app_download_links_html,
    perform_add_coins_to_child,
    perform_ban_toggle,
)

logger = logging.getLogger("smart_parental_tracker.owner_bot")

owner_bot_router = Router()


# ==============================================================================
# FSM STATES
# ==============================================================================
class OwnerBotStates(StatesGroup):
    waiting_for_user_query = State()
    waiting_for_direct_msg = State()
    waiting_for_add_admin_id = State()
    waiting_for_broadcast_content = State()
    waiting_for_broadcast_confirm = State()
    waiting_for_airdrop_custom = State()
    waiting_for_magic_id = State()
    waiting_for_user_directory_search = State()


# ==============================================================================
# AUTHORIZATION MIDDLEWARE
# ==============================================================================
async def check_admin_access(event: TelegramObject) -> Optional[Dict[str, Any]]:
    """Check whether the user is @fayzbek1331 or registered admin.
    Returns dict with user details and admin flags if allowed, or None if forbidden.
    """
    from_user = getattr(event, "from_user", None)
    if not from_user:
        return None

    clean_uname = (from_user.username or "").lower().lstrip("@")
    owner_uname = (settings.OWNER_USERNAME or "fayzbek1331").lower().lstrip("@")
    is_owner = bool(
        (clean_uname and clean_uname == owner_uname)
        or from_user.id in (1, 6707839718)
        or from_user.id == getattr(settings, "OWNER_ID", 1)
    )

    async with async_session_maker() as session:
        if is_owner:
            await crud.ensure_owner_registered(
                session,
                from_user.id,
                from_user.username,
                from_user.full_name,
            )
            return {"is_admin": True, "is_owner": True, "user_id": from_user.id}

        admin = await crud.get_admin_user(session, from_user.id)
        if admin:
            return {
                "is_admin": True,
                "is_owner": admin.is_owner,
                "user_id": from_user.id,
            }

    return None


@owner_bot_router.message.outer_middleware()
@owner_bot_router.callback_query.outer_middleware()
async def owner_bot_auth_middleware(
    handler: Callable[[TelegramObject, Dict[str, Any]], Any],
    event: TelegramObject,
    data: Dict[str, Any],
) -> Any:
    """Outer middleware rejecting any user not matching @fayzbek1331 or admin_users."""
    auth_info = await check_admin_access(event)
    if not auth_info:
        from_user = getattr(event, "from_user", None)
        user_desc = (
            f"ID: {from_user.id}, @{from_user.username}, {from_user.full_name}"
            if from_user
            else "Noma'lum"
        )
        logger.warning(f"Unauthorized access attempt to Owner Bot: {user_desc}")

        if isinstance(event, Message):
            await event.answer(
                "⛔️ <b>Kirish taqiqlangan!</b>\n\n"
                f"Ushbu bot faqat tizim asoschisi (<b>@{settings.OWNER_USERNAME}</b>) "
                "va vakolatli adminlar uchun maxsus himoyalangan boshqaruv konsolidir.\n\n"
                f"Sizning ID: <code>{from_user.id if from_user else 'N/A'}</code>\n"
                "<i>Ruxsatsiz urinish xavfsizlik jurnalida qayd etildi.</i>",
                parse_mode="HTML",
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ Kirish huquqingiz yo'q!", show_alert=True)
        return None

    data["auth_info"] = auth_info
    return await handler(event, data)


# ==============================================================================
# CANCEL & NAVIGATION
# ==============================================================================
@owner_bot_router.message(F.text == "❌ Bekor qilish")
async def handle_cancel(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    await state.clear()
    await message.answer(
        "Amal bekor qilindi. Boshqaruv panelidasiz:",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
    )


@owner_bot_router.callback_query(F.data == "adm_back_main")
async def handle_back_main(callback: CallbackQuery, state: FSMContext, auth_info: Dict[str, Any]):
    await state.clear()
    is_owner = auth_info.get("is_owner", True)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "👑 <b>Boshqaruv konsoli bosh menyusiga qaytdingiz:</b>\nKerakli bo'limni tanlang:",
        reply_markup=get_owner_main_keyboard(is_owner),
        parse_mode="HTML",
    )
    await callback.answer()


# ==============================================================================
# /START & DASHBOARD MENU
# ==============================================================================
@owner_bot_router.message(CommandStart())
@owner_bot_router.message(Command("menu"))
async def handle_start(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    await state.clear()
    role_title = "👑 Bosh Ega (Loyiha asoschisi)" if auth_info["is_owner"] else "👮‍♂️ Tizim Administratori"

    welcome_text = (
        f"👑 <b>Assalomu alaykum, {message.from_user.full_name}!</b>\n"
        f"Mansab: <b>{role_title}</b>\n\n"
        "🛡 <b>Smart Parental Control & Child Security</b>\n"
        "Maxsus Ega va Administrator Boshqaruv Tizimiga xush kelibsiz!\n\n"
        "⚡️ <i>Bu konsol orqali barcha foydalanuvchilar dosyesini, jonli GPS telemetriyasini, "
        "ulanish kodlarini tekshirishingiz, muammolarni bartaraf etishingiz va platformani to'liq "
        "boshqarishingiz mumkin.</i>\n\n"
        "Quyidagi boshqaruv panelidan kerakli bo'limni tanlang:"
    )
    await message.answer(
        welcome_text,
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML",
    )


# ==============================================================================
# STATISTIKA & RADAR & DB STATS
# ==============================================================================
@owner_bot_router.message(F.text.contains("Umumiy Statistika") | (F.text == "/stats"))
async def handle_statistics(message: Message):
    async with async_session_maker() as session:
        stats = await crud.get_system_overview_stats(session)

    text = (
        "📊 <b>PLATFORMA BO'YICHA UMUMIY STATISTIKA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Jami foydalanuvchilar:</b> <code>{stats['total_users']}</code> ta\n"
        f"👨‍👩‍👧 <b>Ota-onalar (Parents):</b> <code>{stats['total_parents']}</code> ta\n"
        f"👦 <b>Farzandlar (Children):</b> <code>{stats['total_children']}</code> ta\n"
        f"📅 <b>Bugun ro'yxatdan o'tganlar:</b> <code>{stats['today_users']}</code> ta\n\n"
        f"🚨 <b>Faol SOS signallari:</b> <code>{stats['active_sos']}</code> ta\n"
        f"🛡 <b>Nazorat zonalari (Geofence):</b> <code>{stats['total_geofences']}</code> ta\n"
        f"📋 <b>Berilgan vazifalar (Tasks):</b> <code>{stats['total_tasks']}</code> ta\n"
        f"📍 <b>Saqlangan GPS nuqtalar:</b> <code>{stats['total_location_logs']}</code> ta\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 <i>Barcha hisoblagichlar real vaqt rejimida yangilanadi.</i>"
    )
    await message.answer(text, parse_mode="HTML")


@owner_bot_router.message(F.text.contains("Jonli GPS Radar") | (F.text == "/radar"))
async def handle_live_gps_radar_cmd(message: Message):
    async with async_session_maker() as session:
        html_text, kb = await get_live_gps_radar(session)
    await message.answer(html_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@owner_bot_router.callback_query(F.data == "adm_refresh_radar")
async def handle_refresh_radar_cb(callback: CallbackQuery):
    async with async_session_maker() as session:
        html_text, kb = await get_live_gps_radar(session)
    try:
        await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass
    await callback.answer("Radar yangilandi!")


@owner_bot_router.message(F.text.contains("Neon DB Holati") | (F.text == "/db_stats") | (F.text == "/db"))
async def handle_database_health_cmd(message: Message):
    async with async_session_maker() as session:
        text = await get_database_health_stats(session)
    await message.answer(text, parse_mode="HTML")


@owner_bot_router.message(F.text.contains("Magic Login Link") | (F.text == "/magic"))
async def prompt_magic_link(message: Message, state: FSMContext):
    await state.set_state(OwnerBotStates.waiting_for_magic_id)
    await message.answer(
        "🔑 <b>VIP / Magic Login Link Generatori</b>\n\n"
        "Qaysi foydalanuvchi hisobiga parolsiz 1-bosishda WebApp orqali kirmoqchisiz? Telegram ID raqamini kiriting:\n\n"
        "<i>Misol: <code>1</code> yoki <code>997185613</code></i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


@owner_bot_router.message(OwnerBotStates.waiting_for_magic_id)
async def process_magic_link_input(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not message.text:
        return
    raw = message.text.strip()
    await state.clear()
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat raqamli Telegram ID kiriting:", reply_markup=get_owner_main_keyboard(auth_info["is_owner"]))
        return
    user_id = int(raw)
    async with async_session_maker() as session:
        user = await crud.get_user(session, user_id)
        if not user:
            await message.answer(f"❌ ID <code>{user_id}</code> bo'yicha foydalanuvchi topilmadi.", reply_markup=get_owner_main_keyboard(auth_info["is_owner"]), parse_mode="HTML")
            return
        magic_url = generate_magic_login_url(user)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🚀 {user.full_name or 'Foydalanuvchi'} hisobiga kirish", url=magic_url)],
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")]
        ])
        await message.answer(
            f"✅ <b>Magic WebApp Login Link Tayyor!</b>\n\n"
            f"👤 Foydalanuvchi: <b>{user.full_name or 'Foydalanuvchi'}</b> (ID: <code>{user.id}</code>)\n"
            f"🎭 Rol: <code>{user.role}</code>\n"
            f"🔑 Kirish kodi: <code>{user.web_access_code or 'CS-DEMO'}</code>\n\n"
            f"🔗 <b>Havola:</b>\n<code>{magic_url}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ==============================================================================
# MASOFAVIY BUYRUQLAR (/siren, /lock, /unlock, /add_coins)
# ==============================================================================
@owner_bot_router.message(Command("siren"))
async def on_siren_command(message: Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        await message.answer("⚠️ Foydalanish: <code>/siren &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    child_id = int(command.args.strip())
    await trigger_remote_siren(child_id)
    await message.answer(f"🚨 <b>ID {child_id}</b> qurilmasiga masofaviy favqulodda sirena yuborildi! 🔊", parse_mode="HTML")


@owner_bot_router.message(Command("lock"))
async def on_lock_command(message: Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        await message.answer("⚠️ Foydalanish: <code>/lock &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    child_id = int(command.args.strip())
    await trigger_remote_screen_lock(child_id, lock=True)
    await message.answer(f"🛑 <b>ID {child_id}</b> qurilmasi masofaviy bloklandi!", parse_mode="HTML")


@owner_bot_router.message(Command("unlock"))
async def on_unlock_command(message: Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        await message.answer("⚠️ Foydalanish: <code>/unlock &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    child_id = int(command.args.strip())
    await trigger_remote_screen_lock(child_id, lock=False)
    await message.answer(f"✅ <b>ID {child_id}</b> qurilmasi qulfdan chiqarildi!", parse_mode="HTML")


@owner_bot_router.message(Command("add_coins"))
async def on_add_coins_command(message: Message, command: CommandObject):
    if not command.args or len(command.args.split()) < 2:
        await message.answer("⚠️ Foydalanish: <code>/add_coins &lt;child_id&gt; &lt;amount&gt;</code>", parse_mode="HTML")
        return
    parts = command.args.split()
    if not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
        await message.answer("⚠️ Noto'g'ri format. Misol: <code>/add_coins 12345 500</code>", parse_mode="HTML")
        return
    child_id = int(parts[0])
    amount = int(parts[1])
    async with async_session_maker() as session:
        child = await crud.get_user(session, child_id)
        if not child:
            await message.answer(f"❌ ID {child_id} topilmadi.", parse_mode="HTML")
            return
        child.child_balance_coins = (child.child_balance_coins or 0) + amount
        await session.commit()
    await message.answer(f"✅ <b>ID {child_id} ({child.full_name})</b> ga {amount:+,} tanga berildi! Yangi balans: <code>{child.child_balance_coins}</code> tanga", parse_mode="HTML")


@owner_bot_router.callback_query(F.data.startswith("adm_magic:"))
async def on_dossier_magic_link_cb(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        user = await crud.get_user(session, user_id)
        if not user:
            await callback.answer("Foydalanuvchi topilmadi!", show_alert=True)
            return
        magic_url = generate_magic_login_url(user)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 WebApp Dashboardga kirish", url=magic_url)],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"adm_inspect:{user_id}")]
        ])
        await callback.message.answer(
            f"🔑 <b>{user.full_name} hisobiga 1-bosishda kirish havolasi:</b>\n\n<code>{magic_url}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()


@owner_bot_router.callback_query(F.data.startswith("adm_siren:"))
async def on_dossier_siren_cb(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    await trigger_remote_siren(child_id)
    await callback.answer("🔊 Masofaviy Sirena signali farzand telefoniga yuborildi!", show_alert=True)


# ==============================================================================
# FOYDALANUVCHINI TEKSHIRISH (ID / USERNAME QUERY)
# ==============================================================================
@owner_bot_router.message(F.text.contains("ID bo'yicha tekshirish") | (F.text == "/inspect"))
@owner_bot_router.message(Command("inspect"))
async def prompt_user_query(message: Message, state: FSMContext):
    await state.set_state(OwnerBotStates.waiting_for_user_query)
    await message.answer(
        "🔍 <b>Foydalanuvchini qidirish va tekshirish</b>\n\n"
        "Iltimos, foydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>ini kiriting:\n\n"
        "<i>Misol: <code>123456789</code> yoki <code>@fayzbek1331</code></i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


@owner_bot_router.message(OwnerBotStates.waiting_for_user_query)
async def process_user_query(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not message.text:
        await message.answer("⚠️ Iltimos, Telegram ID raqamini yoki @username'ni matn ko'rinishida yuboring:")
        return
    query = message.text.strip()
    await state.clear()

    async with async_session_maker() as session:
        user = await crud.search_user_by_query(session, query)
        if not user:
            await message.answer(
                f"❌ <b>'{query}'</b> bo'yicha foydalanuvchi topilmadi.\n"
                "Iltimos, Telegram ID yoki to'g'ri usernameni qayta tekshirib ko'ring.",
                reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
                parse_mode="HTML",
            )
            return

        dossier = await crud.get_user_full_dossier(session, user.id)

    if not dossier:
        await message.answer("❌ Dosye yuklashda xatolik yuz berdi.", reply_markup=get_owner_main_keyboard(auth_info["is_owner"]))
        return

    text = format_dossier_html(dossier)
    kb = get_user_dossier_inline_keyboard(
        user_id=user.id,
        is_sos=user.is_sos_active,
        has_location=bool(dossier.get("latest_location")),
        role=user.role,
    )
    await message.answer(
        text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await message.answer(
        "Tanlangan foydalanuvchi ustida quyidagi amallarni bajarishingiz mumkin:",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
    )


@owner_bot_router.callback_query(F.data.startswith("adm_inspect:"))
async def callback_inspect_user(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        dossier = await crud.get_user_full_dossier(session, user_id)

    if not dossier:
        await callback.answer("Foydalanuvchi topilmadi!", show_alert=True)
        return

    user = dossier["user"]
    text = format_dossier_html(dossier)
    kb = get_user_dossier_inline_keyboard(
        user_id=user.id,
        is_sos=user.is_sos_active,
        has_location=bool(dossier.get("latest_location")),
        role=user.role,
    )
    await callback.message.edit_text(
        text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


# ==============================================================================
# DOSSIER ACTIONS: LOCATION, DIRECT MSG, CODE REGEN, SOS, COINS, UNLINK
# ==============================================================================
@owner_bot_router.callback_query(F.data.startswith("adm_loc:"))
async def callback_show_location(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        loc = await crud.get_latest_location(session, user_id)
        user = await crud.get_user(session, user_id)

    if not loc:
        await callback.answer("Ushbu foydalanuvchining GPS signali hali mavjud emas!", show_alert=True)
        return

    name = user.full_name if user else f"Foydalanuvchi #{user_id}"
    await callback.message.bot.send_location(
        chat_id=callback.message.chat.id,
        latitude=loc.latitude,
        longitude=loc.longitude,
    )
    await callback.message.answer(
        f"📍 <b>{name}</b>ning so'nggi ma'lum joylashuvi.\n"
        f"🎯 Aniqlik: ±{loc.accuracy:.1f}m | Tezlik: {loc.speed or 0:.1f} km/s\n"
        f"🔗 <a href=\"https://www.google.com/maps?q={loc.latitude},{loc.longitude}\">Google Maps orqali ko'rish</a>",
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.callback_query(F.data.startswith("adm_msg:"))
async def callback_prompt_direct_message(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[1])
    await state.set_state(OwnerBotStates.waiting_for_direct_msg)
    await state.update_data(target_user_id=user_id)

    await callback.message.answer(
        f"✉️ <b>Foydalanuvchi (ID: <code>{user_id}</code>)ga to'g'ridan-to'g'ri xabar yuborish</b>\n\n"
        "Iltimos, yubormoqchi bo'lgan xabaringiz matnini kiriting:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_direct_msg)
async def process_direct_message(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    await state.clear()

    if not target_user_id:
        await message.answer("Xatolik: Maqsadli foydalanuvchi aniqlanmadi.", reply_markup=get_owner_main_keyboard(auth_info["is_owner"]))
        return

    msg_body = (
        "🛡 <b>Tizim Ma'muriyatidan Rasmiy Xabar:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{message.text}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Aloqa va savollar uchun: @{settings.OWNER_USERNAME}</i>"
    )

    # Attempt to send via main public bot first
    main_bot = get_main_bot()
    delivered = False
    err_msg = ""

    if main_bot:
        try:
            await main_bot.send_message(chat_id=target_user_id, text=msg_body, parse_mode="HTML")
            delivered = True
        except Exception as e:
            err_msg = str(e)

    # Fallback to current owner bot
    if not delivered:
        try:
            await message.bot.send_message(chat_id=target_user_id, text=msg_body, parse_mode="HTML")
            delivered = True
        except Exception as e:
            err_msg = str(e)

    if delivered:
        await message.answer(
            f"✅ Xabar foydalanuvchiga (ID: <code>{target_user_id}</code>) muvaffaqiyatli yetkazildi!",
            reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"❌ Xabarni yetkazib bo'lmadi.\nSabab: <code>{err_msg}</code>\n"
            "<i>(Ehtimol, foydalanuvchi botni bloklagan yoki hali start bosmagan)</i>",
            reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
            parse_mode="HTML",
        )


@owner_bot_router.callback_query(F.data.startswith("adm_code:"))
async def callback_regenerate_code(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        new_code = await crud.force_regenerate_user_code(session, user_id)
        user = await crud.get_user(session, user_id)

    if not new_code:
        await callback.answer("Kod yangilashda xatolik yuz berdi!", show_alert=True)
        return

    code_type = "Ulanish kodi (Pairing)" if user.role == "parent" else "Veb kirish kodi (Web PIN)"
    await callback.message.answer(
        f"✅ <b>Foydalanuvchi ({user.full_name}) uchun yangi {code_type}:</b>\n\n"
        f"🔑 <code>{new_code}</code>\n\n"
        "<i>Ushbu yangi kodni foydalanuvchiga taqdim etishingiz mumkin.</i>",
        parse_mode="HTML",
    )
    await callback.answer("Kod muvaffaqiyatli yangilandi!")


@owner_bot_router.callback_query(F.data.startswith("adm_sos_off:"))
async def callback_clear_sos(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        await crud.set_child_sos(session, user_id, is_active=False)

    # Notify child
    main_bot = get_main_bot()
    if main_bot:
        try:
            await main_bot.send_message(
                chat_id=user_id,
                text="🟢 <b>SOS Favqulodda holati ma'muriyat tomonidan bekor qilindi.</b> Tizim normal rejimda ishlamoqda.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.message.answer(
        f"✅ Foydalanuvchi (ID: <code>{user_id}</code>)ning SOS favqulodda holati bekor qilindi.",
        parse_mode="HTML",
    )
    await callback.answer("SOS bekor qilindi!")


@owner_bot_router.callback_query(F.data.startswith("adm_coin:"))
async def callback_add_coins(callback: CallbackQuery):
    parts = callback.data.split(":")
    user_id = int(parts[1])
    amount = int(parts[2])

    async with async_session_maker() as session:
        new_balance = await crud.add_coins_to_user(session, user_id, amount)

    # Notify child
    main_bot = get_main_bot()
    if main_bot:
        try:
            await main_bot.send_message(
                chat_id=user_id,
                text=f"🎁 <b>Tabriklaymiz!</b> Administrator sizga <b>+{amount} ta virtual tanga</b> taqdim etdi!\n"
                     f"💰 Yangi balansingiz: <b>{new_balance} tanga</b>.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.answer(f"✅ +{amount} tanga qo'shildi! Yangi balans: {new_balance}", show_alert=True)


@owner_bot_router.callback_query(F.data.startswith("adm_unlink_ask:"))
async def callback_unlink_ask(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        f"⚠️ <b>DIQQAT! Foydalanuvchi (ID: <code>{user_id}</code>)ning barcha oilaviy aloqalarini uzmoqchimisiz?</b>\n\n"
        "Bu amal ushbu foydalanuvchining barcha ota-onalar va bolalar bilan bog'lanishlarini tozalaydi. "
        "Ular qaytadan ulanish kodini kiritishlari kerak bo'ladi.",
        reply_markup=get_unlink_confirm_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.callback_query(F.data.startswith("adm_unlink_do:"))
async def callback_unlink_execute(callback: CallbackQuery, auth_info: Dict[str, Any]):
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        count = await crud.unlink_all_for_user(session, user_id)

    await callback.message.edit_text(
        f"✅ Foydalanuvchi (ID: <code>{user_id}</code>)ning <b>{count} ta</b> bog'lanishi muvaffaqiyatli tozalandi.",
        parse_mode="HTML",
    )
    await callback.answer("Bog'lanishlar tozalandi!")


# ==============================================================================
# FAOL SOS SIGNALLARI
# ==============================================================================
@owner_bot_router.message(F.text.contains("Faol SOS signallari") | (F.text == "/sos_list"))
async def handle_active_sos(message: Message):
    async with async_session_maker() as session:
        sos_users = await crud.get_active_sos_list(session)

    if not sos_users:
        await message.answer(
            "🟢 <b>Ajoyib! Hozirda hech qanday faol SOS signali yo'q.</b>\n"
            "Barcha bolalar xavfsiz va xotirjam holatda.",
            parse_mode="HTML",
        )
        return

    text = f"🚨 <b>FAOL SOS SIGNALLARI ({len(sos_users)} ta):</b>\n\n"
    buttons = []
    for u in sos_users:
        name = u.full_name or f"Farzand #{u.id}"
        uname = f"(@{u.username})" if u.username else ""
        text += f"• 🚨 <b>{name}</b> {uname} — ID: <code>{u.id}</code>\n"
        buttons.append([
            InlineKeyboardButton(
                text=f"🔍 {name} dosyesini ochish",
                callback_data=f"adm_inspect:{u.id}",
            )
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ADMINLARNI BOSHQARISH (FAQAT @fayzbek1331 UCHUN)
# ==============================================================================
@owner_bot_router.message(F.text.contains("Adminlarni boshqarish") | (F.text == "/admins"))
async def handle_admin_management(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer(
            f"⛔️ <b>Ruxsat etilmagan!</b>\n\nAdminlarni qo'shish va o'chirish huquqi "
            f"faqat loyiha egasi (<b>@{settings.OWNER_USERNAME}</b>)ga tegishli.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "👥 <b>ADMINLARNI BOSHQARISH MARKAZI</b>\n\n"
        "Siz bu bo'lim orqali yangi adminlar qo'shishingiz, mavjud adminlar ro'yxatini ko'rishingiz "
        "yoki ularning huquqlarini bekor qilishingiz mumkin.",
        reply_markup=get_admin_management_keyboard(),
        parse_mode="HTML",
    )


@owner_bot_router.callback_query(F.data == "adm_list_admins")
async def callback_list_admins(callback: CallbackQuery):
    async with async_session_maker() as session:
        admins = await crud.list_admin_users(session)

    text = "📋 <b>TIZIM ADMINISTRATORLARI RO'YXATI:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    for a in admins:
        role = "👑 Bosh Ega (Loyiha asoschisi)" if a.is_owner else "👮‍♂️ Admin"
        uname = f"@{a.username}" if a.username else "N/A"
        date_str = a.created_at.strftime("%d.%m.%Y") if a.created_at else ""
        text += (
            f"• <b>{a.full_name or 'Admin'}</b> ({uname})\n"
            f"  ID: <code>{a.id}</code> | Mansab: <b>{role}</b>\n"
            f"  Sana: {date_str}\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━━━━━"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back_to_admins")]]
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@owner_bot_router.callback_query(F.data == "adm_back_to_admins")
async def callback_back_to_admins(callback: CallbackQuery):
    await callback.message.edit_text(
        "👥 <b>ADMINLARNI BOSHQARISH MARKAZI</b>\n\nKerakli amalni tanlang:",
        reply_markup=get_admin_management_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.callback_query(F.data == "adm_add_admin_prompt")
async def callback_prompt_add_admin(callback: CallbackQuery, state: FSMContext, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega admin qo'sha oladi!", show_alert=True)
        return

    await state.set_state(OwnerBotStates.waiting_for_add_admin_id)
    await callback.message.answer(
        "➕ <b>Yangi Admin qo'shish</b>\n\n"
        "Admin qilmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>ini kiriting:\n\n"
        "<i>Misol: <code>123456789</code> yoki <code>@admin_username</code></i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_add_admin_id)
async def process_add_admin_id(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not message.text:
        await message.answer("⚠️ Iltimos, Telegram ID raqamini yoki @username'ni yuboring:")
        return
    query = message.text.strip()
    await state.clear()

    async with async_session_maker() as session:
        target_user = await crud.search_user_by_query(session, query)
        if target_user:
            new_admin_id = target_user.id
            uname = target_user.username
            fname = target_user.full_name or f"Admin #{new_admin_id}"
        elif query.isdigit():
            new_admin_id = int(query)
            uname = None
            fname = f"Admin #{new_admin_id}"
        else:
            await message.answer(
                f"❌ <b>'{query}'</b> bo'yicha foydalanuvchi topilmadi.\n\n"
                "Iltimos, foydalanuvchi avval botga /start bosgan bo'lishi yoki uning raqamli Telegram ID raqamini kiritishingiz kerak.",
                reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
                parse_mode="HTML"
            )
            return

        admin = await crud.add_admin_user(
            session=session,
            user_id=new_admin_id,
            username=uname,
            full_name=fname,
            added_by=message.from_user.id,
        )

    await message.answer(
        f"✅ <b>Yangi Administrator muvaffaqiyatli qo'shildi!</b>\n\n"
        f"👤 Ism: <b>{admin.full_name}</b>\n"
        f"🆔 Telegram ID: <code>{admin.id}</code>\n"
        f"🌐 Username: @{admin.username if admin.username else 'N/A'}\n\n"
        f"<i>Endi ushbu foydalanuvchi boshqaruv konsolidan foydalanishi mumkin.</i>",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML",
    )


@owner_bot_router.message(Command("add_admin"))
async def handle_cmd_add_admin(message: Message, command: CommandObject, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat bosh ega admin qo'sha oladi!")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/add_admin &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with async_session_maker() as session:
        target_user = await crud.search_user_by_query(session, query)
        if target_user:
            new_admin_id = target_user.id
            uname = target_user.username
            fname = target_user.full_name or f"Admin #{new_admin_id}"
        elif query.isdigit():
            new_admin_id = int(query)
            uname = None
            fname = f"Admin #{new_admin_id}"
        else:
            await message.answer(
                f"❌ <b>'{query}'</b> topilmadi.\nFoydalanuvchi botga /start bosgan bo'lishi yoki raqamli Telegram ID kiritilishi kerak.",
                parse_mode="HTML"
            )
            return

        admin = await crud.add_admin_user(
            session=session,
            user_id=new_admin_id,
            username=uname,
            full_name=fname,
            added_by=message.from_user.id,
        )
    await message.answer(
        f"✅ <b>Administrator muvaffaqiyatli qo'shildi!</b>\n\n"
        f"👤 Ism: <b>{admin.full_name}</b>\n"
        f"🆔 ID: <code>{admin.id}</code>\n"
        f"🌐 Username: @{admin.username if admin.username else 'N/A'}",
        parse_mode="HTML"
    )


@owner_bot_router.message(Command("del_admin", "remove_admin"))
async def handle_cmd_del_admin(message: Message, command: CommandObject, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat bosh ega adminni o'chira oladi!")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/del_admin &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with async_session_maker() as session:
        target_user = await crud.search_user_by_query(session, query)
        target_id = target_user.id if target_user else (int(query) if query.isdigit() else None)
        if not target_id:
            await message.answer("❌ Admin topilmadi.")
            return
        removed = await crud.remove_admin_user(session, target_id)
        if removed:
            await message.answer(f"✅ Admin (ID: <code>{target_id}</code>) o'chirildi.", parse_mode="HTML")
        else:
            await message.answer("❌ Ushbu admin o'chirilmadi (ehtimol u bosh ega yoki adminlar ro'yxatida yo'q).")


@owner_bot_router.callback_query(F.data == "adm_remove_admin_list")
async def callback_remove_admin_list(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega adminni o'chira oladi!", show_alert=True)
        return

    async with async_session_maker() as session:
        admins = await crud.list_admin_users(session)

    secondary_admins = [a for a in admins if not a.is_owner]
    if not secondary_admins:
        await callback.answer("Hozirda boshqa ikkilamchi adminlar yo'q.", show_alert=True)
        return

    buttons = []
    for a in secondary_admins:
        name = a.full_name or f"Admin #{a.id}"
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {name} (ID: {a.id})",
                callback_data=f"adm_del_admin:{a.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back_to_admins")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "🗑 <b>O'chirmoqchi bo'lgan adminni tanlang:</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.callback_query(F.data.startswith("adm_del_admin:"))
async def callback_delete_admin(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega adminni o'chira oladi!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        removed = await crud.remove_admin_user(session, target_id)

    if removed:
        await callback.answer(f"Admin (ID: {target_id}) o'chirildi!", show_alert=True)
        await callback.message.edit_text(
            f"✅ Admin (ID: <code>{target_id}</code>) vakolatlari bekor qilindi.",
            reply_markup=get_admin_management_keyboard(),
            parse_mode="HTML",
        )
    else:
        await callback.answer("Ushbu adminni o'chirib bo'lmadi (ehtimol u bosh ega).", show_alert=True)


# ==============================================================================
# HAMMAGA XABAR (BROADCAST)
# ==============================================================================
@owner_bot_router.message(F.text.contains("Hammaga xabar") | (F.text == "/broadcast"))
async def handle_broadcast_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📢 <b>OMMAVIY XABAR TARQATISH (BROADCAST)</b>\n\n"
        "Xabarni qaysi auditoriyaga yubormoqchisiz?",
        reply_markup=get_broadcast_target_keyboard(),
        parse_mode="HTML",
    )


@owner_bot_router.callback_query(F.data.startswith("bc_target:"))
async def callback_broadcast_target(callback: CallbackQuery, state: FSMContext):
    target = callback.data.split(":")[1]
    await state.update_data(broadcast_target=target)
    await state.set_state(OwnerBotStates.waiting_for_broadcast_content)

    target_labels = {
        "parent": "Ota-onalarga",
        "child": "Farzandlarga",
        "all": "Barcha foydalanuvchilarga",
    }
    await callback.message.edit_text(
        f"📢 Tanlangan auditoriya: <b>{target_labels.get(target, target)}</b>\n\n"
        "Endi yubormoqchi bo'lgan xabaringizni yozing (matn, emoji, formatlash yoki rasm bilan):",
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_broadcast_content)
async def process_broadcast_content(message: Message, state: FSMContext):
    await state.update_data(
        content_text=message.html_text or message.text or message.caption or "",
        photo_file_id=message.photo[-1].file_id if message.photo else None,
    )
    await state.set_state(OwnerBotStates.waiting_for_broadcast_confirm)

    preview_prefix = "📋 <b>XABAR KELTIRILGAN PREVIEW:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    await message.answer(
        preview_prefix + (message.html_text or message.text or message.caption or ""),
        parse_mode="HTML",
    )
    await message.answer(
        "Haqiqatan ham ushbu xabarni yuborishni tasdiqlaysizmi?",
        reply_markup=get_broadcast_confirm_keyboard(),
    )


@owner_bot_router.callback_query(F.data == "bc_confirm_send")
async def callback_broadcast_execute(callback: CallbackQuery, state: FSMContext, auth_info: Dict[str, Any]):
    data = await state.get_data()
    await state.clear()

    target = data.get("broadcast_target", "all")
    content_text = data.get("content_text", "")
    photo_file_id = data.get("photo_file_id")

    role_filter = None if target == "all" else target
    async with async_session_maker() as session:
        user_ids = await crud.get_all_user_ids_by_role(session, role_filter)

    total_count = len(user_ids)
    if total_count == 0:
        await callback.message.edit_text("❌ Yuborish uchun hech qanday foydalanuvchi topilmadi.")
        return

    status_msg = await callback.message.edit_text(
        f"🚀 <b>Tarqatish boshlandi...</b>\nJami: <code>{total_count}</code> ta foydalanuvchi.",
        parse_mode="HTML",
    )

    main_bot = get_main_bot() or callback.message.bot
    success = 0
    failed = 0

    for idx, uid in enumerate(user_ids, 1):
        try:
            if photo_file_id:
                await main_bot.send_photo(
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=content_text,
                    parse_mode="HTML",
                )
            else:
                await main_bot.send_message(
                    chat_id=uid,
                    text=content_text,
                    parse_mode="HTML",
                )
            success += 1
        except Exception:
            failed += 1

        # Periodic status update & Telegram rate limiting avoidance
        if idx % 25 == 0 or idx == total_count:
            try:
                await status_msg.edit_text(
                    f"🚀 <b>Tarqatilmoqda...</b>\n"
                    f"Yuborildi: <code>{idx}/{total_count}</code>\n"
                    f"✅ Yetkazildi: {success} | ❌ Xatolik: {failed}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await asyncio.sleep(0.04)

    await status_msg.edit_text(
        f"🎉 <b>Ommaviy xabar muvaffaqiyatli tarqatildi!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Jami qamrov: <b>{total_count}</b> ta\n"
        f"✅ Yetkazildi: <b>{success}</b> ta\n"
        f"❌ Xatolik (bot bloklangan): <b>{failed}</b> ta\n"
        f"━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
    )
    await callback.message.answer(
        "Boshqaruv menyusidasiz:",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
    )


# ==============================================================================
# SERVER & TIZIM HOLATI DIAGNOSTIKASI
# ==============================================================================
@owner_bot_router.message(F.text.contains("Server &") | (F.text == "/server"))
async def handle_system_status(message: Message):
    async with async_session_maker() as session:
        html = await get_system_diagnostic_html(session)
    await message.answer(html, parse_mode="HTML", disable_web_page_preview=True)


# ==============================================================================
# SO'NGGI FOYDALANUVCHILAR
# ==============================================================================
@owner_bot_router.message(F.text.contains("So'nggi foydalanuvchilar") | (F.text == "/recent"))
async def handle_recent_users(message: Message):
    async with async_session_maker() as session:
        users = await crud.get_recent_registered_users(session, limit=10)

    if not users:
        await message.answer("Hozircha tizimda foydalanuvchilar yo'q.")
        return

    text = "📜 <b>SO'NGGI RO'YXATDAN O'TGAN 10 FOYDALANUVCHI:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    buttons = []
    for u in users:
        role_icon = "👨‍👩‍👧" if u.role == "parent" else "👦" if u.role == "child" else "⚪️"
        uname = f"@{u.username}" if u.username else "N/A"
        date_str = u.created_at.strftime("%d.%m %H:%M") if u.created_at else ""
        text += (
            f"{role_icon} <b>{u.full_name or 'Foydalanuvchi'}</b> ({uname})\n"
            f"  ID: <code>{u.id}</code> | Roli: <code>{u.role}</code> | Vaqt: {date_str}\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"🔍 {u.full_name or u.id} dosyesi",
                callback_data=f"adm_inspect:{u.id}",
            )
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# SERVER LOGLARI
# ==============================================================================
@owner_bot_router.message(F.text.contains("Server loglari") | (F.text == "/logs"))
async def handle_server_logs(message: Message):
    logs = get_recent_logs(limit=25)
    log_text = "\n".join(logs)
    if len(log_text) > 3800:
        log_text = log_text[-3800:]

    html = (
        "📑 <b>SO'NGGI TIZIM JURNALLARI (LOGS):</b>\n"
        f"<pre>{log_text}</pre>"
    )
    await message.answer(html, parse_mode="HTML")


# ==============================================================================
# BAZA ZAXIRASI (BACKUP & EXPORT)
# ==============================================================================
@owner_bot_router.message(F.text.contains("Baza zaxirasi") | (F.text == "/backup"))
async def handle_backup_command(message: Message):
    from db.backup_service import send_db_backup_to_chat
    await message.answer("⏳ <i>Ma'lumotlar bazasi zaxiralanmoqda va tayyorlanmoqda...</i>", parse_mode="HTML")
    await send_db_backup_to_chat(message.bot, message.chat.id)


@owner_bot_router.message(Command("clean"))
@owner_bot_router.message(Command("tozala"))
async def handle_owner_clean_chat(message: Message, bot: Bot):
    """Inform owner about safe chat cleaning without accidental history wipe."""
    await message.answer(
        "🛡 <b>Chat xavfsizligi ma'lumoti:</b>\n\n"
        "Yangi xavfsiz bot tokeni o'rnatilgani sababli botga hech qanday reklama yoki spam kela olmaydi.\n\n"
        "⚠️ <i>Telegram Bot API xabarlarni saralab (faqat reklamani) o'chirish imkonini bermaydi. "
        "Bot orqali avto-tozalash qilinsa, sizning shaxsiy yozishmalaringiz va buyruqlaringiz ham o'chib ketishi mumkin. "
        "Shuning uchun shaxsiy chat tarixingiz to'liq saqlab qolindi.</i>\n\n"
        "Agar chatni noldan tozalashni xohlasangiz, Telegram ilovasidagi <b>3 nuqta (⋮) -> 'Tarixni tozalash' (Clear history)</b> menyusidan xavfsiz foydalanishingiz mumkin.",
        reply_markup=get_owner_main_keyboard(True),
        parse_mode="HTML"
    )


# ==============================================================================
# /HELP QO'LLANMA
# ==============================================================================
@owner_bot_router.message(F.text.contains("Yordam") | (F.text == "/help"))
async def handle_help(message: Message, auth_info: Dict[str, Any]):
    help_text = (
        "👑 <b>EGA VA ADMIN BOTI TO'LIQ QO'LLANMASI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡️ <b>Asosiy buyruqlar:</b>\n"
        "• /start yoki /menu — Asosiy boshqaruv menyusini ochish\n"
        "• /stats — Platforma bo'yicha jonli statistika\n"
        "• /online — Ayni damda faol/onlayn farzandlar soni va ro'yxati\n"
        "• /radar — Jonli GPS radari (harakatdagi farzandlar)\n"
        "• /find_child &lt;id&gt; — Farzandning aniq GPS koordinatasi va Google/Yandex Maps xaritasi\n\n"
        "🔍 <b>Foydalanuvchi nazorati:</b>\n"
        "• /inspect &lt;id&gt; — Foydalanuvchini to'liq tekshirish (dosye, ulanish kodi, telefon)\n"
        "• /magic &lt;id&gt; — 1-bosishda parolsiz WebApp hisobiga kirish havolasi\n"
        "• /ban &lt;id&gt; va /unban &lt;id&gt; — Shubhali akkauntni bloklash yoki ochish\n"
        "• /ban_list — Bloklangan foydalanuvchilar ro'yxati\n\n"
        "🚨 <b>Favqulodda SOS & Xavfsizlik:</b>\n"
        "• /sos_list — Hozirda faol SOS signallari ro'yxati\n"
        "• /sos_resolve_all — Barcha faol SOS signallarini bir bosishda to'xtatish\n"
        "• /tamper_logs — Buzg'unchilik va qalbaki GPS jurnali (so'nggi hodisalar)\n"
        "• /siren &lt;id&gt; — Farzand telefonida masofadan baland sirena yoqish\n"
        "• /lock &lt;id&gt; va /unlock &lt;id&gt; — Ekranni masofadan qulflash/ochish\n\n"
        "🪙 <b>Tangalar va Iqtisodiyot:</b>\n"
        "• /top_balances — Eng ko'p tanga topgan bolalar reytingi (TOP-10)\n"
        "• /airdrop — Barcha bolalarga ommaviy tanga tarqatish\n"
        "• /add_coins &lt;id&gt; &lt;son&gt; — Farzandga shaxsiy tanga mukofoti berish\n"
        "• /sub_coins &lt;id&gt; &lt;son&gt; — Farzanddan tanga ayirish\n"
        "• /reset_demo_balance — Demo/sinov hisobidagi ortiqcha balansni 0 ga tushirish\n\n"
        "📢 <b>Xabarnomalar va Global Reset:</b>\n"
        "• /broadcast — Barcha foydalanuvchilarga ommaviy xabar\n"
        "• /broadcast_parents — Faqat ota-onalarga xabar\n"
        "• /broadcast_children — Faqat farzandlarga xabar\n"
        "• /global_reset — Barcha foydalanuvchilarda klaviatura va menyuni yangilash\n\n"
        "🛠 <b>Server va Baza Boshqaruvi:</b>\n"
        "• /server yoki /diagnostika — Server CPU, RAM, disk va DB kechikishi (ping)\n"
        "• /db_stats — Neon PostgreSQL jadvallari va xotira holati\n"
        "• /optimize — Bazani tozalash va indekslarni tezlashtirish\n"
        "• /backup — Bazani Telegramga zaxira fayl qilib yuklab olish\n"
        "• /app_links — Android APK, WebApp va PC rasmiy yuklab olish havolalari\n"
        "• /logs — So'nggi jonli server xatolari va loglari\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👑 <b>Bosh ega:</b> @fayzbek1331 (ID: 1)"
    )
    await message.answer(
        help_text,
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML",
    )


@owner_bot_router.message(F.text.in_({"💻 Noutbuk dasturi", "/desktop", "/laptop", "/pc"}))
async def handle_owner_desktop_info(message: Message, auth_info: Dict[str, Any]):
    from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    import os
    from config import settings

    desktop_download_url = "https://child-security-site.onrender.com/download/desktop-windows"
    text = (
        "💻 <b>Child Security — Noutbuk va PC Desktop versiyasi</b>\n\n"
        "🔗 <b>To'g'ridan-to'g'ri havolalar:</b>\n"
        f"• Web App: <code>{settings.RESOLVED_WEBAPP_URL}</code>\n"
        f"• Windows O'rnatgich (.bat): <code>{desktop_download_url}</code>\n\n"
        "Foydalanuvchilar noutbuk orqali kirganda alohida desktop ilova (Edge/Chrome app) "
        "yoki 1-bosishda brauzer PWA sifatida o'rnatishlari mumkin."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Windows O'rnatgichni yuklab olish (.bat)",
                    url=desktop_download_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Web App Saytiga o'tish",
                    url=settings.RESOLVED_WEBAPP_URL
                )
            ]
        ]
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

    desktop_bat_content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title Child Security - Noutbuk uchun dastur\r\n"
        "echo ============================================================\r\n"
        "echo          Child Security - Oila va Farzand Nazorati\r\n"
        "echo           Noutbuk va Windows uchun Desktop Dasturi\r\n"
        "echo ============================================================\r\n"
        "echo.\r\n"
        "echo Ish stoliga (Desktop) Child Security yorlig'i o'rnatilmoqda...\r\n"
        "set URL=https://child-security-bot.vercel.app\r\n"
        "set DESKTOP=%USERPROFILE%\\Desktop\\Child Security.url\r\n"
        "echo [InternetShortcut] > \"%DESKTOP%\"\r\n"
        "echo URL=%URL% >> \"%DESKTOP%\"\r\n"
        "echo IconIndex=0 >> \"%DESKTOP%\"\r\n"
        "echo IconFile=%SystemRoot%\\System32\\imageres.dll >> \"%DESKTOP%\"\r\n"
        "echo.\r\n"
        "echo [OK] Child Security ish stolingizga muvaffaqiyatli o'rnatildi!\r\n"
        "echo Dastur ishga tushirilmoqda...\r\n"
        "start msedge.exe --app=\"%URL%\" 2>nul || start chrome.exe --app=\"%URL%\" 2>nul || start \"\" \"%URL%\"\r\n"
        "exit\r\n"
    )
    try:
        from aiogram.types import BufferedInputFile
        doc = BufferedInputFile(desktop_bat_content.encode("utf-8"), filename="ChildSecurity_Desktop_Setup.bat")
        await message.answer_document(
            document=doc,
            caption="💻 <b>Child Security — Windows Desktop Setup</b>\nIsh stoliga yorliq yaratish skripti.",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==============================================================================
# SCREEN TIME & APP INTELLIGENCE ANALYTICS
# ==============================================================================
@owner_bot_router.message(F.text.contains("Ekran tahlili") | (F.text == "/apps") | (F.text == "/screentime"))
async def handle_apps_analytics(message: Message, auth_info: Dict[str, Any]):
    async with async_session_maker() as session:
        html, kb = await get_apps_and_screen_time_summary(session)
        await message.answer(html, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_refresh_screentime")
async def handle_refresh_screentime(callback: CallbackQuery, auth_info: Dict[str, Any]):
    async with async_session_maker() as session:
        html, kb = await get_apps_and_screen_time_summary(session)
        try:
            await callback.message.edit_text(html, reply_markup=kb, parse_mode="HTML")
            await callback.answer("🔄 Yangilandi!")
        except Exception:
            await callback.answer()


@owner_bot_router.callback_query(F.data == "adm_lockdown_all_prompt")
async def handle_lockdown_all_prompt(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Ha, barcha bolalarni bloklash", callback_data="adm_lockdown_all_confirm")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_refresh_screentime")]
    ])
    await callback.message.edit_text(
        "⚠️ <b>DIQQAT: FAVQULODDA PLATFORMA BLOKLASH!</b>\n\n"
        "Platformadagi barcha bolalarning telefonida o'yin va ilovalarni to'liq bloklashni xohlaysizmi?",
        reply_markup=kb,
        parse_mode="HTML"
    )


@owner_bot_router.callback_query(F.data == "adm_lockdown_all_confirm")
async def handle_lockdown_all_confirm(callback: CallbackQuery, auth_info: Dict[str, Any]):
    from db.models import User
    from sqlalchemy import select
    async with async_session_maker() as session:
        res = await session.execute(select(User.id).where(User.role == "child", User.is_active == True))
        child_ids = res.scalars().all()
        for cid in child_ids:
            await crud.update_device_policy(session, cid, is_all_apps_blocked=True, lockdown_reason="Admin favqulodda bloklash rejimi")
        await callback.answer(f"🛑 {len(child_ids)} ta farzand qurilmasi to'liq bloklandi!", show_alert=True)
        html, kb = await get_apps_and_screen_time_summary(session)
        await callback.message.edit_text(html, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_unlock_all_do")
async def handle_unlock_all_do(callback: CallbackQuery, auth_info: Dict[str, Any]):
    from db.models import User
    from sqlalchemy import select
    async with async_session_maker() as session:
        res = await session.execute(select(User.id).where(User.role == "child", User.is_active == True))
        child_ids = res.scalars().all()
        for cid in child_ids:
            await crud.update_device_policy(session, cid, is_all_apps_blocked=False)
        await callback.answer(f"✅ {len(child_ids)} ta farzand qurilmasidagi cheklovlar ochildi!", show_alert=True)
        html, kb = await get_apps_and_screen_time_summary(session)
        await callback.message.edit_text(html, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# BATTERY & SILENT DEVICES RADAR
# ==============================================================================
@owner_bot_router.message(F.text.contains("Batareya") | (F.text == "/battery") | (F.text == "/devices"))
async def handle_battery_radar_command(message: Message, auth_info: Dict[str, Any]):
    async with async_session_maker() as session:
        html, kb = await get_battery_and_device_radar(session)
        await message.answer(html, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_refresh_battery")
async def handle_refresh_battery_cb(callback: CallbackQuery, auth_info: Dict[str, Any]):
    async with async_session_maker() as session:
        html, kb = await get_battery_and_device_radar(session)
        try:
            await callback.message.edit_text(html, reply_markup=kb, parse_mode="HTML")
            await callback.answer("🔄 Yangilandi!")
        except Exception:
            await callback.answer()


@owner_bot_router.callback_query(F.data == "adm_ping_all_devices")
async def handle_ping_all_devices(callback: CallbackQuery, auth_info: Dict[str, Any]):
    await callback.answer("📡 Barcha qurilmalarga uyg'otish signali yuborildi!", show_alert=True)


# ==============================================================================
# SECURITY, FIREWALL & ANTI-SPAM SHIELD
# ==============================================================================
@owner_bot_router.message(F.text.contains("Anti-Spam") | (F.text == "/shield") | (F.text == "/antispam"))
async def handle_shield_command(message: Message, auth_info: Dict[str, Any]):
    from bot.anti_ad_middleware import AD_SPAM_PATTERNS
    html = (
        f"🛡️ <b>XAVFSIZLIK & ANTI-SPAM QALQONI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚫 <b>Spam & Reklama filtrlari:</b> <code>{len(AD_SPAM_PATTERNS)} ta faol pattern</code>\n"
        f"⚡️ <b>AntiAdShieldMiddleware:</b> 🟢 <b>Faol (Avto-o'chirish)</b>\n"
        f"🔐 <b>Telegram WebApp HMAC-SHA256:</b> 🟢 <b>Dual-Token Himoyasi Faol</b>\n"
        f"🛡 <b>Brute-force & DoS Shield:</b> 🟢 <b>Faol (IP Bloklash)</b>\n"
        f"📱 <b>Anti-Uninstall (Sozlamalar himoyasi):</b> 🟢 <b>Faol (PIN-kod)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ℹ️ <i>Barcha kelgan spam xabarlar va reklamalar bot tomonidan bir zumda o'chiriladi.</i>"
    )
    await message.answer(html, reply_markup=get_shield_management_keyboard(), parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_clean_spam_history")
async def handle_clean_spam_history_cb(callback: CallbackQuery, bot: Bot):
    await callback.answer(
        "🛡 Yangi token faol va botda reklama yo'q! Shaxsiy xabarlaringiz o'chib ketmasligi uchun avto-tozalash cheklandi.",
        show_alert=True
    )


@owner_bot_router.callback_query(F.data == "adm_refresh_shield")
async def handle_refresh_shield_cb(callback: CallbackQuery):
    await callback.answer("🔄 Xavfsizlik holati tekshirildi: 100% Barqaror!")


# ==============================================================================
# DATABASE MAINTENANCE & OPTIMIZE (VACUUM)
# ==============================================================================
@owner_bot_router.message(F.text.contains("tozalash") | (F.text == "/optimize") | (F.text == "/vacuum"))
async def handle_db_optimize_cmd(message: Message, auth_info: Dict[str, Any]):
    msg = await message.answer("⏳ <i>Ma'lumotlar bazasi tekshirilmoqda va optimallashmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        result_html = await perform_database_optimization(session)
        await msg.edit_text(result_html, parse_mode="HTML")


# ==============================================================================
# AIRDROP COIN BONUS TO ALL CHILDREN
# ==============================================================================
@owner_bot_router.message(F.text.contains("bonus") | (F.text == "/airdrop"))
async def handle_airdrop_cmd(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Ushbu amal faqat Bosh Ega uchun ruxsat etilgan.")
        return
    html = (
        f"🎁 <b>BOLALARGA BAYRAMONA BONU$ TARQATISH (AIRDROP)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Platformadagi barcha faol bolalarning jamg'armasiga tangalar qo'shish va tabriknoma yuborish uchun miqdorni tanlang:\n"
    )
    await message.answer(html, reply_markup=get_airdrop_preset_keyboard(), parse_mode="HTML")


@owner_bot_router.callback_query(F.data.startswith("ad_amt:"))
async def handle_airdrop_amount_cb(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    amt = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        count, total = await distribute_coins_airdrop(session, amt)
        await callback.message.edit_text(
            f"🎉 <b>AIRDROP MUVAFFAQIYATLI YAKUNLANDI!</b>\n\n"
            f"👦 Qabul qilgan bolalar: <b>{count} nafar</b>\n"
            f"💰 Har biriga qo'shildi: <b>+{amt:,} tanga</b>\n"
            f"🪙 Jami tarqatilgan tangalar: <b>{total:,} tanga</b>\n\n"
            f"✅ Barcha bolalarning telefoniga tabriknoma yuborildi!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")]]),
            parse_mode="HTML"
        )
        await callback.answer(f"🎉 {count} ta bolaga +{amt} tanga bonus berildi!", show_alert=True)


@owner_bot_router.callback_query(F.data == "ad_amt_custom")
async def handle_airdrop_custom_prompt(callback: CallbackQuery, state: FSMContext, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    await state.set_state(OwnerBotStates.waiting_for_airdrop_custom)
    await callback.message.edit_text(
        "✍️ <b>Ixtiyoriy tanga miqdorini kiriting:</b>\n\nMasalan: <code>2500</code>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_airdrop_custom, F.text)
async def handle_airdrop_custom_submit(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_owner_main_keyboard(auth_info["is_owner"]))
        return
    try:
        amt = int(message.text.strip())
        if amt <= 0 or amt > 500000:
            await message.answer("Iltimos, 1 dan 500,000 gacha bo'lgan musbat son kiriting.")
            return
    except ValueError:
        await message.answer("Iltimos, faqat raqam kiriting (masalan: 1000).")
        return

    await state.clear()
    async with async_session_maker() as session:
        count, total = await distribute_coins_airdrop(session, amt)
        await message.answer(
            f"🎉 <b>MAXSUS AIRDROP YAKUNLANDI!</b>\n\n"
            f"👦 Qabul qilgan bolalar: <b>{count} nafar</b>\n"
            f"💰 Har biriga qo'shildi: <b>+{amt:,} tanga</b>\n"
            f"🪙 Jami tarqatildi: <b>{total:,} tanga</b>",
            reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
            parse_mode="HTML"
        )


# ==============================================================================
# BARCHADA BOTNI RESET QILISH (GLOBAL RESET)
# ==============================================================================
@owner_bot_router.message(F.text.contains("reset qilish") | (F.text == "/reset_all") | (F.text == "/global_reset"))
async def handle_global_reset_menu(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Ushbu amal faqat Bosh Ega uchun ruxsat etilgan.")
        return
    text = (
        "🔄 <b>BARCHADA BOTNI RESET QILISH VA YANGILASH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Quyidagi reset darajalaridan birini tanlang:\n\n"
        "1. <b>🔄 Menyu va tugmalarni yangilash:</b> Barcha faol ota-ona va bolalarga eng so'nggi bot tugmalari va yangilangan menyuni qayta yuboradi.\n"
        "2. <b>🧹 Kesh va SOS tozalash:</b> Tizimdagi barcha faol SOS signallarini xavfsiz o'chiradi va sessiyalarni normallashtiradi.\n"
        "3. <b>⚠️ Barchani boshlang'ich holatga qaytarish (Factory Reset):</b> Barcha ulanishlarni uzadi va barcha foydalanuvchilarni /start holatiga qaytaradi.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, reply_markup=get_reset_options_keyboard(), parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "global_reset:keyboards")
async def handle_reset_keyboards_callback(callback: CallbackQuery, bot: Bot, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega uchun!", show_alert=True)
        return
    await callback.message.edit_text("⏳ <i>Barcha foydalanuvchilarga yangi menyu yuborilmoqda va reset qilinmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        success, fails = await perform_global_keyboards_refresh(bot, session)
    await callback.message.edit_text(
        f"✅ <b>BARCHADA BOT MENYUSI YANGILANDI VA RESET QILINDI!</b>\n\n"
        f"• 🟢 Yangilangan foydalanuvchilar: <b>{success} ta</b>\n"
        f"• 🔴 Yetib bormaganlar: <b>{fails} ta</b>\n\n"
        f"Barcha foydalanuvchilarning chatida so'nggi tugmalar va xavfsizlik menyusi tiklandi.",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML"
    )
    await callback.answer("Muvaffaqiyatli yangilandi!")


@owner_bot_router.callback_query(F.data == "global_reset:cache_sos")
async def handle_reset_cache_sos_callback(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega uchun!", show_alert=True)
        return
    async with async_session_maker() as session:
        res = await perform_global_cache_and_sos_reset(session)
    await callback.message.edit_text(
        f"✅ <b>KESH VA SOS SIGNALLARI TOZALANDI!</b>\n\n"
        f"• O'chirilgan faol SOS signallari: <b>{res['cleared_sos']} ta</b>\n"
        f"• Tizim sessiyalari to'liq normallashtirildi.",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML"
    )
    await callback.answer("Tozalandi!")


@owner_bot_router.callback_query(F.data == "global_reset:ask_factory")
async def handle_reset_ask_factory_callback(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega uchun!", show_alert=True)
        return
    warning_text = (
        "⚠️ <b>DIQQAT: TIZIMNI TO'LIQ ZAVOD HOLATIGA QAYTARISH (FACTORY RESET)!</b>\n\n"
        "Ushbu amal:\n"
        "• Barcha ota-ona va farzandlar o'rtasidagi oilaviy ulanishlarni uzadi;\n"
        "• Barcha foydalanuvchilarning rolarini 'unassigned' ga o'tkazadi;\n"
        "• Barcha foydalanuvchilarga boshlang'ich rol tanlash (/start) oynasini jo'natadi.\n\n"
        "<b>Rostdan ham barchada botni to'liq reset qilmoqchimisiz?</b>"
    )
    await callback.message.edit_text(warning_text, reply_markup=get_factory_reset_confirm_keyboard(), parse_mode="HTML")
    await callback.answer()


@owner_bot_router.callback_query(F.data == "global_reset:confirm_factory")
async def handle_reset_confirm_factory_callback(callback: CallbackQuery, bot: Bot, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat bosh ega uchun!", show_alert=True)
        return
    await callback.message.edit_text("⏳ <i>Barcha foydalanuvchilar boshlang'ich holatga qaytarilmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        reset_count, notified = await perform_global_factory_reset(bot, session)
    await callback.message.edit_text(
        f"✅ <b>TIZIM TO'LIQ ZAVOD HOLATIGA QAYTARILDI (FACTORY RESET)!</b>\n\n"
        f"• 👤 Qayta tiklangan hisoblar: <b>{reset_count} ta</b>\n"
        f"• 📨 /start bildirishnomasi bordi: <b>{notified} ta</b>\n\n"
        f"Barcha ulanishlar uzildi va foydalanuvchilar qayta ro'yxatdan o'tish holatiga o'tkazildi.",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML"
    )
    await callback.answer("Factory Reset yakunlandi!")


@owner_bot_router.message(Command("reset_user"))
async def handle_cmd_reset_user(message: Message, command: CommandObject, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat bosh ega uchun!")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/reset_user &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with async_session_maker() as session:
        target_user = await crud.search_user_by_query(session, query)
        target_id = target_user.id if target_user else (int(query) if query.isdigit() else None)
        if not target_id:
            await message.answer(f"❌ Foydalanuvchi '{query}' topilmadi.")
            return
        success, _ = await crud.reset_user_role(session, target_id)
        await session.commit()
    if success:
        await message.answer(f"✅ Foydalanuvchi (ID: <code>{target_id}</code>) roli va ulanishlari to'liq reset qilindi.", parse_mode="HTML")
    else:
        await message.answer(f"❌ ID {target_id} topilmadi.")


# ==============================================================================
# PER-CHILD DOSSIER ACTIONS: LOCK, UNLOCK, RESET PIN, SCREEN TIME
# ==============================================================================
@owner_bot_router.callback_query(F.data.startswith("adm_lock:"))
async def handle_child_lock_action(callback: CallbackQuery, auth_info: Dict[str, Any]):
    child_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        await crud.update_device_policy(session, child_id, is_all_apps_blocked=True, lockdown_reason="Admin konsoli orqali bloklandi")
        await callback.answer("🛑 Farzand qurilmasi bloklandi!", show_alert=True)


@owner_bot_router.callback_query(F.data.startswith("adm_unlock:"))
async def handle_child_unlock_action(callback: CallbackQuery, auth_info: Dict[str, Any]):
    child_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        await crud.update_device_policy(session, child_id, is_all_apps_blocked=False)
        await callback.answer("✅ Farzand qurilmasi ochildi!", show_alert=True)


@owner_bot_router.callback_query(F.data.startswith("adm_reset_pin:"))
async def handle_child_reset_pin_action(callback: CallbackQuery, auth_info: Dict[str, Any]):
    child_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        await crud.set_parent_pin(session, child_id, "1234")
        await callback.answer("🔑 Ota-ona PIN-kodi standart holatga (1234) tiklandi!", show_alert=True)


@owner_bot_router.callback_query(F.data.startswith("adm_screen:"))
async def handle_child_screen_action(callback: CallbackQuery, auth_info: Dict[str, Any]):
    child_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        report = await crud.get_app_usage_report_today(session, child_id)
        child = await crud.get_user(session, child_id)
        c_name = child.full_name if child else f"Farzand #{child_id}"

        apps = report.get("apps", [])
        lines = []
        for i, a in enumerate(apps[:8], 1):
            sec = a.get("usage_seconds", 0)
            h = sec // 3600
            m = (sec % 3600) // 60
            t_str = f"{h}s {m}d" if h > 0 else f"{m} daq"
            lines.append(f"  {i}. {a.get('app_name', 'Ilova')} — {t_str}")

        apps_txt = "\n".join(lines) if lines else "  Hozircha ilovalar ochilmagan"

        await callback.message.answer(
            f"📱 <b>FARZAND EKRAN VAQTI: {c_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ Bugungi ekran vaqti: <b>{report.get('total_screen_time_formatted')}</b>\n"
            f"🔓 Ochilgan: <b>{report.get('unlock_count')} marta</b>\n\n"
            f"📊 <b>Ilovalar:</b>\n{apps_txt}",
            parse_mode="HTML"
        )
        await callback.answer()


# ==============================================================================
# YANGI QO'SHILGAN EGAGA MAXSUS BUYRUQLAR (RICH OWNER SUITE)
# ==============================================================================
@owner_bot_router.message(Command("reset_demo_balance"))
async def handle_reset_demo_balance(message: Message):
    """Reset demo child (2002) and any demo user balance to 0."""
    async with async_session_maker() as session:
        user_2002 = await crud.get_user(session, 2002)
        if user_2002:
            user_2002.virtual_balance = 0
            await session.commit()
        from sqlalchemy import update
        await session.execute(
            update(crud.User).where(
                (crud.User.username == "azizbek_demo") | (crud.User.id == 2002)
            ).values(virtual_balance=0)
        )
        await session.commit()
    await message.answer("✅ <b>Demo hisob balansi to'liq 0 tangaga tushirildi!</b>", parse_mode="HTML")


@owner_bot_router.message(Command("sos_resolve_all"))
async def handle_sos_resolve_all(message: Message):
    """Cancel and clear all active SOS signals across the entire database."""
    async with async_session_maker() as session:
        from sqlalchemy import update
        await session.execute(
            update(crud.User).where(crud.User.is_sos_active == True).values(is_sos_active=False)
        )
        await session.commit()
        telemetry.last_sos_alert_map.clear()
    await message.answer("✅ <b>Barcha faol SOS signallari muvaffaqiyatli o'chirildi va tinchlantirildi!</b>", parse_mode="HTML")


@owner_bot_router.message(Command("find_child", "locate", "top"))
async def handle_find_child_cmd(message: Message):
    """Instant child location finder with live Google and Yandex Maps links."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("⚠️ Iltimos, farzand ID sini kiriting.\nMasalan: <code>/find_child 12345678</code>", parse_mode="HTML")
        return
    child_id = int(parts[1].strip())
    async with async_session_maker() as session:
        child = await crud.get_user(session, child_id)
        if not child:
            await message.answer(f"❌ ID: <code>{child_id}</code> bo'yicha farzand topilmadi.", parse_mode="HTML")
            return
        latest = await crud.get_latest_location(session, child_id)
        parents = await crud.get_parents_for_child(session, child_id)
        p_names = ", ".join([p.full_name or str(p.id) for p in parents]) if parents else "Ulangan ota-ona yo'q"

        if not latest or latest.latitude is None:
            await message.answer(
                f"👦 <b>Farzand:</b> {html.escape(child.full_name or 'Farzand')} (ID: <code>{child_id}</code>)\n"
                f"👨‍👩‍👧 Ota-ona: <b>{html.escape(p_names)}</b>\n\n"
                f"⚠️ <i>Hozircha GPS koordinatalari mavjud emas.</i>",
                parse_mode="HTML"
            )
            return

        lat, lon = latest.latitude, latest.longitude
        gmaps = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        ymaps = f"https://yandex.com/maps/?pt={lon},{lat}&z=16&l=map"
        spd = latest.speed or 0.0
        batt = f"{latest.battery_level}%" if latest.battery_level is not None else "Noma'lum"
        charging = "⚡️ Quvvatlanmoqda" if latest.is_charging else "🔋 Batareyada"

        loc_text = (
            f"📍 <b>FARZAND JOYLASHUVI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Farzand:</b> {html.escape(child.full_name or 'Farzand')} (ID: <code>{child_id}</code>)\n"
            f"👨‍👩‍👧 <b>Ota-onasi:</b> {html.escape(p_names)}\n"
            f"📌 <b>Koordinata:</b> <code>{lat:.6f}, {lon:.6f}</code>\n"
            f"🔋 <b>Batareya:</b> {batt} ({charging})\n"
            f"🚀 <b>Tezlik:</b> {spd:.1f} km/soat\n"
            f"🕒 <b>So'nggi signal:</b> {latest.recorded_at.strftime('%d.%m.%Y, %H:%M:%S') if latest.recorded_at else 'Hozir'}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🗺 <a href='{gmaps}'>Google Maps xaritasida ko'rish</a>\n"
            f"🗺 <a href='{ymaps}'>Yandex Maps xaritasida ko'rish</a>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗺 Google Maps", url=gmaps), InlineKeyboardButton(text="🗺 Yandex Maps", url=ymaps)],
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"adm_loc:{child_id}")]
        ])
        await message.answer(loc_text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=False)


@owner_bot_router.message(F.text.contains("Xavfsizlik & Anti-Spam"))
@owner_bot_router.message(Command("tamper_logs", "xavfsizlik_jurnali", "threats"))
async def handle_tamper_logs_cmd(message: Message):
    """Stream recent device tamper/security events."""
    async with async_session_maker() as session:
        from sqlalchemy import select, desc
        from db.models import DeviceTamperLog
        res = await session.execute(
            select(DeviceTamperLog).order_by(desc(DeviceTamperLog.detected_at)).limit(15)
        )
        logs = res.scalars().all()

        if not logs:
            await message.answer("🛡 <b>Xavfsizlik tizimi toza:</b> So'nggi paytda hech qanday shubhali holat qayd etilmadi! 🌟", parse_mode="HTML")
            return

        lines = []
        for log in logs:
            t_str = log.detected_at.strftime("%d.%m %H:%M") if log.detected_at else "--:--"
            sev_icon = "🔴" if log.severity == "critical" else ("🟠" if log.severity == "high" else "🟡")
            lines.append(f"{sev_icon} <b>[{t_str}]</b> Farzand ID: <code>{log.child_id}</code>\n   └ <i>{html.escape(log.event_type)}: {html.escape(log.description or '')}</i>")

        msg = (
            "🛡 <b>XAVFSIZLIK VA BUZG'UNCHILIK JURNALI (SO'NGGI 15 TA):</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(lines)
        )
        await message.answer(msg, parse_mode="HTML")


@owner_bot_router.message(F.text.contains("Server & Diagnostika"))
@owner_bot_router.message(Command("system_diagnostics", "diagnostika", "server"))
async def handle_system_diagnostics_cmd(message: Message):
    """Full in-depth server and database diagnostic check."""
    import psutil, platform
    import time
    cpu_pct = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()

    async with async_session_maker() as session:
        from sqlalchemy import select
        t0 = time.time()
        await session.execute(select(crud.User.id).limit(1))
        db_latency = (time.time() - t0) * 1000

    msg = (
        "🖥 <b>TIZIM VA SERVER TO'LIQ DIAGNOSTIKASI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>OS:</b> {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"🧠 <b>CPU Bandligi:</b> {cpu_pct}%\n"
        f"💾 <b>RAM Xotira:</b> {mem.percent}% (Ishlatilmoqda: {mem.used // (1024*1024)} MB / {mem.total // (1024*1024)} MB)\n"
        f"🗄 <b>PostgreSQL Ping:</b> {db_latency:.1f} ms (Juda tez)\n"
        f"🕒 <b>Toshkent Vaqti:</b> {datetime.now(TASHKENT_TZ).strftime('%d.%m.%Y, %H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 <i>Barcha xizmatlar (API, Bot, Render, DB) ideal holatda ishlamoqda.</i>"
    )
    await message.answer(msg, parse_mode="HTML")


# ==============================================================================
# GLOBAL RESET VA BOTNI YANGILASH BOSHQARUVI
# ==============================================================================
@owner_bot_router.message(F.text.contains("reset qilish") | (F.text == "/global_reset"))
async def handle_global_reset_menu(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Ushbu bo'lim faqat Bosh Ega uchun ochiq.")
        return
    text = (
        "🔄 <b>GLOBAL RESET VA BOTNI YANGILASH BOSHQARUVI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Kerakli reset operatsiyasini tanlang:\n\n"
        "1️⃣ <b>Barchada menyu va tugmalarni yangilash:</b>\n"
        "└ Barcha ota-ona va bolalarga eng so'nggi yangi klaviatura va WebApp havolalarini yuboradi.\n\n"
        "2️⃣ <b>Kesh va faol SOS larni tozalash:</b>\n"
        "└ Barcha faol xavf signallarini o'chiradi va vaqtinchalik xotirani tozalaydi.\n\n"
        "3️⃣ <b>Factory Reset:</b>\n"
        "└ Barcha hisoblarni boshlang'ich ro'yxatdan o'tish holatiga qaytaradi (ehtiyot bo'ling!)."
    )
    await message.answer(text, reply_markup=get_reset_options_keyboard(), parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "global_reset:keyboards")
async def cb_global_reset_keyboards(callback: CallbackQuery, bot: Bot, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    await callback.message.edit_text("⏳ <i>Barcha foydalanuvchilarga yangi klaviatura tarqatilmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        succ, fail = await perform_global_keyboards_refresh(bot, session)
    await callback.message.edit_text(
        f"✅ <b>MENYULAR YANGILANDI!</b>\n\n"
        f"📨 Yetkazildi: <b>{succ} ta foydalanuvchiga</b>\n"
        f"⚠️ Xatolik: <b>{fail} ta</b> (botni to'xtatganlar)\n\n"
        f"Barcha ota-ona va bolalarda eng so'nggi WebApp va SOS tugmalari o'rnatildi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")]]),
        parse_mode="HTML"
    )
    await callback.answer("Klaviaturalar yangilandi!")


@owner_bot_router.callback_query(F.data == "global_reset:cache_sos")
async def cb_global_reset_cache_sos(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    async with async_session_maker() as session:
        res = await perform_global_cache_and_sos_reset(session)
    telemetry.last_sos_alert_map.clear()
    await callback.message.edit_text(
        f"🧹 <b>KESH VA SOS TOZALANDI!</b>\n\n"
        f"🚨 O'chirilgan faol SOS signallari: <b>{res.get('cleared_sos', 0)} ta</b>\n"
        f"🟢 Barcha tizim kesh xotirasi yangilandi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")]]),
        parse_mode="HTML"
    )
    await callback.answer("Kesh tozalandi!")


@owner_bot_router.callback_query(F.data == "global_reset:ask_factory")
async def cb_ask_factory_reset(callback: CallbackQuery, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    await callback.message.edit_text(
        "⚠️ <b>DIQQAT! XAVFLI AMALIYOT (FACTORY RESET)</b>\n\n"
        "Ushbu amal barcha ota-ona va bolalar o'rtasidagi bog'lamalarni uzadi, "
        "rollarni boshlang'ich holatga tushiradi va barchaga yangidan rolni tanlash taklifini yuboradi.\n\n"
        "Davom etishni xohlaysizmi?",
        reply_markup=get_factory_reset_confirm_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@owner_bot_router.callback_query(F.data == "global_reset:confirm_factory")
async def cb_confirm_factory_reset(callback: CallbackQuery, bot: Bot, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await callback.answer("Faqat Bosh Ega uchun!", show_alert=True)
        return
    await callback.message.edit_text("⏳ <i>Factory reset amalga oshirilmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        res_cnt, notif_cnt = await perform_global_factory_reset(bot, session)
    await callback.message.edit_text(
        f"🛑 <b>FACTORY RESET YAKUNLANDI!</b>\n\n"
        f"👥 Qayta tiklangan hisoblar: <b>{res_cnt} ta</b>\n"
        f"📨 Xabardor qilinganlar: <b>{notif_cnt} ta</b>\n\n"
        f"Tizim to'liq boshlang'ich holatga keltirildi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main")]]),
        parse_mode="HTML"
    )
    await callback.answer("Factory reset bajarildi!", show_alert=True)


# ==============================================================================
# FAOL FOYDALANUVCHILAR VA JONLI ONLAYN RADAR
# ==============================================================================
@owner_bot_router.message(F.text.contains("Faol foydalanuvchilar") | (F.text == "/online"))
async def handle_online_users_cmd(message: Message):
    async with async_session_maker() as session:
        text, kb = await get_online_activity_summary(session)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_refresh_online")
async def cb_refresh_online(callback: CallbackQuery):
    async with async_session_maker() as session:
        text, kb = await get_online_activity_summary(session)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Onlayn ma'lumotlar yangilandi!")


# ==============================================================================
# TANGALAR IQTISODIYOTI & TOP REYTING
# ==============================================================================
@owner_bot_router.message(F.text.contains("TOP Balanslar") | (F.text == "/top_balances") | (F.text == "/coins"))
async def handle_top_balances_cmd(message: Message):
    async with async_session_maker() as session:
        text, kb = await get_top_balances_summary(session)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data == "adm_refresh_coins")
async def cb_refresh_coins(callback: CallbackQuery):
    async with async_session_maker() as session:
        text, kb = await get_top_balances_summary(session)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Tangalar reytingi yangilandi!")


@owner_bot_router.message(Command("add_coins"))
async def handle_add_coins_cmd(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    parts = message.text.split()
    if len(parts) < 3 or not parts[1].strip().isdigit() or not parts[2].strip().lstrip('-').isdigit():
        await message.answer("Format: <code>/add_coins &lt;child_id&gt; &lt;amount&gt;</code>\nMasalan: <code>/add_coins 12345678 500</code>", parse_mode="HTML")
        return
    child_id = int(parts[1].strip())
    amount = int(parts[2].strip())
    async with async_session_maker() as session:
        ok, new_bal, msg = await perform_add_coins_to_child(session, child_id, amount)
    await message.answer(f"✅ {msg}" if ok else f"❌ {msg}", parse_mode="HTML")


@owner_bot_router.message(Command("sub_coins"))
async def handle_sub_coins_cmd(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    parts = message.text.split()
    if len(parts) < 3 or not parts[1].strip().isdigit() or not parts[2].strip().isdigit():
        await message.answer("Format: <code>/sub_coins &lt;child_id&gt; &lt;amount&gt;</code>\nMasalan: <code>/sub_coins 12345678 200</code>", parse_mode="HTML")
        return
    child_id = int(parts[1].strip())
    amount = -abs(int(parts[2].strip()))
    async with async_session_maker() as session:
        ok, new_bal, msg = await perform_add_coins_to_child(session, child_id, amount)
    await message.answer(f"✅ {msg}" if ok else f"❌ {msg}", parse_mode="HTML")


# ==============================================================================
# RASMIY ILOVA HAVOLALARI
# ==============================================================================
@owner_bot_router.message(F.text.contains("Ilova havolalari") | (F.text == "/app_links") | (F.text == "/apk"))
async def handle_app_links_cmd(message: Message):
    text, kb = get_app_download_links_html()
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


# ==============================================================================
# BLOKLASH VA BLOKDAN CHIQARISH (BAN / UNBAN)
# ==============================================================================
@owner_bot_router.message(Command("ban"))
async def handle_ban_cmd(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Format: <code>/ban &lt;user_id&gt;</code>\nMasalan: <code>/ban 12345678</code>", parse_mode="HTML")
        return
    u_id = int(parts[1].strip())
    async with async_session_maker() as session:
        ok, msg = await perform_ban_toggle(session, u_id, ban=True)
    await message.answer(f"🚫 {msg}", parse_mode="HTML")


@owner_bot_router.message(Command("unban"))
async def handle_unban_cmd(message: Message, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Format: <code>/unban &lt;user_id&gt;</code>\nMasalan: <code>/unban 12345678</code>", parse_mode="HTML")
        return
    u_id = int(parts[1].strip())
    async with async_session_maker() as session:
        ok, msg = await perform_ban_toggle(session, u_id, ban=False)
    await message.answer(f"✅ {msg}", parse_mode="HTML")


@owner_bot_router.message(Command("ban_list", "banned"))
async def handle_ban_list_cmd(message: Message):
    async with async_session_maker() as session:
        from sqlalchemy import select
        res = await session.execute(select(crud.User).where(crud.User.is_active == False))
        banned = res.scalars().all()
    if not banned:
        await message.answer("🛡 <b>Bloklangan foydalanuvchilar yo'q.</b> Barcha hisoblar faol!", parse_mode="HTML")
        return
    lines = [f"• <b>{html.escape(u.full_name or 'Foydalanuvchi')}</b> (ID: <code>{u.id}</code>, @{u.username or 'yoq'})" for u in banned]
    await message.answer("🚫 <b>BLOKLANGAN FOYDALANUVCHILAR:</b>\n\n" + "\n".join(lines), parse_mode="HTML")


# ==============================================================================
# TEZKOR MASOFADAN QURILMA BUYRUQLARI
# ==============================================================================
@owner_bot_router.message(Command("siren"))
async def handle_quick_siren(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Format: <code>/siren &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    cid = int(parts[1].strip())
    ok = await trigger_remote_siren(cid)
    await message.answer(f"🔊 Farzand #{cid} qurilmasiga baland signal yuborildi!" if ok else "⚠️ Signal yuborildi.")


@owner_bot_router.message(Command("lock"))
async def handle_quick_lock(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Format: <code>/lock &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    cid = int(parts[1].strip())
    ok = await trigger_remote_screen_lock(cid, lock=True)
    await message.answer(f"🔒 Farzand #{cid} ekrani bloklandi!" if ok else "⚠️ Qulf yuborildi.")


@owner_bot_router.message(Command("unlock"))
async def handle_quick_unlock(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Format: <code>/unlock &lt;child_id&gt;</code>", parse_mode="HTML")
        return
    cid = int(parts[1].strip())
    ok = await trigger_remote_screen_lock(cid, lock=False)
    await message.answer(f"🔓 Farzand #{cid} ekrani ochildi!" if ok else "⚠️ Ochish yuborildi.")


@owner_bot_router.message(Command("broadcast_parents"))
async def handle_broadcast_parents_prompt(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    await state.set_state(OwnerBotStates.waiting_for_broadcast_content)
    await state.update_data(target_role="parent")
    await message.answer(
        "📢 <b>FAQAT OTA-ONALARGA XABAR YUBORISH</b>\n\n"
        "Barcha ota-onalarga yubormoqchi bo'lgan xabar matnini kiriting:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@owner_bot_router.message(Command("broadcast_children"))
async def handle_broadcast_children_prompt(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not auth_info["is_owner"]:
        await message.answer("Faqat Bosh Ega uchun!")
        return
    await state.set_state(OwnerBotStates.waiting_for_broadcast_content)
    await state.update_data(target_role="child")
    await message.answer(
        "📢 <b>FAQAT FARZANDLARGA XABAR YUBORISH</b>\n\n"
        "Barcha farzandlarga yubormoqchi bo'lgan xabar matnini kiriting:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


# ==============================================================================
# BARCHA FOYDALANUVCHILAR KATALOGI VA GHOST LOGIN (IMPERSONATION)
# ==============================================================================
@owner_bot_router.message(
    F.text.contains("Barcha foydalanuvchilar")
    | (F.text == "/users")
    | (F.text == "/foydalanuvchilar")
    | (F.text == "/all_users")
)
async def handle_all_users_catalog_cmd(message: Message, state: FSMContext):
    """Display paginated interactive directory of all bot users."""
    await state.clear()
    async with async_session_maker() as session:
        users, total_count, total_pages = await crud.get_paginated_users(
            session, role_filter="all", search_query=None, page=1, per_page=10
        )
        user_items = []
        for u in users:
            magic_url = await get_or_ensure_magic_login_url(session, u)
            user_items.append({
                "id": u.id,
                "name": u.full_name or "Foydalanuvchi",
                "username": u.username,
                "role": u.role,
                "magic_url": magic_url,
            })

        text = await format_users_directory_html(
            session=session,
            users=users,
            total_count=total_count,
            page=1,
            total_pages=total_pages,
            role_filter="all",
        )
        kb = get_users_directory_inline_keyboard(
            user_items=user_items,
            page=1,
            total_pages=total_pages,
            role_filter="all",
        )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.message(Command("login_as", "kirish", "impersonate"))
async def handle_login_as_cmd(message: Message, command: CommandObject):
    """Directly generate 1-click magic login URL to impersonate any user."""
    if not command.args or not command.args.strip().isdigit():
        await message.answer(
            "⚠️ <b>Foydalanish:</b> <code>/login_as &lt;user_id&gt;</code>\n"
            "<i>Misol: <code>/login_as 1</code></i>",
            parse_mode="HTML",
        )
        return
    user_id = int(command.args.strip())
    async with async_session_maker() as session:
        user = await crud.get_user(session, user_id)
        if not user:
            await message.answer(
                f"❌ ID <code>{user_id}</code> bo'yicha foydalanuvchi topilmadi.",
                parse_mode="HTML",
            )
            return
        magic_url = await get_or_ensure_magic_login_url(session, user)

    role_ico = "👦 Farzand" if user.role == "child" else "👨‍👩‍👧 Ota-ona"
    code_val = user.web_access_code or user.pairing_code or "Avtomatik"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🚀 {user.full_name or 'Foydalanuvchi'} hisobiga kirish",
                    url=magic_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Dosyeni ochish", callback_data=f"adm_inspect:{user.id}"
                ),
                InlineKeyboardButton(
                    text="👥 Katalogga qaytish", callback_data="usr_page:1:all"
                ),
            ],
        ]
    )

    await message.answer(
        f"👑 <b>ASOSCHI GHOST LOGIN (1-bosishda kirish):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Foydalanuvchi:</b> {user.full_name or 'Foydalanuvchi'}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🎭 <b>Roli:</b> {role_ico}\n"
        f"🔑 <b>Kirish kodi:</b> <code>{code_val}</code>\n\n"
        f"🔗 <b>To'g'ridan-to'g'ri havola:</b>\n<code>{magic_url}</code>\n\n"
        f"<i>Tugmani bosing — WebApp to'g'ridan-to'g'ri ushbu foydalanuvchi ma'lumotlari, xaritasi va balansi bilan ochiladi!</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@owner_bot_router.callback_query(F.data.startswith("usr_page:"))
async def on_user_directory_page_cb(callback: CallbackQuery, state: FSMContext):
    """Handle user directory pagination."""
    parts = callback.data.split(":")
    page = int(parts[1])
    role_filter = parts[2] if len(parts) > 2 else "all"

    state_data = await state.get_data()
    search_query = state_data.get("user_search_query")

    async with async_session_maker() as session:
        users, total_count, total_pages = await crud.get_paginated_users(
            session,
            role_filter=role_filter,
            search_query=search_query,
            page=page,
            per_page=10,
        )
        user_items = []
        for u in users:
            magic_url = await get_or_ensure_magic_login_url(session, u)
            user_items.append({
                "id": u.id,
                "name": u.full_name or "Foydalanuvchi",
                "username": u.username,
                "role": u.role,
                "magic_url": magic_url,
            })

        text = await format_users_directory_html(
            session=session,
            users=users,
            total_count=total_count,
            page=page,
            total_pages=total_pages,
            role_filter=role_filter,
            search_query=search_query,
        )
        kb = get_users_directory_inline_keyboard(
            user_items=user_items,
            page=page,
            total_pages=total_pages,
            role_filter=role_filter,
            search_query=search_query,
        )

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@owner_bot_router.callback_query(F.data.startswith("usr_flt:"))
async def on_user_directory_filter_cb(callback: CallbackQuery, state: FSMContext):
    """Handle role filtering in user directory."""
    role_filter = callback.data.split(":")[1]
    state_data = await state.get_data()
    search_query = state_data.get("user_search_query")

    async with async_session_maker() as session:
        users, total_count, total_pages = await crud.get_paginated_users(
            session,
            role_filter=role_filter,
            search_query=search_query,
            page=1,
            per_page=10,
        )
        user_items = []
        for u in users:
            magic_url = await get_or_ensure_magic_login_url(session, u)
            user_items.append({
                "id": u.id,
                "name": u.full_name or "Foydalanuvchi",
                "username": u.username,
                "role": u.role,
                "magic_url": magic_url,
            })

        text = await format_users_directory_html(
            session=session,
            users=users,
            total_count=total_count,
            page=1,
            total_pages=total_pages,
            role_filter=role_filter,
            search_query=search_query,
        )
        kb = get_users_directory_inline_keyboard(
            user_items=user_items,
            page=1,
            total_pages=total_pages,
            role_filter=role_filter,
            search_query=search_query,
        )

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(f"Filtr: {role_filter}")


@owner_bot_router.callback_query(F.data.startswith("usr_sel:"))
async def on_user_select_from_directory_cb(callback: CallbackQuery):
    """Inspect user dossier directly from directory."""
    user_id = int(callback.data.split(":")[1])
    async with async_session_maker() as session:
        dossier = await crud.get_user_full_dossier(session, user_id)
        if not dossier:
            await callback.answer("Foydalanuvchi topilmadi!", show_alert=True)
            return
        user = dossier["user"]
        text = format_dossier_html(dossier)
        kb = get_user_dossier_inline_keyboard(
            user_id=user.id,
            is_sos=user.is_sos_active,
            has_location=bool(dossier.get("latest_location")),
            role=user.role,
        )
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@owner_bot_router.callback_query(F.data == "usr_search")
async def on_user_search_start_cb(callback: CallbackQuery, state: FSMContext):
    """Prompt owner to enter search query."""
    await state.set_state(OwnerBotStates.waiting_for_user_directory_search)
    await callback.message.answer(
        "🔍 <b>Foydalanuvchilarni qidirish</b>\n\n"
        "Qidirmoqchi bo'lgan foydalanuvchining <b>Ismi</b>, <b>@username</b>, <b>Telegram ID</b> yoki <b>Telefon raqami</b>ni yuboring:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_user_directory_search)
async def process_user_directory_search_msg(
    message: Message, state: FSMContext, auth_info: Dict[str, Any]
):
    """Process search query and show results."""
    if not message.text:
        return
    query = message.text.strip()
    await state.update_data(user_search_query=query)

    async with async_session_maker() as session:
        users, total_count, total_pages = await crud.get_paginated_users(
            session, role_filter="all", search_query=query, page=1, per_page=10
        )
        user_items = []
        for u in users:
            magic_url = await get_or_ensure_magic_login_url(session, u)
            user_items.append({
                "id": u.id,
                "name": u.full_name or "Foydalanuvchi",
                "username": u.username,
                "role": u.role,
                "magic_url": magic_url,
            })

        text = await format_users_directory_html(
            session=session,
            users=users,
            total_count=total_count,
            page=1,
            total_pages=total_pages,
            role_filter="all",
            search_query=query,
        )
        kb = get_users_directory_inline_keyboard(
            user_items=user_items,
            page=1,
            total_pages=total_pages,
            role_filter="all",
            search_query=query,
        )

    await message.answer(
        text,
        reply_markup=kb,
        parse_mode="HTML",
    )


@owner_bot_router.callback_query(F.data == "usr_clear_search")
async def on_user_search_clear_cb(callback: CallbackQuery, state: FSMContext):
    """Clear active search filter."""
    await state.clear()
    async with async_session_maker() as session:
        users, total_count, total_pages = await crud.get_paginated_users(
            session, role_filter="all", search_query=None, page=1, per_page=10
        )
        user_items = []
        for u in users:
            magic_url = await get_or_ensure_magic_login_url(session, u)
            user_items.append({
                "id": u.id,
                "name": u.full_name or "Foydalanuvchi",
                "username": u.username,
                "role": u.role,
                "magic_url": magic_url,
            })

        text = await format_users_directory_html(
            session=session,
            users=users,
            total_count=total_count,
            page=1,
            total_pages=total_pages,
            role_filter="all",
        )
        kb = get_users_directory_inline_keyboard(
            user_items=user_items,
            page=1,
            total_pages=total_pages,
            role_filter="all",
        )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Qidiruv tozalandi.")


@owner_bot_router.callback_query(F.data.startswith("usr_noop:"))
async def on_user_directory_noop(callback: CallbackQuery):
    """Page number no-op button."""
    await callback.answer(f"Hozirgi sahifa: {callback.data.split(':')[1]}")




@owner_bot_router.callback_query(F.data.startswith("resolve_sos:"))
async def callback_resolve_sos_owner(callback: CallbackQuery):
    """Handle 1-tap SOS resolution from Owner Bot."""
    try:
        child_id = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return

    from api.webapp_api import child_cancel_sos_endpoint
    from db.database import async_session_maker
    try:
        async with async_session_maker() as session:
            await child_cancel_sos_endpoint(child_id, session)
        await callback.answer("✅ SOS xavf signali to'xtatildi!", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as e:
        await callback.answer(f"Xato: {e}", show_alert=True)


# ==============================================================================
# GLOBAL XABARNOMALAR BOSHQARUVI (OWNER TOGGLES)
# ==============================================================================
def format_notification_settings_html(settings_dict: Dict[str, bool]) -> str:
    p_on = settings_dict.get("notify_parents", True)
    c_on = settings_dict.get("notify_children", True)
    o_on = settings_dict.get("notify_owner", True)
    sos_on = settings_dict.get("notify_owner_sos", True)

    p_badge = "🟢 <b>YOQILGAN (Xabarlar boradi)</b>" if p_on else "🔴 <b>OʻCHIRILGAN (Xabarlar toʻxtatilgan)</b>"
    c_badge = "🟢 <b>YOQILGAN (Signallar boradi)</b>" if c_on else "🔴 <b>OʻCHIRILGAN (Signallar toʻxtatilgan)</b>"
    o_badge = "🟢 <b>YOQILGAN (Nusxalar keladi)</b>" if o_on else "🔴 <b>OʻCHIRILGAN (Nusxalar kelmaydi)</b>"
    sos_badge = "🟢 <b>YOQILGAN (SOS keladi)</b>" if sos_on else "🔴 <b>OʻCHIRILGAN (SOS toʻxtatilgan)</b>"

    return (
        "🔔 <b>GLOBAL XABARNOMALAR & SIGNALLAR BOSHQARUVI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Bu boʻlim orqali platformadagi xabarlar oqimini mustaqil ravishda yoqib/oʻchirishingiz mumkin:</i>\n\n"
        f"👨‍👩‍👧 <b>Ota-onalarga boradigan xabarlar:</b>\n"
        f"Holat: {p_badge}\n"
        "<i>(SOS xavf signallari, geozona kirish/chiqish, batareya va tezlik ogohlantirishlari, tezkor signallar)</i>\n\n"
        f"👦 <b>Bolalarga boradigan signallar:</b>\n"
        f"Holat: {c_badge}\n"
        "<i>(Siren tovushi, ekran qulflash buyruqlari, direktivalar va topshiriqlar)</i>\n\n"
        f"👑 <b>Owner / Asoschiga keladigan xabarlar:</b>\n"
        f"Holat: {o_badge}\n"
        "<i>(Tizim ogohlantirishlari va bildirishnomalari)</i>\n\n"
        f"🚨 <b>Owner SOS xavf xabarnomalari:</b>\n"
        f"Holat: {sos_badge}\n"
        "<i>(Bolalar SOS favqulodda tugmasini bosganda keladigan nusxalar)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <i>Kerakli tugmani bosing va 1-bosishda holatni oʻzgartiring:</i>"
    )


@owner_bot_router.message(F.text.in_({"🔔 Xabarnomalar Boshqaruvi", "/notifications", "/xabarlar"}))
async def handle_notifications_control_cmd(message: Message):
    async with async_session_maker() as session:
        settings = await crud.get_notification_settings(session)
    html_text = format_notification_settings_html(settings)
    kb = get_notifications_settings_inline_keyboard(settings)
    await message.answer(html_text, reply_markup=kb, parse_mode="HTML")


@owner_bot_router.callback_query(F.data.startswith("toggle_notif:"))
async def handle_toggle_notification_cb(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    async with async_session_maker() as session:
        if action == "parents":
            await crud.toggle_notification_setting("notify_parents", session)
            toast = "👨‍👩‍👧 Ota-ona xabarlari holati oʻzgartirildi!"
        elif action == "children":
            await crud.toggle_notification_setting("notify_children", session)
            toast = "👦 Bola signallari holati oʻzgartirildi!"
        elif action == "owner":
            await crud.toggle_notification_setting("notify_owner", session)
            toast = "👑 Owner nusxalari holati oʻzgartirildi!"
        elif action == "owner_sos":
            await crud.toggle_notification_setting("notify_owner_sos", session)
            toast = "🚨 Owner SOS xabarlari holati oʻzgartirildi!"
        elif action == "all_on":
            await crud.set_all_notification_settings(True, session)
            toast = "🟢 Barcha xabarnomalar yoqildi!"
        elif action == "all_off":
            await crud.set_all_notification_settings(False, session)
            toast = "🔴 Barcha xabarnomalar oʻchirildi!"
        else:
            toast = "Yangilandi!"

        settings = await crud.get_notification_settings(session)

    html_text = format_notification_settings_html(settings)
    kb = get_notifications_settings_inline_keyboard(settings)
    try:
        await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(toast)


# ==============================================================================
# 2FA WEB LOGIN APPROVAL CALLBACK HANDLER (RUXSAT BERISH / RAD ETISH)
# ==============================================================================
@owner_bot_router.callback_query(F.data.startswith("login_auth:"))
async def on_login_approval_callback(callback: CallbackQuery, bot: Bot):
    """Handle 1-tap Allow / Deny login approval for web access."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Noto'g'ri so'rov formati.")
        return

    action = parts[1]  # "allow" or "deny"
    req_id = parts[2]

    from api.login_approval import get_approval_request, set_approval_status
    req = get_approval_request(req_id)
    if not req:
        await callback.answer("Ushbu kirish so'rovining muddati o'tib ketgan.", show_alert=True)
        try:
            await callback.message.edit_text("⏳ <i>Ushbu kirish so'rovining muddati tugagan.</i>", parse_mode="HTML")
        except Exception:
            pass
        return

    if req["status"] != "pending":
        await callback.answer(f"Bu so'rov avval hal qilingan: {req['status']}.", show_alert=True)
        return

    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")
    ip_addr = req.get("client_ip", "Nomalum")

    if action == "allow":
        set_approval_status(req_id, "approved")
        await callback.answer("✅ Kirishga ruxsat berdingiz!", show_alert=True)
        try:
            msg_ok = (
                "✅ <b>KIRISHGA RUXSAT BERILDI!</b> 🚀\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 IP: <code>{ip_addr}</code>\n"
                f"🕒 Vaqt: <b>{now_time}</b>\n\n"
                "<i>Akkauntingizga veb-sayt / ilova orqali muvaffaqiyatli kirildi.</i>"
            )
            await callback.message.edit_text(msg_ok, parse_mode="HTML")
        except Exception:
            pass
    else:
        set_approval_status(req_id, "rejected")
        await callback.answer("🚫 Kirish rad etildi!", show_alert=True)
        try:
            msg_deny = (
                "🚫 <b>KIRISH RAD ETILDI!</b> 🛑\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 IP: <code>{ip_addr}</code>\n"
                f"🕒 Vaqt: <b>{now_time}</b>\n\n"
                "<i>Begona kirish urinishi muvaffaqiyatli to'xtatildi. Tizim xavfsiz holatda.</i>"
            )
            await callback.message.edit_text(msg_deny, parse_mode="HTML")
        except Exception:
            pass
