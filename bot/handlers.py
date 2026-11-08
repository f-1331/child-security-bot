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
    ErrorEvent,
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


def clean_md(text: Optional[str]) -> str:
    """Escapes Telegram Markdown reserved characters (*, _, `, [) to prevent parsing crashes."""
    if not text:
        return ""
    return str(text).replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")


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
    get_empty_children_kb,
    get_school_mode_inline_keyboard,
    get_parent_directive_inline_keyboard,
    get_child_quick_signals_inline_keyboard,
    get_owner_panel_inline_keyboard,
    get_reset_options_keyboard,
    get_factory_reset_confirm_keyboard,
    get_ambient_sound_inline_keyboard,
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

class ChildPhoneSaveState(StatesGroup):
    waiting_for_child_phone = State()


class DirectMessagingState(StatesGroup):
    waiting_for_parent_reply = State()
    waiting_for_child_reply = State()


class OwnerActionState(StatesGroup):
    waiting_for_inspect_query = State()
    waiting_for_magic_id = State()
    waiting_for_add_admin_query = State()


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
                            reply_markup=get_child_main_keyboard(user_id),
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
                                        f"Ota-onangiz (**{clean_md(full_name)}**) taklifingiz orqali hisobingizga muvaffaqiyatli ulandi! 🛡️"
                                    ),
                                    reply_markup=get_child_main_keyboard(target_child_id),
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
                            "ℹ️ <b>Bu sizning ikkinchi ota-onani taklif qilish havolangiz!</b>\n\n"
                            "Turmush o'rtog'ingiz (dada yoki oyi) ham bolalarni birga kuzatishi uchun ushbu havolani unga yuboring.",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="HTML"
                        )
                        return

                    primary_parent = await crud.get_user(session, primary_parent_id)
                    co_parent_code = await crud.generate_pairing_code(session, user_id)
                    existing_children = await crud.get_children_for_parent(session, primary_parent_id)

                    # Qayta bosilganda qayta-qayta ulandi xabari bormasligi uchun tekshirish
                    current_children_ids = {c.id for c in await crud.get_children_for_parent(session, user_id)}
                    primary_children_ids = {c.id for c in existing_children}

                    safe_pname = html.escape(primary_parent.full_name) if primary_parent and primary_parent.full_name else "Ota-ona"

                    if primary_children_ids and primary_children_ids.issubset(current_children_ids):
                        child_names = ", ".join([html.escape(c.full_name or f"Farzand #{c.id}") for c in existing_children]) or "barcha farzandlar"
                        await message.answer(
                            f"ℹ️ <b>Siz allaqachon ushbu oila guruhiga ulangansiz!</b>\n\n"
                            f"👤 Hamkor ota-ona: <b>{safe_pname}</b>\n"
                            f"👶 Biriktirilgan farzandlar: <b>{child_names}</b>\n\n"
                            f"🔑 <b>Sizning shaxsiy sayt kodingiz:</b> <code>{co_parent_code}</code>\n"
                            f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}",
                            reply_markup=get_parent_main_keyboard(user_id),
                            parse_mode="HTML"
                        )
                        return

                    linked_children = await crud.link_co_parent(session, primary_parent_id, user_id)
                    await session.commit()

                    child_names = ", ".join([html.escape(c.full_name or f"Farzand #{c.id}") for c in linked_children]) or "barcha farzandlar"

                    await message.answer(
                        f"🎉 <b>Ajoyib! Siz oilaviy monitoring guruhiga muvaffaqiyatli qo'shildingiz!</b>\n\n"
                        f"👤 Hamkor ota-ona: <b>{safe_pname}</b>\n"
                        f"👶 Biriktirilgan farzandlar ({len(linked_children)} nafar): <b>{child_names}</b>\n\n"
                        f"🔑 <b>Sizning shaxsiy sayt kodingiz:</b> <code>{co_parent_code}</code>\n"
                        f"🌐 Sayt manzili: {settings.WEBAPP_BASE_URL}\n\n"
                        f"Endi siz ham barcha farzandlaringiz joylashuvini birgalikda kuzatishingiz va topshiriqlar berishingiz mumkin.",
                        reply_markup=get_parent_main_keyboard(user_id),
                        parse_mode="HTML"
                    )

                    if not user.phone_number:
                        await message.answer(
                            "⚠️ <b>Eslatma:</b> Farzandingiz SOS tugmasini bosganida sizga avtomatik qo'ng'iroq ketishi uchun "
                            "istalgan paytda pastdagi <b>«📞 Oila kontaktlari»</b> bo'limi orqali telefon raqamingizni kiritib qo'yishingiz mumkin.",
                            parse_mode="HTML"
                        )

                    try:
                        safe_full_name = html.escape(full_name or "Hamkor")
                        await bot.send_message(
                            chat_id=primary_parent_id,
                            text=(
                                f"🎉 <b>Oilangizga ikkinchi ota-ona qo'shildi!</b>\n\n"
                                f"👤 Hamkor: <b>{safe_full_name}</b>\n\n"
                                f"Endi ikkalangiz ham farzandlaringiz xavfsizligini birgalikda kuzatishingiz mumkin. 🛡️"
                            ),
                            reply_markup=get_parent_main_keyboard(primary_parent_id),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify primary parent: {e}")
                    return
                except Exception as e:
                    logger.error(f"Failed to process coparent invite: {e}")

            elif args.startswith("pair_") or (args.strip().isdigit() and 1 <= len(args.strip()) <= 16):
                pairing_code = args.replace("pair_", "").strip()
                parent = await crud.get_parent_by_pairing_code(session, pairing_code)
                if parent:
                    # O'zining kodi orqali o'ziga ulanishga urinishdan himoya
                    if parent.id == user_id:
                        await message.answer(
                            "ℹ️ <b>Bu sizning shaxsiy oilaviy kodingiz!</b>\n\n"
                            "Ushbu kodni farzandingiz telefonida botga kiritishingiz kerak.\n\n"
                            "💡 Agar hozircha ikkinchi telefoningiz bo'lmasa yoki tizim imkoniyatlarini darhol sinab ko'rmoqchi bo'lsangiz, "
                            "pastdagi <b>«🧪 Namuna farzand bilan sinash»</b> tugmasini bosing:",
                            reply_markup=get_empty_children_kb(),
                            parse_mode="HTML"
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
                            reply_markup=get_child_main_keyboard(user_id),
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
                            reply_markup=get_child_main_keyboard(user_id),
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

            safe_parent_fname = html.escape(full_name or "Ota-ona")
            phone_prompt = ""
            if not user.phone_number:
                phone_prompt = (
                    "\n\n⚠️ <b>Eslatma:</b> SOS xavf tugmasi bosilganda sizga avtomatik qo'ng'iroq ketishi uchun "
                    "pastdagi <b>«📞 Oila kontaktlari»</b> bo'limi orqali telefon raqamingizni ulab qo'yishingiz mumkin."
                )

            start_buttons = []
            demo_hint = ""
            if len(children) == 0:
                demo_hint = (
                    "\n\n💡 <b>Tezkor sinov:</b> Sizda hali ulangan farzand hisobi yo'q. "
                    "Tizimning barcha imkoniyatlarini (jonli xarita, ilovalar tahlili, batareya, topshiriqlar) "
                    "darhol sinab ko'rish uchun <b>«🧪 Namuna farzand bilan sinash»</b> tugmasini bosing!"
                )
                start_buttons.append([
                    InlineKeyboardButton(
                        text="🧪 Namuna farzand bilan sinash (1-bosish)",
                        callback_data="link_demo_child"
                    )
                ])
                start_buttons.append([
                    InlineKeyboardButton(
                        text="➕ Haqiqiy farzandni ulash (Kodi)",
                        callback_data="show_pairing_code_callback"
                    )
                ])

            start_buttons.extend([
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
            ])
            start_inline_kb = InlineKeyboardMarkup(inline_keyboard=start_buttons)
            await message.answer(
                f"Assalomu alaykum, <b>{safe_parent_fname}</b>!\n\n"
                f"👨‍👩‍👧 Siz <b>Ota-ona</b> rejimidasiz.\n"
                f"Ulangan farzandlar soni: <b>{len(children)} nafar</b>.\n\n"
                f"📍 Farzandingiz qayerdaligini bilish uchun pastdagi <b>«📍 Farzandning hozirgi joylashuvi»</b> tugmasini bosing.\n\n"
                f"📲 <b>Mobil ilovalar:</b>\n"
                f"• <b>👨‍👩‍👧 Ota-ona ilovasi:</b> O'z telefoningizdan farzandlarni jonli xaritada kuzatish, topshiriq berish va hisobotlarni ko'rish uchun.\n"
                f"• <b>👦 Farzand ilovasi:</b> Farzand telefonida ekran o'chiq bo'lsa ham 24/7 foniy GPS va SOS ishlashi uchun.{phone_prompt}{demo_hint}",
                reply_markup=get_parent_main_keyboard(user.id),
                parse_mode="HTML"
            )
            await message.answer(
                "⚡ <b>Tezkor kirish va Mobil ilova:</b>",
                reply_markup=start_inline_kb,
                parse_mode="HTML"
            )
            return

        elif user.role == "child":
            parents = await crud.get_parents_for_child(session, user.id)
            parent_names = ", ".join([html.escape(p.full_name or str(p.id)) for p in parents]) if parents else "Ulangan ota-ona yo'q"
            safe_child_name = html.escape(full_name or "Farzand")
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
                f"Salom, <b>{safe_child_name}</b>!\n\n"
                f"👦 Siz <b>Farzand</b> rejimidasiz.\n"
                f"Ulangan ota-ona: <b>{parent_names}</b>.\n\n"
                f"Har qanday favqulodda vaziyatda darhol <b>'🚨 SOS'</b> tugmasini bosing!\n\n"
                f"📲 <b>Child Security Android APK</b> ilovasini o'rnatish uchun pastdagi <b>«📲 Android APK Yuklab Olish»</b> tugmasini bosing.",
                reply_markup=get_child_main_keyboard(user_id),
                parse_mode="HTML"
            )
            await message.answer(
                "⚡ <b>Tezkor kirish va Mobil ilova:</b>",
                reply_markup=child_inline_kb,
                parse_mode="HTML"
            )
            return

        # If user has no role yet, ask for role selection
        safe_unassigned_name = html.escape(full_name or "Foydalanuvchi")
        await message.answer(
            f"Assalomu alaykum, <b>{safe_unassigned_name}</b>!\n\n"
            f"🌟 <b>Smart Bolalar Nazorati & Joylashuv Tizimi</b>ga xush kelibsiz!\n\n"
            f"Iltimos, tizimdan foydalanish uchun o'z rolingizni tanlang:",
            reply_markup=get_role_selection_keyboard(),
            parse_mode="HTML"
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
                reply_markup=get_child_main_keyboard(user_id),
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

    # If user pressed a known bottom menu button while waiting for code, release the state
    if message.text.startswith("/") or message.text in [
        "📍 Jonli joylashuvimni yuborish", "🚨 SOS (Xavf tugmasi)",
        "📱 Web App (Sayt)", "🤖 Android ilovasi (APK)", "🍎 iPhone / iOS (O'rnatish)",
        "⚙️ Sozlamalar / Chiqish", "🔄 Rolni o'zgartirish", "ℹ️ Yordam",
        "📱 Telefon uchun ilova (Android & iOS)", "💻 Noutbuk uchun dastur",
        "📞 Ota-onamga qo'ng'iroq", "🔑 Saytga kirish kodi", "📋 Vazifalarim va Tangalar"
    ]:
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        parent = await crud.get_parent_by_pairing_code(session, code)
        if not parent:
            await message.answer(
                "❌ <b>Kiritilgan kod noto'g'ri yoki mavjud emas!</b>\n\n"
                "Iltimos, ota-onangiz bergan 6 xonali ulanish kodini qayta tekshirib kiriting:",
                parse_mode="HTML"
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
                reply_markup=get_child_main_keyboard(user_id),
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
            reply_markup=get_child_main_keyboard(user_id),
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
                "📍 **Farzandning hozirgi joylashuvi:**\n\n"
                "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                "Jonli xarita va GPS kuzatuvini darhol sinab ko'rish uchun **Namuna farzand**ni ulang yoki haqiqiy farzand kodingizni oling:",
                reply_markup=get_empty_children_kb(),
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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
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

    now_utc = datetime.now(timezone.utc)
    is_offline = False
    if latest_loc and latest_loc.recorded_at:
        rec_time = latest_loc.recorded_at
        if rec_time.tzinfo is None:
            rec_time = rec_time.replace(tzinfo=timezone.utc)
        if (now_utc - rec_time) > timedelta(minutes=5):
            is_offline = True
    elif not latest_loc:
        is_offline = True

    if not latest_loc:
        safe_child_name = html.escape(child.full_name or "Farzand")
        phone = child.phone_number.strip() if child.phone_number else None
        policy = await crud.get_or_create_device_policy(session, child.id)
        pin_code = "1234" if not policy.parent_pin_hash else "PIN"

        sms_rows = []
        if phone:
            sms_rows.append([
                InlineKeyboardButton(text="✉️ 1-Tap SMS: Oflayn GPS so'rash", url=f"sms:{phone}?body=%23GPS:{pin_code}")
            ])
            sms_rows.append([
                InlineKeyboardButton(text=f"📱 Raqam: {phone} (O'zgartirish)", callback_data=f"set_child_phone:{child.id}")
            ])
        else:
            sms_rows.append([
                InlineKeyboardButton(text="📱 Farzand telefon raqamini kiritish", callback_data=f"set_child_phone:{child.id}")
            ])
            sms_rows.append([
                InlineKeyboardButton(text="✉️ Internetsiz SMS Buyruqlar", callback_data=f"sms_controls:{child.id}")
            ])
        sms_rows.append([
            InlineKeyboardButton(text="🔄 Qayta tekshirish", callback_data=f"locate_child:{child.id}")
        ])

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"📍 <b>{safe_child_name}</b> telefonidan hali internet orqali joylashuv kelmagan.\n\n"
                f"💡 <b>Eslatma:</b> Telefonning sun'iy yo'ldosh (GPS) moduli <b>internetsiz ham ishlayveradi</b>!\n"
                f"Agar farzandingiz telefonida internet (Wi-Fi yoki mobil internet) o'chirilgan bo'lsa, "
                f"quyidagi tugma orqali <b>1-bosishda SMS so'rov</b> yuborib, aniq joylashuvni darhol bilib oling."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=sms_rows),
            parse_mode="HTML"
        )

        # Alert the child via Telegram to share their live location
        try:
            await bot.send_message(
                chat_id=child.id,
                text=(
                    f"🔔 <b>Ota-onangiz joylashuvingizni so'ramoqda!</b>\n\n"
                    f"Iltimos, pastdagi <b>«📍 Jonli joylashuvimni yuborish»</b> tugmasini bosing yoki xaritani oching."
                ),
                reply_markup=get_child_main_keyboard(child.id),
                parse_mode="HTML"
            )
        except Exception as alert_err:
            logger.debug(f"Could not alert child via telegram: {alert_err}")
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

    # 1. Send native Telegram Map Location Pin (Live Radar if fresh < 5 min)!
    try:
        if not is_offline:
            try:
                await bot.send_location(
                    chat_id=chat_id,
                    latitude=latest_loc.latitude,
                    longitude=latest_loc.longitude,
                    live_period=1800
                )
            except Exception:
                await bot.send_location(
                    chat_id=chat_id,
                    latitude=latest_loc.latitude,
                    longitude=latest_loc.longitude
                )
        else:
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
        if is_offline:
            card_text += f"\n\n⚠️ <i>Diqqat: Telefon hozir oflayn bo'lishi mumkin (oxirgi signal {time_str} da kelgan). Sun'iy yo'ldosh GPS koordinatasini internetsiz olish uchun pastdagi 1-Tap SMS tugmasini bosing.</i>"

        await bot.send_message(
            chat_id=chat_id,
            text=card_text,
            reply_markup=get_location_actions_keyboard(latest_loc.latitude, latest_loc.longitude, child.id, is_offline=is_offline, child_phone=child.phone_number),
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
            await message.answer(
                "🔋 <b>Batareya va Telefon holati:</b>\n\n"
                "⚠️ Sizga hali hech qanday farzand ulanmagan.\n"
                "Telefon quvvati, quvvatlanish holati va ovoz profilini sinab ko'rish uchun <b>Namuna farzand</b>ni ulashingiz mumkin:",
                reply_markup=get_empty_children_kb(),
                parse_mode="HTML"
            )
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

        text = "🔋 <b>Farzandlar telefonining holati:</b>\n\n"

        for ch in children:
            safe_name = html.escape(ch.full_name or "Farzand")
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

                time_str = html.escape(format_tashkent_time(latest.recorded_at))

                text += (
                    f"👤 <b>{safe_name}</b>\n"
                    f"• Batareya: <b>{lvl}%</b> ({status_emoji})\n"
                    f"• Holat: {charging_icon}\n"
                    f"• Ovoz rejimi: <b>{sound_mode}</b>\n"
                    f"• Oxirgi signal: <b>{time_str}</b>\n\n"
                )
            else:
                text += f"👤 <b>{safe_name}</b> — Telemetriya ma'lumotlari hali olinmagan\n\n"

        refresh_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash (Signal yuborish)", callback_data="refresh_battery_status")]
        ])
        await message.answer(text, reply_markup=refresh_kb, parse_mode="HTML")


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

        text = "🔋 <b>Farzandlar telefonining holati (Yangilandi):</b>\n\n"

        for ch in children:
            safe_name = html.escape(ch.full_name or "Farzand")
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

                time_str = html.escape(format_tashkent_time(latest.recorded_at))

                text += (
                    f"👤 <b>{safe_name}</b>\n"
                    f"• Batareya: <b>{lvl}%</b> ({status_emoji})\n"
                    f"• Holat: {charging_icon}\n"
                    f"• Ovoz rejimi: <b>{sound_mode}</b>\n"
                    f"• Oxirgi signal: <b>{time_str}</b>\n\n"
                )
            else:
                text += f"👤 <b>{safe_name}</b> — Telemetriya ma'lumotlari hali olinmagan\n\n"

        refresh_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash (Signal yuborish)", callback_data="refresh_battery_status")]
        ])
        try:
            await callback.message.edit_text(text, reply_markup=refresh_kb, parse_mode="HTML")
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
            await message.answer(
                "📊 **Bugungi harakat va xavfsizlik hisoboti:**\n\n"
                "⚠️ Sizga hali hech qanday farzand ulanmagan.\n"
                "Kunlik bosib o'tilgan masofa, tashrif buyurilgan hududlar va xavfsizlik hisobotini sinash uchun **Namuna farzand**ni ulashingiz mumkin:",
                reply_markup=get_empty_children_kb(),
                parse_mode="Markdown"
            )
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
                    [
                        InlineKeyboardButton(
                            text="🗺 Bugungi harakat marshruti",
                            web_app=WebAppInfo(url=f"{settings.WEBAPP_BASE_URL}?role=parent&parent_id={parent_id}&child_id={ch.id}&show_route=1")
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔁 Qayta yurilgan joylar (Klaster)",
                            web_app=WebAppInfo(url=f"{settings.WEBAPP_BASE_URL}?role=parent&parent_id={parent_id}&child_id={ch.id}&show_repeats=1")
                        )
                    ],
                    [
                        InlineKeyboardButton(text="🔔 Baland signal", callback_data=f"siren_child_{ch.id}"),
                        InlineKeyboardButton(text="🎁 +100 Tanga", callback_data=f"night_reward_{ch.id}_100")
                    ],
                    [
                        InlineKeyboardButton(text="🛌 Tungi uyqu", callback_data=f"night_bedtime_{ch.id}"),
                        InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"refresh_report:{ch.id}")
                    ]
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

@router.callback_query(F.data.startswith("siren_child_"))
async def on_siren_child_callback(callback: CallbackQuery, bot: Bot):
    """Parent triggers remote siren / loud sound ping to child's device."""
    try:
        child_id = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        child = await crud.get_user(session, child_id)
        child_name = html.escape(child.full_name or "Farzand") if child else "Farzand"

    # Send telegram message to child
    try:
        await bot.send_message(
            chat_id=child_id,
            text="🔔 <b>DIQQAT: Ota-onangiz telefoningizga qidiruv/ogohlantirish signali yubordi!</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug(f"Failed to send siren telegram alert to child {child_id}: {e}")

    # Broadcast to WebApp / Mobile App via WebSocket
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "remote_siren",
            "message": "Ota-onangiz telefoningizga qidiruv signali yubordi!"
        })
    except Exception as e:
        logger.debug(f"WebSocket siren broadcast notice: {e}")

    await callback.answer(f"🔔 {child_name} telefoniga baland signal (sirena) yuborildi!", show_alert=True)


@router.callback_query(F.data.startswith("night_reward_"))
async def on_night_reward_callback(callback: CallbackQuery, bot: Bot):
    """Parent awards coins to child directly from nightly safety report."""
    parts = callback.data.split("_")
    try:
        child_id = int(parts[2])
        amount = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        await crud.add_coins_to_user(session, child_id, amount)
        child = await crud.get_user(session, child_id)
        child_name = html.escape(child.full_name or "Farzand") if child else "Farzand"
        new_balance = child.virtual_balance if child else 0

    # Notify child
    try:
        await bot.send_message(
            chat_id=child_id,
            text=(
                f"🌟 <b>TABRIKLAYMIZ! MUKOFOT TANGA!</b>\n\n"
                f"Ota-onangiz namunali intizomingiz va xavfsizligingiz uchun sizga <b>+{amount:,} tanga</b> mukofot berdi!\n"
                f"💰 Hozirgi balansingiz: <b>{new_balance:,} tanga</b> 🎉"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.debug(f"Failed to notify child {child_id} of reward: {e}")

    # Broadcast balance update to WebApp
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "balance_update",
            "balance": new_balance,
            "reward": amount
        })
    except Exception:
        pass

    await callback.answer(f"🎁 {child_name}ga +{amount} tanga mukofot berildi! Balans: {new_balance:,}", show_alert=True)


@router.callback_query(F.data.startswith("night_bedtime_"))
async def on_night_bedtime_callback(callback: CallbackQuery, bot: Bot):
    """Parent locks child device for bedtime until 07:00 AM."""
    try:
        child_id = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        policy = await crud.update_device_policy(
            session=session,
            child_id=child_id,
            parent_id=parent_id,
            is_all_apps_blocked=True,
            lockdown_reason="Tungi uyqu vaqti (07:00 gacha)",
            bedtime_start="22:00",
            bedtime_end="07:00"
        )
        child = await crud.get_user(session, child_id)
        child_name = html.escape(child.full_name or "Farzand") if child else "Farzand"

    # Notify child
    try:
        await bot.send_message(
            chat_id=child_id,
            text="🛌 <b>TUNGI UYQU REJIMI FAOL!</b>\n\nOta-onangiz telefoningizni soat 07:00 gacha tungi uyqu rejimiga oldi. Xayrli tun!",
            parse_mode="HTML"
        )
    except Exception:
        pass

    # Broadcast policy update to WebApp & Android
    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "device_policy_update",
            "is_all_apps_blocked": True,
            "lockdown_reason": "Tungi uyqu vaqti (07:00 gacha)",
            "bedtime_start": "22:00",
            "bedtime_end": "07:00"
        })
    except Exception:
        pass

    await callback.answer(f"🛌 {child_name} telefoni soat 07:00 gacha tungi uyqu rejimiga olindi!", show_alert=True)



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

        cur_spd = latest_loc.speed if (latest_loc and latest_loc.speed is not None) else 0.0
        if cur_spd >= 30.0:
            speed_info = f"🚗 **{cur_spd:.0f} km/soat** (Avtomobil / Transportda)"
        elif cur_spd >= 6.0:
            speed_info = f"🏃 **{cur_spd:.0f} km/soat** (Yugurmoqda)"
        elif cur_spd >= 1.5:
            speed_info = f"🚶 **{cur_spd:.0f} km/soat** (Piyoda yurmoqda)"
        else:
            speed_info = "🛑 **0 km/soat** (Harakatsiz / To'xtab turibdi)"

        parent_alert = (
            f"🚨🚨🚨 **FAVQULODDA VAZIYAT (SOS)!** 🚨🚨🚨\n\n"
            f"Farzandingiz **{child_name}** xavf tugmasini bosdi!\n\n"
            f"🚀 Hozirgi tezlik: {speed_info}\n"
            f"🕒 Vaqt: **{now_str}**\n"
            f"🔋 Batareya: **{battery_info}**\n"
            f"📍 Koordinata: `{loc_coords}`\n"
            f"🗺 Xaritada: [Google Maps orqali ochish]({maps_link})\n\n"
            f"⚠️ **DIQQAT:** Farzandingiz bilan zudlik bilan bog'laning!"
        )

        parent_phone = parents[0].phone_number if parents and parents[0].phone_number else None

        telemetry.last_sos_alert_map[child_id] = datetime.now(timezone.utc)
        from owner_bot import get_owner_bot
        ob = get_owner_bot()
        notify_owner_allowed = await crud.is_notify_owner_sos_enabled(session)
        sent_chats = set()

        from api.webapp_api import record_sos_message
        for parent in parents:
            target_chat_id = 6707839718 if parent.id in (1, 6707839718) else parent.id
            if target_chat_id in sent_chats:
                continue

            # Respect parent SOS notification setting (or owner SOS toggle if owner):
            if not await crud.is_parent_notification_enabled(parent.id, "sos", session):
                continue

            sent_chats.add(target_chat_id)
            try:
                sent_m = await bot.send_message(
                    chat_id=target_chat_id,
                    text=parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, latest_loc.latitude if latest_loc else None, latest_loc.longitude if latest_loc else None),
                    parse_mode="Markdown"
                )
                record_sos_message(child_id, bot, target_chat_id, sent_m.message_id)
                if latest_loc:
                    sent_loc = await bot.send_location(
                        chat_id=target_chat_id,
                        latitude=latest_loc.latitude,
                        longitude=latest_loc.longitude
                    )
                    record_sos_message(child_id, bot, target_chat_id, sent_loc.message_id)
            except Exception as e:
                logger.error(f"Failed to send SOS to parent {parent.id} (chat: {target_chat_id}): {e}")

        # Deliver to owner bot as well if owner SOS is enabled:
        if ob and notify_owner_allowed and 6707839718 not in sent_chats:
            try:
                sent_ob = await ob.send_message(
                    chat_id=6707839718,
                    text=f"👑 <b>[OWNER SOS OGOHLANTIRISHI]</b>\n\n" + parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, latest_loc.latitude if latest_loc else None, latest_loc.longitude if latest_loc else None),
                    parse_mode="Markdown"
                )
                record_sos_message(child_id, ob, 6707839718, sent_ob.message_id)
                if latest_loc:
                    sent_ob_loc = await ob.send_location(chat_id=6707839718, latitude=latest_loc.latitude, longitude=latest_loc.longitude)
                    record_sos_message(child_id, ob, 6707839718, sent_ob_loc.message_id)
            except Exception as e_ob:
                logger.warning(f"Failed to send SOS to owner bot: {e_ob}")

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
        from api.webapp_api import child_cancel_sos_endpoint, record_sos_message
        if callback.message:
            record_sos_message(child_id, bot, callback.message.chat.id, callback.message.message_id)
        await child_cancel_sos_endpoint(child_id, session)

    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.answer("✅ Xavfsizlik tiklandi, barcha xabarlar tozalandi.", show_alert=False)
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
    "Joylashuvni yuborish",
    "📍 Jonli joylashuv",
    "Jonli lokatsiya",
    "jonli lokatsiya"
]))
async def on_send_location_text_hint(message: Message, bot: Bot):
    """Guide child on how to share native location, or immediately dispatch child location if user is parent."""
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, message.from_user.id)
        if user and user.role == "parent":
            await on_request_current_location(message, bot)
            return

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

        # Forward location pin or live radar to parents in Telegram
        parents = await crud.get_parents_for_child(session, user_id)
        safe_child_name = html.escape(user.full_name or "Farzand")
        time_str = datetime.now(TASHKENT_TZ).strftime("%H:%M")

        # Debounce regular pin alerts to parents (every 20s per child) to avoid spamming on edits
        now_utc = datetime.now(timezone.utc)
        last_fwd = getattr(telemetry, "last_loc_forward_map", {}).get(user_id)
        should_forward_pin = (not last_fwd) or (now_utc - last_fwd) > timedelta(seconds=20) or live_period or user.is_sos_active

        if should_forward_pin and parents:
            if hasattr(telemetry, "last_loc_forward_map"):
                telemetry.last_loc_forward_map[user_id] = now_utc

            for p in parents:
                if not await crud.is_parent_notification_enabled(p.id, "messages", session):
                    continue
                try:
                    if live_period:
                        await bot.send_location(
                            chat_id=p.id,
                            latitude=lat,
                            longitude=lon,
                            live_period=live_period
                        )
                    else:
                        await bot.send_location(
                            chat_id=p.id,
                            latitude=lat,
                            longitude=lon
                        )
                        card_text = (
                            f"📍 <b>{safe_child_name}</b> jonli joylashuvini ulashdi!\n\n"
                            f"🕒 Vaqt: <b>{time_str}</b>\n"
                            f"📌 Koordinata: <code>{lat:.5f}, {lon:.5f}</code>\n\n"
                            f"🗺 Harakatni xaritada kuzatish uchun quyidagi tugmalardan foydalaning:"
                        )
                        await bot.send_message(
                            chat_id=p.id,
                            text=card_text,
                            reply_markup=get_location_actions_keyboard(lat, lon, user_id, is_offline=False, child_phone=user.phone_number),
                            parse_mode="HTML"
                        )
                except Exception as fwd_err:
                    logger.warning(f"Could not forward location to parent {p.id}: {fwd_err}")

        # If child in SOS, forward emergency location to parents with 60s cooldown
        if user.is_sos_active:
            last_loc_sent = telemetry.last_sos_alert_map.get(user_id)
            if not last_loc_sent or (now_utc - last_loc_sent) > timedelta(seconds=60):
                telemetry.last_sos_alert_map[user_id] = now_utc
                from api.webapp_api import record_sos_message
                for p in parents:
                    if not await crud.is_parent_notification_enabled(p.id, "sos", session):
                        continue
                    try:
                        sent_loc = await bot.send_location(chat_id=p.id, latitude=lat, longitude=lon)
                        record_sos_message(user_id, bot, p.id, sent_loc.message_id)
                    except Exception:
                        pass

        # Broadcast live telemetry to connected parents via WebSocket
        try:
            from api.webapp_api import ws_manager
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
# NEW FEATURES: School Mode, Directives & Quick Signals
# ---------------------------------------------------------

@router.message(F.text.startswith("📚 Maktab rejimi") | (F.text == "/school"))
async def on_parent_school_mode_command(message: Message, bot: Optional[Bot] = None):
    """Parent toggles school mode to block games and social media during class hours."""
    parent_id = message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            children = await crud.get_children_for_parent(session, parent_id)
            if not children and parent_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
                from db.models import User
                from sqlalchemy import select
                all_ch = (await session.execute(select(User).where(User.role == "child"))).scalars().all()
                children = list(all_ch)

            if not children:
                await message.answer(
                    "⚠️ Sizga hali hech qanday farzand biriktirilmagan.",
                    reply_markup=get_empty_children_kb(),
                    parse_mode="HTML"
                )
                return

            for ch in children:
                policy = await crud.get_or_create_device_policy(session, ch.id)
                c_name = html.escape(ch.full_name or f"Farzand #{ch.id}")
                status_desc = "🟢 <b>FAOL (Ilovalar cheklangan)</b>" if policy.is_all_apps_blocked else "⚪ <b>O'chiq (Cheklovlar yo'q)</b>"
                await message.answer(
                    f"📚 <b>MAKTAB REJIMI (Dars vaqti)</b>\n\n"
                    f"👤 Farzand: <b>{c_name}</b>\n"
                    f"📊 Joriy holat: {status_desc}\n\n"
                    f"Ushbu rejim yoqilganda farzand telefonidagi chalg'ituvchi o'yinlar va ijtimoiy tarmoqlar dars davomida avtomatik bloklanadi.",
                    reply_markup=get_school_mode_inline_keyboard(ch.id, policy.is_all_apps_blocked),
                    parse_mode="HTML"
                )
    except Exception as e:
        logger.error(f"Error in on_parent_school_mode_command: {e}", exc_info=True)
        await message.answer("⚠️ Maktab rejimini ochishda texnik xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")


@router.callback_query(F.data.startswith("toggle_school:"))
async def on_toggle_school_callback(callback: CallbackQuery, bot: Bot):
    """Handle 1-tap School Mode toggle callback."""
    parent_id = callback.from_user.id
    parts = callback.data.split(":")
    child_id = int(parts[1])
    enable = bool(int(parts[2]))

    async with AsyncSessionLocal() as session:
        is_owner = is_bot_owner(parent_id, callback.from_user.username) or (parent_id in (1, 6707839718, getattr(settings, "OWNER_ID", 1)))
        parents = await crud.get_parents_for_child(session, child_id)
        if not is_owner and not any(p.id == parent_id for p in parents):
            await callback.answer("❌ Siz bu farzandning ota-onasi emassiz!", show_alert=True)
            return

        policy = await crud.get_or_create_device_policy(session, child_id)
        policy.is_all_apps_blocked = enable
        policy.lockdown_reason = "Maktab dars vaqti 📚" if enable else "Dars vaqti"
        await session.commit()

        child_user = await crud.get_user(session, child_id)
        c_name = html.escape(child_user.full_name or f"Farzand #{child_id}")

        try:
            from api.webapp_api import ws_manager
            update_data = {
                "type": "device_policy_update",
                "child_id": child_id,
                "is_all_apps_blocked": enable,
                "lockdown_reason": policy.lockdown_reason,
                "school_mode": enable
            }
            await ws_manager.send_to_user(child_id, update_data)
            for p in parents:
                await ws_manager.send_to_user(p.id, update_data)
        except Exception:
            pass

        try:
            ch_msg = (
                "📚 <b>DIQQAT: Ota-onangiz Maktab Rejimini yoqdi!</b>\n\n"
                "Darslar davomida diqqatni jamlash uchun o'yinlar va tarmoqlar vaqtincha cheklandi."
                if enable else
                "✅ <b>Maktab Rejimi yakunlandi!</b>\n\nIlovalardan foydalanish cheklovlari olib tashlandi."
            )
            await bot.send_message(chat_id=child_id, text=ch_msg, parse_mode="HTML")
        except Exception:
            pass

        status_desc = "🟢 <b>FAOL (Ilovalar cheklangan)</b>" if enable else "⚪ <b>O'chiq (Cheklovlar yo'q)</b>"
        await callback.message.edit_text(
            f"📚 <b>MAKTAB REJIMI YANGILANDI!</b>\n\n"
            f"👤 Farzand: <b>{c_name}</b>\n"
            f"📊 Joriy holat: {status_desc}\n\n"
            f"O'zgarish farzand telefoniga darhol qo'llanildi.",
            reply_markup=get_school_mode_inline_keyboard(child_id, enable),
            parse_mode="HTML"
        )
    await callback.answer("Maktab rejimi muvaffaqiyatli o'zgartirildi.")


@router.message(F.text.startswith("💬 Farzandga ko'rsatma") | (F.text == "/ping"))
async def on_parent_directive_command(message: Message, bot: Bot):
    """Parent sends 1-tap preset guidance or cheer to child."""
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        if not children:
            await message.answer(
                "⚠️ Sizga hali hech qanday farzand biriktirilmagan.",
                reply_markup=get_empty_children_kb(),
                parse_mode="HTML"
            )
            return

        for ch in children:
            c_name = html.escape(ch.full_name or f"Farzand #{ch.id}")
            await message.answer(
                f"💬 <b>FARZANDGA TEZKOR KO'RSATMA YUBORISH</b>\n\n"
                f"👤 Farzand: <b>{c_name}</b>\n\n"
                f"Quyidagi tezkor ko'rsatmalardan birini tanlang. U farzand telefoniga darhol yetkaziladi 👇",
                reply_markup=get_parent_directive_inline_keyboard(ch.id),
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("p_dir:"))
async def on_parent_directive_callback(callback: CallbackQuery, bot: Bot):
    """Handle parental 1-tap directive delivery."""
    parent_id = callback.from_user.id
    parts = callback.data.split(":")
    child_id = int(parts[1])
    ping_type = parts[2]

    directives = {
        "return_home": ("🏠 Uyga qaytish vaqti bo'ldi!", "Kech bo'lib qoldi, sekin uyga qaytishni boshla. 🏡"),
        "call_me": ("📞 Menga qo'ng'iroq qil!", "Seni kutyapman, bo'shashing bilan menga telefon qil. 📲"),
        "take_medicine": ("🍱 Ovqatlanish va dori vaqti!", "Ovqatlanish yoki dorilaringni o'z vaqtida ichishni unutma! 💊"),
        "cheer": ("❤️ Seni yaxshi ko'ramiz!", "O'zingni ehtiyot qil, biz sen bilan faxrlanamiz! 🌟")
    }
    title, desc = directives.get(ping_type, ("💬 Ota-onangizdan xabar!", "Yangi xabarnoma"))

    async with AsyncSessionLocal() as session:
        is_owner = is_bot_owner(parent_id, callback.from_user.username) or (parent_id in (1, 6707839718, getattr(settings, "OWNER_ID", 1)))
        parents = await crud.get_parents_for_child(session, child_id)
        if not is_owner and not any(p.id == parent_id for p in parents):
            await callback.answer("❌ Siz bu farzandning ota-onasi emassiz!", show_alert=True)
            return

        parent_user = await crud.get_user(session, parent_id)
        safe_pname = html.escape(parent_user.full_name or "Ota-onangiz")

        now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")
        try:
            tg_text = (
                f"<b>{title}</b>\n\n"
                f"👤 Yuboruvchi: <b>{safe_pname}</b>\n"
                f"💬 Ko'rsatma: <i>{desc}</i>\n"
                f"🕒 Vaqt: <b>{now_time}</b>"
            )
            await bot.send_message(chat_id=child_id, text=tg_text, parse_mode="HTML")
        except Exception:
            pass

        try:
            from api.webapp_api import ws_manager
            await ws_manager.send_to_user(child_id, {
                "type": "parent_ping",
                "parent_id": parent_id,
                "parent_name": parent_user.full_name or "Ota-onangiz",
                "ping_type": ping_type,
                "title": title,
                "message": desc,
                "time": now_time
            })
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ <b>Ko'rsatma muvaffaqiyatli yetkazildi!</b>\n\n"
        f"📌 <b>{title}</b>\n"
        f"💬 <i>{desc}</i>\n\n"
        f"Farzandingiz telefoniga bildirishnoma bordi.",
        parse_mode="HTML"
    )
    await callback.answer("Ko'rsatma yetkazildi!")


@router.message(F.text.startswith("💬 Ota-onaga tezkor xabar") | (F.text == "/signals"))
async def on_child_quick_signals_command(message: Message):
    """Child sends 1-tap preset message to parents."""
    await message.answer(
        "💬 <b>OTA-ONANGIZGA TEZKOR XABAR YUBORISH</b>\n\n"
        "Matn yozib o'tirmasdan, quyidagi tayyor tugmalardan birini bosing va ota-onangizga darhol xabar yetkaziladi 👇",
        reply_markup=get_child_quick_signals_inline_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("c_sig:"))
async def on_child_quick_signal_callback(callback: CallbackQuery, bot: Bot):
    """Handle child 1-tap quick status callback."""
    child_id = callback.from_user.id
    child_name = html.escape(callback.from_user.full_name or "Farzand")
    signal_type = callback.data.split(":")[1]

    signals = {
        "school_arrived": ("🎒 Maktabga yetib keldim!", "Farzandingiz maktabga yetib bordi va darslarga tayyor. 📚"),
        "home_arrived": ("🏠 Uyga yetib keldim!", "Farzandingiz xavfsiz tarzda uyga yetib keldi. 🏡"),
        "pickup_me": ("🚗 Meni olib keting!", "Farzandingiz dars yoki mashg'ulotlari tugaganini va uni olib ketishingizni so'ramoqda. 🚘"),
        "training": ("⚽ Mashg'ulotdaman!", "Farzandingiz qo'shimcha to'garak / sport mashg'ulotida yoki repetitorda. 🏃‍♂️"),
        "low_battery": ("🪫 Telefonim quvvati oz qoldi!", "Farzandingiz telefonining quvvati tugayotgani haqida xabar berdi. 🔋"),
        "where_are_you": ("❓ Qayerdasiz?", "Farzandingiz: «Dada/Oyi, qayerdasiz? Qo'ng'iroq qiling» deb signal yubordi. 📞"),
    }
    title, desc = signals.get(signal_type, ("📢 Farzandingizdan tezkor signal!", "Yangi signal"))
    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")

    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)

        tg_msg = (
            f"<b>{title}</b>\n\n"
            f"👤 Farzand: <b>{child_name}</b>\n"
            f"💬 Xabar: <i>{desc}</i>\n"
            f"🕒 Vaqt: <b>{now_time}</b>\n"
        )
        if latest_loc and latest_loc.battery_level is not None:
            tg_msg += f"🔋 Batareya: <b>{latest_loc.battery_level}%</b>\n"
        if latest_loc and latest_loc.latitude and latest_loc.longitude:
            maps_url = f"https://www.google.com/maps/search/?api=1&query={latest_loc.latitude},{latest_loc.longitude}"
            tg_msg += f"📍 Joylashuv: <a href=\"{maps_url}\">Xaritada ochish</a>\n"

        sent_chats = set()
        for p in parents:
            target_chat_id = 6707839718 if p.id in (1, 6707839718) else p.id
            if target_chat_id in sent_chats:
                continue
            sent_chats.add(target_chat_id)
            try:
                await bot.send_message(chat_id=target_chat_id, text=tg_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to forward quick signal to parent {target_chat_id}: {e}")

        try:
            from api.webapp_api import ws_manager
            for p in parents:
                await ws_manager.send_to_user(p.id, {
                    "type": "quick_signal",
                    "child_id": child_id,
                    "child_name": callback.from_user.full_name or "Farzand",
                    "signal_type": signal_type,
                    "title": title,
                    "message": desc,
                    "time": now_time,
                    "battery_level": latest_loc.battery_level if latest_loc else None
                })
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ <b>Ota-onangizga xabar yetkazildi!</b>\n\n"
        f"📌 <b>{title}</b>\n"
        f"🕒 Yuborilgan vaqt: <b>{now_time}</b>",
        parse_mode="HTML"
    )
    await callback.answer("Xabar yetkazildi!")


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
            if not await crud.is_parent_notification_enabled(parent.id, "messages", session):
                continue
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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        task_preview = await crud.get_task_by_id(session, task_id)
        if not task_preview:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return
        parents = await crud.get_parents_for_child(session, task_preview.child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        task_preview = await crud.get_task_by_id(session, task_id)
        if not task_preview:
            await callback.answer("Vazifa topilmadi.", show_alert=True)
            return
        parents = await crud.get_parents_for_child(session, task_preview.child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

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
            await message.answer(
                "📋 **Vazifalar va Tangalar (Yosh Qahramon):**\n\n"
                "⚠️ Sizga hali hech qanday farzand ulanmagan.\n"
                "Topshiriq berish, tangalar mukofoti va bajarilgan ishlarni tasdiqlash tizimini sinash uchun **Namuna farzand**ni ulashingiz mumkin:",
                reply_markup=get_empty_children_kb(),
                parse_mode="Markdown"
            )
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
                "⚠️ <b>Sizga hali hech qanday farzand hisobi ulanmagan.</b>\n\n"
                "Tizim imkoniyatlarini darhol sinab ko'rish uchun <b>Namuna farzand</b>ni ulang yoki yangi farzand qo'shing:",
                get_empty_children_kb()
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

            is_demo = (ch.username == "azizbek_demo" or str(ch.id).startswith("900"))
            demo_badge = " <i>[Namuna]</i>" if is_demo else ""

            text += (
                f"👤 <b>{safe_name}</b>{demo_badge} ({safe_user})\n"
                f"• Holat: <b>{status}</b>\n"
                f"• Batareya: <b>{battery}</b> {charging}\n"
                f"• Balans: <b>{ch.virtual_balance:,} tanga</b>\n\n"
            )

            btn_title = (ch.full_name or "Farzand")[:24]
            row = [
                InlineKeyboardButton(
                    text=f"🔍 {btn_title} boshqaruvi",
                    callback_data=f"child_view:{ch.id}"
                )
            ]
            if is_demo:
                row.append(
                    InlineKeyboardButton(
                        text="🗑 Namunani o'chirish",
                        callback_data=f"unlink_demo_child:{ch.id}"
                    )
                )
            buttons.append(row)

        buttons.append([
            InlineKeyboardButton(
                text="➕ Yangi farzand ulash (Kodi)",
                callback_data="show_pairing_code_callback"
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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
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
        deep_link = f"https://t.me/{bot_info.username}?start=pair_{code}"
        safe_link = html.escape(deep_link)
        share_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📲 Farzandga yuborish (Telegram)",
                        url=f"https://t.me/share/url?url={deep_link}&text=Salom!+Ushbu+havola+orqali+Parental+Tracker+botiga+ulanishingiz+mumkin."
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🧪 Namuna farzand bilan sinash",
                        callback_data="link_demo_child"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Yangi kod yaratish",
                        callback_data="refresh_pairing_code"
                    )
                ]
            ]
        )
        await message.answer(
            f"🔑 <b>YAGONA OILA VA SAYT KODINGIZ:</b> <code>{code}</code>\n\n"
            f"🔗 <b>1-bosishda ulash havolasi:</b>\n"
            f"{safe_link}\n\n"
            f"👨‍👩‍👧 <b>Bu bitta kod barcha farzandlaringiz uchun umumiy va doimiydir!</b>\n\n"
            f"1️⃣ <b>Farzandlarni ulash:</b>\n"
            f"Ushbu havolani farzandingizga yuboring yoki uning telefonida botga kirib <code>{code}</code> kodini kiriting. Barcha farzandlaringiz bitta profilga ulanadi.\n\n"
            f"2️⃣ <b>Saytga (Web App) kirish:</b>\n"
            f"🌐 Manzil: {html.escape(settings.WEBAPP_BASE_URL)}\n"
            f"Saytga ham xuddi shu <code>{code}</code> kodini kiritasiz — barcha ulangan farzandlaringiz bir vaqtda chiqadi va bitta ekranda kuzata olasiz!\n\n"
            f"💡 <i>Tizimni ikkinchi telefonsiz hoziroq sinab ko'rmoqchi bo'lsangiz, quyidagi «🧪 Namuna farzand bilan sinash» tugmasini bosing:</i>",
            reply_markup=share_kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "refresh_pairing_code")
async def on_refresh_pairing_code(callback: CallbackQuery, bot: Bot):
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        new_code = await crud.generate_pairing_code(session, parent_id, force_new=True)
        await session.commit()

        bot_info = await bot.get_me()
        new_text = (
            f"🔑 <b>YANGILANGAN YAGONA OILA VA SAYT KODINGIZ:</b> <code>{new_code}</code>\n\n"
            f"👨‍👩‍👧 <b>Bu bitta kod barcha farzandlaringiz uchun umumiy va doimiydir!</b>\n\n"
            f"1️⃣ <b>Farzandlarni ulash:</b>\n"
            f"Farzandingiz botga kirib ushbu yangi <code>{new_code}</code> kodini kiritadi.\n\n"
            f"2️⃣ <b>Saytga (Web App) kirish:</b>\n"
            f"🌐 Manzil: {html.escape(settings.WEBAPP_BASE_URL)}\n"
            f"Saytga ham shu <code>{new_code}</code> kodini kiritib boshqarishingiz mumkin.\n\n"
            f"Ulashish uchun quyidagi tugmani bosing:"
        )
        try:
            await callback.message.edit_text(
                new_text,
                reply_markup=get_pairing_share_keyboard(bot_info.username, new_code),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                new_text,
                reply_markup=get_pairing_share_keyboard(bot_info.username, new_code),
                parse_mode="HTML"
            )
    await callback.answer("✅ Yangi ulanish kodi yaratildi!")


@router.callback_query(F.data == "link_demo_child")
async def on_link_demo_child(callback: CallbackQuery, bot: Bot):
    """Link a fully-featured Demo child (Azizbek) for instant parent trial."""
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parent = await crud.get_user(session, parent_id)
        if not parent or parent.role != "parent":
            await crud.update_user_role(session, parent_id, "parent")

        children = await crud.get_children_for_parent(session, parent_id)
        demo_id = 900000000 + (parent_id % 90000000)
        demo_child = next((c for c in children if c.id == demo_id or c.username == "azizbek_demo"), None)

        if not demo_child:
            demo_user, _ = await crud.get_or_create_user(
                session,
                user_id=demo_id,
                role="child",
                full_name="Azizbek (Namuna farzand)",
                username="azizbek_demo"
            )
            demo_user.virtual_balance = 0

            await crud.link_parent_child(session, parent_id, demo_id, nickname="Azizbek (Namuna)")

            now_utc = datetime.now(timezone.utc)
            t_morning1 = now_utc - timedelta(hours=3, minutes=30)
            t_morning2 = now_utc - timedelta(hours=2, minutes=45)
            t_school = now_utc - timedelta(hours=1, minutes=15)
            t_current = now_utc

            # Uy
            await crud.log_location(
                session,
                child_id=demo_id,
                latitude=41.311500,
                longitude=69.279000,
                accuracy=4.0,
                battery_level=95,
                is_charging=False,
                sound_mode="normal",
                speed=0.0,
                network_type="Wi-Fi",
                is_screen_on=False,
                address="Amir Temur shoh ko'chasi, Toshkent",
                recorded_at=t_morning1
            )

            # Yo'lda
            await crud.log_location(
                session,
                child_id=demo_id,
                latitude=41.308200,
                longitude=69.280500,
                accuracy=6.0,
                battery_level=92,
                is_charging=False,
                sound_mode="normal",
                speed=18.5,
                network_type="4G",
                is_screen_on=True,
                address="Shahrisabz ko'chasi, Toshkent",
                recorded_at=t_morning2
            )

            # 110-Maktab
            await crud.log_location(
                session,
                child_id=demo_id,
                latitude=41.305000,
                longitude=69.282000,
                accuracy=5.0,
                battery_level=89,
                is_charging=False,
                sound_mode="silent",
                speed=0.0,
                network_type="Wi-Fi",
                is_screen_on=False,
                address="Taras Shevchenko ko'chasi, 110-maktab, Toshkent",
                recorded_at=t_school
            )

            # Hozirgi joy (Amir Temur xiyoboni)
            await crud.log_location(
                session,
                child_id=demo_id,
                latitude=41.311151,
                longitude=69.279737,
                accuracy=4.5,
                battery_level=84,
                is_charging=False,
                sound_mode="normal",
                speed=0.0,
                network_type="4G",
                is_screen_on=True,
                address="Amir Temur xiyoboni, Toshkent",
                recorded_at=t_current
            )

            parent_fences = await crud.get_geofences_for_parent(session, parent_id)
            if not parent_fences:
                await crud.create_geofence(
                    session,
                    parent_id=parent_id,
                    name="Uy",
                    latitude=41.311500,
                    longitude=69.279000,
                    radius=200.0,
                    child_id=demo_id
                )
                await crud.create_geofence(
                    session,
                    parent_id=parent_id,
                    name="110-Maktab",
                    latitude=41.305000,
                    longitude=69.282000,
                    radius=250.0,
                    child_id=demo_id
                )

            await crud.create_task(
                session,
                parent_id=parent_id,
                child_id=demo_id,
                title="Matematika darslarini bajarish (5 ta misol)",
                reward_coins=500
            )
            t2 = await crud.create_task(
                session,
                parent_id=parent_id,
                child_id=demo_id,
                title="Ingliz tili lug'atini takrorlash",
                reward_coins=300
            )
            await crud.submit_task_completed(session, t2.id, note="Lug'at yodlandi!")

            await crud.update_child_wishlist(
                session,
                child_id=demo_id,
                title="Yangi velosiped",
                target_som=100000
            )

            sample_apps = [
                {"package": "com.google.android.youtube", "app_name": "YouTube", "seconds": 45 * 60, "category": "media"},
                {"package": "org.telegram.messenger", "app_name": "Telegram", "seconds": 35 * 60, "category": "social"},
                {"package": "com.duolingo", "app_name": "Duolingo", "seconds": 25 * 60, "category": "education"},
                {"package": "com.tencent.ig", "app_name": "PUBG Mobile", "seconds": 20 * 60, "category": "game"},
                {"package": "com.sec.android.app.popupcalculator", "app_name": "Kalkulyator", "seconds": 10 * 60, "category": "tools"},
            ]
            await crud.save_app_usage_report(
                session,
                child_id=demo_id,
                total_screen_time_seconds=135 * 60,
                unlock_count=24,
                apps=sample_apps
            )

            await crud.get_or_create_device_policy(session, demo_id)

            await session.commit()

        success_text = (
            "🎉 **Namuna farzand (Azizbek) muvaffaqiyatli ulandi!**\n\n"
            "Endi bot va saytning barcha imkoniyatlarini darhol sinab ko'rishingiz mumkin:\n"
            "• **📍 Farzandning hozirgi joylashuvi** — xarita nuqtasi va batareya (84%)\n"
            "• **📱 Ilovalar & Ekran tahlili** — qaysi dasturda qancha vaqt o'tirgani, bloklash\n"
            "• **🔋 Batareya va Holat** — telefon holati (84%, ovoz rejimi, 4G)\n"
            "• **📋 Vazifalar va Tangalar** — topshiriqlarni ko'rish va tasdiqlash\n"
            "• **📊 Bugungi hisobot** — bosib o'tilgan yo'l, hududlar va orzular qutisi\n"
            "• **👶 Farzandlarim** — profil boshqaruvi\n"
            "• **🗺️ Jonli Xarita (Web App)** — interaktiv veb-xaritada jonli kuzatuv!\n\n"
            "💡 _Haqiqiy farzandingizni ulaganingizda, ushbu namunani «Farzandlarim» bo'limidan bir bosishda o'chirib yuborishingiz mumkin._"
        )
        unlink_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📍 Azizbekning joylashuvini ko'rish",
                        callback_data=f"locate_child:{demo_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📱 Ekran tahlili & Ilovalar",
                        callback_data=f"st_child:{demo_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 Namuna farzandni o'chirish",
                        callback_data=f"unlink_demo_child:{demo_id}"
                    )
                ]
            ]
        )
        try:
            await callback.message.edit_text(success_text, reply_markup=unlink_kb, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(success_text, reply_markup=unlink_kb, parse_mode="Markdown")
        await callback.answer("✅ Namuna farzand ulandi!")


@router.callback_query(F.data.startswith("unlink_demo_child:"))
async def on_unlink_demo_child(callback: CallbackQuery):
    """Unlink and clean up demo child."""
    demo_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        await crud.delete_child_profile(session, parent_id, demo_id)
        await session.commit()

    await callback.answer("🗑 Namuna farzand o'chirildi", show_alert=True)
    if callback.message:
        try:
            await callback.message.edit_text(
                "✅ **Namuna farzand hisobi tozalandi.**\n\n"
                "Haqiqiy farzandingizni ulash uchun quyidagi tugmani bosing:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="➕ Haqiqiy farzandni ulash (Kodi)",
                                callback_data="show_pairing_code_callback"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🧪 Qayta namuna ulash",
                                callback_data="link_demo_child"
                            )
                        ]
                    ]
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass


@router.callback_query(F.data == "show_pairing_code_callback")
async def on_show_pairing_code_callback(callback: CallbackQuery, bot: Bot):
    """Show pairing code from inline button."""
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        code = await crud.generate_pairing_code(session, parent_id)
        await session.commit()

        bot_info = await bot.get_me()
        deep_link = f"https://t.me/{bot_info.username}?start=pair_{code}"
        safe_link = html.escape(deep_link)
        share_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📲 Farzandga yuborish (Telegram)",
                        url=f"https://t.me/share/url?url={deep_link}&text=Salom!+Ushbu+havola+orqali+Parental+Tracker+botiga+ulanishingiz+mumkin."
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🧪 Namuna farzand bilan sinash",
                        callback_data="link_demo_child"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Yangi kod yaratish",
                        callback_data="refresh_pairing_code"
                    )
                ]
            ]
        )
        msg_text = (
            f"🔑 <b>YAGONA OILA VA SAYT KODINGIZ:</b> <code>{code}</code>\n\n"
            f"🔗 <b>1-bosishda ulash havolasi:</b>\n"
            f"{safe_link}\n\n"
            f"👨‍👩‍👧 <b>Bu bitta kod barcha farzandlaringiz uchun umumiy va doimiydir!</b>\n\n"
            f"1️⃣ <b>Farzandlarni ulash:</b>\n"
            f"Ushbu havolani farzandingizga yuboring yoki uning telefonida botga kirib <code>{code}</code> kodini kiriting. Barcha farzandlaringiz bitta profilga ulanadi.\n\n"
            f"2️⃣ <b>Saytga (Web App) kirish:</b>\n"
            f"🌐 Manzil: {html.escape(settings.WEBAPP_BASE_URL)}\n"
            f"Saytga ham xuddi shu <code>{code}</code> kodini kiritasiz — barcha ulangan farzandlaringiz bir vaqtda chiqadi va bitta ekranda kuzata olasiz!\n\n"
            f"💡 <i>Tizimni ikkinchi telefonsiz hoziroq sinab ko'rmoqchi bo'lsangiz, quyidagi «🧪 Namuna farzand bilan sinash» tugmasini bosing:</i>"
        )
        try:
            await callback.message.edit_text(msg_text, reply_markup=share_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(msg_text, reply_markup=share_kb, parse_mode="HTML")
    await callback.answer()



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
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
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
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        is_owner = (parent_id in (1, 6707839718))
        if not is_owner and (not parents or parent_id not in [p.id for p in parents]):
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        from api.webapp_api import child_cancel_sos_endpoint, record_sos_message
        if callback.message:
            record_sos_message(child_id, bot, callback.message.chat.id, callback.message.message_id)
        await child_cancel_sos_endpoint(child_id, session)

    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.answer("✅ SOS to'xtatildi, oldingi barcha xabarlar va lokatsiyalar tozalandi.", show_alert=False)
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
    """Wipes all data from database tables completely so everyone starts from scratch. STRICTLY OWNER ONLY."""
    from_user = message.from_user
    is_owner = from_user and (
        from_user.id in (settings.OWNER_ID, 1, 6707839718)
        or (from_user.username and from_user.username.lower().lstrip("@") == settings.OWNER_USERNAME.lower().lstrip("@"))
    )
    if not is_owner:
        logger.warning(
            f"Blocked unauthorized /reset_all_data attempt by user {from_user.id if from_user else 'unknown'} "
            f"(@{from_user.username if from_user and from_user.username else 'no_username'})"
        )
        await message.answer(
            "⛔ **Ruxsat berilmagan!**\n\n"
            "Ushbu buyruq butun ma'lumotlar bazasini tozalash uchun faqat loyiha egasi/administratoriga tegishli.",
            parse_mode="Markdown"
        )
        return

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


@router.message(F.text.in_(["⚙️ Sozlamalar / Chiqish", "🔄 Rolni o'zgartirish"]))
@router.message(Command("logout", "chiqish", "reset_role", "sozlamalar"))
async def on_settings_logout_pressed(message: Message, state: FSMContext, bot: Bot):
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

        if user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if parents:
                child_ask_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔔 Ota-onamdan chiqishga ruxsat so'rash",
                                callback_data="logout_start"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="❌ Bekor qilish",
                                callback_data="logout_cancel"
                            )
                        ]
                    ]
                )
                safe_name = html.escape(user.full_name or message.from_user.full_name or "")
                await message.answer(
                    f"🔒 <b>HIMOYALANGAN FARZAND HISOBI</b>\n\n"
                    f"👤 <b>Farzand:</b> {safe_name}\n"
                    f"🛡 <b>Holat:</b> Ota-onangizga ulangansiz.\n\n"
                    f"Farzand hisobidan chiqish va rolni almashtirish faqat <b>ota-onangiz ruxsati bilan</b> amalga oshiriladi.\n\n"
                    f"Chiqish so'rovini ota-onangizga yuborish uchun quyidagi tugmani bosing:",
                    reply_markup=child_ask_kb,
                    parse_mode="HTML"
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

        if user and user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if not parents:
                await message.answer(
                    "⚠️ <b>Siz hali ota-onangizga ulanmagansiz!</b>\n\n"
                    "Saytga (Web App) kirish kodi ota-onangiz profilingizga ulangandan so'ng beriladi.\n\n"
                    "Iltimos, avval ota-onangiz bergan 6 xonali ulanish kodini kiritib hisobingizni ulang.",
                    reply_markup=get_child_main_keyboard(child.id),
                    parse_mode="HTML"
                )
                return

        access_code = None
        if user:
            access_code = await crud.generate_web_access_code(session, user_id)
            await session.commit()

        id_param = f"parent_id={user_id}" if role == "parent" else f"child_id={user_id}"
        code_param = f"&code={access_code}" if access_code else ""
        webapp_url = f"{settings.RESOLVED_WEBAPP_URL}?role={role}&{id_param}{code_param}"

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

        code_text = f"🔑 <b>Sizning saytga kirish kodingiz:</b> <code>{access_code}</code>\n\n<i>(Sayt ochilganda ushbu 6 xonali kod avtomatik tekshiriladi)</i>\n\n" if access_code else ""
        reply_kb = get_parent_main_keyboard(user_id) if role == "parent" else get_child_main_keyboard(user_id)

        await message.answer(
            f"🌐 <b>Child Security — Rasmiy Web App (Sayt):</b>\n\n"
            f"🔗 <b>Rasmiy havola:</b> {html.escape(webapp_url)}\n\n"
            f"{code_text}"
            f"Saytni ochish uchun quyidagi tugmalardan birini tanlang:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await message.answer(
            "📋 Klaviaturangiz yangilandi! Pastdagi «📱 Web App (Sayt)» tugmasi orqali ham istalgan payt ochishingiz mumkin.",
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
        "Pastda chatga yuborilgan <code>ChildSecurity.apk</code> faylini ustiga bosing va yuklab oling.\n"
        "💡 <i>Agar brauzer orqali yuklayotganda «Fayl zararli bo'lishi mumkin» desa, «Baribir yuklab olish» (Все равно скачать) ni bosing!</i>\n\n"
        "2️⃣ <b>2-QADAM: Noma'lum manbalar ruxsati (Agar telefon so'rasa)</b>\n"
        "Telefoningiz <i>«Xavfsizlik maqsadida ushbu manbadan ilova o'rnatish bloklandi»</i> desa:\n"
        "👉 <b>«Sozlamalar» (Настройки)</b> tugmasini bosing ➔ <b>«Ushbu manbaga ruxsat berish» (Разрешить установку)</b> ni yoqing va orqaga qaytib <b>«O'rnatish» (Установить)</b> ni bosing.\n\n"
        "3️⃣ <b>3-QADAM: Google Play Protect ogohlantirishi (Agar chiqsa)</b>\n"
        "Google Play Protect <i>«Noma'lum noshir / Xavfli bo'lishi mumkin»</i> deb ko'rsatsa:\n"
        "👉 Pastdagi <b>«Batafsil» (Подробнее)</b> yozuvini bosing ➔ <b>«Baribir o'rnatish» (Все равно установить)</b> tugmasini bosing.\n"
        "<i>(Bu ilova Play Market do'konidan tashqaridan o'rnatilayotgani va 24/7 foniy GPS ruxsatiga ega bo'lgani sababli Android beradigan standart ogohlantirishdir, ilova 100% xavfsiz!)</i>\n"
        "📱 <i>Xiaomi/Redmi/POCO bo'lsa: 10 soniyadan so'ng «Men tushunaman» belgisini qo'yib «OK» ni bosing.</i>\n\n"
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
        "<i>(⚠️ Eslatma: Apple xavfsizlik qoidasiga ko'ra, profil faqat <b>Safari</b> brauzeri orqali yuklanadi!)</i>\n"
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
    desktop_url_link = f"{webapp_url}/download/desktop-url"

    text = (
        "🪟 <b>Child Security — Windows uchun Desktop Dasturi (Windows 11 / 10 / 8 / 7)</b>\n\n"
        "Windows noutbuk va kompyuterlarda dasturni 100% xatosiz ishga tushirish yo'llari:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>1-USUL (Tavsiya etiladi): Bloklanmaydigan Toza Yorliq (.url)</b>\n"
        "Quyidagi <b>«Child Security.url»</b> faylini yuklab oling va bosing. "
        "U hech qanday skript emas, toza yorliq bo'lgani sababli Windows Smart App Control yoki antivirus uni <b>hech qachon bloklamaydi</b>!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>2-USUL: Brauzer orqali Mustaqil Ilova (PWA) O'rnatish</b>\n"
        "Chrome yoki Edge brauzerida saytni oching: <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n"
        "Manzil satrining o'ng tomonidagi <b>«Установить приложение» (O'rnatish)</b> tugmasini bosing — u ish stoli va vazifalar paneliga rasmiy ilova bo'lib o'rnatiladi!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>3-USUL: Windows O'rnatgich Arxiv (.zip)</b>\n"
        "<code>ChildSecurity_Windows_Setup.zip</code> faylini yuklab oling va arxivdan chiqaring. "
        "Ichidagi <b>«Child Security.url»</b> yorlig'ini bosing — dastur darhol toza oynada ochiladi!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>4-USUL: Avtomatik Ish Stoli O'rnatgichi (.bat)</b>\n"
        "<code>ChildSecurity_Desktop_Setup.bat</code> ish stoliga avtomatik yorliq yaratadi.\n\n"
        "🛡️ <b>WINDOWS 11 BLOKIROVKASINI YECHISH (1 soniyada):</b>\n"
        "Agar Windows 11 <i>«Интеллектуальное управление приложениями заблокировало файл»</i> (Smart App Control) deb chiqarsa:\n"
        "1️⃣ Yuklab olingan fayl ustiga <b>sichqonchaning o'ng tugmasini</b> bosing.\n"
        "2️⃣ <b>«Свойства» (Xususiyatlar / Properties)</b> menyusini oching.\n"
        "3️⃣ Eng pastida <b>«Разблокировать» (Unblock)</b> katagiga galochka (✅) qo'ying va <b>OK</b> bosing.\n"
        "Shundan so'ng dastur bir zumda ishga tushadi!"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Child Security.url (Bloklanmaydigan yorliq)",
                    url=desktop_url_link
                )
            ],
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
                    text="🌐 Dasturni brauzerda ochish (PWA)",
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
    desktop_webloc_url = f"{webapp_url}/download/desktop-webloc"

    text = (
        "🍏 <b>Child Security — Apple Mac (macOS / MacBook) Rasmiy Ilovasi</b>\n\n"
        "Apple kompyuterlarida (MacBook Air/Pro, iMac, Mac mini) Child Security'ni to'laqonli desktop dastur sifatida ishlatish yo'llari:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>1-USUL (Tavsiya etiladi): Bloklanmaydigan Mac Yorlig'i (.webloc)</b>\n"
        "Quyidagi <b>«Child Security.webloc»</b> yorlig'ini yuklab oling va bosing. "
        "U macOS'ning rasmiy yorlig'i bo'lib, Apple Gatekeeper uni <b>hech qachon bloklamaydi</b>!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>2-USUL: Safari / Chrome orqali Dock-ga qo'shish (PWA)</b>\n"
        "1. Saytni oching: <a href=\"https://child-security-bot.vercel.app\">child-security-bot.vercel.app</a>\n"
        "2. <b>Safari'da:</b> Yuqori menyudan <i>«Fayl» (File)</i> ➔ <b>«Dock-ga qo'shish» (Add to Dock)</b> ni bosing.\n"
        "3. <b>Chrome / Edge'da:</b> Manzil satrining o'ng tomonidagi ⬇️ <b>«O'rnatish» (Install)</b> belgisini bosing!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>3-USUL: Avtomatik O'rnatuvchi Paket (.zip)</b>\n"
        "1. <code>ChildSecurity_Mac_Setup.zip</code> arxivini yuklab oling va oching.\n"
        "2. Ichidagi <code>ChildSecurity_Mac_Setup.command</code> fayli ustiga bosing.\n\n"
        "🛡️ <b>macOS GATEKEEPER BLOKIROVKASINI YECHISH:</b>\n"
        "Agar Mac tizimi <i>«Unidentified developer / Dasturchi tasdiqlanmagan»</i> desa:\n"
        "👉 Fayl ustiga <b>Control tugmasini bosib turib</b> bosing (yoki sichqonchaning o'ng tugmasi) ➔ <b>«Открыть» (Open)</b> ➔ yana <b>«Открыть» (Open)</b> bosing!\n"
        "Shundan so'ng Mac dasturni ishonchli deb qabul qiladi."
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Child Security.webloc (Bloklanmaydigan yorliq)",
                    url=desktop_webloc_url
                )
            ],
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

        if not link:
            await callback.message.edit_text("⚠️ Siz ushbu farzandning ota-onasi emassiz yoki so'rov eskirgan.")
            await callback.answer("Ruxsat berilmagan", show_alert=True)
            return

        # Reset child's role
        await crud.reset_user_role(session, child_id)
        await session.commit()

        # Update parent message
        safe_child_name = html.escape(child_name)
        safe_parent_name = html.escape(parent_name)
        await callback.message.edit_text(
            f"✅ <b>Farzandingiz ({safe_child_name}) uchun rolni almashtirish tasdiqlandi.</b>\n\n"
            f"Uning hisobi sizdan uzildi va u o'z Telegramida yangi rolni tanlay oladi.",
            parse_mode="HTML"
        )

        # Notify child
        try:
            await bot.send_message(
                chat_id=child_id,
                text=(
                    f"🎉 <b>Ota-onangiz ({safe_parent_name}) rolni almashtirishingizga ruxsat berdi!</b>\n\n"
                    f"Sizning avvalgi rolingiz bekor qilindi.\n"
                    f"Endi pastdagi tugmalar orqali to'g'ri rolingizni tanlang:"
                ),
                reply_markup=get_role_selection_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify child {child_id} of approval: {e}")

    await callback.answer("Rolni almashtirish tasdiqlandi!")


@router.callback_query(F.data.startswith("reject_role_switch:"))
async def on_parent_reject_role_switch(callback: CallbackQuery, bot: Bot):
    """Parent rejects child's role switch request."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    parent_name = callback.from_user.full_name

    async with AsyncSessionLocal() as session:
        link = await crud.get_parent_child_link(session, parent_id, child_id)
        if not link:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
        child = await crud.get_user(session, child_id)
        child_name = child.full_name if child else f"ID: {child_id}"

    safe_child_name = html.escape(child_name)
    safe_parent_name = html.escape(parent_name)
    await callback.message.edit_text(
        f"❌ <b>Farzandingiz ({safe_child_name}) ning rolni almashtirish so'rovi rad etildi.</b>\n"
        f"Farzandingiz o'z hisobida qoldi.",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            chat_id=child_id,
            text=(
                f"❌ <b>Ota-onangiz ({safe_parent_name}) rolni almashtirish so'rovingizni rad etdi.</b>\n\n"
                f"Siz avvalgidek Farzand hisobida qoldingiz."
            ),
            reply_markup=get_child_main_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to notify child of rejection: {e}")

    await callback.answer("So'rov rad etildi.")


@router.callback_query(F.data == "logout_start")
async def on_logout_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Level 1 confirmation for account logout or child approval request."""
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if user and user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if parents:
                safe_cname = html.escape(user.full_name or callback.from_user.full_name or f"Farzand #{user_id}")
                perm_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Ruxsat berish (Chiqarish)",
                                callback_data=f"approve_child_logout:{user_id}"
                            ),
                            InlineKeyboardButton(
                                text="❌ Rad etish",
                                callback_data=f"reject_child_logout:{user_id}"
                            )
                        ]
                    ]
                )
                for p in parents:
                    try:
                        await bot.send_message(
                            chat_id=p.id,
                            text=(
                                f"🔔 <b>DIQQAT: Farzandingiz hisobdan chiqishga ruxsat so'ramoqda!</b>\n\n"
                                f"👤 Farzand: <b>{safe_cname}</b> (ID: <code>{user_id}</code>)\n\n"
                                f"Farzandingiz o'z hisobidan chiqib, rolni almashtirmoqchi.\n"
                                f"Agar bu sizning roziligingiz bilan bo'layotgan bo'lsa, quyidagi «Ruxsat berish» tugmasini bosing:"
                            ),
                            reply_markup=perm_kb,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify parent {p.id} of child logout request: {e}")

                try:
                    await callback.message.edit_text(
                        "⏳ <b>Hisobdan chiqish uchun ota-onangiz ruxsati kerak!</b>\n\n"
                        "Ota-onangizga hisobdan chiqishingiz bo'yicha ruxsat so'rovi yuborildi. "
                        "Ota-onangiz tasdiqlagach, profilingizdan chiqiladi va yangi rol tanlashingiz mumkin bo'ladi.",
                        parse_mode="HTML"
                    )
                except Exception:
                    await callback.message.answer(
                        "⏳ <b>Hisobdan chiqish uchun ota-onangiz ruxsati kerak!</b>\n\n"
                        "Ota-onangizga hisobdan chiqishingiz bo'yicha ruxsat so'rovi yuborildi. "
                        "Ota-onangiz tasdiqlagach, profilingizdan chiqiladi va yangi rol tanlashingiz mumkin bo'ladi.",
                        parse_mode="HTML"
                    )
                await callback.answer("Ota-onangizga ruxsat so'rovi yuborildi.")
                return

        role_display = "👨‍👩‍👧 Ota-ona" if (user and user.role == "parent") else "👦 Farzand"

    text = (
        f"⚠️ <b>1-BOSQICH: HISOBDAN CHIQISHNI TASDIQLASH (1/2)</b>\n\n"
        f"Siz hozirda: <b>{role_display}</b> hisobidasiz.\n\n"
        f"Agar rolingizni adashib tanlagan bo'lsangiz, hisobdan chiqib to'g'ri rolni tanlashingiz mumkin.\n\n"
        f"❗️ <b>Ogohlantirish:</b> Chiqsangiz, joriy rolingiz va barcha ulanishlar bekor qilinadi.\n\n"
        f"Chiqishni davom ettirishni xohlaysizmi?"
    )
    await callback.message.edit_text(text, reply_markup=get_logout_step1_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("approve_child_logout:"))
async def on_parent_approve_child_logout(callback: CallbackQuery, bot: Bot):
    """Parent approves child's request to logout / switch role."""
    parent_id = callback.from_user.id
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not any(p.id == parent_id for p in parents):
            await callback.answer("❌ Siz bu farzandning ota-onasi emassiz!", show_alert=True)
            return

        child_user = await crud.get_user(session, child_id)
        child_name = html.escape(child_user.full_name) if child_user and child_user.full_name else f"Farzand #{child_id}"

        # Perform complete reset for the child
        await crud.reset_user_role(session, child_id)
        await session.commit()

        try:
            from api.webapp_api import ws_manager
            await ws_manager.send_to_user(child_id, {
                "type": "child_logout_approved",
                "message": "Ota-onangiz hisobdan chiqishga ruxsat berdi."
            })
        except Exception:
            pass

        try:
            await callback.message.edit_text(
                f"✅ <b>Farzand ({child_name}) hisobdan chiqishiga ruxsat berildi!</b>\n\n"
                f"Farzand hisobi muvaffaqiyatli uzildi.",
                parse_mode="HTML"
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                chat_id=child_id,
                text=(
                    f"✅ <b>Ota-onangiz hisobdan chiqishga ruxsat berdi!</b>\n\n"
                    f"Avvalgi hisobingiz yakunlandi. Endi yangi rol tanlashingiz mumkin:"
                ),
                reply_markup=get_role_selection_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not notify child {child_id} after approved logout: {e}")

    await callback.answer("Ruxsat berildi.")


@router.callback_query(F.data.startswith("reject_child_logout:"))
async def on_parent_reject_child_logout(callback: CallbackQuery, bot: Bot):
    """Parent rejects child's request to logout."""
    parent_id = callback.from_user.id
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not any(p.id == parent_id for p in parents):
            await callback.answer("❌ Siz bu farzandning ota-onasi emassiz!", show_alert=True)
            return

        child_user = await crud.get_user(session, child_id)
        child_name = html.escape(child_user.full_name) if child_user and child_user.full_name else f"Farzand #{child_id}"

        try:
            from api.webapp_api import ws_manager
            await ws_manager.send_to_user(child_id, {
                "type": "child_logout_rejected",
                "message": "Ota-onangiz hisobdan chiqish so'rovingizni rad etdi."
            })
        except Exception:
            pass

        try:
            await callback.message.edit_text(
                f"❌ <b>Farzand ({child_name}) hisobdan chiqish so'rovi rad etildi.</b>\n\n"
                f"Farzand hisobda xavfsiz qoldi.",
                parse_mode="HTML"
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                chat_id=child_id,
                text=(
                    f"❌ <b>Ota-onangiz hisobdan chiqish so'rovingizni rad etdi!</b>\n\n"
                    f"Siz farzand hisobida qolasiz va xavfsizlik nazorati davom etadi."
                ),
                reply_markup=get_child_main_keyboard(child.id),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("So'rov rad etildi.")


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
            keyboard = get_parent_main_keyboard(message.from_user.id) if (user and user.role == "parent") else get_child_main_keyboard(message.from_user.id)
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

    keyboard = get_parent_main_keyboard(user_id) if role == "parent" else get_child_main_keyboard(user_id)

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
                        f"Bog'langan foydalanuvchi (**{clean_md(full_name)}**) o'z hisobidan chiqdi va rolni qayta tanlash holatiga o'tdi.\n"
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

        if user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if not parents:
                await message.answer(
                    "⚠️ <b>Siz hali ota-onangizga ulanmagansiz!</b>\n\n"
                    "Saytga (Web App) kirish kodi ota-onangiz profilingizga ulangandan so'ng beriladi.\n\n"
                    "Iltimos, avval ota-onangiz bergan 6 xonali ulanish kodini kiritib hisobingizni ulang.",
                    reply_markup=get_child_main_keyboard(child.id),
                    parse_mode="HTML"
                )
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return

        web_code = await crud.generate_web_access_code(session, user_id)
        await session.commit()

        if user.role == "parent":
            rate_coins, rate_som = await crud.get_coin_rate_for_user(session, user_id)
            text = (
                f"🔑 <b>YAGONA OILA VA SAYT (WEB APP) KODINGIZ:</b>\n\n"
                f"🏷 Kodingiz: <code>{web_code}</code>\n\n"
                f"🌐 <b>Sayt manzili:</b> {html.escape(settings.WEBAPP_BASE_URL)}\n\n"
                f"✨ <b>Bu bitta kod barcha farzandlaringiz uchun yagona va doimiydir!</b>\n"
                f"• Saytga ushbu kodni bir marta kiritsangiz, barcha ulangan farzandlaringiz bir vaqtda chiqadi.\n"
                f"• Akkauntdan chiqib boshqa bola kodini kiritish umuman shart emas!\n\n"
                f"👨‍👩‍👧 <b>Ota-ona imkoniyatlari:</b>\n"
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
                f"🔑 <b>FARZANDNING SHAXSIY SAYT (WEB APP) KODINGIZ:</b>\n\n"
                f"🏷 Kodingiz: <code>{web_code}</code>\n\n"
                f"🌐 <b>Sayt manzili:</b> {html.escape(settings.WEBAPP_BASE_URL)}\n\n"
                f"👦 <b>Farzand imkoniyatlari:</b>\n"
                f"• Saytga ushbu 6 xonali shaxsiy kodingiz bilan kirasiz\n"
                f"• O'zingizning topshiriqlaringiz, xazinangiz va sarguzasht profilingiz ochiladi\n"
                f"• 'Ko'rdim' va 'Shu vaqtda bajaraman' deb vaqt belgilash\n"
                f"• Topshiriqni bajarib rasm yuborish va tangalarga ega bo'lish\n"
                f"• Sizda hozir: <b>{user.virtual_balance:,} tanga</b> ({money_som:,} so'm) mavjud\n\n"
                f"📌 Saytimizga kirganda ushbu kodingizni kiriting!"
            )

        await message.answer(text, parse_mode="HTML")
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

    child_name = html.escape(child.full_name or child.username or f"Farzand #{child.id}")
    total_time = html.escape(report.get('total_screen_time_formatted', '0 daqiqa'))
    total_sec = report.get('total_screen_time_seconds', 0)
    unlock_count = report.get('unlock_count', 0)

    if total_sec > 14400:
        time_badge = "⚠️ <b>Me'yordan ko'p (4s+)</b>"
    elif total_sec > 7200:
        time_badge = "🟡 <b>O'rtacha me'yorda</b>"
    else:
        time_badge = "🟢 <b>Me'yorda</b>"

    lock_status_text = "🛑 <b>BARCHA ILOVALAR BLOKLANGAN!</b>" if policy.is_all_apps_blocked else "🟢 <b>Ochiq (Cheklovsiz)</b>"
    net_status_text = "📴 <b>Cheklangan (Offline rejim / Firewall faol)</b>" if policy.is_network_restricted else "📶 <b>Ochiq (Normal)</b>"
    anti_un_status = "🔒 <b>Faol</b> <i>(Bola sozlamalardan ruxsatlarni o'chira olmaydi)</i>" if policy.anti_uninstall_enabled else "⚠️ O'chiq"
    bedtime_info = f"🌙 <b>Uyqu jadvali:</b> <code>{html.escape(policy.bedtime_start)} - {html.escape(policy.bedtime_end)}</code>\n" if (policy.bedtime_start and policy.bedtime_end) else ""

    apps_list = report.get("apps", [])
    grand_sec = max(1, sum(a.get("usage_seconds", 0) for a in apps_list))

    apps_lines = []
    if apps_list:
        for i, a in enumerate(apps_list[:6], 1):
            name = a.get("app_name", "Ilova")
            safe_name = html.escape(str(name))
            sec = a.get("usage_seconds", 0)
            h = sec // 3600
            m = (sec % 3600) // 60
            dur_str = f"{h}s {m}d" if h > 0 else f"{m}d"
            pct = int(round((sec / grand_sec) * 100))
            bar = progress_bar(pct, width=8)

            cat = a.get("category", "")
            icon = "🟥" if "youtube" in name.lower() else ("🎵" if "tiktok" in name.lower() else ("🎮" if cat == "game" or "pubg" in name.lower() or "roblox" in name.lower() else ("✈️" if "telegram" in name.lower() else "📱")))
            bl = " 🛑 <i>Bloklangan</i>" if a.get("is_blocked") else ""
            apps_lines.append(f"{i}. {icon} <b>{safe_name}</b> — <code>{dur_str}</code> ({pct}%)\n   <code>[{bar}]</code>{bl}")
    else:
        apps_lines.append(
            "<i>Bugun hali ilovalar sarfi qayd etilmagan.</i>\n\n"
            "💡 <b>Nega 0 daqiqa ko'rsatmoqda?</b>\n"
            "1. <b>Android ilova (APK):</b> Boshqa ilovalar sarfini (YouTube, TikTok, o'yinlar) aniqlash uchun bola telefoniga <b>Android APK</b> ilovamiz o'rnatilgan bo'lishi lozim (brauzerlar boshqa ilovalarni ko'ra olmaydi);\n"
            "2. <b>Maxsus ruxsat:</b> Farzand telefonida <i>Sozlamalar ➔ Maxsus kirish ➔ Foydalanish statistikasi (Usage Access)</i> ruxsati yoqilgan bo'lishi shart."
        )

    apps_text = "\n".join(apps_lines)

    text = (
        f"📱 <b>FARZANDINGIZNING TELEFON ISHLATISH TAHLILI (BUGUN)</b>\n\n"
        f"👤 Farzand: <b>{child_name}</b>\n"
        f"⏱ Jami ekran vaqti: <b>{total_time}</b> ({time_badge})\n"
        f"🔓 Telefon ochilgan: <b>{unlock_count} marta</b>\n\n"
        f"📊 <b>Eng ko'p ishlatilgan ilovalar:</b>\n"
        f"{apps_text}\n\n"
        f"🛑 <b>Ilovalar holati:</b> {lock_status_text}\n"
        f"🌐 <b>Internet holati:</b> {net_status_text}\n"
        f"{bedtime_info}"
        f"🛡 <b>Sozlamalar himoyasi:</b> {anti_un_status}\n\n"
        f"ℹ️ <i>Quyidagi tugmalar orqali ilovalarni yoki internetni bir zumda bloklashingiz, internetsiz SMS buyruqlarni yuborishingiz mumkin:</i>"
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
    buttons.append([
        InlineKeyboardButton(text="🛡 Ruxsatlarni yoqish yo'riqnomasi", callback_data=f"st_perm_guide:{child.id}"),
        InlineKeyboardButton(text="⚙️ Ilovalarni boshqarish", web_app=WebAppInfo(url=settings.MINI_APP_URL))
    ])

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
async def parent_screen_time_handler(message: Message, bot: Optional[Bot] = None):
    """View child screen time, daily app usage stats, and master lock toggle."""
    user_id = message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            user = await crud.get_user(session, user_id)
            if not user or user.role != "parent":
                await message.answer("Ushbu bo'lim faqat ota-onalar uchun mo'ljallangan.")
                return

            children = await crud.get_children_for_parent(session, user_id)
            if not children and user_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
                from db.models import User
                from sqlalchemy import select
                all_ch = (await session.execute(select(User).where(User.role == "child"))).scalars().all()
                children = list(all_ch)

            if not children:
                await message.answer(
                    "📱 <b>Ilovalar va Ekran vaqti tahlili:</b>\n\n"
                    "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                    "Ilovalar sarfi, ekran vaqti, ilovalarni masofadan bloklash va uxlash rejimi sozlamalarini darhol sinab ko'rish uchun <b>Namuna farzand</b>ni ulashingiz mumkin:",
                    reply_markup=get_empty_children_kb(),
                    parse_mode="HTML"
                )
                return

            child = children[0]
            report = await crud.get_app_usage_report_today(session, child.id)
            policy = await crud.get_or_create_device_policy(session, child.id)
            text, kb = generate_screen_time_card(child, report, policy, children)
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in parent_screen_time_handler: {e}", exc_info=True)
        await message.answer("⚠️ Ilovalar va ekran tahlilini yuklashda texnik xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")


@router.callback_query(F.data.startswith("st_child:"))
async def st_child_callback_handler(callback: CallbackQuery):
    """Switch child when parent clicks a child pill in screen time."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, callback.from_user.id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        target_child = next((c for c in children if c.id == child_id), None)
        if not target_child:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        report = await crud.get_app_usage_report_today(session, target_child.id)
        policy = await crud.get_or_create_device_policy(session, target_child.id)
        text, kb = generate_screen_time_card(target_child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer(f"👦 {target_child.full_name or 'Farzand'} statistikasi")


@router.callback_query(F.data.startswith("st_refresh:"))
async def st_refresh_callback_handler(callback: CallbackQuery):
    """Refresh child screen time data."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        target_child = next((c for c in children if c.id == child_id), None)
        if not target_child:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        report = await crud.get_app_usage_report_today(session, target_child.id)
        policy = await crud.get_or_create_device_policy(session, target_child.id)
        text, kb = generate_screen_time_card(target_child, report, policy, children)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer("🔄 Yangilandi!")
        except Exception:
            await callback.answer("O'zgarish yo'q.")

@router.callback_query(F.data.startswith("toggle_net_lock:"))
async def toggle_net_lock_callback_handler(callback: CallbackQuery):
    """Handle 1-tap toggle network lockdown (Local VPN Firewall) from Telegram."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
        policy = await crud.get_or_create_device_policy(session, child_id)
        new_state = not bool(policy.is_network_restricted)
        updated_policy = await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_network_restricted=new_state
        )
        status_msg = "📴 Internet cheklandi (Offline rejim yoqildi)!" if new_state else "🌐 Internet cheklovi yechildi (Online tiklandi)!"
        await callback.answer(status_msg, show_alert=True)

        try:
            from api.webapp_api import ws_manager
            import json
            pol_msg = {
                "type": "device_policy_update",
                "child_id": child_id,
                "is_all_apps_blocked": updated_policy.is_all_apps_blocked,
                "is_network_restricted": updated_policy.is_network_restricted,
                "bedtime_start": updated_policy.bedtime_start,
                "bedtime_end": updated_policy.bedtime_end,
                "daily_limit_minutes": updated_policy.daily_limit_minutes,
                "lockdown_reason": updated_policy.lockdown_reason,
                "blocked_packages": json.loads(updated_policy.blocked_packages or "[]"),
                "anti_uninstall_enabled": updated_policy.anti_uninstall_enabled,
                "has_parent_pin": bool(updated_policy.parent_pin_hash),
            }
            await ws_manager.send_to_user(child_id, pol_msg)
            for p in parents:
                await ws_manager.send_to_user(p.id, pol_msg)
        except Exception:
            pass

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        text, kb = generate_screen_time_card(child, report, updated_policy, children)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("sms_controls:"))
async def sms_controls_callback_handler(callback: CallbackQuery):
    """Display encrypted offline SMS fallback instructions and templates."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
        child = await crud.get_user(session, child_id)
        child_name = child.full_name or f"Farzand #{child_id}" if child else "Farzand"
        policy = await crud.get_or_create_device_policy(session, child_id)
        pin_hint = "1234 (standart)" if not policy.parent_pin_hash else "Siz o'rnatgan 4 xonali PIN"

        safe_child_name = html.escape(child_name)
        safe_pin_hint = html.escape(pin_hint)
        text = (
            f"✉️ <b>INTERNETSIZ (OFLAYN) SMS BUYRUQLAR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Farzand: <b>{safe_child_name}</b>\n"
            f"🔑 Xavfsizlik PIN-kodi: <code>{safe_pin_hint}</code>\n\n"
            f"Agar bolaning telefonida <b>internet (Wi-Fi / Mobile Data) butunlay o'chirilgan</b> bo'lsa, "
            f"siz bolaning telefon raqamiga oddiy SMS yuborish orqali boshqarishingiz mumkin:\n\n"
            f"• <code>#LOCK:PIN</code> — Telefonni internetsiz darhol to'liq bloklash.\n"
            f"  <i>Misol:</i> <code>#LOCK:1234</code>\n\n"
            f"• <code>#UNLOCK:PIN</code> — Barcha cheklovlarni yechish va ochish.\n"
            f"  <i>Misol:</i> <code>#UNLOCK:1234</code>\n\n"
            f"• <code>#GPS:PIN</code> — Farzand telefonidan internetsiz aniq <b>GPS koordinatalari</b> va batareyasini so'rash. "
            f"Farzand telefoni darhol javob SMS orqali Google Maps havolasini qaytaradi!\n"
            f"  <i>Misol:</i> <code>#GPS:1234</code>\n\n"
            f"• <code>#SIREN:PIN</code> — Farzand telefonida qattiq signal (sirena) chalish.\n"
            f"  <i>Misol:</i> <code>#SIREN:1234</code>\n\n"
            f"ℹ️ <i>Farzand telefoni SMS ni olishi bilanoq (internetsiz) buyruqni bajaradi va ota-onaga hisobot beradi.</i>"
        )
        phone = child.phone_number.strip() if child and child.phone_number else None
        pin_code = "1234"
        if policy and policy.parent_pin_hash:
            pin_code = "PIN"

        kb_rows = []
        if phone:
            kb_rows.append([
                InlineKeyboardButton(
                    text="📍 1-Tap SMS: GPS so'rash",
                    url=f"sms:{phone}?body=%23GPS:{pin_code}"
                )
            ])
            kb_rows.append([
                InlineKeyboardButton(
                    text="🔔 1-Tap SMS: Sirena",
                    url=f"sms:{phone}?body=%23SIREN:{pin_code}"
                ),
                InlineKeyboardButton(
                    text="🛑 1-Tap SMS: Bloklash",
                    url=f"sms:{phone}?body=%23LOCK:{pin_code}"
                )
            ])
            kb_rows.append([
                InlineKeyboardButton(
                    text=f"📱 Raqam: {phone} (O'zgartirish)",
                    callback_data=f"set_child_phone:{child_id}"
                )
            ])
        else:
            kb_rows.append([
                InlineKeyboardButton(
                    text="📱 Farzand telefon raqamini kiritish",
                    callback_data=f"set_child_phone:{child_id}"
                )
            ])

        kb_rows.append([
            InlineKeyboardButton(
                text="⬅️ Ekran tahliliga qaytish",
                callback_data=f"st_child:{child_id}"
            )
        ])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            await callback.answer()
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("lock_all:"))
async def lock_all_callback_handler(callback: CallbackQuery):
    """Handle 1-tap master lock all apps from Telegram inline button."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
        updated_policy = await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_all_apps_blocked=True,
            lockdown_reason="Ota-ona Telegram orqali barcha ilovalarni blokladi"
        )
        await callback.answer("🛑 Farzand telefonida barcha ilovalar bloklandi!", show_alert=True)

        try:
            from api.webapp_api import ws_manager
            import json
            pol_msg = {
                "type": "device_policy_update",
                "child_id": child_id,
                "is_all_apps_blocked": True,
                "is_network_restricted": updated_policy.is_network_restricted,
                "bedtime_start": updated_policy.bedtime_start,
                "bedtime_end": updated_policy.bedtime_end,
                "daily_limit_minutes": updated_policy.daily_limit_minutes,
                "lockdown_reason": updated_policy.lockdown_reason,
                "blocked_packages": json.loads(updated_policy.blocked_packages or "[]"),
                "anti_uninstall_enabled": updated_policy.anti_uninstall_enabled,
                "has_parent_pin": bool(updated_policy.parent_pin_hash),
            }
            await ws_manager.send_to_user(child_id, pol_msg)
            for p in parents:
                await ws_manager.send_to_user(p.id, pol_msg)
        except Exception:
            pass

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        policy = await crud.get_or_create_device_policy(session, child_id)
        text, kb = generate_screen_time_card(child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("unlock_all:"))
async def unlock_all_callback_handler(callback: CallbackQuery):
    """Handle 1-tap unblock all apps from Telegram inline button."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return
        updated_policy = await crud.update_device_policy(
            session=session,
            child_id=child_id,
            is_all_apps_blocked=False
        )
        await callback.answer("✅ Barcha ilovalardan cheklov olib tashlandi!", show_alert=True)

        try:
            from api.webapp_api import ws_manager
            import json
            pol_msg = {
                "type": "device_policy_update",
                "child_id": child_id,
                "is_all_apps_blocked": False,
                "is_network_restricted": updated_policy.is_network_restricted,
                "bedtime_start": updated_policy.bedtime_start,
                "bedtime_end": updated_policy.bedtime_end,
                "daily_limit_minutes": updated_policy.daily_limit_minutes,
                "lockdown_reason": updated_policy.lockdown_reason,
                "blocked_packages": json.loads(updated_policy.blocked_packages or "[]"),
                "anti_uninstall_enabled": updated_policy.anti_uninstall_enabled,
                "has_parent_pin": bool(updated_policy.parent_pin_hash),
            }
            await ws_manager.send_to_user(child_id, pol_msg)
            for p in parents:
                await ws_manager.send_to_user(p.id, pol_msg)
        except Exception:
            pass

        child = await crud.get_user(session, child_id)
        children = await crud.get_children_for_parent(session, callback.from_user.id)
        report = await crud.get_app_usage_report_today(session, child_id)
        policy = await crud.get_or_create_device_policy(session, child_id)
        text, kb = generate_screen_time_card(child, report, policy, children)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("st_perm_guide:"))
async def st_perm_guide_callback_handler(callback: CallbackQuery):
    """Send step-by-step guidance on how to enable Usage Access, Overlay, and Accessibility on child device."""
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        child_name = child.full_name or f"Farzand #{child_id}" if child else "Farzand"

    safe_child_name = html.escape(child_name)
    guide_text = (
        f"🛡 <b>FARZAND TELEFONIDA RUXSATLARNI YOQISH QO'LLANMASI</b>\n\n"
        f"👤 Farzandingiz: <b>{safe_child_name}</b>\n\n"
        f"Ilovalar tahlili (qancha vaqt o'tirgani) va ularni masofadan bir zumda bloklash ishlashi uchun <b>bola telefoniga bizning Android APK ilovamiz o'rnatilgan bo'lishi</b> va quyidagi 3 ta ruxsat yoqilishi shart:\n\n"
        f"1️⃣ <b>Foydalanish statistikasi (Usage Access) — Ekran tahlili uchun:</b>\n"
        f"• Bola telefonida: <i>Sozlamalar ➔ Maxsus kirish (Special app access) ➔ Foydalanish ma'lumotlariga kirish (Usage access)</i> ga kiring;\n"
        f"• Ro'yxatdan <b>Child Security</b> ilovasini topib, ruxsat bering.\n\n"
        f"2️⃣ <b>Boshqa ilovalar ustida ko'rinish (Overlay) — Bloklash ekrani uchun:</b>\n"
        f"• <i>Sozlamalar ➔ Ilovalar ➔ Maxsus kirish ➔ Boshqa ilovalar ustida ko'rinish (Display over other apps)</i> ga kiring;\n"
        f"• <b>Child Security</b> ga ruxsat bering.\n\n"
        f"3️⃣ <b>Maxsus imkoniyatlar (Accessibility) — Tezkor bloklash uchun:</b>\n"
        f"• <i>Sozlamalar ➔ Maxsus imkoniyatlar (Accessibility) ➔ O'rnatilgan xizmatlar (Downloaded apps)</i> ga kiring;\n"
        f"• <b>Child Security</b> xizmatini yoqing.\n\n"
        f"⚠️ <b>Android 13, 14 va 15 foydalanuvchilari diqqatiga!</b>\n"
        f"Agar tizim <i>«Cheklangan sozlama» (Restricted setting)</i> deb ruxsat bermasa:\n"
        f"1. Bola telefonida <i>Sozlamalar ➔ Ilovalar (Apps) ➔ Child Security</i> ga kiring;\n"
        f"2. Yuqori o'ng burchakdagi <b>3 ta nuqta (⋮)</b> tugmasini bosing;\n"
        f"3. <b>«Cheklangan sozlamalarga ruxsat berish» (Allow restricted settings)</b> ni bosing va telefon PIN kodini tering;\n"
        f"4. Shundan keyin Maxsus imkoniyatlar (Accessibility) bo'limida Child Security bemalol yoqiladi!\n\n"
        f"💡 <i>Bola ilovasida «⚡ Ruxsatlarni yoqish» tugmasi orqali ham bularni 1-marta bosib bosqichma-bosqich yoqish mumkin.</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Ekran tahliliga qaytish", callback_data=f"st_child:{child_id}")]
    ])
    try:
        await callback.message.edit_text(guide_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(guide_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()



# ==============================================================================
# ASOSCHI (OWNER) BOSHQARUV PANELI VA ROLNI O'ZGARTIRISH (@fayzbek1331)
# ==============================================================================
def is_bot_owner(user_id: int, username: Optional[str] = None) -> bool:
    clean_uname = (username or "").lower().lstrip("@")
    return (
        user_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718)
        or clean_uname == "fayzbek1331"
    )


@router.message(Command("owner", "panel", "stats", "statistika"))
@router.message(F.text.in_({"👑 Asoschi (Owner) Boshqaruv", "📊 Umumiy Statistika"}))
async def on_owner_dashboard_command(message: Message, bot: Bot):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha asoschisi (@fayzbek1331) uchun ochiq.")
        return

    async with AsyncSessionLocal() as session:
        stats = await crud.get_system_overview_stats(session)

    text = (
        "👑 <b>ASOSCHI (OWNER) BOSHQARUV PANELI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Asoschi: <b>@{settings.OWNER_USERNAME}</b> (ID: <code>{user_id}</code>)\n\n"
        "📊 <b>PLATFORMA BO'YICHA JONLI STATISTIKA:</b>\n"
        f"• 👥 Jami foydalanuvchilar: <b>{stats['total_users']}</b> ta\n"
        f"  ├ 👨‍👩‍👧 Ota-onalar: <b>{stats['total_parents']}</b> ta\n"
        f"  └ 👦 Farzandlar: <b>{stats['total_children']}</b> ta\n"
        f"• 📅 Bugungi a'zolar: <b>{stats['today_users']}</b> ta\n"
        f"• 🚨 Faol SOS signallari: <b>{stats['active_sos']}</b> ta\n"
        f"• 🛡 Geofence hududlar: <b>{stats['total_geofences']}</b> ta\n"
        f"• 📍 GPS koordinatalar: <b>{stats['total_location_logs']}</b> ta\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Quyidagi tugmalar orqali platformani to'liq boshqarishingiz yoki oddiy mijoz (ota-ona/farzand) sifatida kirib sinashingiz mumkin:</i>"
    )
    await message.answer(text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("own_main:"))
async def on_owner_main_callback(callback: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    action = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        if action == "stats":
            stats = await crud.get_system_overview_stats(session)
            text = (
                "📊 <b>PLATFORMA BO'YICHA STATISTIKA</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 Jami foydalanuvchilar: <code>{stats['total_users']}</code> ta\n"
                f"👨‍👩‍👧 Ota-onalar: <code>{stats['total_parents']}</code> ta\n"
                f"👦 Farzandlar: <code>{stats['total_children']}</code> ta\n"
                f"📅 Bugun qo'shilganlar: <code>{stats['today_users']}</code> ta\n"
                f"🚨 Faol SOS signallari: <code>{stats['active_sos']}</code> ta\n"
                f"🛡 Geofence zonalar: <code>{stats['total_geofences']}</code> ta\n"
                f"📋 Topshiriqlar: <code>{stats['total_tasks']}</code> ta\n"
                f"📍 GPS nuqtalar bazasi: <code>{stats['total_location_logs']}</code> ta\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await callback.message.edit_text(text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
            await callback.answer("Statistika yangilandi!")
        elif action == "radar":
            from db.models import User
            from sqlalchemy import select
            res = await session.execute(select(User).where(User.role == "child", User.is_active == True))
            children = res.scalars().all()
            if not children:
                await callback.answer("Hozirda ro'yxatdan o'tgan bolalar yo'q.", show_alert=True)
                return
            radar_lines = ["📡 <b>JONLI GPS RADAR (SO'NGGI QURILMALAR)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            valid_c = 0
            for ch in children:
                loc = await crud.get_latest_location(session, ch.id)
                if loc and loc.latitude and loc.longitude:
                    valid_c += 1
                    c_name = html.escape(ch.full_name or f"Farzand #{ch.id}")
                    maps_url = f"https://maps.google.com/?q={loc.latitude:.6f},{loc.longitude:.6f}"
                    batt_str = f"🔋 {loc.battery_level}%" if loc.battery_level is not None else ""
                    radar_lines.append(
                        f"👤 <b>{c_name}</b> (ID: <code>{ch.id}</code>) {batt_str}\n"
                        f"📍 <a href=\"{maps_url}\">Google Maps ({loc.latitude:.5f}, {loc.longitude:.5f})</a>\n"
                    )
            if valid_c == 0:
                radar_lines.append("<i>Hozircha birorta ham GPS signali kelmagan.</i>")
            radar_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            await callback.message.edit_text("\n".join(radar_lines), reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML", disable_web_page_preview=True)
            await callback.answer()
        elif action == "sos_list":
            active_sos_list = await crud.get_active_sos_list(session)
            if not active_sos_list:
                await callback.message.edit_text(
                    "🟢 <b>Faol SOS signallari yo'q!</b>\n\nBarcha bolalar tinch va xavfsiz holatda.",
                    reply_markup=get_owner_panel_inline_keyboard(),
                    parse_mode="HTML"
                )
                await callback.answer()
                return
            lines = ["🚨 <b>FAOL SOS SIGNALLARI:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for c in active_sos_list:
                safe_name = html.escape(c.full_name or "Farzand")
                lines.append(f"⚠️ <b>{safe_name}</b> (ID: <code>{c.id}</code>) — SOS holatida!")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            await callback.message.edit_text("\n".join(lines), reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
            await callback.answer()
        elif action == "battery":
            from owner_bot.service import get_battery_and_device_radar
            html_text, _ = await get_battery_and_device_radar(session)
            await callback.message.edit_text(html_text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
            await callback.answer()
        elif action == "inspect":
            await state.set_state(OwnerActionState.waiting_for_inspect_query)
            await callback.message.answer(
                "🔍 <b>Foydalanuvchi ma'lumotlarini tekshirish</b>\n\n"
                "Iltimos, foydalanuvchining <b>Telegram ID</b>si yoki <b>@username</b>ini yozing:",
                parse_mode="HTML"
            )
            await callback.answer()
        elif action == "magic":
            await state.set_state(OwnerActionState.waiting_for_magic_id)
            await callback.message.answer(
                "🔑 <b>VIP / Magic Login Link Generatori</b>\n\n"
                "Qaysi foydalanuvchi hisobiga parolsiz 1-bosishda WebApp orqali kirmoqchisiz? Telegram ID raqamini kiriting:",
                parse_mode="HTML"
            )
            await callback.answer()
        elif action == "admins":
            admins = await crud.list_admin_users(session)
            lines = [
                "👥 <b>ADMINLARNI BOSHQARISH MARKAZI</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ]
            if not admins:
                lines.append("<i>Hozircha tizimda ikkilamchi adminlar mavjud emas.</i>")
            else:
                for a in admins:
                    tag = "👑 Bosh Ega" if a.is_owner else "👮 Admin"
                    u_str = f"@{a.username}" if a.username else "N/A"
                    lines.append(f"• {tag}: <b>{html.escape(a.full_name or 'Admin')}</b>\n  🆔 ID: <code>{a.id}</code> | {u_str}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ Yangi Admin qo'shish", callback_data="own_adm:add"),
                    InlineKeyboardButton(text="🗑 Adminni o'chirish", callback_data="own_adm:del_list"),
                ],
                [
                    InlineKeyboardButton(text="🔙 Asosiy Panel", callback_data="own_main:stats"),
                ]
            ])
            await callback.message.edit_text("\n".join(lines), reply_markup=admin_kb, parse_mode="HTML")
            await callback.answer()
        elif action == "reset_menu":
            text = (
                "🔄 <b>BARCHADA BOTNI RESET QILISH VA YANGILASH</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Quyidagi reset darajalaridan birini tanlang:\n\n"
                "1. <b>🔄 Menyu va tugmalarni yangilash:</b> Barcha faol ota-ona va bolalarga eng so'nggi bot tugmalari va menyularini qayta yuboradi.\n"
                "2. <b>🧹 Kesh va SOS tozalash:</b> Tizimdagi barcha faol SOS signallarini tozalaydi va sessiyalarni normallashtiradi.\n"
                "3. <b>⚠️ Factory Reset (Boshlang'ich holatga qaytarish):</b> Barcha ulanishlarni uzadi va barchani /start rol tanlashiga qaytaradi.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await callback.message.edit_text(text, reply_markup=get_reset_options_keyboard(), parse_mode="HTML")
            await callback.answer()
        elif action == "notifications":
            settings = await crud.get_notification_settings(session)
            from owner_bot.handlers import format_notification_settings_html
            from owner_bot.keyboards import get_notifications_settings_inline_keyboard
            html_text = format_notification_settings_html(settings)
            kb = get_notifications_settings_inline_keyboard(settings)
            await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML")
            await callback.answer()
            return
        elif action == "close":
            user = await crud.get_user(session, user_id)
            role = user.role if user else "parent"
            kb = get_parent_main_keyboard(user_id) if role == "parent" else get_child_main_keyboard(user_id)
            await callback.message.delete()
            await callback.message.answer("Menyuga qaytdingiz:", reply_markup=kb)
            await callback.answer()


@router.callback_query(F.data.startswith("own_switch:"))
async def on_owner_switch_role_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    new_role = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if user:
            user.role = new_role
            await session.commit()

    if new_role == "parent":
        await callback.message.answer(
            "👨‍👩‍👧 <b>Siz Ota-ona (Parent) rejimiga o'tdingiz!</b>\n\n"
            "Platformadan oddiy ota-ona sifatida to'liq foydalanishingiz mumkin. "
            "Istalgan vaqtda <b>«👑 Asoschi (Owner) Boshqaruv»</b> tugmasi orqali boshqaruvga qayta olasiz.",
            reply_markup=get_parent_main_keyboard(user_id),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            "👦 <b>Siz Farzand (Child) rejimiga o'tdingiz!</b>\n\n"
            "Platformani farzand sifatida sinab ko'rishingiz, geolokatsiya va SOS tugmasini tekshirishingiz mumkin. "
            "Istalgan vaqtda <b>«👑 Asoschi (Owner) Boshqaruv»</b> tugmasi orqali boshqaruvga qayta olasiz.",
            reply_markup=get_child_main_keyboard(user_id),
            parse_mode="HTML"
        )
    await callback.answer("Rol muvaffaqiyatli o'zgartirildi!")


@router.message(F.text.in_({"🔔 Xabarnomalar Boshqaruvi", "/notifications", "/xabarlar"}))
async def on_main_bot_notifications_cmd(message: Message):
    if not is_bot_owner(message.from_user.id, message.from_user.username):
        return
    async with AsyncSessionLocal() as session:
        notif_settings = await crud.get_notification_settings(session)
    from owner_bot.handlers import format_notification_settings_html
    from owner_bot.keyboards import get_notifications_settings_inline_keyboard
    html_text = format_notification_settings_html(notif_settings)
    kb = get_notifications_settings_inline_keyboard(notif_settings)
    await message.answer(html_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("toggle_notif:"))
async def on_main_bot_toggle_notification_cb(callback: CallbackQuery):
    if not is_bot_owner(callback.from_user.id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return
    action = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        if action == "parents":
            await crud.toggle_notification_setting("notify_parents", session)
            toast = "👨‍👩‍👧 Ota-ona xabarlari o'zgartirildi!"
        elif action == "children":
            await crud.toggle_notification_setting("notify_children", session)
            toast = "👦 Bola signallari o'zgartirildi!"
        elif action == "owner":
            await crud.toggle_notification_setting("notify_owner", session)
            toast = "👑 Owner nusxalari o'zgartirildi!"
        elif action == "all_on":
            await crud.set_all_notification_settings(True, session)
            toast = "🟢 Barcha xabarnomalar yoqildi!"
        elif action == "all_off":
            await crud.set_all_notification_settings(False, session)
            toast = "🔴 Barcha xabarnomalar o'chirildi!"
        elif action == "back_owner":
            from bot.keyboards import get_owner_panel_inline_keyboard
            text = (
                "👑 <b>ASOSCHI (OWNER) BOSHQARUV PANELI</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Asoschi: <b>@{settings.OWNER_USERNAME}</b>\n\n"
                "<i>Quyidagi tugmalar orqali platformani boshqarishingiz mumkin:</i>"
            )
            await callback.message.edit_text(text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
            await callback.answer()
            return
        else:
            toast = "Yangilandi!"

        notif_settings = await crud.get_notification_settings(session)

    from owner_bot.handlers import format_notification_settings_html
    from owner_bot.keyboards import get_notifications_settings_inline_keyboard
    html_text = format_notification_settings_html(notif_settings)
    kb = get_notifications_settings_inline_keyboard(notif_settings)
    try:
        await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(toast)


@router.callback_query(F.data == "adm_back_main")
async def on_owner_back_main_cb(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        stats = await crud.get_system_overview_stats(session)
    text = (
        "👑 <b>ASOSCHI (OWNER) BOSHQARUV PANELI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Asoschi: <b>@{settings.OWNER_USERNAME}</b> (ID: <code>{user_id}</code>)\n\n"
        "📊 <b>PLATFORMA BO'YICHA JONLI STATISTIKA:</b>\n"
        f"• 👥 Jami foydalanuvchilar: <b>{stats['total_users']}</b> ta\n"
        f"  ├ 👨‍👩‍👧 Ota-onalar: <b>{stats['total_parents']}</b> ta\n"
        f"  └ 👦 Farzandlar: <b>{stats['total_children']}</b> ta\n"
        f"• 📅 Bugungi a'zolar: <b>{stats['today_users']}</b> ta\n"
        f"• 🚨 Faol SOS signallari: <b>{stats['active_sos']}</b> ta\n"
        f"• 🛡 Geofence hududlar: <b>{stats['total_geofences']}</b> ta\n"
        f"• 📍 GPS koordinatalar: <b>{stats['total_location_logs']}</b> ta\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Quyidagi tugmalar orqali platformani to'liq boshqarishingiz yoki oddiy mijoz (ota-ona/farzand) sifatida kirib sinashingiz mumkin:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_owner_panel_inline_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(OwnerActionState.waiting_for_inspect_query, F.text)
async def on_owner_inspect_entered(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    async with AsyncSessionLocal() as session:
        user = await crud.search_user_by_query(session, query)
        if not user:
            await message.answer(f"❌ <b>'{query}'</b> bo'yicha foydalanuvchi topilmadi.", parse_mode="HTML")
            return

        parents = await crud.get_parents_for_child(session, user.id) if user.role == "child" else []
        children = await crud.get_children_for_parent(session, user.id) if user.role == "parent" else []
        latest_loc = await crud.get_latest_location(session, user.id)

        safe_name = html.escape(user.full_name or "Kiritilmagan")
        safe_uname = f"@{user.username}" if user.username else "Mavjud emas"
        phone = user.phone_number or "Kiritilmagan"
        code = user.web_access_code or "Mavjud emas"

        info = [
            f"👤 <b>FOYDALANUVCHI MA'LUMOTLARI:</b>\n━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>ID:</b> <code>{user.id}</code>",
            f"• <b>Ism:</b> <b>{safe_name}</b>",
            f"• <b>Username:</b> {safe_uname}",
            f"• <b>Rol:</b> <code>{user.role}</code>",
            f"• <b>Telefon:</b> <code>{phone}</code>",
            f"• <b>Web Kirish Kodi:</b> <code>{code}</code>",
            f"• <b>Tangalar:</b> 🪙 <code>{user.child_balance_coins or 0}</code> (so'm: <code>{user.child_balance_som or 0}</code>)",
            f"• <b>SOS Holati:</b> {'🚨 FAOL SOS!' if user.is_sos_active else '🟢 Xavfsiz'}",
        ]
        if parents:
            info.append(f"• <b>Ota-onalar:</b> " + ", ".join([f"{p.full_name} (<code>{p.id}</code>)" for p in parents]))
        if children:
            info.append(f"• <b>Farzandlar:</b> " + ", ".join([f"{c.full_name} (<code>{c.id}</code>)" for c in children]))
        if latest_loc:
            info.append(f"• <b>So'nggi GPS:</b> <a href=\"https://maps.google.com/?q={latest_loc.latitude},{latest_loc.longitude}\">{latest_loc.latitude:.5f}, {latest_loc.longitude:.5f}</a> (🔋 {latest_loc.battery_level}%)")

        info.append("━━━━━━━━━━━━━━━━━━━━━━")

        magic_url = f"{settings.RESOLVED_WEBAPP_URL}?auth_code={code}&role={user.role or 'parent'}&user_id={user.id}"
        inspect_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌐 WebApp Profilini Ochish (Magic Link)", url=magic_url)],
            [InlineKeyboardButton(text="🔙 Asosiy Panel", callback_data="own_main:stats")]
        ])
        await message.answer("\n".join(info), reply_markup=inspect_kb, parse_mode="HTML")


@router.message(OwnerActionState.waiting_for_magic_id, F.text)
async def on_owner_magic_entered(message: Message, state: FSMContext):
    await state.clear()
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat raqamli Telegram ID kiriting:")
        return

    target_id = int(raw)
    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, target_id)
        if not user:
            await message.answer(f"❌ ID <code>{target_id}</code> bo'yicha foydalanuvchi topilmadi.", parse_mode="HTML")
            return

        code = user.web_access_code or "CS-DEMO"
        role = user.role or "parent"
        magic_url = f"{settings.RESOLVED_WEBAPP_URL}?auth_code={code}&role={role}&user_id={user.id}"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🚀 {user.full_name or 'Foydalanuvchi'} hisobiga kirish", url=magic_url)],
            [InlineKeyboardButton(text="🔙 Panel", callback_data="own_main:stats")]
        ])
        await message.answer(
            f"✅ <b>Magic WebApp Login Link Tayyor!</b>\n\n"
            f"👤 Foydalanuvchi: <b>{html.escape(user.full_name or 'Foydalanuvchi')}</b> (ID: <code>{user.id}</code>)\n"
            f"🔑 Kirish kodi: <code>{code}</code>\n"
            f"🎭 Rol: <code>{role}</code>\n\n"
            f"🔗 <b>To'g'ridan-to'g'ri havola:</b>\n<code>{magic_url}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ==============================================================================
# ASOSCHI (OWNER) ADMINLARNI BOSHQARISH VA GLOBAL RESET HANDLERLARI
# ==============================================================================
@router.callback_query(F.data == "own_adm:add")
async def on_owner_adm_add_prompt(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return
    await state.set_state(OwnerActionState.waiting_for_add_admin_query)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="own_main:admins")]
    ])
    await callback.message.edit_text(
        "➕ <b>Yangi Admin qo'shish</b>\n\n"
        "Admin qilmoqchi bo'lgan foydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>ini kiriting:\n\n"
        "<i>Misol: <code>123456789</code> yoki <code>@foydalanuvchi</code></i>",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(OwnerActionState.waiting_for_add_admin_query, F.text)
async def on_owner_add_admin_entered(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        return
    query = message.text.strip()
    async with AsyncSessionLocal() as session:
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
                "Foydalanuvchi avval botga /start bosgan bo'lishi yoki uning raqamli Telegram ID raqami kiritilishi lozim.",
                parse_mode="HTML"
            )
            return

        admin = await crud.add_admin_user(
            session=session,
            user_id=new_admin_id,
            username=uname,
            full_name=fname,
            added_by=user_id,
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Adminlar ro'yxati", callback_data="own_main:admins")],
        [InlineKeyboardButton(text="🔙 Panel", callback_data="own_main:stats")]
    ])
    await message.answer(
        f"✅ <b>Yangi Administrator muvaffaqiyatli qo'shildi!</b>\n\n"
        f"👤 Ism: <b>{html.escape(admin.full_name or 'Admin')}</b>\n"
        f"🆔 Telegram ID: <code>{admin.id}</code>\n"
        f"🌐 Username: @{admin.username if admin.username else 'Mavjud emas'}\n\n"
        f"<i>Endi ushbu foydalanuvchi tizimda administrator vakolatlariga ega.</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "own_adm:del_list")
async def on_owner_adm_del_list(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        admins = await crud.list_admin_users(session)

    secondary_admins = [a for a in admins if not a.is_owner]
    if not secondary_admins:
        await callback.answer("Hozirda o'chirish uchun boshqa adminlar yo'q.", show_alert=True)
        return

    buttons = []
    for a in secondary_admins:
        name = a.full_name or f"Admin #{a.id}"
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {name} (ID: {a.id})",
                callback_data=f"own_adm_del:{a.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Adminlar ro'yxatiga", callback_data="own_main:admins")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "🗑 <b>O'chirmoqchi bo'lgan adminni tanlang:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("own_adm_del:"))
async def on_owner_adm_del_execute(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        removed = await crud.remove_admin_user(session, target_id)

    if removed:
        await callback.answer(f"Admin (ID: {target_id}) o'chirildi!", show_alert=True)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Adminlar ro'yxati", callback_data="own_main:admins")],
            [InlineKeyboardButton(text="🔙 Panel", callback_data="own_main:stats")]
        ])
        await callback.message.edit_text(
            f"✅ Admin (ID: <code>{target_id}</code>) vakolatlari bekor qilindi.",
            reply_markup=back_kb,
            parse_mode="HTML"
        )
    else:
        await callback.answer("Ushbu adminni o'chirib bo'lmadi (ehtimol u bosh ega).", show_alert=True)


@router.callback_query(F.data == "global_reset:keyboards")
async def on_global_reset_keyboards(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    await callback.message.edit_text("⏳ <i>Barcha foydalanuvchilarga yangi menyu yuborilmoqda va reset qilinmoqda...</i>", parse_mode="HTML")
    from owner_bot.service import perform_global_keyboards_refresh
    async with AsyncSessionLocal() as session:
        success, fails = await perform_global_keyboards_refresh(bot, session)

    await callback.message.edit_text(
        f"✅ <b>BARCHADA BOT MENYUSI YANGILANDI VA RESET QILINDI!</b>\n\n"
        f"• 🟢 Yangilangan foydalanuvchilar: <b>{success} ta</b>\n"
        f"• 🔴 Yetib bormaganlar: <b>{fails} ta</b>\n\n"
        f"Barcha foydalanuvchilarning chatida so'nggi tugmalar va xavfsizlik menyusi tiklandi.",
        reply_markup=get_owner_panel_inline_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Menyular yangilandi!")


@router.callback_query(F.data == "global_reset:cache_sos")
async def on_global_reset_cache_sos(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    from owner_bot.service import perform_global_cache_and_sos_reset
    async with AsyncSessionLocal() as session:
        res = await perform_global_cache_and_sos_reset(session)

    await callback.message.edit_text(
        f"✅ <b>KESH VA SOS SIGNALLARI TOZALANDI!</b>\n\n"
        f"• O'chirilgan faol SOS signallari: <b>{res['cleared_sos']} ta</b>\n"
        f"• Tizim sessiyalari to'liq normallashtirildi.",
        reply_markup=get_owner_panel_inline_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Tozalandi!")


@router.callback_query(F.data == "global_reset:ask_factory")
async def on_global_reset_ask_factory(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
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


@router.callback_query(F.data == "global_reset:confirm_factory")
async def on_global_reset_confirm_factory(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    if not is_bot_owner(user_id, callback.from_user.username):
        await callback.answer("⛔ Ruxsat berilmagan!", show_alert=True)
        return

    await callback.message.edit_text("⏳ <i>Barcha foydalanuvchilar boshlang'ich holatga qaytarilmoqda...</i>", parse_mode="HTML")
    from owner_bot.service import perform_global_factory_reset
    async with AsyncSessionLocal() as session:
        reset_count, notified = await perform_global_factory_reset(bot, session)

    await callback.message.edit_text(
        f"✅ <b>TIZIM TO'LIQ ZAVOD HOLATIGA QAYTARILDI (FACTORY RESET)!</b>\n\n"
        f"• 👤 Qayta tiklangan hisoblar: <b>{reset_count} ta</b>\n"
        f"• 📨 /start bildirishnomasi bordi: <b>{notified} ta</b>\n\n"
        f"Barcha ulanishlar uzildi va foydalanuvchilar qayta ro'yxatdan o'tish holatiga o'tkazildi.",
        reply_markup=get_owner_panel_inline_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Factory Reset yakunlandi!")


@router.message(Command("add_admin"))
async def handle_cmd_add_admin_main(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha egasi uchun ruxsat etilgan.")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/add_admin &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with AsyncSessionLocal() as session:
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
            added_by=user_id,
        )

    await message.answer(
        f"✅ <b>Administrator muvaffaqiyatli qo'shildi!</b>\n\n"
        f"👤 Ism: <b>{html.escape(admin.full_name or 'Admin')}</b>\n"
        f"🆔 ID: <code>{admin.id}</code>\n"
        f"🌐 Username: @{admin.username if admin.username else 'Mavjud emas'}",
        parse_mode="HTML"
    )


@router.message(Command("del_admin", "remove_admin"))
async def handle_cmd_del_admin_main(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha egasi uchun ruxsat etilgan.")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/del_admin &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with AsyncSessionLocal() as session:
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


@router.message(Command("admins"))
async def handle_cmd_admins_main(message: Message):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha egasi uchun ruxsat etilgan.")
        return
    async with AsyncSessionLocal() as session:
        admins = await crud.list_admin_users(session)
    lines = ["👥 <b>ADMINLAR RO'YXATI</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if not admins:
        lines.append("<i>Adminlar mavjud emas.</i>")
    else:
        for a in admins:
            tag = "👑 Bosh Ega" if a.is_owner else "👮 Admin"
            u_str = f"@{a.username}" if a.username else "N/A"
            lines.append(f"• {tag}: <b>{html.escape(a.full_name or 'Admin')}</b>\n  🆔 ID: <code>{a.id}</code> | {u_str}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("reset_all", "global_reset"))
async def handle_cmd_reset_all_main(message: Message):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha egasi uchun ruxsat etilgan.")
        return
    text = (
        "🔄 <b>BARCHADA BOTNI RESET QILISH VA YANGILASH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Quyidagi reset darajalaridan birini tanlang:\n\n"
        "1. <b>🔄 Menyu va tugmalarni yangilash:</b> Barcha faol ota-ona va bolalarga eng so'nggi bot tugmalari va menyularini qayta yuboradi.\n"
        "2. <b>🧹 Kesh va SOS tozalash:</b> Tizimdagi barcha faol SOS signallarini tozalaydi va sessiyalarni normallashtiradi.\n"
        "3. <b>⚠️ Factory Reset (Boshlang'ich holatga qaytarish):</b> Barcha ulanishlarni uzadi va barchani /start rol tanlashiga qaytaradi.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, reply_markup=get_reset_options_keyboard(), parse_mode="HTML")


@router.message(Command("reset_user"))
async def handle_cmd_reset_user_main(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not is_bot_owner(user_id, message.from_user.username):
        await message.answer("⚠️ Ushbu buyruq faqat loyiha egasi uchun ruxsat etilgan.")
        return
    if not command.args or not command.args.strip():
        await message.answer("⚠️ Foydalanish: <code>/reset_user &lt;Telegram_ID yoki @username&gt;</code>", parse_mode="HTML")
        return
    query = command.args.strip()
    async with AsyncSessionLocal() as session:
        target_user = await crud.search_user_by_query(session, query)
        target_id = target_user.id if target_user else (int(query) if query.isdigit() else None)
        if not target_id:
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return

        success, _ = await crud.reset_user_role(session, target_id)
        await session.commit()
        if success:
            await message.answer(f"✅ Foydalanuvchi (ID: <code>{target_id}</code>) to'liq reset qilindi va boshlang'ich holatga qaytarildi.", parse_mode="HTML")
        else:
            await message.answer(f"❌ Foydalanuvchi topilmadi yoki reset qilib bo'lmadi.")


# ==============================================================================
# TELEGRAM TWO-WAY DIRECT MESSAGING (PARENT <-> CHILD REAL-TIME WALKIE-TALKIE)
# ==============================================================================
@router.callback_query(F.data.startswith("p_reply:"))
async def on_parent_reply_callback(callback: CallbackQuery, state: FSMContext):
    child_id = int(callback.data.split(":")[1])
    await state.set_state(DirectMessagingState.waiting_for_parent_reply)
    await state.update_data(target_child_id=child_id)
    await callback.message.answer(
        "✍️ <b>Farzandingizga javob xabaringizni yozing:</b>\n\n"
        "<i>Xabaringiz farzandingiz telefoniga darhol Telegram va mobil ilova orqali yetkaziladi.</i>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(DirectMessagingState.waiting_for_parent_reply, F.text)
async def on_parent_reply_entered(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    child_id = data.get("target_child_id")
    await state.clear()
    if not child_id:
        return

    parent_id = message.from_user.id
    parent_name = html.escape(message.from_user.full_name or "Ota-onangiz")
    safe_text = html.escape(message.text)
    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")

    child_alert = (
        f"💬 <b>OTA-ONANGIZDAN TEZKOR XABAR!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Yuboruvchi: <b>{parent_name}</b>\n"
        f"💬 Xabar: <b>«{safe_text}»</b>\n"
        f"🕒 Vaqt: <b>{now_time}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Javob yozish uchun to'g'ridan-to'g'ri botga yozing yoki pastdagi tugmani bosing:</i>"
    )
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"c_reply:{parent_id}")]
    ])
    try:
        await bot.send_message(chat_id=child_id, text=child_alert, reply_markup=reply_kb, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to forward parent reply to child {child_id}: {e}")

    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(child_id, {
            "type": "parent_direct_message",
            "parent_id": parent_id,
            "parent_name": message.from_user.full_name or "Ota-onangiz",
            "text": message.text,
            "time": now_time
        })
    except Exception:
        pass

    await message.answer(
        f"✅ <b>Xabaringiz yetkazildi!</b> 🚀\n\n💬 <i>«{safe_text}»</i>",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("c_reply:"))
async def on_child_reply_callback(callback: CallbackQuery, state: FSMContext):
    parent_id = int(callback.data.split(":")[1])
    await state.set_state(DirectMessagingState.waiting_for_child_reply)
    await state.update_data(target_parent_id=parent_id)
    await callback.message.answer(
        "✍️ <b>Ota-onangizga javob xabaringizni yozing:</b>\n\n"
        "<i>Xabaringiz ota-onangiz telefoniga zudlik bilan yetkaziladi.</i>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(DirectMessagingState.waiting_for_child_reply, F.text)
async def on_child_reply_entered(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    parent_id = data.get("target_parent_id")
    await state.clear()
    if not parent_id:
        return

    child_id = message.from_user.id
    child_name = html.escape(message.from_user.full_name or "Farzand")
    safe_text = html.escape(message.text)
    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")

    parent_alert = (
        f"💬 <b>FARZANDINGIZDAN TEZKOR JAVOB!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Farzand: <b>{child_name}</b>\n"
        f"💬 Xabar: <b>«{safe_text}»</b>\n"
        f"🕒 Vaqt: <b>{now_time}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"p_reply:{child_id}")],
        [InlineKeyboardButton(text="📍 Xaritada ko'rish", callback_data=f"child_view:{child_id}")]
    ])
    try:
        await bot.send_message(chat_id=parent_id, text=parent_alert, reply_markup=reply_kb, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to deliver child reply to parent {parent_id}: {e}")

    try:
        from api.webapp_api import ws_manager
        await ws_manager.send_to_user(parent_id, {
            "type": "child_direct_message",
            "child_id": child_id,
            "child_name": message.from_user.full_name or "Farzand",
            "text": message.text,
            "time": now_time
        })
    except Exception:
        pass

    await message.answer(
        f"✅ <b>Xabaringiz ota-onangizga darhol yetkazildi!</b> 🚀\n\n💬 <i>«{safe_text}»</i>",
        reply_markup=get_child_main_keyboard(child_id),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("set_child_phone:"))
async def on_set_child_phone_callback(callback: CallbackQuery, state: FSMContext):
    """Parent initiates setting or updating child's phone number for offline SMS commands."""
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        if not parents or parent_id not in [p.id for p in parents]:
            await callback.answer("Ruxsat berilmagan: Siz ushbu farzandning ota-onasi emassiz.", show_alert=True)
            return

        child = await crud.get_user(session, child_id)
        child_name = child.full_name or "Farzand" if child else "Farzand"
        cur_phone = child.phone_number or "Kiritilmagan" if child else "Kiritilmagan"

    await state.set_state(ChildPhoneSaveState.waiting_for_child_phone)
    await state.update_data(target_child_id=child_id)

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"child_view:{child_id}")]
    ])
    safe_name = html.escape(child_name)
    safe_p = html.escape(cur_phone)
    await callback.message.answer(
        f"📱 <b>Farzandingiz telefon raqamini kiriting</b>\n\n"
        f"👤 Farzand: <b>{safe_name}</b>\n"
        f"📞 Hozirgi raqam: <code>{safe_p}</code>\n\n"
        f"Farzandingiz telefonidagi SIM-karta raqamini yozing (masalan: <code>+998901234567</code>):\n"
        f"<i>Ushbu raqam internet o'chirilgan holatlarda 1-bosishda SMS orqali GPS olish va boshqarish uchun xizmat qiladi.</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(ChildPhoneSaveState.waiting_for_child_phone, F.text)
async def on_child_phone_entered(message: Message, state: FSMContext):
    data = await state.get_data()
    child_id = data.get("target_child_id")
    raw_text = message.text.strip()

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
        await message.answer("⚠️ Telefon raqam noto'g'ri formatda. Iltimos, qaytadan kiriting (masalan: `+998901234567`):", parse_mode="Markdown")
        return

    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if child:
            child.phone_number = cleaned
            await session.commit()
            child_name = child.full_name or "Farzand"
        else:
            child_name = "Farzand"

    await state.clear()
    
    test_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 1-Tap SMS: GPS so'rab ko'rish", url=f"sms:{cleaned}?body=%23GPS:1234")],
        [InlineKeyboardButton(text="⬅️ Farzand boshqaruviga qaytish", callback_data=f"child_view:{child_id}")]
    ])
    safe_cname = html.escape(child_name)
    await message.answer(
        f"✅ <b>{safe_cname}</b> uchun telefon raqami saqlandi: <code>{cleaned}</code>\n\n"
        f"Endi internet butunlay o'chirilgan bo'lsa ham, pastdagi <b>1-Tap SMS</b> tugmasi orqali bolaning sun'iy yo'ldosh GPS koordinatasini olishingiz mumkin!",
        reply_markup=test_kb,
        parse_mode="HTML"
    )


# ==============================================================================
# "ZVUK VOKRUG" / AMBIENT SOUND MONITORING HANDLERS (KILLER BENCHMARK FEATURE)
# ==============================================================================

@router.message(Command("listen", "zvuk", "audio"))
@router.message(F.text.in_({
    "🎙 Atrof ovozi («Звук вокруг»)",
    "Atrof ovozi («Звук вокруг»)",
    "🎙 Atrof ovozi",
    "Atrof ovozi",
    "Звук вокруг"
}))
async def parent_ambient_sound_handler(message: Message, bot: Optional[Bot] = None):
    """Parent triggers remote ambient sound recording on child device."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, user_id)
        if not children and user_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
            all_ch = (await session.execute(select(User).where(User.role == "child"))).scalars().all()
            children = list(all_ch)

        if not children:
            await message.answer(
                "🎙 <b>Atrof ovozini eshitish («Звук вокруг»):</b>\n\n"
                "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.\n"
                "Iltimos, avval farzandingizni ulang yoki botni sinab ko'rish uchun namuna bolani tanlang.",
                reply_markup=get_empty_children_kb(),
                parse_mode="HTML"
            )
            return

        if len(children) == 1:
            child = children[0]
            c_name = html.escape(child.full_name or f"Farzand #{child.id}")
            kb = get_ambient_sound_inline_keyboard(child.id)
            await message.answer(
                f"🎙 <b>ATROF OVOZINI ESHITISH («ЗВУК ВОКРУГ»)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Farzand: <b>{c_name}</b> (ID: <code>{child.id}</code>)\n\n"
                f"<i>Bolangiz atrofidagi suhbatlar va vaziyatni yozib olish uchun vaqtni tanlang:</i>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            buttons = [
                [InlineKeyboardButton(text=f"👦 {c.full_name or c.id}", callback_data=f"sel_ambient:{c.id}")]
                for c in children
            ]
            buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")])
            await message.answer(
                "🎙 <b>Qaysi farzandingizning atrof ovozini eshitmoqchisiz?</b>\n\n"
                "Kerakli farzandni tanlang:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("sel_ambient:"))
async def on_select_ambient_child_cb(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
    c_name = html.escape(child.full_name or f"Farzand #{child.id}") if child else f"Farzand #{child_id}"
    kb = get_ambient_sound_inline_keyboard(child_id)
    await callback.message.edit_text(
        f"🎙 <b>ATROF OVOZINI ESHITISH («ЗВУК ВОКРУГ»)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Farzand: <b>{c_name}</b>\n\n"
        f"<i>Yozib olish davomiyligini tanlang:</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rec_ambient:"))
async def on_record_ambient_cb(callback: CallbackQuery):
    parts = callback.data.split(":")
    child_id = int(parts[1])
    duration = int(parts[2]) if len(parts) > 2 else 20
    parent_id = callback.from_user.id

    from api.webapp_api import pending_child_notifications, ws_manager
    import uuid

    cmd = {
        "type": "record_ambient_sound",
        "duration_seconds": duration,
        "parent_id": parent_id,
        "request_id": uuid.uuid4().hex[:8],
        "time": datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")
    }

    if child_id not in pending_child_notifications:
        pending_child_notifications[child_id] = []
    pending_child_notifications[child_id].append(cmd)

    try:
        await ws_manager.send_to_user(child_id, cmd)
    except Exception:
        pass

    is_child_online = bool(ws_manager.active_connections.get(child_id))
    online_status = (
        "🟢 <b>Qurilma onlayn:</b> Buyruq yetkazildi, audio yozish boshlandi."
        if is_child_online else
        "📡 <b>Qurilma holati:</b> Bola telefoni hozirda fon rejimida / oflayn. Buyruq navbatga qo'yildi va bola ilovani ochishi yoki Android xizmati uyg'onishi bilanoq yozib olinadi."
    )
    await callback.message.edit_text(
        f"⏳ <b>BUYRUQ YUBORILDI!</b>\n\n"
        f"Farzandingiz telefoniga <b>{duration} soniyalik</b> atrof ovozini yozish buyrug'i jo'natildi.\n\n"
        f"{online_status}\n\n"
        f"<i>📡 Audio yozib olingach, ovozli xabar (Voice) ushbu chatga avtomatik yetkaziladi...</i>",
        parse_mode="HTML"
    )
    await callback.answer(f"{duration} soniyalik yozish boshlandi!", show_alert=True)


# ==============================================================================
# REMOTE SIREN (LOUD ALARM SEARCH / OVERRIDE SILENT MODE)
# ==============================================================================

@router.message(Command("siren", "signal", "chalish"))
@router.message(F.text.in_({
    "🚨 Masofaviy Siren (Qidiruv)",
    "Masofaviy Siren (Qidiruv)",
    "🚨 Baland Siren (Qidiruv)",
    "Baland Siren (Qidiruv)",
    "Siren",
    "Signal"
}))
async def parent_siren_trigger_handler(message: Message, bot: Optional[Bot] = None):
    """Parent triggers loud search siren on child device, overriding silent mode."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, user_id)
        if not children and user_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
            all_ch = (await session.execute(select(User).where(User.role == "child"))).scalars().all()
            children = list(all_ch)

        if not children:
            await message.answer(
                "🚨 <b>Baland ovozli signal (Siren):</b>\n\n"
                "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.",
                reply_markup=get_empty_children_kb(),
                parse_mode="HTML"
            )
            return

        if len(children) == 1:
            child = children[0]
            c_name = html.escape(child.full_name or f"Farzand #{child.id}")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔊 Ha, baland signal chalish!", callback_data=f"do_siren:{child.id}")],
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")]
            ])
            await message.answer(
                f"🚨 <b>MASOFAVIY BALAND SIGNAL (SIREN)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Farzand: <b>{c_name}</b>\n\n"
                f"<i>Telefon ovozsiz (silent/bez zvuk) rejimda bo'lsa ham maksimal balandlikda ovoz chiqariladi. Qidiruv signalini yoqasizmi?</i>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            buttons = [
                [InlineKeyboardButton(text=f"👦 {c.full_name or c.id}", callback_data=f"do_siren:{c.id}")]
                for c in children
            ]
            buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")])
            await message.answer(
                "🚨 <b>Qaysi farzandingizning telefoniga baland signal yubormoqchisiz?</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode="HTML"
            )


@router.callback_query(F.data.startswith("do_siren:"))
async def on_do_siren_cb(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id

    from api.webapp_api import parent_siren_endpoint
    try:
        async with AsyncSessionLocal() as session:
            res = await parent_siren_endpoint(child_id=child_id, parent_id=parent_id, session=session)
        await callback.message.edit_text(
            "🔊 <b>BALAND SIREN CHALINDI!</b>\n\n"
            "Farzandingiz telefoniga maksimal ovozli qidiruv signali yuborildi. Telefon ovozsiz rejimda bo'lsa ham yangraydi!",
            parse_mode="HTML"
        )
        await callback.answer("Baland signal yuborildi!", show_alert=True)
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)


# ==============================================================================
# FALLBACK TEXT HANDLER WITH TWO-WAY WALKIE-TALKIE DISPATCH
# ==============================================================================
@router.message(StateFilter(None), F.text)
async def fallback_text_handler(message: Message, bot: Bot):
    """
    Intelligent two-way text dispatcher:
    - If Owner (@fayzbek1331) -> routes owner commands & panels
    - If Child -> forwards message INSTANTLY to all linked parents (Telegram + WebApp WebSocket)
    - If Parent -> forwards message INSTANTLY to child (Telegram + WebApp WebSocket)
    """
    user_id = message.from_user.id
    raw_text = (message.text or "").strip()

    # Explicit fallback router for known feature buttons
    if raw_text.startswith("🎙 Atrof ovozi") or "Звук вокруг" in raw_text:
        await parent_ambient_sound_handler(message, bot)
        return
    if raw_text.startswith("🚨 Masofaviy Siren") or raw_text.startswith("🚨 Baland Siren") or raw_text in {"Siren", "Signal"}:
        await parent_siren_trigger_handler(message, bot)
        return
    if raw_text in {
        "📱 Ilovalar & Ekran tahlili", "Ilovalar & Ekran tahlili", "📱 Ekran vaqti va Bloklash",
        "Ekran vaqti va Bloklash", "📱 Ekran vaqti", "Ekran vaqti", "📱 Ilovalar nazorati",
        "Ilovalar nazorati", "Ilovalar", "ilovalar", "bloklash"
    }:
        await parent_screen_time_handler(message)
        return
    if raw_text.startswith("📚 Maktab rejimi") or raw_text == "/school":
        await on_parent_school_mode_command(message, bot)
        return
    if raw_text.startswith("👟 Kunlik harakat maqsadi") or raw_text in {"/goal", "/walking_goal", "/qadam", "Harakat maqsadi"}:
        await on_parent_walking_goal_command(message, bot)
        return

    # Ignore system command buttons so they are never forwarded as messages
    KNOWN_SYSTEM_BUTTONS = {
        "🎙 Atrof ovozi («Звук вокруг»)", "Atrof ovozi («Звук вокруг»)", "🎙 Atrof ovozi", "Atrof ovozi", "Звук вокруг",
        "🚨 Masofaviy Siren (Qidiruv)", "Masofaviy Siren (Qidiruv)", "🚨 Baland Siren (Qidiruv)", "Baland Siren (Qidiruv)",
        "Siren", "Signal",
        "📱 Telefon uchun ilova (Android & iOS)", "💻 Noutbuk uchun dastur",
        "🤖 Android ilovasi (APK)", "🍎 iPhone / iOS (O'rnatish)",
        "📍 Farzandning hozirgi joylashuvi", "🔋 Batareya va Holat",
        "💬 Farzandga ko'rsatma", "📍 Kerakli joylar",
        "📋 Vazifalar va Tangalar", "👶 Farzandlarim", "➕ Farzand ulash (Kodi)",
        "👑 Asoschi (Owner) Boshqaruv", "🚨 SOS (Xavf tugmasi)", "SOS", "sos",
        "✅ Yetib keldim (Check-in)", "📞 Ota-onamga qo'ng'iroq",
        "💬 Ota-onaga tezkor xabar", "📍 Jonli joylashuvimni yuborish", "📱 Web App (Sayt)"
    }
    if raw_text in KNOWN_SYSTEM_BUTTONS:
        return

    # 1. Check if Owner command was sent
    if is_bot_owner(user_id, message.from_user.username):
        if raw_text in {"👑 Asoschi (Owner) Boshqaruv", "/owner", "/panel", "/stats", "📊 Umumiy Statistika", "Statistika", "statistika"}:
            await on_owner_dashboard_command(message, bot)
            return

    async with AsyncSessionLocal() as session:
        user = await crud.get_user(session, user_id)
        if not user or not user.role:
            await message.answer(
                "Iltimos, avval rolingizni tanlang:",
                reply_markup=get_role_selection_keyboard()
            )
            return

        now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")
        safe_text = html.escape(raw_text)

        # 2. CHILD SENDS TEXT -> Deliver to parents
        if user.role == "child":
            parents = await crud.get_parents_for_child(session, user_id)
            if not parents:
                await message.answer(
                    "⚠️ <b>Siz hali birorta ham ota-onaga ulanmagansiz.</b>\n\n"
                    "Ota-onangizga ulanish uchun <b>«👨‍👩‍👧 Ota-onamni taklif qilish»</b> tugmasini bosing va kodni bering.",
                    reply_markup=get_child_main_keyboard(user_id),
                    parse_mode="HTML"
                )
                return

            child_name = html.escape(user.full_name or "Farzand")
            latest_loc = await crud.get_latest_location(session, user_id)
            loc_str = ""
            if latest_loc and latest_loc.latitude and latest_loc.longitude:
                maps_url = f"https://maps.google.com/?q={latest_loc.latitude:.6f},{latest_loc.longitude:.6f}"
                batt_str = f" (🔋 {latest_loc.battery_level}%)" if latest_loc.battery_level is not None else ""
                loc_str = f"\n📍 <b>Joylashuv:</b> <a href=\"{maps_url}\">Xaritada ochish</a>{batt_str}"

            parent_alert = (
                f"💬 <b>FARZANDINGIZDAN TEZKOR XABAR!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Farzand: <b>{child_name}</b>\n"
                f"💬 Xabar: <b>«{safe_text}»</b>\n"
                f"🕒 Vaqt: <b>{now_time}</b>"
                f"{loc_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Javob berish uchun pastdagi tugmani bosing:</i>"
            )
            reply_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"p_reply:{user_id}")],
                [InlineKeyboardButton(text="📍 Xaritada ko'rish", callback_data=f"child_view:{user_id}")]
            ])

            for p in parents:
                if not await crud.is_parent_notification_enabled(p.id, "messages", session):
                    continue
                try:
                    await bot.send_message(chat_id=p.id, text=parent_alert, reply_markup=reply_kb, parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"Failed to forward child message to parent {p.id}: {e}")

            try:
                from api.webapp_api import ws_manager
                for p in parents:
                    await ws_manager.send_to_user(p.id, {
                        "type": "child_direct_message",
                        "child_id": user_id,
                        "child_name": user.full_name or "Farzand",
                        "text": raw_text,
                        "time": now_time
                    })
            except Exception:
                pass

            await message.answer(
                f"✅ <b>Xabaringiz ota-onangizga darhol yetkazildi!</b> 🚀\n\n"
                f"💬 <i>«{safe_text}»</i>\n\n"
                f"Ota-onangiz javob yozishi bilan bu yerda ko'rinadi.",
                reply_markup=get_child_main_keyboard(user_id),
                parse_mode="HTML"
            )
            return

        # 3. PARENT SENDS TEXT -> Deliver to child
        if user.role == "parent":
            children = await crud.get_children_for_parent(session, user_id)
            if not children:
                await message.answer(
                    "⚠️ <b>Sizga hali farzand biriktirilmagan.</b>\n\n"
                    "Farzand telefonini ulash uchun <b>«➕ Farzand ulash (Kodi)»</b> tugmasini bosing.",
                    reply_markup=get_parent_main_keyboard(user_id),
                    parse_mode="HTML"
                )
                return

            parent_name = html.escape(user.full_name or "Ota-onangiz")
            child_alert = (
                f"💬 <b>OTA-ONANGIZDAN TEZKOR XABAR!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Yuboruvchi: <b>{parent_name}</b>\n"
                f"💬 Xabar: <b>«{safe_text}»</b>\n"
                f"🕒 Vaqt: <b>{now_time}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Javob yozish uchun to'g'ridan-to'g'ri botga yozing yoki pastdagi tugmani bosing:</i>"
            )
            reply_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Javob yozish", callback_data=f"c_reply:{user_id}")]
            ])

            for ch in children:
                try:
                    await bot.send_message(chat_id=ch.id, text=child_alert, reply_markup=reply_kb, parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"Failed to forward parent message to child {ch.id}: {e}")

            try:
                from api.webapp_api import ws_manager
                for ch in children:
                    await ws_manager.send_to_user(ch.id, {
                        "type": "parent_direct_message",
                        "parent_id": user_id,
                        "parent_name": user.full_name or "Ota-onangiz",
                        "text": raw_text,
                        "time": now_time
                    })
            except Exception:
                pass

            target_names = ", ".join([html.escape(c.full_name or "Farzand") for c in children])
            await message.answer(
                f"✅ <b>Xabaringiz {target_names}ga darhol yetkazildi!</b> 🚀\n\n"
                f"💬 <i>«{safe_text}»</i>",
                reply_markup=get_parent_main_keyboard(user_id),
                parse_mode="HTML"
            )
            return



@router.errors()
async def bot_global_error_handler(event: ErrorEvent):
    """Global catch-all error handler for Telegram bot updates to prevent silent failures."""
    logger.error(f"Global bot error intercepted: {event.exception}", exc_info=event.exception)
    try:
        update = event.update
        if update and update.callback_query:
            try:
                await update.callback_query.answer("⚠️ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.", show_alert=True)
            except Exception:
                pass
        elif update and update.message:
            try:
                await update.message.answer("⚠️ Buyruqni bajarishda texnik xatolik yuz berdi. Iltimos, bir ozdan so'ng qayta urinib ko'ring.")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Error handler dispatch failed: {e}")

# ==============================================================================
# PARENT WALKING GOAL COMMAND & HANDLER (KUNLIK HARAKAT MAQSADI)
# ==============================================================================
@router.message(Command("goal", "walking_goal", "qadam", "masofa"))
async def on_parent_walking_goal_command(message: Message, bot: Optional[Bot] = None):
    """Parent sets custom daily walking distance / step goal for child."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, user_id)
        if not children and user_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
            from db.models import User
            from sqlalchemy import select
            all_ch = (await session.execute(select(User).where(User.role == "child"))).scalars().all()
            children = list(all_ch)

        if not children:
            await message.answer(
                "👟 <b>Kunlik Harakat Maqsadi:</b>\n\n"
                "⚠️ Sizga hali hech qanday farzand hisobi ulanmagan.",
                parse_mode="HTML"
            )
            return

        child = children[0]
        policy = await crud.get_or_create_device_policy(session, child.id)
        cur_km = getattr(policy, "daily_walking_goal_km", 3.0) or 3.0
        cur_steps = getattr(policy, "daily_walking_goal_steps", 5000) or int(cur_km * 1428)

        c_name = html.escape(child.full_name or f"Farzand #{child.id}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 2.0 km (~2,800 qadam)", callback_data=f"set_wgoal:{child.id}:2.0:2856"),
                InlineKeyboardButton(text="🎯 3.0 km (~4,300 qadam)", callback_data=f"set_wgoal:{child.id}:3.0:4284")
            ],
            [
                InlineKeyboardButton(text="🎯 5.0 km (~7,100 qadam)", callback_data=f"set_wgoal:{child.id}:5.0:7140"),
                InlineKeyboardButton(text="🎯 8.0 km (~11,400 qadam)", callback_data=f"set_wgoal:{child.id}:8.0:11424")
            ],
            [
                InlineKeyboardButton(text="📱 Saytda to'liq sozlash", web_app=WebAppInfo(url=settings.RESOLVED_WEBAPP_URL)),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")
            ]
        ])

        await message.answer(
            f"👟 <b>KUNLIK HARAKAT MAQSADI (FAVOLLIK REJASI)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Farzand: <b>{c_name}</b>\n"
            f"📊 Joriy maqsad: <b>{cur_km} km ({cur_steps:,} qadam)</b>\n\n"
            f"<i>Farzandingiz bugun sog'lom faollik uchun qancha masofa bosib o'tishi kerakligini belgilang:</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("set_wgoal:"))
async def on_set_walking_goal_cb(callback: CallbackQuery):
    """Save selected walking goal."""
    parts = callback.data.split(":")
    child_id = int(parts[1])
    km = float(parts[2])
    steps = int(parts[3])

    async with AsyncSessionLocal() as session:
        policy = await crud.get_or_create_device_policy(session, child_id)
        policy.daily_walking_goal_km = km
        policy.daily_walking_goal_steps = steps
        await session.commit()

        # Broadcast via WebSocket to WebApp and Child App
        try:
            from api.webapp_api import ws_manager
            parents = await crud.get_parents_for_child(session, child_id)
            ws_msg = {
                "type": "walking_goal_update",
                "child_id": child_id,
                "target_km": km,
                "target_steps": steps
            }
            for p in parents:
                await ws_manager.send_to_user(p.id, ws_msg)
            await ws_manager.send_to_user(child_id, ws_msg)
        except Exception:
            pass

    await callback.message.edit_text(
        f"✅ <b>KUNLIK MAQSAD SAQLANDI!</b>\n\n"
        f"Farzandingiz uchun bugungi harakat normasi: <b>{km} km ({steps:,} qadam)</b> qilib belgilandi. 🎯🏃‍♂️\n\n"
        f"<i>Farzand ushbu masofani bosib o'tgach, tizim uni avtomatik rag'batlantiradi va sizga xabar beradi.</i>",
        parse_mode="HTML"
    )
    await callback.answer(f"Maqsad: {km} km qilib belgilandi!")


# ==============================================================================
# 2FA WEB LOGIN APPROVAL CALLBACK HANDLER (RUXSAT BERISH / RAD ETISH)
# ==============================================================================
@router.callback_query(F.data.startswith("login_auth:"))
async def on_login_approval_callback(callback: CallbackQuery, bot: Bot):
    """Handle 1-tap Allow / Deny login approval for web access with zero-failure guarantee."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Noto'g'ri so'rov formati.")
        return

    action = parts[1]  # "allow" or "deny"
    req_id = parts[2]

    # Security check: only verified owner
    is_owner = (callback.from_user.id in (6707839718, 1) or callback.from_user.username == "fayzbek1331")
    if not is_owner:
        await callback.answer("❌ Bu amal faqat tizim egasiga ruxsat etilgan!", show_alert=True)
        return

    now_time = datetime.now(TASHKENT_TZ).strftime("%H:%M")
    from api.login_approval import get_approval_request, set_approval_status
    req = await get_approval_request(req_id)

    # If server redeployed/restarted while user waited, reconstruct approval gracefully
    if not req:
        if action == "allow":
            await set_approval_status(req_id, "approved", user_id=6707839718, code="1")
            await callback.answer("✅ Kirishga ruxsat berildi!", show_alert=True)
            try:
                msg_ok = (
                    "✅ <b>KIRISHGA RUXSAT BERILDI!</b> 🚀\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 Vaqt: <b>{now_time}</b>\n\n"
                    "<i>Akkauntingizga veb-sayt / ilova orqali muvaffaqiyatli kirildi.</i>"
                )
                await callback.message.edit_text(msg_ok, parse_mode="HTML")
            except Exception:
                pass
            return
        else:
            await callback.answer("Kirish so'rovi bekor qilingan.", show_alert=True)
            return

    if req["status"] != "pending":
        if req["status"] == "expired" and action == "allow":
            # Owner explicitly approved even if window elapsed: honor Owner decision
            pass
        else:
            await callback.answer(f"Bu so'rov avval hal qilingan: {req['status']}.", show_alert=True)
            return

    ip_addr = req.get("client_ip", "Nomalum")

    if action == "allow":
        await set_approval_status(req_id, "approved", user_id=req.get("user_id", 6707839718), code=req.get("code", "1"))
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
        await set_approval_status(req_id, "rejected", user_id=req.get("user_id", 6707839718), code=req.get("code", "1"))
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


@router.message(Command("test_daily_report"))
@router.message(Command("hisobot"))
async def handle_main_bot_test_daily_report(message: Message, bot: Bot):
    """Manually triggers daily safety report dispatch for owner / parents."""
    user_id = message.from_user.id
    await message.answer("🌙 <i>Kunlik xavfsizlik hisoboti tayyorlanmoqda va yuborilmoqda...</i>", parse_mode="HTML")
    try:
        from bot.scheduler import dispatch_nightly_safety_reports
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            sent_count = await dispatch_nightly_safety_reports(bot, session, force_target_id=user_id)
            await session.commit()
        if sent_count > 0:
            await message.answer(f"✅ <b>Kunlik xavfsizlik hisoboti muvaffaqiyatli jo'natildi!</b>\n({sent_count} ta hisobot xabari yuborildi)", parse_mode="HTML")
        else:
            await message.answer("ℹ️ <i>Bog'langan farzandlar hisoboti topilmadi.</i>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in handle_main_bot_test_daily_report: {e}")
        await message.answer(f"❌ Xatolik yuz berdi: {html.escape(str(e))}")


# ==============================================================================
# PARENT NOTIFICATIONS & MUTE MANAGEMENT HANDLERS
# ==============================================================================

@router.message(F.text.in_(["🔔 Xabarnomalar", "🔔 Xabarnomalar sozlamasi", "🔔 Xabarnomalar / Ovoz", "/notifications", "/xabarlar", "/bildirishnomalar"]))
async def on_parent_notifications_cmd(message: Message):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        settings = await crud.get_parent_notification_settings(user_id, session)
    from bot.keyboards import format_parent_notification_settings_html, get_parent_notifications_inline_keyboard
    html_text = format_parent_notification_settings_html(settings)
    kb = get_parent_notifications_inline_keyboard(settings)
    await message.answer(html_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "parent_notifications_menu")
async def on_parent_notifications_cb_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        settings = await crud.get_parent_notification_settings(user_id, session)
    from bot.keyboards import format_parent_notification_settings_html, get_parent_notifications_inline_keyboard
    html_text = format_parent_notification_settings_html(settings)
    kb = get_parent_notifications_inline_keyboard(settings)
    try:
        await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(html_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_parent_notif:"))
async def on_toggle_parent_notif_cb(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        if action == "sos":
            await crud.toggle_parent_notification_setting(user_id, "notify_sos", session)
            toast = "🚨 SOS xabarlari holati o'zgartirildi!"
        elif action == "messages":
            await crud.toggle_parent_notification_setting(user_id, "notify_messages", session)
            toast = "💬 Boladan keladigan xabarlar holati o'zgartirildi!"
        elif action == "geofence":
            await crud.toggle_parent_notification_setting(user_id, "notify_geofence", session)
            toast = "📍 Geozona signallari holati o'zgartirildi!"
        elif action == "all_off":
            await crud.set_all_parent_notification_settings(user_id, False, session)
            toast = "🔴 Barcha bildirishnomalar o'chirildi (Mute)!"
        elif action == "all_on":
            await crud.set_all_parent_notification_settings(user_id, True, session)
            toast = "🟢 Barcha bildirishnomalar yoqildi!"
        else:
            toast = "Yangilandi!"

        settings = await crud.get_parent_notification_settings(user_id, session)

    from bot.keyboards import format_parent_notification_settings_html, get_parent_notifications_inline_keyboard
    html_text = format_parent_notification_settings_html(settings)
    kb = get_parent_notifications_inline_keyboard(settings)
    try:
        await callback.message.edit_text(html_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(toast)


@router.callback_query(F.data == "back_to_settings")
async def on_back_to_settings_cb(callback: CallbackQuery):
    from bot.keyboards import get_settings_keyboard
    text = "⚙️ <b>SOZLAMALAR VA HISOB BOSHQARUVI</b>\n\nQuyidagi amallardan birini tanlang:"
    try:
        await callback.message.edit_text(text, reply_markup=get_settings_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_settings_keyboard(), parse_mode="HTML")
    await callback.answer()
