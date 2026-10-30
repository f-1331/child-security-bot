import html
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    FSInputFile,
    URLInputFile,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import AsyncSessionLocal
import db.crud as crud
from api import telemetry
from bot.geofence import evaluate_geofences_and_notify, haversine_distance

# Permanent Telegram CDN cached file_ids for zero-delay instant document delivery
TG_FILE_ID_ANDROID_APK = "BQACAgIAAxkDAAIDE2rePUqXXN1HRXu8WgF9IcJ7gkVQAAL-pAACkXzxSrQLyr5ZFkFMPQQ"
TG_FILE_ID_PARENT_APK = "BQACAgIAAxkDAAIC5mreMhe2YYW7kXQ3dRC_RDdx4BrAAAJYpAACkXzxSqCXbmUrcbZgPQQ"
TG_FILE_ID_CHILD_APK = "BQACAgIAAxkDAAIDAmreN2MZxN9GawxtrRnCVUZUj6aeAAKnpAACkXzxSpPrP6AaP55sPQQ"
TG_FILE_ID_DESKTOP_BAT = "BQACAgIAAxkDAAIDD2reO4j6pMRtUikOHywnoJBAqI_tAALapAACkXzxSg5fFfSpQJnhPQQ"
TG_FILE_ID_WINDOWS_ZIP = "BQACAgIAAxkDAAIDEGreO4kfHj7Rz_wPD38RUdD_3oolAALbpAACkXzxSjkBVlQWAAEfFz0E"
TG_FILE_ID_MAC_ZIP = "BQACAgIAAxkDAAIDEmreO4p4Hu6hwtcAAVviC3MTGQ9ZMQAC3aQAApF88Up6toUOMmeOVj0E"
TG_FILE_ID_LINUX_ZIP = "BQACAgIAAxkDAAIDEWreO4n4CJalT9Nbfchlk2quzCIdAALcpAACkXzxSnpUS1UaDiU9PQQ"
TG_FILE_ID_IOS_PROFILE = "BQACAgIAAxkDAAIDBGreN2RP4HQZPqZFSP0xiRIiSYxhAAKqpAACkXzxSk1ziz3geGp_PQQ"

# Local Tashkent timezone (UTC+5) for accurate timestamps and freshness badges
TASHKENT_TZ = timezone(timedelta(hours=5))


def format_tashkent_time(dt: Optional[datetime], include_date: bool = False, include_freshness: bool = True) -> str:
    """Formats a UTC timestamp from DB to Tashkent local time (UTC+5) with relative freshness badge."""
    if not dt:
        return "Hali yangilanmagan"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(TASHKENT_TZ)
    now_local = datetime.now(TASHKENT_TZ)

    fmt = "%d.%m.%Y, %H:%M:%S" if include_date else "%H:%M:%S"
    time_str = local_dt.strftime(fmt)

    if not include_freshness:
        return time_str

    diff_sec = max(0, int((now_local - local_dt).total_seconds()))
    if diff_sec < 45:
        badge = "🟢 Hozirgina (Jonli)"
    elif diff_sec < 120:
        badge = f"🟢 {diff_sec} soniya oldin"
    elif diff_sec < 3600:
        badge = f"🟡 {diff_sec // 60} daqiqa oldin"
    elif diff_sec < 86400:
        badge = f"⚪ {diff_sec // 3600} soat oldin"
    else:
        badge = f"🔴 {diff_sec // 86400} kun oldin"

    return f"{time_str} ({badge})"

from bot.keyboards import (
    get_role_selection_keyboard,
    get_parent_main_keyboard,
    get_child_main_keyboard,
    get_contact_request_keyboard,
    get_pairing_share_keyboard,
    get_child_details_keyboard,
    get_location_actions_keyboard,
    get_child_sos_call_keyboard,
    get_sos_alert_parent_keyboard,
    get_task_approval_keyboard,
    get_child_task_action_keyboard,
    get_task_schedule_options_keyboard,
    get_kerakli_joylar_menu_keyboard,
    get_radius_selection_keyboard,
    get_settings_keyboard,
    get_child_role_switch_request_keyboard,
    get_parent_role_switch_approval_keyboard,
    get_logout_step1_keyboard,
    get_logout_step2_keyboard,
    get_coin_rate_preset_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


# FSM States
class ChildLinkState(StatesGroup):
    waiting_for_code = State()


class CoinRateState(StatesGroup):
    waiting_for_custom_rate = State()


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
    waiting_for_media = State()


class TaskProofState(StatesGroup):
    waiting_for_photo = State()


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
        await session.commit()

        # Check deep-link pairing code
        args = command.args
        if args:
            # 1. Child invited Parent: /start link_child_12345 or /start child_12345
            if args.startswith("link_child_") or args.startswith("child_"):
                try:
                    clean_arg = args.replace("link_child_", "").replace("child_", "").strip()
                    if clean_arg == "demo" or clean_arg == "2002":
                        await message.answer(
                            f"⚠️ **Diqqat:** Siz sinov (demo) havolasi orqali kirdingiz!\n\n"
                            f"Haqiqiy farzandingiz hisobini ulash uchun, farzandingiz botimizga (@{settings.BOT_USERNAME}) "
                            f"kirib, o'zining haqiqiy havolasini sizga yuborishi yoki 6 xonali kodini aytishi kerak.",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="Markdown"
                        )
                        return

                    target_child_id = int(clean_arg)

                    # Agar bola o'zining havolasini o'zi bosgan bo'lsa
                    if target_child_id == user_id:
                        await message.answer(
                            "ℹ️ **Bu sizning shaxsiy ulanish havolangiz!**\n\n"
                            "Ota-onangiz sizning xavfsizligingizni kuzatib borishi va topshiriqlar bera olishi uchun "
                            "ushbu havolani **ota-onangizga yuboring**.",
                            reply_markup=get_child_main_keyboard(),
                            parse_mode="Markdown"
                        )
                        return

                    child_user = await crud.get_user(session, target_child_id)
                    if not child_user:
                        child_user, _ = await crud.get_or_create_user(session, target_child_id, role="child")

                    if child_user:
                        parent_code = await crud.generate_pairing_code(session, user_id)

                        # Farzand allaqachon hisobga ulanganligini tekshirish (qayta-qayta ulandi xabarlarining oldini olish)
                        existing_link = await crud.get_parent_child_link(session, user_id, target_child_id)
                        if existing_link:
                            await session.commit()
                            await message.answer(
                                f"ℹ️ **{child_user.full_name or 'Farzandingiz'} allaqachon hisobingizga ulangan!**\n\n"
                                f"👦 Farzand: **{child_user.full_name or 'Farzandingiz'}**\n"
                                f"🔑 **Sizning oilaviy sayt kodingiz:** ` {parent_code} `\n"
                                f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                                f"Qaytadan ulash shart emas. Farzandingiz joylashuvini pastdagi **«📍 Farzandning hozirgi joylashuvi»** tugmasi orqali ko'rishingiz mumkin.",
                                reply_markup=get_parent_main_keyboard(user_id),
                                parse_mode="Markdown"
                            )
                            return

                        await crud.update_user_role(session, user_id, "parent")
                        link, is_new = await crud.link_parent_child(session, user_id, target_child_id, nickname=child_user.full_name)
                        await session.commit()

                        if is_new:
                            await message.answer(
                                f"🎉 **Tabriklaymiz! Farzandingiz muvaffaqiyatli ulandi!**\n\n"
                                f"👦 Farzand: **{child_user.full_name or 'Farzandingiz'}**\n"
                                f"🔑 **Sizning oilaviy sayt kodingiz:** ` {parent_code} `\n"
                                f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                                f"Endi farzandingizning joriy joylashuvini bir tugma bilan ko'ra olasiz.",
                                reply_markup=get_parent_main_keyboard(user_id),
                                parse_mode="Markdown"
                            )

                            if not user.phone_number:
                                await message.answer(
                                    "⚠️ **Eslatma:** Farzandingiz SOS tugmasini bosganida sizga avtomatik qo'ng'iroq ketishi uchun "
                                    "istalgan paytda pastdagi **«📞 Oila kontaktlari»** bo'limi orqali telefon raqamingizni kiritib qo'yishingiz mumkin.",
                                    parse_mode="Markdown"
                                )

                            try:
                                await bot.send_message(
                                    chat_id=target_child_id,
                                    text=(
                                        f"🎉 **Ajoyib yangilik!**\n\n"
                                        f"Ota-onangiz (**{full_name}**) taklifingiz orqali hisobingizga muvaffaqiyatli ulandi! 🛡️"
                                    ),
                                    reply_markup=get_child_main_keyboard(),
                                    parse_mode="Markdown"
                                )
                            except Exception as e:
                                logger.error(f"Failed to notify child about parent link: {e}")
                        return
                    else:
                        await message.answer(
                            "❌ **Ulanishda xatolik:** Taklif qilingan farzand hisobi topilmadi.\n\n"
                            "Iltimos, farzandingizdan yangi havolani so'rang yoki uning 6 xonali ulanish kodini kiriting.",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="Markdown"
                        )
                        return
                except Exception as e:
                    logger.error(f"Failed to process child invite deep link: {e}")

            # 2. Mother invited Father (Co-parent invitation): /start coparent_12345 or /start parent_12345
            elif args.startswith("coparent_") or args.startswith("parent_"):
                try:
                    primary_parent_id = int(args.replace("coparent_", "").replace("parent_", "").strip())
                    if primary_parent_id == user_id:
                        await message.answer(
                            "ℹ️ **Bu sizning ikkinchi ota-onani taklif qilish havolangiz!**\n\n"
                            "Turmush o'rtog'ingiz (dada yoki oyi) ham bolalarni birga kuzatishi uchun ushbu havolani unga yuboring.",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="Markdown"
                        )
                        return

                    primary_parent = await crud.get_user(session, primary_parent_id)
                    co_parent_code = await crud.generate_pairing_code(session, user_id)
                    existing_children = await crud.get_children_for_parent(session, primary_parent_id)

                    # Qayta bosilganda qayta-qayta ulandi xabari bormasligi uchun tekshirish
                    current_children_ids = {c.id for c in await crud.get_children_for_parent(session, user_id)}
                    primary_children_ids = {c.id for c in existing_children}

                    if primary_children_ids and primary_children_ids.issubset(current_children_ids):
                        child_names = ", ".join([c.full_name or f"Farzand #{c.id}" for c in existing_children]) or "barcha farzandlar"
                        await message.answer(
                            f"ℹ️ **Siz allaqachon ushbu oila guruhiga ulangansiz!**\n\n"
                            f"👤 Hamkor ota-ona: **{primary_parent.full_name if primary_parent else 'Ota-ona'}**\n"
                            f"👶 Biriktirilgan farzandlar: **{child_names}**\n\n"
                            f"🔑 **Sizning shaxsiy sayt kodingiz:** ` {co_parent_code} `\n"
                            f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="Markdown"
                        )
                        return

                    linked_children = await crud.link_co_parent(session, primary_parent_id, user_id)
                    await session.commit()

                    child_names = ", ".join([c.full_name or f"Farzand #{c.id}" for c in linked_children]) or "barcha farzandlar"

                    await message.answer(
                        f"🎉 **Ajoyib! Siz oilaviy monitoring guruhiga muvaffaqiyatli qo'shildingiz!**\n\n"
                        f"👤 Hamkor ota-ona: **{primary_parent.full_name if primary_parent else 'Ota-ona'}**\n"
                        f"👶 Biriktirilgan farzandlar ({len(linked_children)} nafar): **{child_names}**\n\n"
                        f"🔑 **Sizning shaxsiy sayt kodingiz:** ` {co_parent_code} `\n"
                        f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                        f"Endi siz ham barcha farzandlaringiz joylashuvini birgalikda kuzatishingiz va topshiriqlar berishingiz mumkin.",
                        reply_markup=get_parent_main_keyboard(user_id),
                        parse_mode="Markdown"
                    )

                    if not user.phone_number:
                        await message.answer(
                            "⚠️ **Eslatma:** Farzandingiz SOS tugmasini bosganida sizga avtomatik qo'ng'iroq ketishi uchun "
                            "istalgan paytda pastdagi **«📞 Oila kontaktlari»** bo'limi orqali telefon raqamingizni kiritib qo'yishingiz mumkin.",
                            parse_mode="Markdown"
                        )

                    try:
                        await bot.send_message(
                            chat_id=primary_parent_id,
                            text=(
                                f"🎉 **Oilangizga ikkinchi ota-ona qo'shildi!**\n\n"
                                f"👤 Hamkor: **{full_name}**\n\n"
                                f"Endi ikkalangiz ham farzandlaringiz xavfsizligini birgalikda kuzatishingiz mumkin. 🛡️"
                            ),
                            reply_markup=get_parent_main_keyboard(primary_parent_id),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify primary parent: {e}")
                    return
                except Exception as e:
                    logger.error(f"Failed to process coparent invite: {e}")

            elif args.startswith("pair_") or (args.strip().isdigit() and len(args.strip()) == 6):
                pairing_code = args.replace("pair_", "").strip()
                parent = await crud.get_parent_by_pairing_code(session, pairing_code)
                if parent:
                    # O'zining kodi orqali o'ziga ulanishga urinishdan himoya
                    if parent.id == user_id:
                        await message.answer(
                            "ℹ️ **Bu sizning shaxsiy oilaviy kodingiz!**\n\n"
                            "Ushbu kodni farzandingiz telefonida kiritishingiz kerak, shunda u hisobingizga ulanadi.",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="Markdown"
                        )
                        return

                    child_web_code = await crud.generate_web_access_code(session, user_id)

                    # Farzand allaqachon ushbu ota-onaga ulangan bo'lsa, qayta ulandi deb bezovta qilmaslik
                    existing_link = await crud.get_parent_child_link(session, parent.id, user_id)
                    if existing_link:
                        safe_parent_name = html.escape(parent.full_name or "Ota-onangiz")
                        await message.answer(
                            f"ℹ️ <b>Siz allaqachon {safe_parent_name} hisobiga ulangansiz!</b>\n\n"
                            f"🔑 <b>Saytga (Web App) kirish kodingiz:</b> <code>{child_web_code}</code>\n"
                            f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                            f"Quyidagi xavfsizlik menyusidan foydalanishingiz mumkin:",
                            reply_markup=get_child_main_keyboard(),
                            parse_mode="HTML"
                        )
                        return

                    user.role = "child"
                    user.full_name = full_name
                    user.username = username
                    await crud.update_user_role(session, user_id, "child")
                    link, is_new = await crud.link_parent_child(session, parent.id, user_id, nickname=full_name)
                    await session.commit()

                    if is_new:
                        # Notify child with safe HTML
                        safe_parent_name = html.escape(parent.full_name or "Ota-onangiz")
                        await message.answer(
                            f"🎉 <b>Tabriklaymiz!</b>\n\n"
                            f"Siz <b>{safe_parent_name}</b> hisobiga muvaffaqiyatli ulandingiz.\n\n"
                            f"🔑 <b>Saytga (Web App) kirish kodingiz:</b> <code>{child_web_code}</code>\n"
                            f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                            f"<i>(Saytga kirganda ushbu 6 xonali shaxsiy kodingizni kiriting)</i>\n\n"
                            f"Quyidagi xavfsizlik menyusidan foydalanishingiz mumkin:",
                            reply_markup=get_child_main_keyboard(),
                            parse_mode="HTML"
                        )

                        # Notify parent with requested format:
                        safe_child_name = html.escape(full_name or "Farzand")
                        safe_child_username = f"@{html.escape(username)}" if username else "Mavjud emas"

                        parent_notify_html = (
                            f"🎉 <b>Yangi farzand muvaffaqiyatli ulandi!</b>\n\n"
                            f"Siz quyidagi farzandingizni hisobingizga uladingiz:\n"
                            f"🔹 <b>Foydalanuvchi nomi:</b> {safe_child_username}\n"
                            f"🔹 <b>Ismi (Nick):</b> {safe_child_name}\n"
                            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
                            f"Endi farzandingizning joylashuvini bir bosishda ko'rishingiz mumkin. 🛡️"
                        )

                        try:
                            await bot.send_message(
                                chat_id=parent.id,
                                text=parent_notify_html,
                                reply_markup=get_parent_main_keyboard(parent.id),
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"HTML send failed, fallback to plain text: {e}")
                            raw_parent_notify = (
                                f"🎉 Yangi farzand muvaffaqiyatli ulandi!\n\n"
                                f"Siz quyidagi farzandingizni hisobingizga uladingiz:\n"
                                f"🔹 Foydalanuvchi nomi: {f'@{username}' if username else 'Mavjud emas'}\n"
                                f"🔹 Ismi (Nick): {full_name or 'Farzand'}\n"
                                f"🆔 ID: {user_id}\n\n"
                                f"Endi farzandingizning joylashuvini bir bosishda ko'rishingiz mumkin. 🛡️"
                            )
                            await bot.send_message(
                                chat_id=parent.id,
                                text=raw_parent_notify,
                                reply_markup=get_parent_main_keyboard(parent.id)
                            )

                        # Also notify WebApp parent dashboard in real-time
                        try:
                            from api.webapp_api import ws_manager
                            await ws_manager.send_to_user(parent.id, {
                                "type": "child_linked",
                                "child_id": user_id,
                                "full_name": full_name,
                                "username": username
                            })
                        except Exception:
                            pass
                    return
                else:
                    await message.answer(
                        "❌ <b>Ulanish kodi noto'g'ri yoki eskirgan!</b>\n\n"
                        "Iltimos, ota-onangiz bergan 6 xonali ulanish kodini qayta tekshirib kiring:",
                        reply_markup=get_role_selection_keyboard(),
                        parse_mode="HTML"
                    )
                    return

        # If user is already assigned a role
        if user.role == "parent":
            children = await crud.get_children_for_parent(session, user.id)
            await state.clear()

            phone_prompt = ""
            if not user.phone_number:
                phone_prompt = (
                    "\n\n⚠️ **Eslatma:** SOS xavf tugmasi bosilganda sizga avtomatik qo'ng'iroq ketishi uchun "
                    "pastdagi **«📞 Oila kontaktlari»** bo'limi orqali telefon raqamingizni ulab qo'yishingiz mumkin."
                )

            start_inline_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👨‍👩‍👧 Ota-ona (.apk)",
                            callback_data="download_parent_apk"
                        ),
                        InlineKeyboardButton(
                            text="👦 Farzand (.apk)",
                            callback_data="download_child_apk"
                        ),
                        InlineKeyboardButton(
                            text="💻 Noutbuk",
                            callback_data="download_desktop"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🌐 Web App (Sayt)ni ochish",
                            web_app=WebAppInfo(url=settings.MINI_APP_URL)
                        )
                    ]
                ]
            )
            await message.answer(
                f"Assalomu alaykum, **{full_name}**!\n\n"
                f"👨‍👩‍👧 Siz **Ota-ona** rejimidasiz.\n"
                f"Ulangan farzandlar soni: **{len(children)} nafar**.\n\n"
                f"📍 Farzandingiz qayerdaligini bilish uchun pastdagi **«📍 Farzandning hozirgi joylashuvi»** tugmasini bosing.\n\n"
                f"📲 **Mobil ilovalar:**\n"
                f"• **👨‍👩‍👧 Ota-ona ilovasi:** O'z telefoningizdan farzandlarni jonli xaritada kuzatish, topshiriq berish va hisobotlarni ko'rish uchun.\n"
                f"• **👦 Farzand ilovasi:** Farzand telefonida ekran o'chiq bo'lsa ham 24/7 foniy GPS va SOS ishlashi uchun.{phone_prompt}",
                reply_markup=get_parent_main_keyboard(user.id),
                parse_mode="Markdown"
            )
            await message.answer(
                "⚡ **Tezkor kirish va Mobil ilova:**",
                reply_markup=start_inline_kb,
                parse_mode="Markdown"
            )
            return

        elif user.role == "child":
            parents = await crud.get_parents_for_child(session, user.id)
            parent_names = ", ".join([p.full_name or str(p.id) for p in parents]) if parents else "Ulangan ota-ona yo'q"
            child_inline_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👦 Farzand ilovasi (.apk)",
                            callback_data="download_child_apk"
                        ),
                        InlineKeyboardButton(
                            text="💻 Noutbuk dasturi",
                            callback_data="download_desktop"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🌐 Web App (Sayt)ni ochish",
                            web_app=WebAppInfo(url=settings.MINI_APP_URL)
                        )
                    ]
                ]
            )
            await message.answer(
                f"Salom, **{full_name}**!\n\n"
                f"👦 Siz **Farzand** rejimidasiz.\n"
                f"Ulangan ota-ona: **{parent_names}**.\n\n"
                f"Har qanday favqulodda vaziyatda darhol **'🚨 SOS'** tugmasini bosing!\n\n"
                f"📲 **Child Security Android APK** ilovasini o'rnatish uchun pastdagi **«📲 Android APK Yuklab Olish»** tugmasini bosing.",
                reply_markup=get_child_main_keyboard(),
                parse_mode="Markdown"
            )
            await message.answer(
                "⚡ **Tezkor kirish va Mobil ilova:**",
                reply_markup=child_inline_kb,
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
            web_code = await crud.generate_web_access_code(session, user_id)
            await session.commit()

            bot_info = await bot.get_me()
            await callback.message.delete()
            await callback.message.answer(
                f"👨‍👩‍👧 **Ota-ona hisobi faollashtirildi!**\n\n"
                f"1️⃣ **Farzandingizni ulash uchun** unga quyidagi 6 xonali ulanish kodini bering:\n"
                f"🔑 Ulanish kodi: ` {code} `\n\n"
                f"2️⃣ **Sayt (Web App) boshqaruv kodingiz:**\n"
                f"💻 Saytga kirish kodi: ` {web_code} `\n"
                f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n"
                f"*(Saytga kirganda ushbu 6 xonali kodni kiriting)*\n\n"
                f"Yoki pastdagi tugma orqali to'g'ridan-to'g'ri havolani farzandingizga yuboring:",
                reply_markup=get_pairing_share_keyboard(bot_info.username, code),
                parse_mode="Markdown"
            )
            await state.clear()
            role_inline = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📥 Ilovani yuklab olish (.apk)",
                            callback_data="download_apk_direct"
                        )
                    ]
                ]
            )
            await callback.message.answer(
                f"📲 **Farzandingiz telefoniga ilovani o'rnatish:**\n"
                f"Pastdagi **«📥 Ilovani yuklab olish»** menyu tugmasini bosing yoki quyidagi tugma orqali to'g'ridan-to'g'ri yuklab oling:",
                reply_markup=get_parent_main_keyboard(user_id),
                parse_mode="Markdown"
            )
            await callback.message.answer(
                "⚡ **Tezkor yuklab olish tugmasi:**",
                reply_markup=role_inline,
                parse_mode="Markdown"
            )

        elif role == "child":
            await session.commit()
            await callback.message.delete()
            await state.set_state(ChildLinkState.waiting_for_code)
            await callback.message.answer(
                "👦 **Farzand hisobi tanlandi!**\n\n"
                "1️⃣ Ota-onangiz bergan **6 xonali ulanish kodi**ni kiriting (masalan: `123456`)\n"
                "2️⃣ Yoki pastdagi **«📍 Jonli joylashuvimni yuborish»** tugmasi orqali joylashuvingizni ulashing!\n\n"
                "*(Rolni almashtirish uchun istalgan payt /chiqish deb yozing)*",
                reply_markup=get_child_main_keyboard(),
                parse_mode="Markdown"
            )

    await callback.answer()


@router.message(ChildLinkState.waiting_for_code)
async def on_child_code_entered(message: Message, state: FSMContext, bot: Bot):
    """Process pairing code entered manually by child."""
    if not message.text:
        await message.answer("⚠️ Iltimos, ulanish kodini matn ko'rinishida yuboring (masalan: `123456`):")
        return

    code = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    username = message.from_user.username

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

        # O'zining kodi orqali o'ziga ulanishga urinishdan himoya
        if parent.id == user_id:
            await state.clear()
            await message.answer(
                "ℹ️ **Bu sizning shaxsiy oilaviy kodingiz!**\n\n"
                "Ushbu kodni farzandingiz telefonida kiritishingiz kerak, shunda u hisobingizga ulanadi.",
                reply_markup=get_parent_main_keyboard(user_id),
                parse_mode="Markdown"
            )
            return

        child_web_code = await crud.generate_web_access_code(session, user_id)

        # Farzand allaqachon ushbu ota-onaga ulangan bo'lsa, qayta ulandi deb bezovta qilmaslik
        existing_link = await crud.get_parent_child_link(session, parent.id, user_id)
        if existing_link:
            await state.clear()
            safe_parent_name = html.escape(parent.full_name or "Ota-onangiz")
            await message.answer(
                f"ℹ️ <b>Siz allaqachon {safe_parent_name} hisobiga ulangansiz!</b>\n\n"
                f"🔑 <b>Saytga kirish kodingiz:</b> <code>{child_web_code}</code>\n"
                f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                f"Quyidagi xavfsizlik menyusi faol:",
                reply_markup=get_child_main_keyboard(),
                parse_mode="HTML"
            )
            return

        # Ensure child user role & profile in DB
        child_user = await crud.get_user(session, user_id)
        if child_user:
            child_user.role = "child"
            child_user.full_name = full_name
            child_user.username = username
        await crud.update_user_role(session, user_id, "child")
        link, is_new = await crud.link_parent_child(session, parent.id, user_id, nickname=full_name)
        await session.commit()
        await state.clear()

        safe_parent_name = html.escape(parent.full_name or "Ota-onangiz")
        await message.answer(
            f"🎉 <b>Ajoyib! Ota-onangizga ({safe_parent_name}) muvaffaqiyatli ulandingiz!</b>\n\n"
            f"🔑 <b>Sizning shaxsiy sayt kodingiz:</b> <code>{child_web_code}</code>\n"
            f"🌐 <b>Sayt manzili:</b> {settings.WEBAPP_BASE_URL}\n\n"
            f"<i>(Saytga ushbu o'zingizning shaxsiy kodingiz bilan kirsangiz, sizning topshiriqlaringiz, xazinangiz va sarguzasht profilingiz ochiladi)</i>\n\n"
            f"Quyidagi xavfsizlik menyusi faollashtirildi:",
            reply_markup=get_child_main_keyboard(),
            parse_mode="HTML"
        )

        # Notify parent with requested format:
        safe_child_name = html.escape(full_name or "Farzand")
        safe_child_username = f"@{html.escape(username)}" if username else "Mavjud emas"

        parent_notify_text = (
            f"🎉 <b>Yangi farzand muvaffaqiyatli ulandi!</b>\n\n"
            f"Siz quyidagi farzandingizni hisobingizga uladingiz:\n"
            f"🔹 <b>Foydalanuvchi nomi:</b> {safe_child_username}\n"
            f"🔹 <b>Ismi (Nick):</b> {safe_child_name}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
            f"Endi farzandingizning joriy joylashuvini bir tugma bilan ko'ra olasiz. 🛡️"
        )

        try:
            await bot.send_message(
                chat_id=parent.id,
                text=parent_notify_text,
                reply_markup=get_parent_main_keyboard(parent.id),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"HTML send failed, fallback plain text to parent: {e}")
            raw_text = (
                f"🎉 Yangi farzand muvaffaqiyatli ulandi!\n\n"
                f"Siz quyidagi farzandingizni hisobingizga uladingiz:\n"
                f"🔹 Foydalanuvchi nomi: {f'@{username}' if username else 'Mavjud emas'}\n"
                f"🔹 Ismi (Nick): {full_name or 'Farzand'}\n"
                f"🆔 ID: {user_id}\n\n"
                f"Endi farzandingizning joriy joylashuvini bir tugma bilan ko'ra olasiz. 🛡️"
            )
            await bot.send_message(
                chat_id=parent.id,
                text=raw_text,
                reply_markup=get_parent_main_keyboard(parent.id)
            )

        # Also broadcast to WebApp parent dashboard in real-time
        try:
            from api.webapp_api import ws_manager
            await ws_manager.send_to_user(parent.id, {
                "type": "child_linked",
                "child_id": user_id,
                "full_name": full_name,
                "username": username
            })
        except Exception:
            pass


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
    # Trigger real-time wake up alert to child on Mobile App / WebApp
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child.id, {
            "type": "active_check",
            "time": datetime.now(TASHKENT_TZ).strftime("%H:%M")
        })
    except Exception:
        pass

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

        safe_child_name = html.escape(child.full_name or "Farzand")
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"📍 <b>{safe_child_name}</b> hali birorta ham joylashuv yubormagan.\n\n"
                f"Farzandingiz telefoniga joylashuvni yangilash haqida eslatma yuborildi."
            ),
            parse_mode="HTML"
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

    safe_zone = html.escape(current_zone) if current_zone else None
    if safe_zone:
        zone_str = f"✅ <b>'{safe_zone}'</b> hududida (markazdan {int(min_dist)}m)"
    else:
        zone_str = "📍 Kerakli joylar chegarasidan tashqarida (yo'lda)"

    battery_str = f"{latest_loc.battery_level}%" if latest_loc.battery_level is not None else "Noma'lum"
    charging_icon = "⚡ (Quvvatlanmoqda)" if latest_loc.is_charging else ""
    time_str = format_tashkent_time(latest_loc.recorded_at) if latest_loc and latest_loc.recorded_at else "Hozirgina"

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
    safe_child_name = html.escape(child.full_name or "Farzand")
    card_text = (
        f"👤 Farzand: <b>{safe_child_name}</b>\n"
        f"🕒 Oxirgi signal: <b>{time_str}</b>\n"
        f"🔋 Batareya: <b>{battery_str}</b> {charging_icon}\n"
        f"📍 Joylashuv: {zone_str}\n"
        f"📌 Koordinata: <code>{latest_loc.latitude:.5f}, {latest_loc.longitude:.5f}</code>"
    )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=card_text,
            reply_markup=get_location_actions_keyboard(latest_loc.latitude, latest_loc.longitude, child.id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"HTML send failed for location card: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"👤 Farzand: {child.full_name or 'Farzand'}\n"
                f"🕒 Oxirgi signal: {time_str}\n"
                f"🔋 Batareya: {battery_str} {charging_icon}\n"
                f"📍 Joylashuv: {zone_str}\n"
                f"📌 Koordinata: {latest_loc.latitude:.5f}, {latest_loc.longitude:.5f}"
            ),
            reply_markup=get_location_actions_keyboard(latest_loc.latitude, latest_loc.longitude, child.id)
        )

    # Ping child's bot that parent saw location
    try:
        await bot.send_message(
            chat_id=child.id,
            text="🛡️ **Ota-onangiz joylashuvingizni tekshirdi.**\n\nJoylashuvingiz va xavfsizligingiz avtomatik himoyada.",
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
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Iltimos, joy nomini matn ko'rinishida kiriting (masalan: _'Maktab'_, _'Uyimiz'_):", parse_mode="Markdown")
        return

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


@router.message(KerakliJoyAddState.waiting_for_location)
async def on_kerakli_joy_location_fallback(message: Message):
    await message.answer(
        "📍 **Iltimos, geolokatsiya yuboring!**\n\n"
        "Buning uchun Telegram'dagi qisqich (📎) tugmasini bosing va **Joylashuv (Location)** ni tanlab yuboring.",
        parse_mode="Markdown"
    )


@router.callback_query(KerakliJoyAddState.waiting_for_radius, F.data.startswith("radius:"))
async def on_kerakli_joy_radius_selected(callback: CallbackQuery, state: FSMContext):
    radius = float(callback.data.split(":")[1])
    await _save_custom_geofence(callback.message, state, callback.from_user.id, radius, is_callback=True)
    await callback.answer("Saqlandi!")


@router.message(KerakliJoyAddState.waiting_for_radius, F.text)
async def on_kerakli_joy_custom_radius_entered(message: Message, state: FSMContext):
    """Parent types custom radius distance in meters (e.g. 150 or 75m)."""
    raw_text = message.text.strip().lower().replace("metr", "").replace("m", "").replace(" ", "")
    try:
        radius = float(raw_text)
        if radius < 30 or radius > 5000:
            await message.answer("⚠️ Masofa radiusi kamida 30 metr va ko'pi bilan 5,000 metr (5 km) bo'lishi kerak. Iltimos, qayta kiriting:")
            return
        await _save_custom_geofence(message, state, message.from_user.id, radius, is_callback=False)
    except ValueError:
        await message.answer(
            "⚠️ Iltimos, masofani son ko'rinishida kiriting (masalan: `150`) yoki quyidagi tugmalardan birini tanlang:",
            reply_markup=get_radius_selection_keyboard(),
            parse_mode="Markdown"
        )


async def _save_custom_geofence(event_target: Message, state: FSMContext, parent_id: int, radius: float, is_callback: bool = False):
    data = await state.get_data()
    name = data.get("name", "Xavfsiz joy")
    lat = data.get("lat")
    lon = data.get("lon")
    target_child_id = data.get("target_child_id")

    async with AsyncSessionLocal() as session:
        await crud.create_geofence(
            session=session,
            parent_id=parent_id,
            name=name,
            latitude=lat,
            longitude=lon,
            radius_meters=radius,
            child_id=target_child_id
        )
        await session.commit()

    await state.clear()
    success_text = (
        f"✅ **Yangi kerakli joy muvaffaqiyatli saqlandi!**\n\n"
        f"📍 Nomi: **{name}**\n"
        f"📏 Radiusi: **{int(radius)} metr**\n"
        f"🗺 Koordinatalar: `{lat:.5f}, {lon:.5f}`\n\n"
        f"Farzandingiz bu joyga kirganda yoki chiqqanda sizga avtomatik bildirishnoma boradi."
    )
    if is_callback:
        try:
            await event_target.edit_text(success_text, parse_mode="Markdown")
        except Exception:
            await event_target.answer(success_text, parse_mode="Markdown")
    else:
        await event_target.answer(success_text, parse_mode="Markdown")


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


@router.callback_query(F.data.startswith("view_fence:"))
async def on_view_fence(callback: CallbackQuery):
    fence_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        fence = await crud.get_geofence(session, fence_id)
        if not fence:
            await callback.answer("⚠️ Kerakli joy topilmadi.", show_alert=True)
            return

        maps_link = f"https://www.google.com/maps/search/?api=1&query={fence.latitude},{fence.longitude}"
        text = (
            f"📍 **Kerakli joy:** **{fence.name}**\n\n"
            f"🎯 Radiusi: **{int(fence.radius_meters)} metr**\n"
            f"🗺 Koordinatalar: `{fence.latitude}, {fence.longitude}`\n"
            f"🌐 [Google Maps orqali ochish]({maps_link})\n\n"
            f"Farzandingiz ushbu hududga kirganda yoki undan chiqqanda sizga darhol xabarnoma yuboriladi."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗺 Xaritada ko'rish", url=maps_link)],
                [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"delete_fence:{fence.id}")],
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_fences")]
            ]
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "back_to_fences")
async def on_back_to_fences(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        fences = await crud.get_geofences_for_user(session, user_id)
        text = "📍 **Kerakli joylar (Xavfsiz hududlar):**\n\n"
        if not fences:
            text += "Hozircha hech qanday joy belgilanmagan.\n\nYangi joy qo'shish uchun pastdagi tugmani bosing:"
        else:
            text += "Belgilangan hududlar ro'yxati:"
        try:
            await callback.message.edit_text(text, reply_markup=get_kerakli_joylar_menu_keyboard(fences), parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, reply_markup=get_kerakli_joylar_menu_keyboard(fences), parse_mode="Markdown")
    await callback.answer()


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

        # Ping online children devices over WebSocket to push freshest telemetry immediately
        try:
            from api.webapp_api import ws_manager
            for ch in children:
                await ws_manager.send_to_user(ch.id, {
                    "type": "active_check",
                    "time": datetime.now(TASHKENT_TZ).strftime("%H:%M")
                })
        except Exception:
            pass

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

                time_str = format_tashkent_time(latest.recorded_at)

                text += (
                    f"👤 **{ch.full_name}**\n"
                    f"• Batareya: **{lvl}%** ({status_emoji})\n"
                    f"• Holat: {charging_icon}\n"
                    f"• Ovoz rejimi: **{sound_mode}**\n"
                    f"• Oxirgi signal: **{time_str}**\n\n"
                )
            else:
                text += f"👤 **{ch.full_name}** — Telemetriya ma'lumotlari hali olinmagan\n\n"

        refresh_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash (Signal yuborish)", callback_data="refresh_battery_status")]
        ])
        await message.answer(text, reply_markup=refresh_kb, parse_mode="Markdown")


@router.callback_query(F.data == "refresh_battery_status")
async def on_refresh_battery_callback(callback: CallbackQuery):
    """Parent clicks refresh to ping child and update telemetry card in-place."""
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await callback.answer("Sizga hali farzand ulanmagan.", show_alert=True)
            return

        # Ping connected children over WebSocket
        try:
            from api.webapp_api import ws_manager
            for ch in children:
                await ws_manager.send_to_user(ch.id, {
                    "type": "active_check",
                    "time": datetime.now(TASHKENT_TZ).strftime("%H:%M")
                })
            await asyncio.sleep(0.3)
        except Exception:
            pass

        text = "🔋 **Farzandlar telefonining holati (Yangilandi):**\n\n"

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

                time_str = format_tashkent_time(latest.recorded_at)

                text += (
                    f"👤 **{ch.full_name}**\n"
                    f"• Batareya: **{lvl}%** ({status_emoji})\n"
                    f"• Holat: {charging_icon}\n"
                    f"• Ovoz rejimi: **{sound_mode}**\n"
                    f"• Oxirgi signal: **{time_str}**\n\n"
                )
            else:
                text += f"👤 **{ch.full_name}** — Telemetriya ma'lumotlari hali olinmagan\n\n"

        refresh_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash (Signal yuborish)", callback_data="refresh_battery_status")]
        ])
        try:
            await callback.message.edit_text(text, reply_markup=refresh_kb, parse_mode="Markdown")
        except Exception:
            pass
    await callback.answer("✅ Signal yuborildi va ma'lumot yangilandi!")


# ---------------------------------------------------------
# Daily Route Summary ("📊 Bugungi hisobot")
# ---------------------------------------------------------

@router.message(Command("report", "hisobot", "kunlik"))
@router.message(F.text.in_({"📊 Bugungi hisobot", "Bugungi hisobot", "Hisobot", "hisobot"}))
async def on_daily_summary_pressed(message: Message):
    """Parent checks summary of movements and daily safety statistics today."""
    parent_id = message.from_user.id

    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer("Sizga hali farzand ulanmagan.")
            return

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

                wishlist = rep.get("wishlist")
                if wishlist and wishlist.get("title") and wishlist.get("target_som", 0) > 0:
                    w_title = html.escape(wishlist.get("title"))
                    w_prog = wishlist.get("progress_percent", 0)
                    w_cur = wishlist.get("current_som", 0)
                    w_tgt = wishlist.get("target_som", 0)
                    wishlist_line = f"\n🎯 <b>Orzular qutisi ({w_title}):</b> {w_prog}% ({w_cur:,} / {w_tgt:,} so'm)"
                else:
                    wishlist_line = ""

                dist_str = rep.get("distance_formatted") or f"{rep.get('total_distance_km', 0)} km"
                batt_str = f"{rep['battery_level']}%" if rep['battery_level'] is not None else "Noma'lum"
                spd_str = f"{rep['max_speed_kmh']} km/soat" if rep['max_speed_kmh'] > 0 else "Piyoda"

                report_msg = (
                    f"📊 <b>KUNLIK XAVFSIZLIK VA HARAKAT HISOBOTI</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Farzand:</b> {c_name}\n\n"
                    f"📍 <b>Tashrif buyurilgan joylar:</b>\n"
                    f"   └ {zones_str}\n\n"
                    f"⏱ <b>Xavfsiz joylarda bo'lgan vaqti:</b> {time_str}\n"
                    f"🚶‍♂️ <b>Kuzatilgan masofa:</b> {dist_str} ({rep['points_count']} ta signal)\n"
                    f"🚀 <b>Maksimal tezlik:</b> {spd_str}\n"
                    f"🔋 <b>Hozirgi batareya:</b> {batt_str}\n\n"
                    f"✅ <b>Bajarilgan topshiriqlar:</b> {rep['completed_tasks_count']} ta (+{rep['earned_som']:,} so'm)"
                    f"{wishlist_line}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"✨ <i>Barcha ma'lumotlar real vaqtda GPS telemetriyasi orqali hisoblandi.</i>"
                )
                report_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗺 Xaritada harakat marshrutini ko'rish", web_app=WebAppInfo(url=settings.MINI_APP_URL))],
                    [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"refresh_report:{ch.id}")]
                ])
                await message.answer(report_msg, reply_markup=report_kb, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Error generating daily summary for child {ch.id}: {e}", exc_info=True)
                safe_name = html.escape(ch.full_name or "Farzand")
                await message.answer(
                    f"📊 <b>{safe_name}</b> uchun bugungi ma'lumotlar jamlanmoqda...\n"
                    f"Farzandingiz telefonidan yangi GPS nuqtalari kelishi bilan hisobot to'liq shakllanadi.",
                    parse_mode="HTML"
                )


@router.callback_query(F.data.startswith("refresh_report:"))
async def on_refresh_report_callback(callback: CallbackQuery):
    """Parent refreshes daily report inline."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        try:
            rep = await crud.generate_daily_safety_report(session, child_id)
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

            wishlist = rep.get("wishlist")
            if wishlist and wishlist.get("title") and wishlist.get("target_som", 0) > 0:
                w_title = html.escape(wishlist.get("title"))
                w_prog = wishlist.get("progress_percent", 0)
                w_cur = wishlist.get("current_som", 0)
                w_tgt = wishlist.get("target_som", 0)
                wishlist_line = f"\n🎯 <b>Orzular qutisi ({w_title}):</b> {w_prog}% ({w_cur:,} / {w_tgt:,} so'm)"
            else:
                wishlist_line = ""

            dist_str = rep.get("distance_formatted") or f"{rep.get('total_distance_km', 0)} km"
            batt_str = f"{rep['battery_level']}%" if rep['battery_level'] is not None else "Noma'lum"
            spd_str = f"{rep['max_speed_kmh']} km/soat" if rep['max_speed_kmh'] > 0 else "Piyoda"

            report_msg = (
                f"📊 <b>KUNLIK XAVFSIZLIK VA HARAKAT HISOBOTI</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Farzand:</b> {c_name}\n\n"
                f"📍 <b>Tashrif buyurilgan joylar:</b>\n"
                f"   └ {zones_str}\n\n"
                f"⏱ <b>Xavfsiz joylarda bo'lgan vaqti:</b> {time_str}\n"
                f"🚶‍♂️ <b>Kuzatilgan masofa:</b> {dist_str} ({rep['points_count']} ta signal)\n"
                f"🚀 <b>Maksimal tezlik:</b> {spd_str}\n"
                f"🔋 <b>Hozirgi batareya:</b> {batt_str}\n\n"
                f"✅ <b>Bajarilgan topshiriqlar:</b> {rep['completed_tasks_count']} ta (+{rep['earned_som']:,} so'm)"
                f"{wishlist_line}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✨ <i>Barcha ma'lumotlar real vaqtda GPS telemetriyasi orqali hisoblandi.</i>"
            )
            report_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗺 Xaritada harakat marshrutini ko'rish", web_app=WebAppInfo(url=settings.MINI_APP_URL))],
                [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"refresh_report:{child_id}")]
            ])
            await callback.message.edit_text(report_msg, reply_markup=report_kb, parse_mode="HTML")
            await callback.answer("🔄 Hisobot yangilandi!")
        except Exception as e:
            logger.error(f"Error refreshing daily report: {e}")
            await callback.answer("Hozircha yangi ma'lumot yo'q.")


# ---------------------------------------------------------
# Emergency SOS & Calling
# ---------------------------------------------------------

@router.message(F.text == "🚨 SOS (Xavf tugmasi)")
async def on_child_sos_pressed(message: Message, bot: Bot):
    """Child activates SOS emergency."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name
    now_str = datetime.now(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M:%S")

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

        telemetry.last_sos_alert_map[child_id] = datetime.now(timezone.utc)
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

        # Broadcast SOS alert to parents connected to WebApp
        try:
            from api.webapp_api import ws_manager
            for parent in parents:
                await ws_manager.send_to_user(parent.id, {
                    "type": "sos_alert",
                    "child_id": child_id,
                    "child_name": child_name,
                    "latitude": latest_loc.latitude if latest_loc else None,
                    "longitude": latest_loc.longitude if latest_loc else None,
                    "battery_level": latest_loc.battery_level if latest_loc else None,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
        except Exception as ws_err:
            logger.error(f"Failed to broadcast SOS to WebApp: {ws_err}")

        phone_hint = (
            f"📞 **Ota-onangiz bilan bog'lanish:**\n"
            f"Telefon: `{parent_phone}` *(Qo'ng'iroq qilish uchun ustiga bosing)*\n\n"
            if parent_phone else
            "⚠️ Ota-onangiz telefon raqamini hali ulamagan.\n\n"
        )

        # Send immediate emergency response to child with Direct Call button
        await message.answer(
            f"🚨 **SOS SIGNALI OTA-ONANGIZGA YUBORILDI!**\n\n"
            f"Iltimos, xotirjam bo'ling. Ota-onangizga xabar va joriy joylashuvingiz yetkazildi.\n\n"
            f"{phone_hint}"
            f"Darhol ota-onangizga qo'ng'iroq qiling yoki ovozli xabar (golos) yuboring — biz uni yetkazamiz!",
            reply_markup=get_child_sos_call_keyboard(parent_phone, parents[0].id if parents else None),
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

        # Broadcast SOS resolution to parents connected to WebApp
        try:
            from api.webapp_api import ws_manager
            for p in parents:
                await ws_manager.send_to_user(p.id, {
                    "type": "sos_resolved",
                    "child_id": child_id,
                    "is_sos_active": False
                })
        except Exception:
            pass

    telemetry.last_sos_alert_map.pop(child_id, None)
    try:
        await callback.message.edit_text("✅ Xavfsizlik tiklandi va ota-onangizga xabar berildi.")
    except Exception:
        pass
    try:
        await callback.answer()
    except Exception:
        pass


@router.message(F.text == "📞 Ota-onamga qo'ng'iroq")
async def on_child_call_parent_pressed(message: Message):
    """Child wants to call parent directly in 1 tap with native Contact Card and direct dial."""
    child_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child and child.full_name else "Farzandingiz"
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents:
            await message.answer("Sizga hali ota-ona ulanmagan.")
            return

        for p in parents:
            p_name = p.full_name or "Ota-ona"
            if p.phone_number:
                clean_phone = p.phone_number.strip().replace(" ", "").replace("-", "")
                # 1. Send native interactive Telegram Contact Card (Renders big green CALL button in 1 tap!)
                try:
                    await message.answer_contact(
                        phone_number=clean_phone,
                        first_name=p_name,
                        last_name="(Ota-onangiz)"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send contact card: {e}")

                buttons = []
                if p.username:
                    buttons.append([InlineKeyboardButton(text=f"📞 {p_name} (Telegram qo'ng'iroq)", url=f"https://t.me/{p.username}")])
                else:
                    buttons.append([InlineKeyboardButton(text=f"📞 {p_name} (Telegram orqali)", url=f"tg://user?id={p.id}")])

                call_text = (
                    f"📞 **{p_name} ga qo'ng'iroq qilish:**\n\n"
                    f"📱 Yuqoridagi kontakt kartasida **«QO'NG'IROQ QILISH»** tugmasini bosing yoki quyidagi raqam ustiga bosing:\n\n"
                    f"👉 **{p.phone_number}**"
                )
                await message.answer(
                    call_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    parse_mode="Markdown"
                )
            else:
                buttons = [
                    [InlineKeyboardButton(
                        text=f"💬 {p_name} bilan Telegramda bog'lanish",
                        url=f"https://t.me/{p.username}" if p.username else f"tg://user?id={p.id}"
                    )]
                ]
                await message.answer(
                    f"⚠️ **{p_name}** o'z telefon raqamini botga kiritmagan.\n\n"
                    f"Ular bilan Telegram orqali qo'ng'iroq qilishingiz yoki yozishingiz mumkin:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    parse_mode="Markdown"
                )
                # Notify parent to add their phone number immediately so child can call in 1 tap
                try:
                    await message.bot.send_message(
                        chat_id=p.id,
                        text=(
                            f"📞 **DIQQAT!** Farzandingiz **{child_name}** sizga telefon qilmoqchi bo'ldi, "
                            f"lekin profilingizda telefon raqamingiz kiritilmagan.\n\n"
                            f"Farzandingiz 1 ta tugma orqali sizga qo'ng'iroq qila olishi uchun "
                            f"iltimos, pastdagi **«📱 Raqamimni ulash»** tugmasini bosing:"
                        ),
                        reply_markup=get_contact_request_keyboard(),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

@router.callback_query(F.data.startswith("contact_child:"))
async def on_contact_child_callback(callback: CallbackQuery):
    """Parent wants to call/contact their child in 1 tap."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if not child:
            await callback.answer("Farzand topilmadi", show_alert=True)
            return

        child_name = child.full_name or "Farzand"
        if child.phone_number:
            clean_phone = child.phone_number.strip().replace(" ", "").replace("-", "")
            try:
                await callback.message.answer_contact(
                    phone_number=clean_phone,
                    first_name=child_name,
                    last_name="(Farzandingiz)"
                )
            except Exception:
                pass
            buttons = []
            if child.username:
                buttons.append([InlineKeyboardButton(text=f"📞 {child_name} (Telegram)", url=f"https://t.me/{child.username}")])
            else:
                buttons.append([InlineKeyboardButton(text=f"📞 {child_name} (Telegram)", url=f"tg://user?id={child.id}")])
            await callback.message.answer(
                f"📞 **{child_name} ga qo'ng'iroq qilish:**\n\n"
                f"📱 Kontakt kartasida **«QO'NG'IROQ QILISH»** tugmasini bosing yoki quyidagi raqam ustiga bosing:\n\n"
                f"👉 **{child.phone_number}**",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode="Markdown"
            )
        else:
            buttons = [
                [InlineKeyboardButton(
                    text=f"💬 {child_name} bilan Telegramda bog'lanish",
                    url=f"https://t.me/{child.username}" if child.username else f"tg://user?id={child.id}"
                )]
            ]
            await callback.message.answer(
                f"👤 **{child_name}** bilan Telegram orqali bog'lanishingiz mumkin:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode="Markdown"
            )
    await callback.answer()



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
            f"Farzandingiz **🚨 SOS** tugmasini bosganda to'g'ridan-to'g'ri bir tugma bilan sizga **qo'ng'iroq ketishi** uchun telefon raqamingizni ulab qo'ying!\n\n"
            f"Pastdagi **«📱 Raqamimni ulash»** tugmasini bosing yoki raqamingizni yozib yuboring (masalan: `+998901234567`):",
            reply_markup=get_contact_request_keyboard(),
            parse_mode="Markdown"
        )


@router.message(PhoneSaveState.waiting_for_phone, F.contact)
async def on_contact_received(message: Message, state: FSMContext):
    """Parent sends native contact card via Telegram button."""
    contact = message.contact
    parent_id = message.from_user.id
    phone = contact.phone_number.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, parent_id)
        if user:
            user.phone_number = phone
            await session.commit()

    await state.clear()
    await message.answer(
        f"✅ **Telefon raqamingiz muvaffaqiyatli saqlandi:** `{phone}`\n\n"
        f"Endi farzandingiz **🚨 SOS** xavf tugmasini bosganida darhol sizga bir bosishda qo'ng'iroq qila oladi! 🛡️",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="Markdown"
    )


@router.message(PhoneSaveState.waiting_for_phone, F.text)
async def on_phone_entered(message: Message, state: FSMContext, bot: Bot):
    """Parent enters phone number manually as text."""
    raw_text = message.text.strip()
    parent_id = message.from_user.id

    # If user pressed any app button or command, exit state immediately!
    if any(w in raw_text.lower() for w in ["ilova", "apk", "mobil", "yuklab", "yuklash", "app", "start"]):
        await state.clear()
        if "start" in raw_text.lower():
            await message.answer("Bosh menyu:", reply_markup=get_parent_main_keyboard(parent_id))
        else:
            await on_download_app_pressed(message, bot)
        return

    if "keyinroq" in raw_text.lower():
        await state.clear()
        await message.answer(
            "Tushunarli. Telefon raqamingizni keyinroq **«📞 Oila kontaktlari»** bo'limi orqali istalgan payt kiritishingiz mumkin.",
            reply_markup=get_parent_main_keyboard(parent_id)
        )
        return

    # Normalize phone
    cleaned = "".join([c for c in raw_text if c.isdigit() or c == "+"])
    if not cleaned.startswith("+"):
        if cleaned.startswith("998"):
            cleaned = "+" + cleaned
        elif len(cleaned) == 9:
            cleaned = "+998" + cleaned
        else:
            cleaned = "+" + cleaned

    digits = [c for c in cleaned if c.isdigit()]
    if len(digits) < 7:
        await message.answer(
            "⚠️ Telefon raqam noto'g'ri formatda kiritildi.\n\n"
            "Iltimos, pastdagi **«📱 Raqamimni ulash»** tugmasini bosing yoki raqamingizni to'liq yozing (masalan: `+998901234567`):",
            reply_markup=get_contact_request_keyboard()
        )
        return

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, parent_id)
        if user:
            user.phone_number = cleaned
            await session.commit()

    await state.clear()
    await message.answer(
        f"✅ **Telefon raqamingiz muvaffaqiyatli saqlandi:** `{cleaned}`\n\n"
        f"Endi farzandingiz **🚨 SOS** xavf tugmasini bosganida darhol sizga bir bosishda qo'ng'iroq qila oladi! 🛡️",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="Markdown"
    )


@router.message(PhoneSaveState.waiting_for_phone)
async def on_phone_invalid_media(message: Message):
    await message.answer(
        "📱 **Iltimos, telefon raqamingizni yuboring!**\n\n"
        "Pastdagi **«📱 Raqamimni ulash»** tugmasini bosing yoki raqamingizni yozib yuboring (masalan: `+998901234567`):",
        reply_markup=get_contact_request_keyboard()
    )


@router.message(F.text.in_([
    "📍 Jonli joylashuvimni yuborish",
    "Jonli joylashuvimni yuborish",
    "jonli joylashuvni yuborish",
    "Jonli joylashuv",
    "jonli joylashuv",
    "Joylashuvni yuborish"
]))
async def on_send_location_text_hint(message: Message):
    """Guide user on how to share native location if sent as text."""
    await message.answer(
        "📍 **Jonli joylashuvni yuborish uchun:**\n\n"
        "1️⃣ Pastdagi **«📍 Jonli joylashuvimni yuborish»** tugmasini bosing va 'Ulashish / Share' ni tasdiqlang.\n"
        "2️⃣ Yoki Telegram xabar yozish joyidagi qog'oz qisqich (📎) belgisini bosib, **Joylashuv (Location)** -> **Jonli joylashuvimni ulashish (Share Live Location)** ni tanlang.",
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

        # If user is parent and sent location:
        if user.role == "parent":
            caption = message.caption
            if caption:
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
                    reply_markup=get_parent_main_keyboard(user_id),
                    parse_mode="Markdown"
                )
            else:
                await crud.log_location(
                    session=session,
                    child_id=user_id,
                    latitude=lat,
                    longitude=lon,
                    accuracy=accuracy,
                    source=source
                )
                await session.commit()
                await message.answer(
                    f"📍 **Joylashuvingiz muvaffaqiyatli qabul qilindi!**\n\n"
                    f"📌 Koordinata: `{lat:.5f}, {lon:.5f}`\n"
                    f"🕒 Vaqt: Hozirgina\n\n"
                    f"Farzandingiz joylashuvini ko'rish uchun **«📍 Farzandning hozirgi joylashuvi»** tugmasini bosing.",
                    reply_markup=get_parent_main_keyboard(user_id),
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

        # If child in SOS, forward to parents with 60s cooldown to prevent duplicate balloons
        if user.is_sos_active:
            now_utc = datetime.now(timezone.utc)
            last_loc_sent = telemetry.last_sos_alert_map.get(user_id)
            if not last_loc_sent or (now_utc - last_loc_sent) > timedelta(seconds=60):
                telemetry.last_sos_alert_map[user_id] = now_utc
                parents = await crud.get_parents_for_child(session, user_id)
                for p in parents:
                    try:
                        await bot.send_location(chat_id=p.id, latitude=lat, longitude=lon)
                    except Exception:
                        pass

        # Broadcast live telemetry to connected parents via WebSocket
        try:
            from api.webapp_api import ws_manager
            parents = await crud.get_parents_for_child(session, user_id)
            for p in parents:
                await ws_manager.send_to_user(p.id, {
                    "type": "child_location_update",
                    "child_id": user_id,
                    "latitude": lat,
                    "longitude": lon,
                    "accuracy": accuracy,
                    "speed": None,
                    "battery_level": None,
                    "is_charging": False,
                    "sound_mode": "normal",
                    "recorded_at": datetime.now(timezone.utc).isoformat()
                })
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
    now_str = datetime.now(TASHKENT_TZ).strftime("%H:%M")

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

        safe_cname = html.escape(child_name)
        for parent in parents:
            try:
                checkin_text = (
                    f"✅ <b>YETIB KELDIM (Check-in)!</b>\n\n"
                    f"Farzandingiz <b>{safe_cname}</b> manzilga eson-omon yetib kelganini xabar qildi.{place_info}\n"
                    f"🕒 Vaqt: <b>{now_str}</b>"
                )
                await bot.send_message(
                    chat_id=parent.id,
                    text=checkin_text,
                    parse_mode="HTML"
                )
                if latest_loc:
                    await bot.send_location(
                        chat_id=parent.id,
                        latitude=latest_loc.latitude,
                        longitude=latest_loc.longitude
                    )
            except Exception as e:
                logger.error(f"Failed to send checkin to parent {parent.id}: {e}")

        # Broadcast check-in to parents on WebApp
        try:
            from api.webapp_api import ws_manager
            for parent in parents:
                await ws_manager.send_to_user(parent.id, {
                    "type": "checkin",
                    "child_id": child_id,
                    "child_name": child_name,
                    "time": now_str
                })
        except Exception:
            pass

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
        rate_coins, rate_som = await crud.get_coin_rate_for_user(session, child_id)
        money_som = crud.calculate_som_from_coins(balance, rate_coins, rate_som)

        text = (
            f"💰 **Sizning balansingiz:**\n"
            f"🪙 **{balance:,} tanga**\n"
            f"💵 **Sizda hozir {money_som:,} so'm pulingiz bor!**\n"
            f"📌 *(Kurs: {rate_coins:,} tanga = {rate_som:,} so'm)*\n\n"
        )

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
            caption = f"🎯 **Vazifa:** {t.title}\n💰 Mukofot: {t.reward_coins:,} tanga"
            if t.description:
                caption += f"\n📝 Tavsif: {t.description}"
            if t.promised_time:
                caption += f"\n⏰ Belgilangan vaqt: {t.promised_time}"

            markup = get_child_task_action_keyboard(t.id, t.status, t.promised_time)
            sent = False
            if t.media_type == "photo" and t.media_url:
                try:
                    await message.answer_photo(t.media_url, caption=caption, reply_markup=markup, parse_mode="Markdown")
                    sent = True
                except Exception:
                    pass
            elif t.media_type == "video" and t.media_url:
                try:
                    await message.answer_video(t.media_url, caption=caption, reply_markup=markup, parse_mode="Markdown")
                    sent = True
                except Exception:
                    pass

            if not sent:
                await message.answer(caption, reply_markup=markup, parse_mode="Markdown")


# ---------------------------------------------------------
# Child Task Action Handlers: Ko'rdim, Shu vaqtda bajaraman, Rasm hisoboti
# ---------------------------------------------------------

@router.callback_query(F.data.startswith("task_seen:"))
async def on_task_seen_clicked(callback: CallbackQuery, bot: Bot):
    """Child acknowledges viewing the task: notifies parent 'Bolangiz bu habarni ko'rdi'."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.mark_task_seen(session, task_id)
        if not task:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return

        child = await crud.get_user(session, task.child_id)
        child_name = child.full_name if child else "Farzandingiz"

        try:
            await bot.send_message(
                chat_id=task.parent_id,
                text=(
                    f"👁 **Bolangiz bu habarni ko'rdi**\n\n"
                    f"📌 Topshiriq: **{task.title}**\n"
                    f"👤 Farzand: **{child_name}**"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about task seen: {e}")

        await session.commit()

    try:
        new_markup = get_child_task_action_keyboard(task.id, task.status, task.promised_time)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
    except Exception as e:
        logger.debug(f"Could not update task markup: {e}")

    await callback.answer("Ota-onangizga xabarni ko'rganingiz bildirildi!")


@router.callback_query(F.data.startswith("task_sched_menu:"))
async def on_task_sched_menu_clicked(callback: CallbackQuery):
    """Open time schedule picker options for child."""
    task_id = int(callback.data.split(":")[1])
    await callback.message.edit_reply_markup(
        reply_markup=get_task_schedule_options_keyboard(task_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_back:"))
async def on_task_back_clicked(callback: CallbackQuery):
    """Return back to main task action buttons."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.get_task_by_id(session, task_id)
        if not task:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return
        markup = get_child_task_action_keyboard(task.id, task.status, task.promised_time)
        await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("task_sched:"))
async def on_task_sched_picked(callback: CallbackQuery, bot: Bot):
    """Child committed to a specific completion time."""
    parts = callback.data.split(":", 2)
    task_id = int(parts[1])
    promised_time = parts[2]

    async with AsyncSessionLocal() as session:
        task = await crud.set_task_schedule(session, task_id, promised_time)
        if not task:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return

        child = await crud.get_user(session, task.child_id)
        child_name = child.full_name if child else "Farzandingiz"

        try:
            await bot.send_message(
                chat_id=task.parent_id,
                text=(
                    f"⏰ **Bolangiz {promised_time}da bu topshiriqni bajarishga qaror qildi**\n\n"
                    f"📌 Topshiriq: **{task.title}**\n"
                    f"👤 Farzand: **{child_name}**"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about task schedule: {e}")

        await session.commit()

    try:
        new_markup = get_child_task_action_keyboard(task.id, task.status, task.promised_time)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
    except Exception as e:
        logger.debug(f"Could not update task markup: {e}")

    await callback.answer(f"Vaqt belgilandi: {promised_time}")


@router.callback_query(F.data.startswith("task_submit_photo:") | F.data.startswith("complete_task:"))
async def on_task_submit_photo_clicked(callback: CallbackQuery, state: FSMContext):
    """Child initiates submitting required proof photo for completed task."""
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        task = await crud.get_task_by_id(session, task_id)
        if not task:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return

    await state.update_data(task_id=task_id)
    await state.set_state(TaskProofState.waiting_for_photo)

    await callback.message.answer(
        f"📸 **'{task.title}' topshirig'ini topshirish:**\n\n"
        f"Topshiriq bajarilganini tasdiqlovchi rasmni yuboring (kameradan rasmga oling yoki galereyadan yuboring).\n"
        f"⚠️ _Eslatma: Ota-onaga rasm jo'natishingiz shart!_",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_task_proof")
        ]]),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(TaskProofState.waiting_for_photo, F.photo)
async def on_task_proof_photo_received(message: Message, state: FSMContext, bot: Bot):
    """Child submits the required proof photo."""
    data = await state.get_data()
    task_id = data.get("task_id")
    if not task_id:
        await state.clear()
        await message.answer("Xatolik yuz berdi. Iltimos, qaytadan boshlang.")
        return

    photo_id = message.photo[-1].file_id

    async with AsyncSessionLocal() as session:
        task = await crud.submit_task_completed(session, task_id, proof_photo_url=photo_id)
        if not task:
            await state.clear()
            await message.answer("Vazifa topilmadi.")
            return

        child = await crud.get_user(session, task.child_id)
        child_name = child.full_name if child else "Farzandingiz"

        safe_cname = html.escape(child_name)
        safe_title = html.escape(task.title)
        caption_html = (
            f"🔔 <b>Topshiriq bajarildi! Farzandingiz hisobot rasmini yubordi:</b>\n\n"
            f"👤 Farzand: <b>{safe_cname}</b>\n"
            f"📌 Topshiriq: <b>{safe_title}</b>\n"
            f"💰 Mukofot: <b>{task.reward_coins:,}</b> tanga\n\n"
            f"Topshiriq to'g'ri bajarilgan bo'lsa, tasdiqlang:"
        )
        markup = get_task_approval_keyboard(task.id, task.reward_coins)
        try:
            await bot.send_photo(
                chat_id=task.parent_id,
                photo=photo_id,
                caption=caption_html,
                reply_markup=markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to forward task proof photo HTML, falling back: {e}")
            raw_cap = (
                f"🔔 Topshiriq bajarildi! Farzandingiz hisobot rasmini yubordi:\n\n"
                f"👤 Farzand: {child_name}\n"
                f"📌 Topshiriq: {task.title}\n"
                f"💰 Mukofot: {task.reward_coins:,} tanga\n\n"
                f"Topshiriq to'g'ri bajarilgan bo'lsa, tasdiqlang:"
            )
            await bot.send_photo(
                chat_id=task.parent_id,
                photo=photo_id,
                caption=raw_cap,
                reply_markup=markup
            )

        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ **Rasm qabul qilindi va ota-onangizga tekshirish uchun yuborildi!**\n\n"
        f"Ota-onangiz tasdiqlaganidan so'ng hisobingizga **+{task.reward_coins:,} tanga** o'tkaziladi. 👏"
    )


@router.message(TaskProofState.waiting_for_photo)
async def on_task_proof_invalid(message: Message):
    """Enforce that proof MUST be a photo."""
    await message.answer(
        "⚠️ **Topshiriq bajarilganini tasdiqlash uchun rasm yuborishingiz shart!**\n\n"
        "Iltimos, kamera orqali rasmga tushirib yoki galereyadan rasm yuboring."
    )


@router.callback_query(F.data == "cancel_task_proof")
async def on_cancel_task_proof(callback: CallbackQuery, state: FSMContext):
    """Cancel proof submission."""
    await state.clear()
    await callback.message.edit_text("❌ Rasm yuborish bekor qilindi.")
    await callback.answer()


@router.callback_query(F.data == "noop")
async def on_noop_callback(callback: CallbackQuery):
    await callback.answer()


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

        safe_title = html.escape(task.title)
        try:
            await bot.send_message(
                chat_id=task.child_id,
                text=(
                    f"🎉🎉 <b>TABRIKLAYMIZ!</b> 🎉🎉\n\n"
                    f"Ota-onangiz <b>'{safe_title}'</b> vazifangizni tasdiqladi!\n"
                    f"💰 Balansingizga: <b>+{task.reward_coins:,} tanga</b> qo'shildi!\n"
                    f"Hozirgi umumiy balansingiz: <b>{new_balance:,} tanga</b>."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of approved task: {e}")

        # Broadcast approval to child on WebApp
        try:
            from api.webapp_api import ws_manager
            await ws_manager.send_to_user(task.child_id, {
                "type": "task_approved",
                "task_id": task.id,
                "reward_coins": task.reward_coins,
                "new_balance": new_balance
            })
        except Exception:
            pass

        await session.commit()

    confirm_msg = (
        f"✅ **Vazifa tasdiqlandi!**\n\n"
        f"Farzandingiz hisobiga **+{task.reward_coins:,} tanga** muvaffaqiyatli o'tkazildi."
    )
    if callback.message.photo or callback.message.video:
        try:
            await callback.message.edit_caption(caption=confirm_msg, reply_markup=None, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(confirm_msg, parse_mode="Markdown")
    else:
        try:
            await callback.message.edit_text(confirm_msg, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(confirm_msg, parse_mode="Markdown")
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

    reject_msg = "❌ Vazifa rad etildi."
    if callback.message.photo or callback.message.video:
        try:
            await callback.message.edit_caption(caption=reject_msg, reply_markup=None)
        except Exception:
            await callback.message.answer(reject_msg)
    else:
        try:
            await callback.message.edit_text(reject_msg)
        except Exception:
            await callback.message.answer(reject_msg)
    await callback.answer("Rad etildi!")


# ---------------------------------------------------------
# Parent Task Management & Creation with Photo/Video Attachment
# ---------------------------------------------------------

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
        rate_coins, rate_som = await crud.get_coin_rate_for_user(session, parent_id)
        text = (
            f"📋 **Vazifalar boshqaruvi**\n"
            f"📌 Belgilangan kurs: **{rate_coins:,} tanga = {rate_som:,} so'm**\n"
            f"Jami yaratilgan vazifalar: {len(tasks)} ta.\n\n"
        )

        for child in children:
            c_money = crud.calculate_som_from_coins(child.virtual_balance, rate_coins, rate_som)
            child_tasks = [t for t in tasks if t.child_id == child.id and t.status in ["pending", "seen", "scheduled", "completed_by_child"]]
            text += f"👤 **{child.full_name}** (Balans: {child.virtual_balance:,} tanga ≈ {c_money:,} so'm)\n"
            if child_tasks:
                for t in child_tasks:
                    if t.status == "completed_by_child":
                        status_text = "⏳ Tekshiruvda"
                    elif t.status == "scheduled":
                        status_text = f"⏰ Vaqt belgilangan: {t.promised_time}"
                    elif t.status == "seen":
                        status_text = "👁 Ko'rildi"
                    else:
                        status_text = "▫️ Kutilmoqda"
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
        buttons.append([
            InlineKeyboardButton(
                text=f"💰 Kursni sozlash ({rate_coins:,} = {rate_som:,} so'm)",
                callback_data="coin_rate_start"
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
        "📝 **Yangi vazifa nomini kiriting:**\n\n(Masalan: _'Matematika darslarini qilish'_, _'Xonani tartibga keltirish'_, _'Kitob o'qish'_)",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(TaskCreateState.waiting_for_title)
async def on_task_title_entered(message: Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer("⚠️ Iltimos, vazifa nomini matn ko'rinishida kiriting (masalan: _'Kitob o'qish'_):", parse_mode="Markdown")
        return

    title = message.text.strip()
    if len(title) > 200:
        await message.answer("⚠️ Vazifa nomi juda uzun (maksimal 200 ta belgi). Iltimos, qisqaroq nom kiriting:")
        return

    await state.update_data(title=title)
    await state.set_state(TaskCreateState.waiting_for_reward)

    await message.answer(
        f"💰 **Ushbu vazifa uchun mukofot miqdorini tanga / so'mda kiriting:**\n(Masalan: `5000` yoki `10000`):",
        parse_mode="Markdown"
    )


@router.message(TaskCreateState.waiting_for_reward)
async def on_task_reward_entered(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Iltimos, mukofot miqdorini butun son bilan kiriting (masalan: `5000`):")
        return

    try:
        reward = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("Iltimos, faqat butun son kiriting (masalan: `5000`):")
        return

    if reward <= 0:
        await message.answer("⚠️ Mukofot miqdori 0 dan katta bo'lishi kerak. Iltimos, qaytadan kiriting (masalan: `5000`):")
        return
    if reward > 100_000_000:
        await message.answer("⚠️ Mukofot miqdori juda katta (maksimal: 100,000,000). Iltimos, qaytadan kiriting:")
        return

    await state.update_data(reward=reward)
    await state.set_state(TaskCreateState.waiting_for_media)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏭ O'tkazib yuborish (Rasmsiz)", callback_data="skip_task_media")
    ]])

    await message.answer(
        "📷 / 🎥 **Vazifaga rasm yoki video biriktirasizmi?**\n\n"
        "Farzandingiz topshiriqni yaxshiroq tushunishi uchun rasm yoki video yuborishingiz mumkin.\n"
        "Agar kerak bo'lmasa, pastdagi tugmani bosing:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def _finalize_task_creation(
    event_target,
    state: FSMContext,
    bot: Bot,
    media_type: Optional[str] = None,
    media_url: Optional[str] = None,
    parent_id: Optional[int] = None
):
    """Helper to commit task to DB and send notification to child."""
    data = await state.get_data()
    child_id = data["child_id"]
    title = data["title"]
    reward = data.get("reward", 1000)
    if not parent_id:
        parent_id = event_target.from_user.id

    async with AsyncSessionLocal() as session:
        task = await crud.create_task(
            session=session,
            parent_id=parent_id,
            child_id=child_id,
            title=title,
            reward_coins=reward,
            media_type=media_type,
            media_url=media_url
        )
        await session.commit()

        child_text = (
            f"🎯 **SIZGA YANGI VAZIFA BERILDI!**\n\n"
            f"📌 **Vazifa:** {title}\n"
            f"💰 **Mukofot:** +{reward:,} tanga!\n\n"
            f"Pastdagi tugmalar orqali xabarni ko'rganingizni tasdiqlang va qachon bajarishingizni belgilang 👇"
        )
        child_markup = get_child_task_action_keyboard(task.id, task.status, task.promised_time)

        try:
            if media_type == "photo" and media_url:
                await bot.send_photo(
                    chat_id=child_id,
                    photo=media_url,
                    caption=child_text,
                    reply_markup=child_markup,
                    parse_mode="Markdown"
                )
            elif media_type == "video" and media_url:
                await bot.send_video(
                    chat_id=child_id,
                    video=media_url,
                    caption=child_text,
                    reply_markup=child_markup,
                    parse_mode="Markdown"
                )
            else:
                await bot.send_message(
                    chat_id=child_id,
                    text=child_text,
                    reply_markup=child_markup,
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Failed to notify child: {e}")

    await state.clear()
    media_info = f"\n📎 Biriktirilgan fayl: {media_type.capitalize()}" if media_type else ""
    success_text = (
        f"✅ **Vazifa muvaffaqiyatli saqlandi va farzandga yuborildi!**\n\n"
        f"📌 Nomi: **{title}**\n"
        f"💰 Mukofot: **{reward:,} tanga**{media_info}"
    )

    if isinstance(event_target, CallbackQuery):
        await event_target.message.answer(
            success_text,
            reply_markup=get_parent_main_keyboard(parent_id),
            parse_mode="Markdown"
        )
    else:
        await event_target.answer(
            success_text,
            reply_markup=get_parent_main_keyboard(parent_id),
            parse_mode="Markdown"
        )


@router.message(TaskCreateState.waiting_for_media, F.photo)
async def on_task_media_photo(message: Message, state: FSMContext, bot: Bot):
    photo_id = message.photo[-1].file_id
    await _finalize_task_creation(message, state, bot, media_type="photo", media_url=photo_id)


@router.message(TaskCreateState.waiting_for_media, F.video)
async def on_task_media_video(message: Message, state: FSMContext, bot: Bot):
    video_id = message.video.file_id
    await _finalize_task_creation(message, state, bot, media_type="video", media_url=video_id)


@router.callback_query(TaskCreateState.waiting_for_media, F.data == "skip_task_media")
async def on_skip_task_media(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _finalize_task_creation(callback, state, bot, media_type=None, media_url=None, parent_id=callback.from_user.id)


# ---------------------------------------------------------
# Parent Children List & Pairing Code
# ---------------------------------------------------------

async def _build_children_list_message(parent_id: int):
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            return (
                "Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                "Farzand ulash uchun **'➕ Farzand ulash (Kodi)'** tugmasini bosing.",
                None
            )

        text = f"👶 <b>Ulangan farzandlar ro'yxati ({len(children)} nafar):</b>\n\n"
        buttons = []

        for ch in children:
            latest_loc = await crud.get_latest_location(session, ch.id)
            battery = f"{latest_loc.battery_level}%" if latest_loc and latest_loc.battery_level is not None else "Noma'lum"
            charging = "🔌 (Quvvatlanmoqda)" if latest_loc and latest_loc.is_charging else ""
            status = "🚨 SOS FAVQULODDA!" if ch.is_sos_active else "🟢 Faol"

            safe_name = html.escape(ch.full_name or "Farzand")
            safe_user = f"@{html.escape(ch.username)}" if ch.username else "yo'q"

            text += (
                f"👤 <b>{safe_name}</b> ({safe_user})\n"
                f"• Holat: <b>{status}</b>\n"
                f"• Batareya: <b>{battery}</b> {charging}\n"
                f"• Balans: <b>{ch.virtual_balance:,} tanga</b>\n\n"
            )

            btn_title = (ch.full_name or "Farzand")[:24]
            buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 {btn_title} boshqaruvi",
                    callback_data=f"child_view:{ch.id}"
                )
            ])

        return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(StateFilter("*"), F.text.in_(["👶 Farzandlarim", "Farzandlarim", "/farzandlarim", "farzandlarim"]))
async def on_list_children_pressed(message: Message, state: FSMContext):
    await state.clear()
    text, reply_markup = await _build_children_list_message(message.from_user.id)
    try:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Markdown send failed for children list, falling back to plain text: {e}")
        await message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data == "list_children")
async def on_list_children_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, reply_markup = await _build_children_list_message(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


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
        time_str = format_tashkent_time(latest_loc.recorded_at if latest_loc else None)
        charging = "🔌 (Quvvatlanmoqda)" if latest_loc and latest_loc.is_charging else ""
        sound_mode = latest_loc.sound_mode if latest_loc and latest_loc.sound_mode else "normal"
        sound_text = "🔊 Ovozli" if sound_mode == "normal" else ("📳 Vibratsiya" if sound_mode == "vibrate" else "🔇 Ovoz o'chiq")

        safe_name = html.escape(child.full_name or "Farzand")
        safe_user = f"@{html.escape(child.username)}" if child.username else "yo'q"

        text = (
            f"👤 <b>Farzand:</b> {safe_name} ({safe_user})\n"
            f"🆔 <b>ID:</b> <code>{child.id}</code>\n"
            f"🔋 <b>Batareya:</b> <b>{battery}</b> {charging}\n"
            f"🔔 <b>Ovoz holati:</b> {sound_text}\n"
            f"🕒 <b>Oxirgi signal:</b> {time_str}\n"
            f"💰 <b>Tangalar balansi:</b> <b>{child.virtual_balance:,} tanga</b>\n"
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_child_details_keyboard(child_id),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                text,
                reply_markup=get_child_details_keyboard(child_id)
            )
    await callback.answer()


@router.callback_query(F.data.startswith("battery_child:"))
async def on_child_battery_callback(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)

        if not child:
            await callback.answer("Farzand topilmadi.", show_alert=True)
            return

        safe_name = html.escape(child.full_name or "Farzand")
        if not latest_loc:
            text = f"🔋 <b>{safe_name}</b> telefonidan hali batareya ma'lumoti kelmagan."
        else:
            batt = f"{latest_loc.battery_level}%" if latest_loc.battery_level is not None else "Noma'lum"
            chg = "Ha 🔌" if latest_loc.is_charging else "Yo'q 🔋"
            snd = "Oddiy (Ovozli)" if latest_loc.sound_mode == "normal" else ("Vibratsiya" if latest_loc.sound_mode == "vibrate" else "Ovozsiz")
            time_str = format_tashkent_time(latest_loc.recorded_at if latest_loc else None, include_date=True)
            text = (
                f"🔋 <b>{safe_name} — Telefon holati:</b>\n\n"
                f"• Quvvat (Batareya): <b>{batt}</b>\n"
                f"• Quvvatlanmoqda: <b>{chg}</b>\n"
                f"• Ovoz rejimi: <b>{snd}</b>\n"
                f"• Oxirgi yangilanish: <b>{time_str}</b>\n"
            )

        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Farzand boshqaruviga qaytish", callback_data=f"child_view:{child_id}")],
            [InlineKeyboardButton(text="👶 Farzandlar ro'yxatiga qaytish", callback_data="list_children")]
        ])

        try:
            await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=back_kb)
    await callback.answer()


@router.callback_query(F.data.startswith("add_zone:"))
async def on_child_add_zone_callback(callback: CallbackQuery, state: FSMContext):
    child_id = int(callback.data.split(":")[1])
    await state.set_state(KerakliJoyAddState.waiting_for_name)
    await state.update_data(target_child_id=child_id)
    await callback.message.answer(
        "🏷 **Farzandingiz uchun yangi kerakli joy nomini kiriting:**\n(Masalan: _'Maktab №45'_, _'Uyimiz'_, _'Ingliz tili kursi'_)",
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
            f"🔑 **YAGONA OILA VA SAYT KODINGIZ:** ` {code} `\n\n"
            f"👨‍👩‍👧 **Bu bitta kod barcha farzandlaringiz uchun umumiy va doimiydir!**\n\n"
            f"1️⃣ **Farzandlarni ulash:**\n"
            f"Har bir farzandingiz telefonida botga kirib, '👶 Farzandman'ni tanlaydi va ushbu ` {code} ` kodini kiritadi. 1-bola, 2-bola, 3-bola... barchasi shu bitta kod bilan sizga ulanadi.\n\n"
            f"2️⃣ **Saytga (Web App) kirish:**\n"
            f"🌐 Manzil: {settings.WEBAPP_BASE_URL}\n"
            f"Saytga ham xuddi shu ` {code} ` kodini kiritasiz — barcha ulangan farzandlaringiz bir vaqtda chiqadi va bitta ekranda ularning barchasini kuzatib, boshqara olasiz!\n\n"
            f"Ulashish uchun quyidagi tugmani bosing:",
            reply_markup=get_pairing_share_keyboard(bot_info.username, code),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "refresh_pairing_code")
async def on_refresh_pairing_code(callback: CallbackQuery, bot: Bot):
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        new_code = await crud.generate_pairing_code(session, parent_id)
        await session.commit()

        bot_info = await bot.get_me()
        new_text = (
            f"🔑 **YANGILANGAN YAGONA OILA VA SAYT KODINGIZ:** ` {new_code} `\n\n"
            f"👨‍👩‍👧 **Bu bitta kod barcha farzandlaringiz uchun umumiy va doimiydir!**\n\n"
            f"1️⃣ **Farzandlarni ulash:**\n"
            f"Farzandingiz botga kirib ushbu yangi ` {new_code} ` kodini kiritadi.\n\n"
            f"2️⃣ **Saytga (Web App) kirish:**\n"
            f"🌐 Manzil: {settings.WEBAPP_BASE_URL}\n"
            f"Saytga ham shu ` {new_code} ` kodini kiritib boshqarishingiz mumkin.\n\n"
            f"Ulashish uchun quyidagi tugmani bosing:"
        )
        try:
            await callback.message.edit_text(
                new_text,
                reply_markup=get_pairing_share_keyboard(bot_info.username, new_code),
                parse_mode="Markdown"
            )
        except Exception:
            await callback.message.answer(
                new_text,
                reply_markup=get_pairing_share_keyboard(bot_info.username, new_code),
                parse_mode="Markdown"
            )
    await callback.answer("✅ Yangi ulanish kodi yaratildi!")


@router.callback_query(F.data == "cancel_action")
async def on_cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Amal bekor qilindi.")
    except Exception:
        await callback.message.delete()
    await callback.answer("Bekor qilindi.")


@router.callback_query(F.data.startswith("delete_child_confirm:"))
async def on_delete_child_confirm(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else "Farzand"

    text = (
        f"⚠️ **DIQQAT: Farzandni o'chirishni tasdiqlaysizmi?**\n\n"
        f"👤 Farzand: **{child_name}**\n\n"
        f"Agar tasdiqlasangiz, farzand hisobingizdan va tizimdan to'liq chiqariladi."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Ha, o'chirish", callback_data=f"delete_child_do:{child_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"child_view:{child_id}")
            ]
        ]
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("delete_child_do:"))
async def on_delete_child_do(callback: CallbackQuery, bot: Bot):
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        success = await crud.delete_child_profile(session, parent_id, child_id)
        await session.commit()

    if success:
        await callback.answer("✅ Farzand hisobi muvaffaqiyatli o'chirildi!", show_alert=True)
        # Notify child if active
        try:
            await bot.send_message(
                chat_id=child_id,
                text="ℹ️ Ota-onangiz profilingizni hisobdan chiqardi.",
                reply_markup=ReplyKeyboardRemove()
            )
        except Exception:
            pass
        # Return to children list
        text, reply_markup = await _build_children_list_message(parent_id)
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=reply_markup)
    else:
        await callback.answer("⚠️ Farzandni o'chirishda xatolik yuz berdi.", show_alert=True)


@router.callback_query(F.data.startswith("resolve_sos:"))
async def on_resolve_sos(callback: CallbackQuery, bot: Bot):
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    telemetry.last_sos_alert_map.pop(child_id, None)
    async with AsyncSessionLocal() as session:
        await crud.set_child_sos(session, child_id, False)
        child = await crud.get_user(session, child_id)
        parents = await crud.get_parents_for_child(session, child_id)
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

        # Broadcast SOS resolution to WebApp
        try:
            from api.webapp_api import ws_manager
            target_ids = {p.id for p in parents} | {parent_id}
            for pid in target_ids:
                await ws_manager.send_to_user(pid, {
                    "type": "sos_resolved",
                    "child_id": child_id,
                    "is_sos_active": False
                })
        except Exception:
            pass

    try:
        await callback.message.edit_text("✅ SOS signali bekor qilindi.")
    except Exception:
        pass
    try:
        await callback.answer("✅ SOS signali to'xtatildi.", show_alert=False)
    except Exception:
        pass


# ---------------------------------------------------------
# Comprehensive Interactive Help & Documentation Center
# ---------------------------------------------------------

def get_help_main_keyboard() -> InlineKeyboardMarkup:
    """Main categorized Help navigation keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Mobil Ilovalar (O'rnatish & Foniy GPS)",
                    callback_data="help_topic:apps"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Farzand & Ota-onani ulash tartibi",
                    callback_data="help_topic:pairing"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚨 SOS, Masofaviy Sirena & Aloqa",
                    callback_data="help_topic:safety"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡️ Kerakli joylar (Geozonalar) & Batareya",
                    callback_data="help_topic:zones"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Topshiriqlar & Tangalar tizimi",
                    callback_data="help_topic:tasks"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Web App (Sayt) & Kirish kodi",
                    callback_data="help_topic:webapp"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 Ilovani yuklab olish (.apk)",
                    url="https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk"
                )
            ]
        ]
    )

def get_help_back_keyboard() -> InlineKeyboardMarkup:
    """Back button to Help index."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Barcha mavzular (Yordam)",
                    callback_data="help_topic:main"
                )
            ]
        ]
    )

@router.message(F.text.in_(["ℹ️ Yordam", "Yordam", "yordam", "Qo'llanma", "qollanma"]))
@router.message(Command("help", "yordam", "faq", "info", "qollanma", "yoriqnoma"))
async def on_help_pressed(message: Message):
    """Display rich categorized Help menu."""
    text = (
        "ℹ️ **CHILD SECURITY — TO'LIQ QO'LLANMA & YORDAM**\n\n"
        "**Smart Bolalar Nazorati Tizimi** orqali farzandlaringiz xavfsizligini 24/7 nazorat qilishingiz, "
        "ularning joylashuvini jonli xaritada ko'rishingiz va foydali topshiriqlar bilan rag'batlantirishingiz mumkin.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌟 **Asosiy imkoniyatlar qisqacha:**\n"
        "• **📍 Jonli Xarita:** Farzandning hozirgi joylashuvini 1 tugmada ko'rish.\n"
        "• **📱 Foniy GPS:** Ekran o'chiq bo'lsa ham 24/7 joylashuv to'xtovsiz yuboriladi.\n"
        "• **🛡️ Xavfsiz Makonlar:** Maktab, uy, bog'chaga kirib-chiqqanini avtomatik bilish.\n"
        "• **🚨 SOS Signal:** Bola 1 bosishda ota-onaga xavf signali va koordinata yuboradi.\n"
        "• **🔔 Masofaviy Sirena:** Farzand telefonida ovozsiz rejimda ham baland ovoz chiqarish.\n"
        "• **📋 Topshiriqlar & Tangalar:** Vazifalar berish, fotosuratli hisobot va mukofotlar.\n"
        "• **🌐 Rasmiy Sayt (Vercel):** https://child-security-bot.vercel.app\n\n"
        "👇 **Batafsil ma'lumot olish uchun quyidagi bo'limlardan birini tanlang:**"
    )
    await message.answer(text, reply_markup=get_help_main_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("help_topic:"))
async def on_help_topic_callback(callback: CallbackQuery):
    """Handle interactive topic selection in Help menu."""
    topic = callback.data.split(":")[1]

    if topic == "main":
        text = (
            "ℹ️ **CHILD SECURITY — TO'LIQ QO'LLANMA & YORDAM**\n\n"
            "Qo'shimcha ma'lumot olish uchun quyidagi mavzulardan birini tanlang:"
        )
        await callback.message.edit_text(text, reply_markup=get_help_main_keyboard(), parse_mode="Markdown")
        await callback.answer()
        return

    elif topic == "apps":
        text = (
            "📱 **MOBIL ILOVALAR (ANDROID APK) QO'LLANMASI**\n\n"
            "Tizimda 2 ta alohida moslashtirilgan ilova mavjud:\n\n"
            "👨‍👩‍👧 **1. Ota-ona ilovasi (Parent App):**\n"
            "• O'zingizning telefoningizga o'rnatiladi.\n"
            "• Barcha farzandlaringizni jonli xaritada kuzatish.\n"
            "• Masofaviy sirena chalish va ovoz chiqarish.\n"
            "• Xavfsiz makonlar (geozonalar) qo'shish va boshqarish.\n"
            "• Farzandlarga vazifalar berish va hisobotlarini tasdiqlash.\n\n"
            "👦 **2. Farzand ilovasi (Child App):**\n"
            "• Farzandingizning telefoniga o'rnatiladi.\n"
            "• **24/7 Foniy GPS:** Telefon ekrani o'chirilgan, qulflangan yoki ilova yopilgan holatda ham GPS koordinatalarini to'xtovsiz ota-onaga yuboradi.\n"
            "• **Avtomatik ishga tushish:** Telefon o'chib-yonsa ham orqa fonda o'zi ishlab ketadi.\n"
            "• **🚨 SOS xavf tugmasi:** 1 bosishda barcha ota-onalarga baland signal yuborish.\n"
            "• **Missiyalar:** Vazifalarni bajarib, virtual tangalar yig'ish.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ **Muhim o'rnatish qoidasi:**\n"
            "Farzand telefoniga o'rnatgach, telefon sozlamalaridan:\n"
            "1. *«Joylashuvga doimiy ruxsat berish» (Allow all the time)*;\n"
            "2. *«Batareyani tejashni cheklamaslik» (No battery restrictions / Unrestricted)*\n"
            "ruxsatlarini bering. Shunda tizim hech qachon to'xtamaydi."
        )

    elif topic == "pairing":
        text = (
            "🔗 **ULANISH VA OILAVIY GURUH YARATISH TARTIBI**\n\n"
            "Tizimda hisoblarni bog'lashning 3 ta oson usuli bor:\n\n"
            "🔹 **1. Farzandni 6 xonali kod orqali ulash:**\n"
            "1️⃣ Ota-ona botida **«➕ Farzand ulash (Kodi)»** tugmasini bosing.\n"
            "2️⃣ Bot sizga 6 xonali ulanish kodini beradi (masalan: `482910`).\n"
            "3️⃣ Farzandingiz o'z telefonida botga kirib, «Farzand» rolini tanlaydi va shu kodni kiritadi.\n"
            "4️⃣ Ulanish darhol tasdiqlanadi va farzand xaritangizda paydo bo'ladi.\n\n"
            "🔹 **2. Taklif havolasi orqali (Tezkor 1-bosish):**\n"
            "1️⃣ Ota-ona botidagi «➕ Farzand ulash (Kodi)» bo'limidagi taklif havolasini Telegram orqali bolaga yuboring.\n"
            "2️⃣ Bola havolani bosishi bilan hisoblar avtomatik ulanadi.\n\n"
            "🔹 **3. Ikkinchi ota-onani (Hamkorni) taklif qilish:**\n"
            "• Ota va ona birgalikda farzandlarni kuzatishi uchun **«👨‍👩‍👧 Ikkinchi ota-onani taklif qilish»** tugmasini bosing va turmush o'rtog'ingizga havolani yuboring.\n"
            "• U havolani bosishi bilan barcha farzandlar unga ham avtomatik biriktiriladi va ikkalangiz ham to'liq monitoring qilasizlar."
        )

    elif topic == "safety":
        text = (
            "🚨 **SOS, MASOFAVIY SIRENA VA FAVQULODDA ALOQA**\n\n"
            "🛡️ **1. SOS (Xavf signali):**\n"
            "• Farzand har qanday xavfli yoki shubhali vaziyatda **«🚨 SOS (Xavf tugmasi)»** ni bosadi.\n"
            "• Barcha ota-onalarning telefoniga (bot, sayt va ilovada) darhol **qizil signal, jonli lokatsiya va ogohlantirish** yetib boradi.\n"
            "• Farzandning ekranida esa ota-onaga 1 bosishda qo'ng'iroq qilish tugmasi chiqadi.\n\n"
            "🔔 **2. Masofaviy Sirena (Qidiruv signali):**\n"
            "• Farzandingiz telefonini qayergadir qo'yib topolmayotgan bo'lsa yoki ovozsiz (bez zvuk) rejimda bo'lsa:\n"
            "• Ota-ona saytdan yoki botdan **«🔔 Sirena chalish»** tugmasini bosadi.\n"
            "• Farzandning telefonida (hatto ovozsiz rejimda ham) eng baland ovozda signal yangraydi va vibratsiya ishga tushadi.\n\n"
            "✅ **3. «Men yetib keldim» (Check-in):**\n"
            "• Farzand maktab, uy yoki manzilga eson-omon yetib borganda «✅ Yetib keldim» tugmasini bosadi va ota-onalarga darhol bildirishnoma boradi."
        )

    elif topic == "zones":
        text = (
            "🛡️ **KERAKLI JOYLAR (GEOZONALAR) VA BATAREYA NAZORATI**\n\n"
            "📍 **1. Kerakli joylar (Xavfsiz makonlar) nima?**\n"
            "• Bu — maktab, uy, buvisi, o'quv markazi kabi muhim hududlar.\n"
            "• Siz xaritada ushbu joylarni belgilab qo'yasiz (masalan: 200 metr radiusda).\n"
            "• Farzandingiz maktabga kirganida: *«✅ Farzandingiz Maktab hududiga yetib bordi»* deb xabar keladi.\n"
            "• Maktabdan chiqqanida: *«⚠️ Farzandingiz Maktab hududidan chiqdi»* deb avtomatik bildirishnoma olasiz.\n\n"
            "📍 **Joy qo'shish usullari:**\n"
            "1️⃣ Botda **«📍 Kerakli joylar»** ➡️ **«➕ Yangi joy qo'shish»** ni bosing.\n"
            "2️⃣ Yoki Telegram orqali istalgan lokatsiya nuqtasini chatga tashlang.\n"
            "3️⃣ Yoki Web App xaritasidan to'g'ridan-to'g'ri nuqta tanlang.\n\n"
            "🔋 **2. Batareya va Holat nazorati:**\n"
            "• Farzand telefonining quvvati **20% dan pastga tushsa**, bot sizga avtomatik eslatma yuboradi.\n"
            "• Shuningdek, telefon hozir quvvatlanayotgani yoki quvvatlanmayotgani, ovoz rejimi (ovozli / vibratsiya / ovozsiz) doim ko'rinib turadi.\n\n"
            "🚗 **3. Tezlik nazorati:**\n"
            "• Farzand tezligi 30 km/soat dan oshsa (transportda ketayotganida), bot ota-onaga tezlik va xarita havolasini yuboradi."
        )

    elif topic == "tasks":
        text = (
            "💰 **TOPSHIRIQLAR, MISSIYALAR VA VIRTUAL TANGALAR**\n\n"
            "Farzandni foydali ishlarga o'rgatish va rag'batlantirish tizimi:\n\n"
            "📋 **1. Topshiriq yaratish:**\n"
            "• Ota-ona bot yoki sayt orqali yangi vazifa qo'shadi (masalan: *«Kitob o'qish (30 bet)»*, *«Uy vazifasini bajarish»*, *«Xonani yig'ishtirish»*).\n"
            "• Vazifaga mukofot tangalari belgilanadi (masalan: 50 tanga).\n\n"
            "📸 **2. Farzandning hisobot topshirishi:**\n"
            "• Farzand vazifani bajargach, bot yoki ilova orqali **fotosurat bilan hisobot** yuboradi.\n\n"
            "✅ **3. Ota-onaning tasdiqlashi:**\n"
            "• Ota-ona botda kelgan fotosuratni ko'rib, **«✅ Tasdiqlash»** yoki **«❌ Qaytarish»** tugmasini bosadi.\n"
            "• Tasdiqlansa, tangalar farzandning virtual hamyoniga o'tadi.\n\n"
            "💱 **4. Tangalar kursi:**\n"
            "• Ota-ona **«💰 Tangalar kursini sozlash»** orqali 100 tanga necha so'mga teng bo'lishini (masalan: 100 tanga = 10,000 so'm) mustaqil belgilaydi.\n"
            "• Farzand yig'ilgan tangalarni orzu qilgan sovg'asiga almashtirishi mumkin!"
        )

    elif topic == "webapp":
        text = (
            "🌐 **WEB APP (SAYT) VA SHAXSIY KODLAR**\n\n"
            "🔗 **Rasmiy manzil:** https://child-security-bot.vercel.app\n\n"
            "🔑 **Saytga qanday kiriladi?**\n"
            "• Saytga kirish uchun murakkab login yoki parol eslab qolish shart emas!\n"
            "• Botda **«🔑 Saytga kirish kodi»** tugmasini bosing yoki `/sayt` deb yozing.\n"
            "• Bot sizga 6 xonali shaxsiy kod beradi.\n"
            "• Sayt ochilganda ushbu 6 xonali kodni kiritasiz — bo'ldi!\n\n"
            "🌟 **Saytdagi imkoniyatlar:**\n"
            "• Katta to'liq ekranli jonli xarita.\n"
            "• Yo'ldosh (Satellite) va ko'cha xaritalarini almashtirish.\n"
            "• Bugungi harakatlar tarixi (marshrut chizig'i va animatsion ko'rish).\n"
            "• Kunlik xavfsizlik hisoboti (qancha vaqt qayerda bo'lgani, bosib o'tgan masofasi).\n"
            "• 12 xil zamonaviy rang mavzulari (Kosmik, Neon, Qahramon, Zümrad...).\n"
            "• To'g'ridan-to'g'ri masofaviy sirena va joylashuv yangilash."
        )

    else:
        text = "Mavzu topilmadi."

    await callback.message.edit_text(text, reply_markup=get_help_back_keyboard(), parse_mode="Markdown")
    await callback.answer()



@router.message(Command("reset_all_data", "baza_tozalash", "restart_all"))
async def on_reset_all_data_command(message: Message, state: FSMContext, bot: Bot):
    """Wipes all data from database tables completely so everyone starts from scratch."""
    await state.clear()
    from db.database import engine, Base
    import db.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await message.answer(
        "🔄 **Server va Baza butunlay tozalandi!**\n\n"
        "Barcha eski foydalanuvchilar, bolalar va ota-onalar ulanishlari, joylar tarixi 100% o'chirildi.\n\n"
        "Endi hamma tizimni noldan boshlaydi. Iltimos, o'z rolingizni tanlang:",
        reply_markup=get_role_selection_keyboard(),
        parse_mode="Markdown"
    )


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


# ---------------------------------------------------------
# Web App (Sayt) Dedicated Opener & Link Handler
# ---------------------------------------------------------

@router.message(F.text.in_([
    "📱 Web App (Sayt)",
    "Web App (Sayt)",
    "Web App",
    "web app",
    "Sayt",
    "sayt",
    "🌐 Web App",
    "Saytga kirish",
    "saytga kirish"
]))
@router.message(Command("webapp", "sayt", "site", "web"))
async def on_webapp_requested(message: Message):
    """Handle Web App request and provide instant 1-tap open buttons and browser link."""
    user_id = message.from_user.id
    webapp_url = settings.RESOLVED_WEBAPP_URL

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        role = user.role if user else "parent"

        access_code = None
        if user:
            access_code = await crud.generate_web_access_code(session, user_id)
            await session.commit()

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌐 Web App (Sayt)ni ochish",
                        web_app=WebAppInfo(url=webapp_url)
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔗 Brauzerda ochish (Vercel)",
                        url=webapp_url
                    )
                ]
            ]
        )

        code_text = f"🔑 **Sizning saytga kirish kodingiz:** ` {access_code} `\n\n*(Sayt ochilganda ushbu 6 xonali kodni kiriting)*\n\n" if access_code else ""

        reply_kb = get_parent_main_keyboard(user_id) if role == "parent" else get_child_main_keyboard()

        await message.answer(
            f"🌐 **Child Security — Rasmiy Web App (Sayt):**\n\n"
            f"🔗 **Rasmiy havola:** {webapp_url}\n\n"
            f"{code_text}"
            f"Saytni ochish uchun quyidagi tugmalardan birini tanlang:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await message.answer(
            "📋 **Klaviaturangiz yangilandi!** Pastdagi **«📱 Web App (Sayt)»** tugmasi orqali ham istalgan payt ochishingiz mumkin.",
            reply_markup=reply_kb
        )


# =====================================================================
# 📱 MOBILE APPS (ANDROID & IOS)
# =====================================================================

@router.message(StateFilter("*"), F.text.in_([
    "📱 Telefon uchun ilova (Android & iOS)",
    "📱 Telefon uchun ilova",
    "📱 Telefon uchun",
    "Telefon uchun ilova",
    "Telefon uchun",
    "telefon uchun",
    "telefon ilova",
    "telefon",
    "📱 Mobil ilova (Android & iPhone)",
    "📱 Mobil ilova",
    "Mobil ilova",
    "📥 Ilovani yuklab olish",
    "📥 Ilovani yuklab olish (APK)",
    "Ilovani yuklab olish"
]))
@router.message(StateFilter("*"), Command("phone", "mobile", "app", "telefon"))
@router.callback_query(F.data == "download_phone")
async def on_download_phone(event, bot: Bot, state: FSMContext = None):
    """Show interactive phone OS choice menu (Android vs Apple iOS)."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("📱 Telefon menyusi...")

    text = (
        "📱 <b>Child Security — Telefonlar uchun Mobil Ilova</b>\n\n"
        "Qaysi turdagi telefondan foydalanasiz? Operatsion tizimni tanlang:\n\n"
        "🤖 <b>Android</b> — Samsung, Xiaomi, Redmi, Honor, Huawei va boshqalar (.apk)\n"
        "🍎 <b>Apple iPhone (iOS)</b> — iPhone va iPad uchun (Safari PWA & Profil)\n\n"
        "👇 <i>O'rnatish yo'riqnomasi va faylni olish uchun telefoningiz turini bosing:</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤖 Android (Samsung / Xiaomi / Honor / Huawei)",
                    callback_data="download_android"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍎 Apple iPhone (iOS / iPad)",
                    callback_data="download_ios"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Brauzerda ochish (Web App)",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL)
                )
            ],
            [
                InlineKeyboardButton(
                    text="💻 Noutbuk uchun dastur (PC / Mac / Linux)",
                    callback_data="download_desktop"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.message(StateFilter("*"), F.text.in_([
    "🤖 Android ilovasi (APK)",
    "🤖 Android ilovasi",
    "Android ilovasi (APK)",
    "Android ilovasi",
    "Android ilova",
    "Android",
    "android",
    "apk",
    "APK",
    "👨‍👩‍👧 Ota-ona ilovasi (APK)",
    "👨‍👩‍👧 Ota-ona ilovasi",
    "Ota-ona ilovasi (APK)",
    "Ota-ona ilovasi",
    "👦 Farzand ilovasi (APK)",
    "👦 Farzand ilovasi",
    "Farzand ilovasi (APK)",
    "Farzand ilovasi"
]))
@router.message(StateFilter("*"), Command("android", "apk", "parent_app", "child_app"))
@router.callback_query(F.data.in_(["download_android", "download_android_apk", "download_parent_apk", "download_child_apk", "download_apk_direct"]))
async def on_download_android(event, bot: Bot, state: FSMContext = None):
    """Deliver universal Android APK and 0-to-100 step-by-step installation tutorial."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("🤖 Android ilovasi tayyorlanmoqda...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"
    direct_apk_url = f"{webapp_url}/download/child-apk"

    text = (
        "🤖 <b>Child Security — Android uchun Rasmiy Mobil Ilova (v1.0.0)</b>\n\n"
        "💡 <b>DIQQAT:</b> Ota-ona va Farzand rejimlari <b>bitta universal ilovaning ichida</b> birlashtirilgan! "
        "Ilovani o'rnatgach, telefon kimniki ekanligiga qarab 1 ta bosishda <b>«👨‍👩‍👧 Ota-ona»</b> yoki <b>«👦 Farzand»</b> rejimini tanlaysiz.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>0-DAN BOSHLAB O'RNATISH BO'YICHA 1-GA-1 QADAM-BA-QADAM YO'RIQNOMA:</b>\n\n"
        "1️⃣ <b>1-QADAM: Faylni yuklab oling</b>\n"
        "Pastda chatga yuborilgan <code>ChildSecurity.apk</code> faylini ustiga bosing va yuklab oling.\n\n"
        "2️⃣ <b>2-QADAM: Noma'lum manbalar ruxsati (Agar telefon so'rasa)</b>\n"
        "Telefoningiz <i>«Xavfsizlik maqsadida ushbu manbadan ilova o'rnatish bloklandi»</i> desa:\n"
        "👉 <b>«Sozlamalar» (Настройки)</b> tugmasini bosing ➔ <b>«Ushbu manbaga ruxsat berish» (Разрешить установку)</b> ni yoqing va orqaga qaytib <b>«O'rnatish» (Установить)</b> ni bosing.\n\n"
        "3️⃣ <b>3-QADAM: Google Play Protect ogohlantirishi (Agar chiqsa)</b>\n"
        "Google Play Protect <i>«Noma'lum noshir / Xavfli bo'lishi mumkin»</i> deb ko'rsatsa:\n"
        "👉 Pastdagi <b>«Batafsil» (Подробнее)</b> yozuvini bosing ➔ <b>«Baribir o'rnatish» (Все равно установить)</b> tugmasini bosing.\n"
        "<i>(Bu ilova Play Market do'konidan tashqaridan o'rnatilayotgani uchun Android tizimining standart ogohlantirishidir, ilova 100% xavfsiz!)</i>\n\n"
        "4️⃣ <b>4-QADAM: Ilovani oching va Rolni tanlang</b>\n"
        "O'rnatish tugagach, ilovani oching:\n"
        "• O'zingizning telefoningizda: <b>«👨‍👩‍👧 Ota-ona»</b> tugmasini bosing (barcha farzandlarni xaritada nazorat qilish uchun).\n"
        "• Farzandingizning telefonida: <b>«👦 Farzand»</b> tugmasini bosing (monitoring va xavfsizlik uchun).\n\n"
        "5️⃣ <b>5-QADAM: Farzand telefonida 24/7 doimiy GPS ruxsatini bering (ENG MUHIM!)</b>\n"
        "Telefon ekrani o'chiq va qulflangan paytda ham joylashuv uzluksiz borishi uchun:\n"
        "• <b>Joylashuv (Lokatsiya):</b> «Har doim ruxsat berish» (Разрешать в любом режиме / Allow all the time) ni tanlang.\n"
        "• <b>Batareya sarfi:</b> «Cheklovsiz» (Без ограничений / No restrictions) qiling.\n"
        "• <b>Avto-ishga tushish:</b> Telefon o'chib-yonsa ham orqa fonda o'zi ishlashiga ruxsat bering.\n\n"
        "6️⃣ <b>6-QADAM: Farzandni ota-onaga ulang</b>\n"
        "Farzand telefonida chiqqan 6 xonali kodni ota-ona ilovasida «Farzand ulash» bo'limiga kiriting. Tamom, 24/7 himoya yoqildi! 🛡️✨"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 To'g'ridan-to'g'ri yuklab olish (.apk)",
                    url=direct_apk_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍎 iPhone / iOS qo'llanmasi",
                    callback_data="download_ios"
                ),
                InlineKeyboardButton(
                    text="💻 Noutbuk dasturi",
                    callback_data="download_desktop"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Web App Saytini ochish",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL)
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Telefon menyusiga qaytish",
                    callback_data="download_phone"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    apk_caption = (
        "🤖 <b>Child Security — Android Ilovasi (v1.0.0)</b>\n\n"
        "1️⃣ Ushbu fayl ustiga bosing va o'rnating.\n"
        "2️⃣ Ilovani ochib, <b>«Ota-ona»</b> yoki <b>«Farzand»</b> rejimini tanlang!\n"
        "3️⃣ Farzand telefonida GPS'ga «Har doim ruxsat berish»ni unutmang! 🛡️✨"
    )

    sent = False
    try:
        await message.answer_document(
            document=TG_FILE_ID_ANDROID_APK,
            caption=apk_caption,
            parse_mode=ParseMode.HTML
        )
        sent = True
    except Exception as e_fid:
        logger.warning(f"Could not send android APK by file_id: {e_fid}")
        try:
            await message.answer_document(
                document=TG_FILE_ID_CHILD_APK,
                caption=apk_caption,
                parse_mode=ParseMode.HTML
            )
            sent = True
        except Exception:
            pass

    if not sent:
        local_apk = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
        if os.path.exists(local_apk):
            try:
                from aiogram.types import FSInputFile
                await message.answer_document(
                    document=FSInputFile(local_apk, filename="ChildSecurity.apk"),
                    caption=apk_caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not send local APK: {e}")


@router.message(StateFilter("*"), F.text.in_([
    "🍎 iPhone / iOS (O'rnatish)",
    "🍎 iPhone / iOS",
    "iPhone / iOS",
    "iPhone ilovasi",
    "Apple ilovasi",
    "iOS ilovasi",
    "iPhone",
    "iphone",
    "apple",
    "ios"
]))
@router.message(StateFilter("*"), Command("ios", "iphone", "apple"))
@router.callback_query(F.data == "download_ios")
async def on_download_ios(event, bot: Bot, state: FSMContext = None):
    """Provide 0-to-100 step-by-step Apple iOS (iPhone/iPad) installation guide."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("🍎 iPhone qo'llanmasi tayyorlanmoqda...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"
    ios_profile_url = f"{webapp_url}/download/ios-profile"

    text = (
        "🍎 <b>Child Security — Apple iPhone va iPad uchun Rasmiy Ilova</b>\n\n"
        "Apple iOS tizimida ilovani 2 xil qulay va tezkor usulda to'liq mustaqil ilova qilib o'rnatishingiz mumkin:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📲 <b>1-USUL: Safari PWA orqali 1-bosishda o'rnatish (TAVSIYA ETILADI):</b>\n\n"
        "1️⃣ iPhone'ingizda Safari brauzerini oching va saytga kiring:\n"
        "👉 <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n\n"
        "2️⃣ Ekranning eng pastki qismidagi <b>«Ulashish»</b> belgisini bosing:\n"
        "👉 <b>(⬆️ kvadrat ichidagi yuqoriga qaragan o'qcha / Поделиться / Share)</b>.\n\n"
        "3️⃣ Chiqqan menyuni biroz pastga suring va tanlang:\n"
        "👉 <b>«Asosiy ekranga qo'shish» (➕ На экран «Домой» / Add to Home Screen)</b>.\n\n"
        "4️⃣ Yuqori o'ng burchakdagi <b>«Qo'shish» (Добавить / Add)</b> tugmasini bosing.\n\n"
        "✨ <b>NATIJA:</b> iPhone ish stolingizda xuddi App Store'dan yuklangandek rasmiy <b>Child Security</b> ilovasi paydo bo'ladi va brauzer satrisiz to'liq ekranda ishga tushadi! 🚀\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>2-USUL: Apple Konfiguratsiya Profili (.mobileconfig):</b>\n\n"
        "1️⃣ Pastdagi <code>ChildSecurity.mobileconfig</code> faylini bosing yoki quyidagi tugmani bosing.\n"
        "2️⃣ Safari <i>«Konfiguratsiya profilini yuklamoqchi, ruxsat berasizmi?»</i> desa:\n"
        "👉 <b>«Ruxsat berish» (Разрешить / Allow)</b> ni bosing.\n"
        "3️⃣ iPhone <b>«Sozlamalar» (Настройки)</b> ilovasiga kiring:\n"
        "👉 Eng tepada <b>«Profil yuklandi» (Профиль загружен / Profile Downloaded)</b> yozuvi chiqadi, uni bosing.\n"
        "4️⃣ Yuqori o'ng burchakdagi <b>«O'rnatish» (Установить / Install)</b> tugmasini bosing va parolingizni tasdiqlang.\n"
        "5️⃣ Tamom! Ilova iPhone ekraningizga alohida ilova bo'lib o'rnatildi! 🛡️📱"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 1-bosishda iPhone'ga o'rnatish (.mobileconfig)",
                    url=ios_profile_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Safari'da Web App'ni ochish",
                    url=webapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Android ilovasi (APK)",
                    callback_data="download_android"
                ),
                InlineKeyboardButton(
                    text="💻 Noutbuk dasturi",
                    callback_data="download_desktop"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Telefon menyusiga qaytish",
                    callback_data="download_phone"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    ios_caption = (
        "🍎 <b>Child Security — Apple iPhone O'rnatuvchi Fayli (.mobileconfig)</b>\n\n"
        "1️⃣ Faylni yuklab oling va oching («Ruxsat berish» / Allow).\n"
        "2️⃣ iPhone <b>«Sozlamalar» (Настройки)</b> ➔ <b>«Profil yuklandi» (Профиль загружен)</b> ga kiring.\n"
        "3️⃣ <b>«O'rnatish» (Установить)</b> tugmasini bosing.\n\n"
        "✨ Ilova iPhone ekraningizga alohida ilova bo'lib o'rnatildi! 🛡️📱"
    )

    try:
        await message.answer_document(
            document=TG_FILE_ID_IOS_PROFILE,
            caption=ios_caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e_fid:
        logger.warning(f"Could not send iOS mobileconfig by file_id: {e_fid}")
        profile_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.mobileconfig")
        if os.path.exists(profile_path):
            try:
                from aiogram.types import FSInputFile
                await message.answer_document(
                    document=FSInputFile(profile_path, filename="ChildSecurity.mobileconfig"),
                    caption=ios_caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not send iOS mobileconfig document: {e}")


@router.message(StateFilter("*"), F.text.in_([
    "💻 Noutbuk uchun dastur",
    "💻 Noutbuk uchun versiya",
    "💻 Noutbuk dasturi",
    "Noutbuk uchun dastur",
    "noutbuk uchun dastur",
    "Noutbuk versiya",
    "noutbuk versiya",
    "Kompyuter versiya",
    "kompyuter versiya",
    "💻 Kompyuter / Noutbuk",
    "💻 Noutbuk",
    "noutbuk",
    "kompyuter",
    "desktop",
    "laptop"
]))
@router.message(StateFilter("*"), Command("desktop", "laptop", "noutbuk", "kompyuter", "pc"))
@router.callback_query(F.data == "download_desktop")
async def on_download_desktop(event, bot: Bot, state: FSMContext = None):
    """Show interactive 3-OS selection menu for Windows, Mac, and Linux."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("💻 Noutbuk menyusi...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"

    text = (
        "💻 <b>Child Security — Noutbuk va Kompyuterlar uchun</b>\n\n"
        "Qaysi operatsion tizimdan foydalanasiz? Noutbukingiz turini tanlang:\n\n"
        "🪟 <b>Windows</b> — Windows 11, 10, 8, 7 uchun (.bat & .zip)\n"
        "🍏 <b>Apple Mac</b> — MacBook, iMac, Mac mini uchun (.zip)\n"
        "🐧 <b>Linux</b> — Ubuntu, Debian, Fedora, Mint uchun (.zip & .sh)\n\n"
        "👇 <i>Kerakli tizim tugmasini bosing:</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪟 Windows (11 / 10 / 8 / 7)",
                    callback_data="desktop_os_windows"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍏 Apple Mac (macOS / MacBook)",
                    callback_data="desktop_os_mac"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🐧 Linux (Ubuntu / Mint / Fedora)",
                    callback_data="desktop_os_linux"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Brauzerda ochish (PWA)",
                    url=webapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="👨‍👩‍👧 Ota-ona APK",
                    callback_data="download_parent_apk"
                ),
                InlineKeyboardButton(
                    text="🍎 iPhone / iOS",
                    callback_data="download_ios"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.message(StateFilter("*"), F.text.in_([
    "🪟 Windows",
    "Windows",
    "windows",
    "windovs",
    "vindovs"
]))
@router.message(StateFilter("*"), Command("windows", "windovs"))
@router.callback_query(F.data == "desktop_os_windows")
async def on_download_windows(event, bot: Bot, state: FSMContext = None):
    """Deliver Windows desktop installer (.bat) and ZIP package (.url)."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("🪟 Windows fayllari tayyorlanmoqda...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"
    desktop_bat_url = f"{webapp_url}/download/desktop-windows"
    desktop_zip_url = f"{webapp_url}/download/desktop-windows-zip"

    text = (
        "🪟 <b>Child Security — Windows uchun Desktop Dasturi (Windows 11 / 10 / 8 / 7)</b>\n\n"
        "Windows tizimida dasturni 100% xatosiz ishga tushirishning qulay yo'llari:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>1-USUL: To'g'ridan-to'g'ri Saytni Ochish (100% Kafolatli)</b>\n"
        "Quyidagi <b>«Child Security.url»</b> fayli yoki sayt havolasi orqali dastur bir zumda ochiladi:\n"
        "👉 <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>2-USUL: Windows O'rnatgich Arxiv (.zip)</b>\n"
        "Quyidagi <code>ChildSecurity_Windows_Setup.zip</code> faylini yuklab oling va oching. "
        "Ichidagi <b>«Child Security.url»</b> yorlig'ini bosing — dastur darhol ishga tushadi!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>3-USUL: Avtomatik Ish Stoli O'rnatgichi (.bat)</b>\n"
        "<code>ChildSecurity_Desktop_Setup.bat</code> faylini ishga tushirsangiz, ish stolingizga avtomatik doimiy yorliq yaratiladi.\n"
        "⚠️ <i>Agar SmartScreen (ko'k oyna) chiqsa: «Подробнее» ➔ «Выполнить в любом случае» bosing!</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Windows O'rnatgich (.zip)",
                    url=desktop_zip_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Windows O'rnatgich (.bat)",
                    url=desktop_bat_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Dasturni brauzerda ochish",
                    url=webapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Boshqa tizimni tanlash (Mac / Linux)",
                    callback_data="download_desktop"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    # 1. Send Windows ZIP (Most reliable, no antivirus block)
    win_zip_caption = (
        "📦 <b>Child Security — Windows To'liq Paketi (.zip)</b>\n\n"
        "1️⃣ Arxivni yuklab oling va oching (Extract all).\n"
        "2️⃣ Ichidagi <b>Child Security.url</b> ni bosing — sayt bir zumda ochiladi!\n"
        "3️⃣ Yoki <b>ChildSecurity_Desktop_Setup.bat</b> orqali doimiy yorliq yarating. 🚀"
    )
    try:
        await message.answer_document(
            document=TG_FILE_ID_WINDOWS_ZIP,
            caption=win_zip_caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e_wz:
        logger.warning(f"Could not send Windows zip by file_id: {e_wz}")
        wz_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Windows_Setup.zip")
        if os.path.exists(wz_path):
            try:
                from aiogram.types import FSInputFile
                await message.answer_document(
                    document=FSInputFile(wz_path, filename="ChildSecurity_Windows_Setup.zip"),
                    caption=win_zip_caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not send Windows zip file: {e}")

    # 2. Send Windows BAT
    desktop_caption = (
        "⚙️ <b>Child Security — Windows Ish Stoli O'rnatgichi (.bat)</b>\n\n"
        "1️⃣ Ushbu faylni yuklab oling va ishga tushiring.\n"
        "2️⃣ Ish stolingizda (Desktop) <b>Child Security</b> paydo bo'ladi!\n"
        "⚠️ <i>Agar SmartScreen oynasi chiqsa: «Подробнее» ➔ «Выполнить в любом случае» bosing.</i>"
    )
    try:
        await message.answer_document(
            document=TG_FILE_ID_DESKTOP_BAT,
            caption=desktop_caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e_fid:
        logger.warning(f"Could not send desktop BAT by file_id: {e_fid}")


@router.message(StateFilter("*"), F.text.in_([
    "🍏 Apple Mac",
    "🍏 Mac",
    "MacBook",
    "macbook",
    "macos",
    "mac"
]))
@router.message(StateFilter("*"), Command("mac", "macbook", "macos"))
@router.callback_query(F.data.in_(["desktop_os_mac", "download_mac"]))
async def on_download_mac(event, bot: Bot, state: FSMContext = None):
    """Send dedicated Apple Mac (macOS / MacBook) setup file and Web App instructions."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("🍏 Mac versiyasi tayyorlanmoqda...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"
    desktop_mac_url = f"{webapp_url}/download/desktop-mac"

    text = (
        "🍏 <b>Child Security — Apple Mac (macOS / MacBook) Rasmiy Ilovasi</b>\n\n"
        "Apple kompyuterlarida (MacBook Air/Pro, iMac, Mac mini) Child Security'ni to'laqonli desktop dastur sifatida ishlatishingiz mumkin:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>1-USUL: Avtomatik O'rnatuvchi Paket (.zip)</b>\n"
        "1. Quyidagi <code>ChildSecurity_Mac_Setup.zip</code> arxivini yuklab oling va oching.\n"
        "2. Ichidagi <code>ChildSecurity_Mac_Setup.command</code> fayli ustiga 2 marta bosing.\n"
        "3. Ish stolingizda (Desktop) <b>Child Security</b> paydo bo'ladi va mustaqil dastur bo'lib ishga tushadi!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>2-USUL: Safari / Chrome orqali to'g'ridan-to'g'ri o'rnatish</b>\n"
        "1. Brauzerda oching: <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n"
        "2. <b>Safari'da:</b> Yuqori menyudan <i>«Fayl» (File)</i> ➔ <b>«Dock-ga qo'shish» (Add to Dock)</b> ni bosing.\n"
        "3. <b>Chrome / Edge'da:</b> Manzil satrining o'ng tomonidagi ⬇️ <b>«O'rnatish» (Install)</b> belgisini bosing!\n\n"
        "✨ <b>Natija:</b> Ilova Mac ilovalar ro'yxatiga (Launchpad) va Dock-ga alohida dastur bo'lib qo'shiladi! 🚀"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍏 Mac o'rnatgichini yuklab olish (.zip)",
                    url=desktop_mac_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Safari / Mac Web App'ni ochish",
                    url=webapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Boshqa tizimni tanlash (Windows / Linux)",
                    callback_data="download_desktop"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    mac_caption = (
        "🍏 <b>Child Security — Apple Mac (macOS / MacBook) O'rnatgichi</b>\n\n"
        "1️⃣ Ushbu <code>ChildSecurity_Mac_Setup.zip</code> arxivini yuklab oling va oching (unzip).\n"
        "2️⃣ Ichidagi <code>ChildSecurity_Mac_Setup.command</code> fayli ustiga 2 marta bosing.\n"
        "3️⃣ Mac ish stolingizda <b>Child Security</b> paydo bo'ladi va to'liq ekranda ishga tushadi! 🚀"
    )

    try:
        await message.answer_document(
            document=TG_FILE_ID_MAC_ZIP,
            caption=mac_caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e_mac:
        logger.warning(f"Could not send Mac setup zip by file_id: {e_mac}")
        mac_zip_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Mac_Setup.zip")
        if os.path.exists(mac_zip_path):
            try:
                from aiogram.types import FSInputFile
                await message.answer_document(
                    document=FSInputFile(mac_zip_path, filename="ChildSecurity_Mac_Setup.zip"),
                    caption=mac_caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not send Mac setup zip file: {e}")


@router.message(StateFilter("*"), F.text.in_([
    "🐧 Linux",
    "Linux",
    "linux",
    "ubuntu",
    "Ubuntu",
    "debian",
    "mint"
]))
@router.message(StateFilter("*"), Command("linux", "ubuntu"))
@router.callback_query(F.data.in_(["desktop_os_linux", "download_linux"]))
async def on_download_linux(event, bot: Bot, state: FSMContext = None):
    """Send dedicated Linux setup file and Web App instructions."""
    if state:
        await state.clear()
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer("🐧 Linux versiyasi tayyorlanmoqda...")

    webapp_url = settings.MINI_APP_URL or "https://child-security-bot.vercel.app"
    desktop_linux_url = f"{webapp_url}/download/desktop-linux"

    text = (
        "🐧 <b>Child Security — Linux uchun Rasmiy Dastur (Ubuntu / Debian / Fedora / Mint)</b>\n\n"
        "Linux tizimlarida Child Security'ni ish stoli ilovasi sifatida o'rnatish yo'llari:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>1-USUL: Avtomatik Linux O'rnatgichi (.zip & .sh)</b>\n"
        "1. Quyidagi <code>ChildSecurity_Linux_Setup.zip</code> arxivini yuklab oling va oching.\n"
        "2. Terminalda quyidagi buyruqni bering:\n"
        "   <code>chmod +x ChildSecurity_Linux_Setup.sh && ./ChildSecurity_Linux_Setup.sh</code>\n"
        "3. Ish stolingizda (Desktop) va Ilovalar menyusida <b>Child Security</b> paydo bo'ladi!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>2-USUL: Chrome / Chromium orqali 1-bosishda o'rnatish</b>\n"
        "1. Brauzerda oching: <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n"
        "2. Manzil satridagi ⬇️ <b>«O'rnatish» (Install App)</b> belgisini bosing!\n\n"
        "✨ <b>Natija:</b> Ilova mustaqil desktop oynada brauzersiz toza ochiladi! 🚀"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🐧 Linux o'rnatgichini yuklab olish (.zip)",
                    url=desktop_linux_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Linux Web App'ni ochish",
                    url=webapp_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Boshqa tizimni tanlash (Windows / Mac)",
                    callback_data="download_desktop"
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    linux_caption = (
        "🐧 <b>Child Security — Linux O'rnatgichi (.zip)</b>\n\n"
        "1️⃣ Arxivni oching va <code>ChildSecurity_Linux_Setup.sh</code> ni ishga tushiring.\n"
        "2️⃣ Ish stolingizda (Desktop) <b>Child Security</b> mustaqil ilovasi yaratiladi! 🚀"
    )

    try:
        await message.answer_document(
            document=TG_FILE_ID_LINUX_ZIP,
            caption=linux_caption,
            parse_mode=ParseMode.HTML
        )
    except Exception as e_lin:
        logger.warning(f"Could not send Linux setup zip by file_id: {e_lin}")
        lin_zip_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity_Linux_Setup.zip")
        if os.path.exists(lin_zip_path):
            try:
                from aiogram.types import FSInputFile
                await message.answer_document(
                    document=FSInputFile(lin_zip_path, filename="ChildSecurity_Linux_Setup.zip"),
                    caption=linux_caption,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not send Linux setup zip file: {e}")


@router.message(StateFilter("*"), F.text.in_([
    "📥 Ilovani yuklab olish",
    "📥 Ilovani yuklab olish (APK)",
    "📲 Ilovani yuklab olish",
    "Ilovani yuklab olish",
    "Ilovani yuklash",
    "📲 Mobil ilova (Android APK)",
    "📲 Mobil ilova",
    "📲 Mobil ilova (Android & iPhone)",
    "Mobil ilova",
    "Ilova",
    "ilova",
    "APK",
    "apk",
    "Android APK",
    "android apk",
    "ilova qani",
    "ilovani ber",
    "ilovani topolmadim",
    "app",
    "prilojenie"
]))
@router.message(StateFilter("*"), Command("app", "download", "apk", "ilova", "mobil", "programma"))
@router.message(StateFilter("*"), F.text.regexp(r"(?i).*(yuklab\s*olish|ilovani|mobil\s*ilova|apk|android).*"))
async def on_download_app_pressed(message: Message, bot: Bot, state: FSMContext = None):
    """Provide mobile application download links and send APK file directly into chat."""
    if state:
        await state.clear()
    webapp_url = settings.RESOLVED_WEBAPP_URL
    apk_download_url = f"{webapp_url}/download/apk"
    direct_github_apk = "https://github.com/f-1331/child-security-bot/releases/download/v1.0.0-apk/app-debug.apk"

    text = (
        "📲 <b>Child Security Mobil Ilovalari</b>\n\n"
        "Farzandingiz xavfsizligini ta'minlash va telefon ekrani o'chiq bo'lsa ham joylashuvini "
        "24/7 uzluksiz kuzatib borish uchun o'zingizga va farzandingizga mos versiyani tanlang:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 <b>Android imkoniyatlari:</b>\n"
        "• <b>24/7 Doimiy Foniy GPS:</b> Ekran o'chiq, qulflangan yoki ilova berkitilgan holatda ham GPS to'xtovsiz yuboriladi.\n"
        "• <b>Avtomatik qayta ishga tushish:</b> Telefon o'chib-yonsa ham orqa fonda o'zi ishlayveradi.\n"
        "• <b>Batareyani tejash:</b> Kam energiya sarflovchi aqlli sensorlar.\n\n"
        "🛡️ <b>Android'da o'rnatish:</b>\n"
        "1. «Sozlamalar» ➔ «Noma'lum manbalardan o'rnatishga ruxsat berish»ni yoqing.\n"
        "2. Agar Play Protect ogohlantirsa: «Batafsil» ➔ «Baribir o'rnatish»ni bosing.\n\n"
        "🍎 <b>Apple iPhone uchun:</b> Pastdagi «🍎 iPhone / iOS» tugmasini bosing."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👨‍👩‍👧 Ota-ona ilovasi (APK)",
                    callback_data="download_parent_apk"
                ),
                InlineKeyboardButton(
                    text="👦 Farzand ilovasi (APK)",
                    callback_data="download_child_apk"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍎 iPhone / iPad (iOS qo'llanma)",
                    callback_data="download_ios"
                ),
                InlineKeyboardButton(
                    text="💻 Noutbuk dasturi",
                    callback_data="download_desktop"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Web App Saytini ochish",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL)
                )
            ]
        ]
    )

    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    # Send the actual APK document directly into the Telegram chat!
    local_apk_path = os.path.join(settings.BASE_DIR, "downloads", "ChildSecurity.apk")
    debug_apk_path = os.path.join(settings.BASE_DIR, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    
    target_apk = None
    if os.path.exists(local_apk_path) and os.path.getsize(local_apk_path) > 1_000_000:
        target_apk = local_apk_path
    elif os.path.exists(debug_apk_path) and os.path.getsize(debug_apk_path) > 1_000_000:
        target_apk = debug_apk_path

    try:
        if target_apk:
            doc = FSInputFile(target_apk, filename="ChildSecurity.apk")
            await message.answer_document(
                document=doc,
                caption="📲 <b>Child Security Android APK (v1.0.0)</b>\n\n"
                        "O'rnatish uchun ushbu fayl ustiga bosing. Farzandingiz telefonida 24/7 foniy GPS to'xtovsiz ishlaydi! 🛡️✨",
                parse_mode=ParseMode.HTML
            )
        else:
            import httpx
            from aiogram.types import BufferedInputFile
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                resp = await client.get(direct_github_apk)
                if resp.status_code == 200 and len(resp.content) > 1_000_000:
                    os.makedirs(os.path.dirname(local_apk_path), exist_ok=True)
                    with open(local_apk_path, "wb") as f_out:
                        f_out.write(resp.content)
                    doc = BufferedInputFile(resp.content, filename="ChildSecurity.apk")
                    await message.answer_document(
                        document=doc,
                        caption=(
                            "📲 <b>Child Security Android APK (v1.0.0)</b>\n\n"
                            "⚠️ <b>O'RNATGANDAN SO'NG JUDA MUHIM QADAM:</b>\n"
                            "1️⃣ Ilovani oching.\n"
                            "2️⃣ Ilovaga kirib, <b>«Doimiy joylashuvga ruxsat berish»</b> (Har doim ruxsat berish / Allow all the time) tugmasini bosing!\n"
                            "3️⃣ Shunda ota-onangiz xaritada qayerdaligingizni ko'ra oladi! 🛡️✨"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await message.answer(
                        f"📲 <b>Child Security APK faylini yuklab olish manzili:</b>\n\n"
                        f"📥 Havola: <a href=\"{apk_download_url}\">{apk_download_url}</a>\n"
                        f"⚡ Zaxira havola: <a href=\"{direct_github_apk}\">Yuklab olish</a>",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
    except Exception as e:
        logger.warning(f"Could not send APK document directly: {e}")
        try:
            await message.answer(
                f"📥 <b>APK faylini brauzer orqali yuklab olish:</b>\n\n"
                f"Quyidagi havola orqali 1 bosishda yuklab oling:\n<a href=\"{apk_download_url}\">{apk_download_url}</a>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        except Exception:
            pass

@router.callback_query(F.data == "download_apk_direct")
async def on_download_apk_callback(callback: CallbackQuery, bot: Bot):
    """Handle 1-click APK download request from inline keyboard button."""
    await callback.answer("📲 APK fayl tayyorlanmoqda...")
    await on_download_app_pressed(callback.message, bot)



@router.message(F.text.in_([
    "🔄 Rolni o'zgartirish",
    "Rolni o'zgartirish",
    "🔄 Rolni almashtirish",
    "Rolni almashtirish"
]))
@router.message(Command("switch_role", "rol", "rolni_almashtirish", "rolni_ozgartirish"))
async def on_role_switch_pressed(message: Message, state: FSMContext, bot: Bot):
    """Handle role switch request directly from keyboard or slash command."""
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

        if user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if parents:
                parent_names = ", ".join([p.full_name or f"ID: {p.id}" for p in parents])
                text = (
                    f"🔒 **OTA-ONANGIZ TASDIG'I TALAB QILINADI!**\n\n"
                    f"👦 Siz hozirda **Farzand** hisobidasiz va **{parent_names}** hisobiga ulangansiz.\n\n"
                    f"Xavfsizlik qoidalariga ko'ra, siz rolingizni o'zboshimchalik bilan o'zgartira olmaysiz. "
                    f"Rolingizni o'zgartirish uchun ota-onangiz o'z Telegram akkauntidan ruxsat berishi shart!\n\n"
                    f"Ota-onangizga rolni almashtirish so'rovi yuborilsinmi?"
                )
                await message.answer(
                    text,
                    reply_markup=get_child_role_switch_request_keyboard(),
                    parse_mode="Markdown"
                )
                return
            else:
                text = (
                    f"⚠️ **Rolni almashtirish (Farzand rejimi)**\n\n"
                    f"Sizga hali hech qanday ota-ona ulanmagan. Rolingizni erkin almashtirishingiz mumkin.\n\n"
                    f"Davom ettirishni xohlaysizmi?"
                )
                await message.answer(
                    text,
                    reply_markup=get_logout_step1_keyboard(),
                    parse_mode="Markdown"
                )
                return

        elif user.role == "parent":
            children = await crud.get_children_for_parent(session, user_id)
            extra = f"\n\n⚠️ *Diqqat: Sizga {len(children)} nafar farzand ulangan. Chiqsangiz ulanishlar bekor qilinadi.*" if children else ""
            text = (
                f"👨‍👩‍👧 **Rolni almashtirish (Ota-ona rejimi)**\n\n"
                f"Ota-ona hisobidan chiqib, rolni o'zgartirmoqchimisiz?{extra}\n\n"
                f"Davom ettirish uchun 2-darajali xavfsizlik tasdiqlashidan o'ting:"
            )
            await message.answer(
                text,
                reply_markup=get_logout_step1_keyboard(),
                parse_mode="Markdown"
            )


@router.callback_query(F.data == "role_switch_start")
async def on_role_switch_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle role switch triggered from inline settings menu."""
    await state.clear()
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role == "unassigned":
            await callback.message.edit_text(
                "Siz hali biron-bir rol tanlamagansiz. Iltimos, o'z rolingizni tanlang:"
            )
            await callback.message.answer(
                "Rolni tanlang:",
                reply_markup=get_role_selection_keyboard()
            )
            await callback.answer()
            return

        if user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if parents:
                parent_names = ", ".join([p.full_name or f"ID: {p.id}" for p in parents])
                text = (
                    f"🔒 **OTA-ONANGIZ TASDIG'I TALAB QILINADI!**\n\n"
                    f"👦 Siz hozirda **Farzand** hisobidasiz va **{parent_names}** hisobiga ulangansiz.\n\n"
                    f"Xavfsizlik qoidalariga ko'ra, siz rolingizni o'zboshimchalik bilan o'zgartira olmaysiz. "
                    f"Rolingizni o'zgartirish uchun ota-onangiz o'z Telegram akkauntidan ruxsat berishi shart!\n\n"
                    f"Ota-onangizga rolni almashtirish so'rovi yuborilsinmi?"
                )
                await callback.message.edit_text(
                    text,
                    reply_markup=get_child_role_switch_request_keyboard(),
                    parse_mode="Markdown"
                )
                await callback.answer()
                return

    # If not a linked child, proceed to standard step 1
    await on_logout_start(callback, state)


@router.callback_query(F.data == "child_request_role_switch")
async def on_child_request_role_switch(callback: CallbackQuery, bot: Bot):
    """Child dispatches role switch request to linked parents."""
    child_id = callback.from_user.id
    child_name = callback.from_user.full_name

    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents:
            await callback.answer("Ulangan ota-ona topilmadi.", show_alert=True)
            return

        sent_count = 0
        for parent in parents:
            try:
                await bot.send_message(
                    chat_id=parent.id,
                    text=(
                        f"🔔 **DIQQAT: Farzandingiz rolni o'zgartirishni so'ramoqda!**\n\n"
                        f"👤 Farzand: **{child_name}** (`{child_id}`)\n\n"
                        f"Farzandingiz hisobdan chiqib, rolni o'zgartirmoqchi (masalan, noto'g'ri rolni tanlagan yoki yangilamoqchi).\n\n"
                        f"⚠️ *Agar ruxsat bersangiz, uning ulanishi sizdan uziladi va u qaytadan rol tanlay oladi.*\n\n"
                        f"Farzandingizga rolni almashtirishga ruxsat berasizmi?"
                    ),
                    reply_markup=get_parent_role_switch_approval_keyboard(child_id),
                    parse_mode="Markdown"
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to notify parent {parent.id} of role switch request: {e}")

    await callback.message.edit_text(
        "📨 **So'rov ota-onangizga yuborildi!**\n\n"
        "Ota-onangiz o'z Telegram akkauntida ruxsat berishi bilan sizga xabar keladi va yangi rol tanlashingiz mumkin bo'ladi.\n\n"
        "Ungacha hisobingiz xavfsiz holda saqlanadi.",
        parse_mode="Markdown"
    )
    await callback.answer("So'rov yuborildi!")


@router.callback_query(F.data.startswith("approve_role_switch:"))
async def on_parent_approve_role_switch(callback: CallbackQuery, bot: Bot):
    """Parent approves child's role switch request."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    parent_name = callback.from_user.full_name

    async with AsyncSessionLocal() as session:
        link = await crud.get_parent_child_link(session, parent_id, child_id)
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else f"ID: {child_id}"

        if not link and (not child or child.role != "child"):
            await callback.message.edit_text("ℹ️ Ushbu so'rov allaqachon bajarilgan yoki eskirgan.")
            await callback.answer()
            return

        # Reset child's role
        await crud.reset_user_role(session, child_id)
        await session.commit()

        # Update parent message
        await callback.message.edit_text(
            f"✅ **Farzandingiz ({child_name}) uchun rolni almashtirish tasdiqlandi.**\n\n"
            f"Uning hisobi sizdan uzildi va u o'z Telegramida yangi rolni tanlay oladi.",
            parse_mode="Markdown"
        )

        # Notify child
        try:
            await bot.send_message(
                chat_id=child_id,
                text=(
                    f"🎉 **Ota-onangiz ({parent_name}) rolni almashtirishingizga ruxsat berdi!**\n\n"
                    f"Sizning avvalgi rolingiz bekor qilindi.\n"
                    f"Endi pastdagi tugmalar orqali to'g'ri rolingizni tanlang:"
                ),
                reply_markup=get_role_selection_keyboard(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child {child_id} of approval: {e}")

    await callback.answer("Rolni almashtirish tasdiqlandi!")


@router.callback_query(F.data.startswith("reject_role_switch:"))
async def on_parent_reject_role_switch(callback: CallbackQuery, bot: Bot):
    """Parent rejects child's role switch request."""
    child_id = int(callback.data.split(":")[1])
    parent_name = callback.from_user.full_name

    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else f"ID: {child_id}"

    await callback.message.edit_text(
        f"❌ **Farzandingiz ({child_name}) ning rolni almashtirish so'rovi rad etildi.**\n"
        f"Farzandingiz o'z hisobida qoldi.",
        parse_mode="Markdown"
    )

    try:
        await bot.send_message(
            chat_id=child_id,
            text=(
                f"❌ **Ota-onangiz ({parent_name}) rolni almashtirish so'rovingizni rad etdi.**\n\n"
                f"Siz avvalgidek Farzand hisobida qoldingiz."
            ),
            reply_markup=get_child_main_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Failed to notify child of rejection: {e}")

    await callback.answer("So'rov rad etildi.")


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
    if not message.text:
        await message.answer("⚠️ Iltimos, tasdiqlash kodini matn ko'rinishida kiriting:")
        return

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


# ---------------------------------------------------------
# Web Access Code & Coin Rate Settings Handlers
# ---------------------------------------------------------

@router.message(F.text == "🔑 Saytga kirish kodi")
@router.callback_query(F.data == "show_web_access_code")
async def on_show_web_access_code(event: Message | CallbackQuery):
    """Provide user with their unique 6-digit access code for the Web App."""
    message = event if isinstance(event, Message) else event.message
    user_id = event.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role == "unassigned":
            await message.answer("Iltimos, avval /start orqali rolingizni tanlang.")
            if isinstance(event, CallbackQuery):
                await event.answer()
            return

        web_code = await crud.generate_web_access_code(session, user_id)
        await session.commit()

        if user.role == "parent":
            rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user_id)
            text = (
                f"🔑 **YAGONA OILA VA SAYT (WEB APP) KODINGIZ:**\n\n"
                f"🏷 Kodingiz: ` {web_code} `\n\n"
                f"🌐 **Sayt manzili:** {settings.WEBAPP_BASE_URL}\n\n"
                f"✨ **Bu bitta kod barcha farzandlaringiz uchun yagona va doimiydir!**\n"
                f"• Saytga ushbu kodni bir marta kiritsangiz, barcha ulangan farzandlaringiz bir vaqtda chiqadi.\n"
                f"• Akkauntdan chiqib boshqa bola kodini kiritish umuman shart emas!\n\n"
                f"👨‍👩‍👧 **Ota-ona imkoniyatlari:**\n"
                f"• Barcha farzandlaringiz joylashuvini jonli xaritada kuzatish\n"
                f"• Boladan bolaga bir bosish bilan osongina o'tish\n"
                f"• Yangi topshiriqlar yuklash (foto/video biriktirish bilan)\n"
                f"• Kerakli xavfsiz joylar (Geofence) belgilash\n"
                f"• Tangalar kursini sozlash (Hozir: {rate_coins:,} tanga = {rate_som:,} so'm)\n\n"
                f"📌 Saytimizga kirganda ushbu 6 xonali kodni kiriting!"
            )
        else:
            rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user_id)
            money_som = crud.calculate_som_from_coins(user.virtual_balance, rate_coins, rate_som)
            text = (
                f"🔑 **FARZANDNING SHAXSIY SAYT (WEB APP) KODINGIZ:**\n\n"
                f"🏷 Kodingiz: ` {web_code} `\n\n"
                f"🌐 **Sayt manzili:** {settings.WEBAPP_BASE_URL}\n\n"
                f"👦 **Farzand imkoniyatlari:**\n"
                f"• Saytga ushbu 6 xonali shaxsiy kodingiz bilan kirasiz\n"
                f"• O'zingizning topshiriqlaringiz, xazinangiz va sarguzasht profilingiz ochiladi\n"
                f"• 'Ko'rdim' va 'Shu vaqtda bajaraman' deb vaqt belgilash\n"
                f"• Topshiriqni bajarib rasm yuborish va tangalarga ega bo'lish\n"
                f"• Sizda hozir: **{user.virtual_balance:,} tanga** ({money_som:,} so'm) mavjud\n\n"
                f"📌 Saytimizga kirganda ushbu kodingizni kiriting!"
            )

        await message.answer(text, parse_mode="Markdown")
        if isinstance(event, CallbackQuery):
            await event.answer()


@router.message(F.text == "💰 Tangalar kursini sozlash")
@router.callback_query(F.data == "coin_rate_start")
async def on_coin_rate_settings_pressed(event: Message | CallbackQuery, state: FSMContext):
    """Parent opens coin exchange rate configuration with psychological recommendation."""
    message = event if isinstance(event, Message) else event.message
    user_id = event.from_user.id
    await state.clear()

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role != "parent":
            await message.answer("Ushbu bo'lim faqat ota-onalar uchun mo'ljallangan.")
            if isinstance(event, CallbackQuery):
                await event.answer()
            return

        rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user_id)

        text = (
            f"💰 **TANGALAR KURSINI SOZLASH (Rag'batlantirish tizimi)**\n\n"
            f"💡 **Tavsiya va Psixologik maslahat:**\n"
            f"Farzandingiz bajargan har bir foydali vazifasi (kitob o'qish, dars qilish, uyni tozalash) uchun "
            f"tangalar to'planadi. Ushbu tangalar real pulga aylanishi bolada mehnat qadrini, maqsad sari intilishni "
            f"va sog'lom moliyaviy savodxonlikni shakllantiradi.\n\n"
            f"📌 **Hozirgi belgilangan kurs:**\n"
            f"🪙 **{rate_coins:,} tanga = {rate_som:,} so'm**\n"
            f"(ya'ni 1 tanga = {rate_som / rate_coins:,.1f} so'm)\n\n"
            f"Quyidagi tayyor kurslardan birini tanlang yoki o'z qiymatingizni kiriting:"
        )

        await message.answer(
            text,
            reply_markup=get_coin_rate_preset_keyboard(),
            parse_mode="Markdown"
        )
        if isinstance(event, CallbackQuery):
            await event.answer()


@router.callback_query(F.data.startswith("set_coin_rate:"))
async def on_preset_coin_rate_selected(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    coins = int(parts[1])
    som = int(parts[2])
    parent_id = callback.from_user.id

    async with AsyncSessionLocal() as session:
        await crud.update_coin_rate(session, parent_id, coins, som)
        await session.commit()

        children = await crud.get_children_for_parent(session, parent_id)
        for ch in children:
            c_money = crud.calculate_som_from_coins(ch.virtual_balance, coins, som)
            try:
                await bot.send_message(
                    chat_id=ch.id,
                    text=(
                        f"📢 **Tangalar kursi yangilandi!**\n\n"
                        f"📌 Ota-onangiz yangi kursni belgiladi:\n"
                        f"💰 **{coins:,} tanga = {som:,} so'm**\n"
                        f"💵 Sizning hozirgi {ch.virtual_balance:,} tangangiz = **{c_money:,} so'm** bo'ldi!\n\n"
                        f"Vazifalarni faol bajarib, ko'proq pul jamg'arishingiz mumkin! 🚀"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify child {ch.id} of rate update: {e}")

    await callback.message.edit_text(
        f"✅ **Tangalar kursi muvaffaqiyatli saqlandi!**\n\n"
        f"🪙 **{coins:,} tanga = {som:,} so'm**\n"
        f"Ulangan barcha farzandlaringizga bu haqda bildirishnoma yuborildi.",
        parse_mode="Markdown"
    )
    await callback.answer("Kurs saqlandi!")


@router.callback_query(F.data == "custom_coin_rate")
async def on_custom_coin_rate_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CoinRateState.waiting_for_custom_rate)
    await callback.message.edit_text(
        "✏️ **O'zingiz xohlagan kursni kiriting:**\n\n"
        "1,000 tanga uchun necha so'm bermoqchisiz?\n"
        "Masalan: `15000` yoki `1000 15000` deb yozib yuboring:\n\n"
        "*(Bekor qilish uchun /cancel deb yozing)*",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(CoinRateState.waiting_for_custom_rate)
async def on_custom_coin_rate_entered(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        await message.answer("⚠️ Iltimos, kursni raqam ko'rinishida yozib yuboring (masalan: `15000`):")
        return

    text = message.text.strip()
    if text.lower() in ["/cancel", "bekor", "cancel"]:
        await state.clear()
        await message.answer("Tangalar kursini o'zgartirish bekor qilindi.")
        return

    tokens = [t for t in text.replace(",", " ").replace("=", " ").split() if t.isdigit()]
    if len(tokens) == 1:
        coins = 1000
        som = int(tokens[0])
    elif len(tokens) >= 2:
        coins = int(tokens[0])
        som = int(tokens[1])
    else:
        await message.answer("❌ Noto'g'ri format. Iltimos, faqat raqam kiriting (masalan: `15000`):")
        return

    if coins <= 0 or som <= 0:
        await message.answer("❌ Qiymatlar musbat son bo'lishi kerak. Qaytadan kiriting:")
        return

    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        await crud.update_coin_rate(session, parent_id, coins, som)
        await session.commit()
        await state.clear()

        children = await crud.get_children_for_parent(session, parent_id)
        for ch in children:
            c_money = crud.calculate_som_from_coins(ch.virtual_balance, coins, som)
            try:
                await bot.send_message(
                    chat_id=ch.id,
                    text=(
                        f"📢 **Tangalar kursi yangilandi!**\n\n"
                        f"📌 Ota-onangiz yangi kursni belgiladi:\n"
                        f"💰 **{coins:,} tanga = {som:,} so'm**\n"
                        f"💵 Sizning hozirgi {ch.virtual_balance:,} tangangiz = **{c_money:,} so'm** bo'ldi!\n\n"
                        f"Vazifalarni faol bajarib, ko'proq pul jamg'arishingiz mumkin! 🚀"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to notify child {ch.id} of custom rate update: {e}")

    await message.answer(
        f"✅ **Yangi kurs saqlandi!**\n\n"
        f"🪙 **{coins:,} tanga = {som:,} so'm**\n"
        f"Farzandlaringiz hisobida ushbu kurs bo'yicha hisob-kitob qilinadi.",
        parse_mode="Markdown"
    )


# ---------------------------------------------------------
# Ota-onani taklif qilish (Farzand -> Ota-ona) &
# Hamkor ota-onani taklif qilish (Ona <-> Ota) Handlers
# ---------------------------------------------------------

@router.message(F.text == "👨‍👩‍👧 Ota-onamni taklif qilish")
async def on_child_invite_parent(message: Message, bot: Bot):
    """Child invites father/mother to monitor their safety."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role != "child":
            await message.answer("Ushbu tugma farzandlar hisobi uchun mo'ljallangan.")
            return

        bot_info = await bot.get_me()
        invite_link = f"https://t.me/{bot_info.username}?start=link_child_{user_id}"
        web_code = await crud.generate_web_access_code(session, user_id)
        await session.commit()

        share_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Ota-onamga yuborish (Telegram)",
                    url=f"https://t.me/share/url?url={invite_link}&text=Dada/Oyi,%20men%20Child%20Security%20botiga%20ulanyapman.%20Xavfsizligimni%20kuzatib%20turishingiz%20uchun%20ushbu%20havolani%20bosing!"
                )
            ]
        ])

        await message.answer(
            f"👨‍👩‍👧 **OTA-ONANGIZNI TAKLIF QILISH**\n\n"
            f"Ota-onangiz sizning xavfsizligingizni kuzatib turishi va sizga topshiriqlar berishi uchun quyidagi taklif havolasini ularga yuboring:\n\n"
            f"🔗 **Tezkor ulanish havolasi:**\n{invite_link}\n\n"
            f"🔑 **Sizning shaxsiy sayt kodingiz:** ` {web_code} `\n\n"
            f"*(Ota-onangiz ushbu havolani bosishi bilanoq bot orqali sizga avtomatik ulanadi!)*",
            reply_markup=share_kb,
            parse_mode="Markdown"
        )


@router.message(F.text == "👨‍👩‍👧 Ikkinchi ota-onani taklif qilish")
async def on_parent_invite_coparent(message: Message, bot: Bot):
    """Parent invites spouse (second parent: Mother <-> Father) to co-monitor all children."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role != "parent":
            await message.answer("Ushbu tugma faqat ota-onalar uchun mo'ljallangan.")
            return

        bot_info = await bot.get_me()
        invite_link = f"https://t.me/{bot_info.username}?start=coparent_{user_id}"
        children = await crud.get_children_for_parent(session, user_id)

        share_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Turmush o'rtog'imga yuborish (Telegram)",
                    url=f"https://t.me/share/url?url={invite_link}&text=Assalomu%20alaykum!%20Farzandlarimizni%20birgalikda%20kuzatishimiz%20uchun%20ushbu%20oilaviy%20havolani%20bosing."
                )
            ]
        ])

        await message.answer(
            f"👨‍👩‍👧 **IKKINCHI OTA-ONANI (HAMKORNI) TAKLIF QILISH**\n\n"
            f"Turmush o'rtog'ingiz (dada yoki oyi) ham farzandlaringizni birgalikda kuzatishi uchun ushbu taklif havolasini yuboring:\n\n"
            f"🔗 **Hamkor ota-ona havolasi:**\n{invite_link}\n\n"
            f"👶 **Biriktiriladigan farzandlar soni:** {len(children)} nafar\n\n"
            f"*(Turmush o'rtog'ingiz ushbu havolani bosishi bilan oilangizdagi barcha bolalar unga ham avtomatik biriktiriladi va ikkalangiz ham to'liq monitoring qila olasizlar)*",
            reply_markup=share_kb,
            parse_mode="Markdown"
        )


@router.message(Command("clean"))
@router.message(Command("tozala"))
@router.message(F.text.lower().in_(["🧹 chatni tozalash", "chatni tozalash", "tozalash", "/clean", "/tozala"]))
async def handle_clean_chat_command(message: Message, bot: Bot):
    """Purge up to 50 previous messages and ads from chat and deliver a fresh, clean menu."""
    from bot.anti_ad_middleware import purge_chat_history
    start_mid = message.message_id
    try:
        await message.delete()
    except Exception:
        pass

    await purge_chat_history(bot, message.chat.id, start_mid, limit=50)

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, message.from_user.id)
        role = user.role if user else "unassigned"
        if role == "parent":
            kb = get_parent_main_keyboard(message.from_user.id)
        elif role == "child":
            kb = get_child_main_keyboard()
        else:
            kb = get_role_selection_keyboard()

        await bot.send_message(
            chat_id=message.chat.id,
            text=(
                "🧹 **Chat to'liq tozalandi!**\n\n"
                "Barcha eski xabarlar, e'lonlar va nojo'ya qoldiqlar muvaffaqiyatli o'chirildi. "
                "Quyidagi xavfsiz boshqaruv menyusidan foydalanishingiz mumkin:"
            ),
            reply_markup=kb,
            parse_mode="Markdown"
        )



# ------------------------------------------------------------------------------
# SCREEN TIME, APP USAGE & MASTER APP LOCKDOWN HANDLERS (OTA-ONA UCHUN)
# ------------------------------------------------------------------------------

def generate_screen_time_card(child, report, policy, all_children=None):
    """Format rich screen time, top apps breakdown, network lockdown, and master lock controls for parent."""
    def progress_bar(pct, width=8):
        filled = int(round((pct / 100.0) * width))
        filled = max(0, min(width, filled))
        return "█" * filled + "░" * (width - filled)

    child_name = child.full_name or child.username or f"Farzand #{child.id}"
    total_time = report.get('total_screen_time_formatted', '0 daqiqa')
    total_sec = report.get('total_screen_time_seconds', 0)
    unlock_count = report.get('unlock_count', 0)

    if total_sec > 14400:
        time_badge = "⚠️ **Me'yordan ko'p (4s+)**"
    elif total_sec > 7200:
        time_badge = "🟡 **O'rtacha me'yorda**"
    else:
        time_badge = "🟢 **Me'yorda**"

    lock_status_text = "🛑 **BARCHA ILOVALAR BLOKLANGAN!**" if policy.is_all_apps_blocked else "🟢 **Ochiq (Cheklovsiz)**"
    net_status_text = "📴 **Cheklangan (Offline rejim / Firewall faol)**" if policy.is_network_restricted else "📶 **Ochiq (Normal)**"
    anti_un_status = "🔒 **Faol** _(Bola sozlamalardan ruxsatlarni o'chira olmaydi)_" if policy.anti_uninstall_enabled else "⚠️ O'chiq"
    bedtime_info = f"🌙 **Uyqu jadvali:** `{policy.bedtime_start} - {policy.bedtime_end}`\n" if (policy.bedtime_start and policy.bedtime_end) else ""

    apps_list = report.get("apps", [])
    grand_sec = max(1, sum(a.get("usage_seconds", 0) for a in apps_list))

    apps_lines = []
    if apps_list:
        for i, a in enumerate(apps_list[:6], 1):
            name = a.get("app_name", "Ilova")
            sec = a.get("usage_seconds", 0)
            h = sec // 3600
            m = (sec % 3600) // 60
            dur_str = f"{h}s {m}d" if h > 0 else f"{m}d"
            pct = int(round((sec / grand_sec) * 100))
            bar = progress_bar(pct, width=8)

            cat = a.get("category", "")
            icon = "🟥" if "youtube" in name.lower() else ("🎵" if "tiktok" in name.lower() else ("🎮" if cat == "game" or "pubg" in name.lower() or "roblox" in name.lower() else ("✈️" if "telegram" in name.lower() else "📱")))
            bl = " 🛑 _Bloklangan_" if a.get("is_blocked") else ""
            apps_lines.append(f"{i}. {icon} **{name}** — `{dur_str}` ({pct}%)\n   `[{bar}]`{bl}")
    else:
        apps_lines.append(
            "_Bugun hali ilovalar sarfi qayd etilmagan._\n\n"
            "💡 **Nega 0 daqiqa ko'rsatmoqda?**\n"
            "1. **Android ilova (APK):** Boshqa ilovalar sarfini (YouTube, TikTok, o'yinlar) aniqlash uchun bola telefoniga **Android APK** ilovamiz o'rnatilgan bo'lishi lozim (brauzerlar boshqa ilovalarni ko'ra olmaydi);\n"
            "2. **Maxsus ruxsat:** Farzand telefonida _Sozlamalar ➔ Maxsus kirish ➔ Foydalanish statistikasi (Usage Access)_ ruxsati yoqilgan bo'lishi shart."
        )

    apps_text = "\n".join(apps_lines)

    text = (
        f"📱 **FARZANDINGIZNING TELEFON ISHLATISH TAHLILI (BUGUN)**\n\n"
        f"👤 Farzand: **{child_name}**\n"
        f"⏱ Jami ekran vaqti: **{total_time}** ({time_badge})\n"
        f"🔓 Telefon ochilgan: **{unlock_count} marta**\n\n"
        f"📊 **Eng ko'p ishlatilgan ilovalar:**\n"
        f"{apps_text}\n\n"
        f"🛑 **Ilovalar holati:** {lock_status_text}\n"
        f"🌐 **Internet holati:** {net_status_text}\n"
        f"{bedtime_info}"
        f"🛡 **Sozlamalar himoyasi:** {anti_un_status}\n\n"
        f"ℹ️ _Quyidagi tugmalar orqali ilovalarni yoki internetni bir zumda bloklashingiz, internetsiz SMS buyruqlarni yuborishingiz mumkin:_"
    )

    buttons = []
    if all_children and len(all_children) > 1:
        child_row = []
        for c in all_children[:4]:
            is_cur = (c.id == child.id)
            c_name = c.full_name or f"Farzand #{c.id}"
            btn_txt = f"👉 {c_name}" if is_cur else f"👦 {c_name}"
            child_row.append(InlineKeyboardButton(text=btn_txt, callback_data=f"st_child:{c.id}"))
        buttons.append(child_row)

    if policy.is_all_apps_blocked:
        buttons.append([InlineKeyboardButton(text="✅ Barcha ilovalarni ochish (Ruxsat berish)", callback_data=f"unlock_all:{child.id}")])
    else:
        buttons.append([InlineKeyboardButton(text="🛑 Barcha ilovalarni bloklash (Dars / Uyqu)", callback_data=f"lock_all:{child.id}")])

    if policy.is_network_restricted:
        buttons.append([InlineKeyboardButton(text="🌐 Internetni Tiklash (Online)", callback_data=f"toggle_net_lock:{child.id}")])
    else:
        buttons.append([InlineKeyboardButton(text="📴 Internetni Cheklash (Offline)", callback_data=f"toggle_net_lock:{child.id}")])

    buttons.append([
        InlineKeyboardButton(text="✉️ Oflayn SMS Buyruqlar", callback_data=f"sms_controls:{child.id}"),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"st_refresh:{child.id}")
    ])
    buttons.append([InlineKeyboardButton(text="⚙️ Har bir ilovani alohida boshqarish", web_app=WebAppInfo(url=settings.MINI_APP_URL))])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("screentime", "ekran", "vaqt", "bloklash", "apps", "ilovalar"))
@router.message(F.text.in_({
    "📱 Ilovalar & Ekran tahlili",
    "Ilovalar & Ekran tahlili",
    "📱 Ekran vaqti va Bloklash",
    "Ekran vaqti va Bloklash",
    "📱 Ekran vaqti",
    "Ekran vaqti",
    "📱 Ilovalar nazorati",
    "Ilovalar nazorati",
    "Ilovalar",
    "ilovalar",
    "bloklash"
}))
async def parent_screen_time_handler(message: Message):
    """View child screen time, daily app usage stats, and master lock toggle."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or user.role != "parent":
            await message.answer("Ushbu bo'lim faqat ota-onalar uchun mo'ljallangan.")
            return

        children = await crud.get_children_for_parent(session, user_id)
        if not children:
            await message.answer(
                "👶 Sizga hali farzand ulanmagan.\n"
                "Iltimos, avval farzandingiz telefonida botga ulanish kodini kiriting.",
                reply_markup=get_parent_main_keyboard(user_id)
            )
            return

        child = children[0]
        report = await crud.get_app_usage_report_today(session, child.id)
        policy = await crud.get_or_create_device_policy(session, child.id)
        text, kb = generate_screen_time_card(child, report, policy, children)
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("st_child:"))
async def st_child_callback_handler(callback: CallbackQuery):
    """Switch child when parent clicks a child pill in screen time."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, callback.from_user.id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        target_child = next((c for c in children if c.id == child_id), None)
        if not target_child:
            target_child = await crud.get_user(session, child_id)
        if not target_child:
            await callback.answer("Farzand topilmadi.", show_alert=True)
            return

        report = await crud.get_app_usage_report_today(session, target_child.id)
        policy = await crud.get_or_create_device_policy(session, target_child.id)
        text, kb = generate_screen_time_card(target_child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        await callback.answer(f"👦 {target_child.full_name or 'Farzand'} statistikasi")


@router.callback_query(F.data.startswith("st_refresh:"))
async def st_refresh_callback_handler(callback: CallbackQuery):
    """Refresh child screen time data."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        target_child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        if not target_child:
            await callback.answer("Ma'lumot topilmadi.")
            return

        report = await crud.get_app_usage_report_today(session, target_child.id)
        policy = await crud.get_or_create_device_policy(session, target_child.id)
        text, kb = generate_screen_time_card(target_child, report, policy, children)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
            await callback.answer("🔄 Yangilandi!")
        except Exception:
            await callback.answer("O'zgarish yo'q.")

@router.callback_query(F.data.startswith("toggle_net_lock:"))
async def toggle_net_lock_callback_handler(callback: CallbackQuery):
    """Handle 1-tap toggle network lockdown (Local VPN Firewall) from Telegram."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        policy = await crud.get_or_create_device_policy(session, child_id)
        new_state = not bool(policy.is_network_restricted)
        updated_policy = await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_network_restricted=new_state
        )
        status_msg = "📴 Internet cheklandi (Offline rejim yoqildi)!" if new_state else "🌐 Internet cheklovi yechildi (Online tiklandi)!"
        await callback.answer(status_msg, show_alert=True)

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        text, kb = generate_screen_time_card(child, report, updated_policy, children)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass


@router.callback_query(F.data.startswith("sms_controls:"))
async def sms_controls_callback_handler(callback: CallbackQuery):
    """Display encrypted offline SMS fallback instructions and templates."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name or f"Farzand #{child_id}" if child else "Farzand"
        policy = await crud.get_or_create_device_policy(session, child_id)
        pin_hint = "1234 (standart)" if not policy.parent_pin_hash else "Siz o'rnatgan 4 xonali PIN"

        text = (
            f"✉️ **INTERNETSIZ (OFLAYN) SMS BUYRUQLAR**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Farzand: **{child_name}**\n"
            f"🔑 Xavfsizlik PIN-kodi: `{pin_hint}`\n\n"
            f"Agar bolaning telefonida **internet (Wi-Fi / Mobile Data) butunlay o'chirilgan** bo'lsa, "
            f"siz bolaning telefon raqamiga oddiy SMS yuborish orqali boshqarishingiz mumkin:\n\n"
            f"• `#LOCK:PIN` — Telefonni internetsiz darhol to'liq bloklash.\n"
            f"  *Misol:* `#LOCK:1234`\n\n"
            f"• `#UNLOCK:PIN` — Barcha cheklovlarni yechish va ochish.\n"
            f"  *Misol:* `#UNLOCK:1234`\n\n"
            f"• `#GPS:PIN` — Farzand telefonidan internetsiz aniq **GPS koordinatalari** va batareyasini so'rash. "
            f"Farzand telefoni darhol javob SMS orqali Google Maps havolasini qaytaradi!\n"
            f"  *Misol:* `#GPS:1234`\n\n"
            f"• `#SIREN:PIN` — Farzand telefonida qattiq signal (sirena) chalish.\n"
            f"  *Misol:* `#SIREN:1234`\n\n"
            f"ℹ️ _Farzand telefoni SMS ni olishi bilanoq (internetsiz) buyruqni bajaradi va ota-onaga hisobot beradi._"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Ekran tahliliga qaytish",
                        callback_data=f"st_child:{child_id}"
                    )
                ]
            ]
        )
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
            await callback.answer()
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("lock_all:"))
async def lock_all_callback_handler(callback: CallbackQuery):
    """Handle 1-tap master lock all apps from Telegram inline button."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_all_apps_blocked=True,
            lockdown_reason="Ota-ona Telegram orqali barcha ilovalarni blokladi"
        )
        await callback.answer("🛑 Farzand telefonida barcha ilovalar bloklandi!", show_alert=True)

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        policy = await crud.get_or_create_device_policy(session, child_id)
        text, kb = generate_screen_time_card(child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("unlock_all:"))
async def unlock_all_callback_handler(callback: CallbackQuery):
    """Handle 1-tap unblock all apps from Telegram inline button."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_all_apps_blocked=False
        )
        await callback.answer("✅ Barcha ilovalardan cheklov olib tashlandi!", show_alert=True)

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        policy = await crud.get_or_create_device_policy(session, child_id)
        text, kb = generate_screen_time_card(child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")



@router.message(F.text)
async def fallback_text_handler(message: Message):
    """Fallback handler for unrecognized text messages: automatically restores and refreshes the main keyboard."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or not user.role:
            await message.answer(
                "Iltimos, avval rolingizni tanlang:",
                reply_markup=get_role_selection_keyboard()
            )
            return

        if user.role == "parent":
            await message.answer(
                "📋 **Menyu yangilandi!**\n\n"
                "Quyidagi menyu tugmalaridan birini tanlang. Mobil ilovani yuklab olish uchun pastdagi **«📲 Mobil ilova (Android APK)»** tugmasini bosing:",
                reply_markup=get_parent_main_keyboard(user_id),
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "📋 **Menyu yangilandi!**\n\n"
                "Quyidagi xavfsizlik menyusi tugmalaridan birini tanlang:",
                reply_markup=get_child_main_keyboard(),
                parse_mode="Markdown"
            )
