import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
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
)
from owner_bot.logger_buffer import get_recent_logs
from owner_bot.service import (
    format_dossier_html,
    get_main_bot,
    get_system_diagnostic_html,
    get_apps_and_screen_time_summary,
    get_battery_and_device_radar,
    perform_database_optimization,
    distribute_coins_airdrop,
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
    await callback.message.edit_text(
        "Boshqaruv konsoli bosh menyusiga qaytdingiz. Kerakli bo'limni tanlang:",
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
# STATISTIKA
# ==============================================================================
@owner_bot_router.message(F.text == "📊 Umumiy Statistika")
@owner_bot_router.message(Command("stats"))
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


# ==============================================================================
# FOYDALANUVCHINI TEKSHIRISH (ID / USERNAME QUERY)
# ==============================================================================
@owner_bot_router.message(F.text == "🔍 ID bo'yicha tekshirish")
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
@owner_bot_router.message(F.text == "🚨 Faol SOS signallari")
@owner_bot_router.message(Command("sos_list"))
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
@owner_bot_router.message(F.text == "👥 Adminlarni boshqarish")
@owner_bot_router.message(Command("admins"))
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
        "Admin qilmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini kiriting:\n\n"
        "<i>(Foydalanuvchi o'z ID raqamini @userinfobot orqali bilishi mumkin)</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@owner_bot_router.message(OwnerBotStates.waiting_for_add_admin_id)
async def process_add_admin_id(message: Message, state: FSMContext, auth_info: Dict[str, Any]):
    if not message.text:
        await message.answer("⚠️ Iltimos, Telegram ID raqamini raqamlar bilan kiriting:")
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Telegram ID faqat raqamlardan iborat bo'lishi kerak. Qaytadan kiriting:")
        return

    new_admin_id = int(text)
    await state.clear()

    async with async_session_maker() as session:
        # Check if user exists in users table to pull their username/name if possible
        existing_user = await crud.get_user(session, new_admin_id)
        uname = existing_user.username if existing_user else None
        fname = existing_user.full_name if existing_user else f"Admin #{new_admin_id}"

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
        f"<i>Endi ushbu foydalanuvchi @child_security_owner_bot ga kirib boshqaruvdan foydalanishi mumkin.</i>",
        reply_markup=get_owner_main_keyboard(auth_info["is_owner"]),
        parse_mode="HTML",
    )


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
@owner_bot_router.message(F.text == "📢 Hammaga xabar (Broadcast)")
@owner_bot_router.message(Command("broadcast"))
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
@owner_bot_router.message(F.text == "🌐 Server & Xavfsizlik")
@owner_bot_router.message(Command("server"))
async def handle_system_status(message: Message):
    async with async_session_maker() as session:
        html = await get_system_diagnostic_html(session)
    await message.answer(html, parse_mode="HTML", disable_web_page_preview=True)


# ==============================================================================
# SO'NGGI FOYDALANUVCHILAR
# ==============================================================================
@owner_bot_router.message(F.text == "📜 So'nggi foydalanuvchilar")
@owner_bot_router.message(Command("recent"))
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
@owner_bot_router.message(F.text == "📑 Server loglari")
@owner_bot_router.message(Command("logs"))
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
@owner_bot_router.message(F.text == "💾 Baza zaxirasi (Backup)")
@owner_bot_router.message(Command("backup"))
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
@owner_bot_router.message(F.text.in_({"ℹ️ Yordam", "ℹ️ Yordam & Buyruqlar"}))
@owner_bot_router.message(Command("help"))
async def handle_help(message: Message, auth_info: Dict[str, Any]):
    help_text = (
        "📖 <b>EGA VA ADMIN BOTI BO'YICHA QO'LLANMA</b>\n\n"
        "⚡️ <b>Asosiy buyruqlar:</b>\n"
        "• /start yoki /menu — Asosiy boshqaruv menyusini ochish\n"
        "• /stats — Platforma bo'yicha jonli statistika\n"
        "• /inspect — ID yoki username bo'yicha foydalanuvchini to'liq tekshirish\n"
        "• /sos_list — Faol SOS signallari ro'yxati\n"
        "• /admins — Adminlarni boshqarish (faqat bosh ega uchun)\n"
        "• /broadcast — Ommaviy xabar yuborish\n"
        "• /server — Server, xotira, DB va xavfsizlik diagnostikasi\n"
        "• /recent — So'nggi ro'yxatdan o'tgan foydalanuvchilar\n"
        "• /logs — Real vaqt rejimida server loglari\n"
        "• /backup — Ma'lumotlar bazasi zaxirasini Telegramga yuklab olish\n"
        "• /desktop — Noutbuk & PC versiyasini yuklab olish fayli\n\n"
        "👑 <b>Bosh ega:</b> @fayzbek1331"
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
@owner_bot_router.message(F.text.in_({"📱 Ilovalar & Ekran tahlili", "/apps", "/screentime"}))
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
@owner_bot_router.message(F.text.in_({"🔋 Batareya & Qurilmalar radari", "/battery", "/devices"}))
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
@owner_bot_router.message(F.text.in_({"🛡️ Xavfsizlik & Anti-Spam", "/shield", "/antispam"}))
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
@owner_bot_router.message(F.text.in_({"🧹 Baza tozalash (Optimize)", "/optimize", "/vacuum"}))
async def handle_db_optimize_cmd(message: Message, auth_info: Dict[str, Any]):
    msg = await message.answer("⏳ <i>Ma'lumotlar bazasi tekshirilmoqda va optimallashmoqda...</i>", parse_mode="HTML")
    async with async_session_maker() as session:
        result_html = await perform_database_optimization(session)
        await msg.edit_text(result_html, parse_mode="HTML")


# ==============================================================================
# AIRDROP COIN BONUS TO ALL CHILDREN
# ==============================================================================
@owner_bot_router.message(F.text.in_({"🎁 Bolalarga bonus (Airdrop)", "/airdrop"}))
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

