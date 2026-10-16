import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.database import AsyncSessionLocal
import db.crud as crud
from bot.geofence import evaluate_geofences_and_notify
from bot.keyboards import (
    get_role_selection_keyboard,
    get_parent_main_keyboard,
    get_child_main_keyboard,
    get_pairing_share_keyboard,
    get_child_details_keyboard,
    get_task_approval_keyboard,
    get_child_task_action_keyboard,
    get_sos_alert_parent_keyboard,
    get_geofences_menu_keyboard,
    get_radius_selection_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()


# FSM States
class ChildLinkState(StatesGroup):
    waiting_for_code = State()


class GeofenceAddState(StatesGroup):
    waiting_for_name = State()
    waiting_for_location = State()
    waiting_for_radius = State()


class TaskCreateState(StatesGroup):
    waiting_for_child = State()
    waiting_for_title = State()
    waiting_for_reward = State()


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
                # Set user as child and link to parent
                await crud.update_user_role(session, user_id, "child")
                await crud.link_parent_child(session, parent.id, user_id, nickname=full_name)
                await session.commit()

                # Notify child
                await message.answer(
                    f"🎉 **Tabriklaymiz!**\n\n"
                    f"Siz **{parent.full_name or 'Ota-onangiz'}** hisobiga muvaffaqiyatli ulandingiz.\n"
                    f"Endi siz xavfsiz himoyadasiz. Quyidagi menyudan foydalanishingiz mumkin:",
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
                            f"Endi farzandingizning joylashuvini jonli kuzatishingiz mumkin."
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
                f"Uланган farzandlar soni: **{len(children)} nafar**.\n\n"
                f"Farzandingiz joylashuvini real vaqt rejimida kuzatish uchun "
                f"**'🗺 Xaritada ko'rish (Web App)'** tugmasini bosing.",
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
            f"🌟 **Smart Parental Control & Child Location Tracker** tizimiga xush kelibsiz!\n\n"
            f"Iltimos, tizimdan foydalanish uchun rolingizni tanlang:",
            reply_markup=get_role_selection_keyboard(),
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("set_role:"))
async def on_role_selected(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle role selection callback."""
    role = callback.data.split(":")[1]
    user_id = callback.from_user.id
    full_name = callback.from_user.full_name

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
                "Iltimos, ota-onangiz bergan **6 xonali ulanish kodi**ni kiriting (masalan: `123456`):",
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

        # Success messages
        await message.answer(
            f"🎉 **Ajoyib! Ota-onangizga ({parent.full_name or 'Ota-ona'}) muvaffaqiyatli ulandingiz!**\n\n"
            f"Endi siz xavfsiz himoyadasiz. Quyidagi menyu orqali har doim aloqada bo'lishingiz mumkin:",
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
                    f"Farzandingiz endi sizning xaritangizda ko'rinadi."
                ),
                reply_markup=get_parent_main_keyboard(parent.id),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify parent about code link: {e}")


# ---------------------------------------------------------
# Real-Time Location Tracking Handlers (Live & Static Pins)
# ---------------------------------------------------------

@router.message(F.location)
@router.edited_message(F.location)
async def on_location_received(message: Message, bot: Bot):
    """Handle incoming location from child (both native live location updates and static pins)."""
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
        if not user or user.role != "child":
            return

        # Log location
        await crud.log_location(
            session=session,
            child_id=user_id,
            latitude=lat,
            longitude=lon,
            accuracy=accuracy,
            source=source
        )

        # Check geofences and send alerts if entered/exited
        events = await evaluate_geofences_and_notify(
            bot=bot,
            session=session,
            child=user,
            lat=lat,
            lon=lon
        )

        # If child is in active SOS state, forward location update directly to parents
        if user.is_sos_active:
            parents = await crud.get_parents_for_child(session, user_id)
            for p in parents:
                try:
                    await bot.send_location(
                        chat_id=p.id,
                        latitude=lat,
                        longitude=lon
                    )
                except Exception as e:
                    logger.error(f"Failed to forward SOS location to parent {p.id}: {e}")

        await session.commit()

    # If static pin message, send a confirmation to child
    if not live_period and message.text is None:
        await message.answer("📍 Joylashuvingiz qabul qilindi va tizimda yangilandi!", parse_mode="Markdown")


# ---------------------------------------------------------
# SOS Emergency Feature
# ---------------------------------------------------------

@router.message(F.text == "🚨 SOS (Xavf tugmasi)")
async def on_sos_pressed(message: Message, bot: Bot):
    """Urgent SOS alert triggered by child."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    async with AsyncSessionLocal() as session:
        child = await crud.get_user(session, child_id)
        if not child or child.role != "child":
            await message.answer("Ushbu tugma faqat farzand hisobi uchun mo'ljallangan.")
            return

        # Activate SOS flag
        await crud.set_child_sos(session, child_id, True)
        parents = await crud.get_parents_for_child(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)
        await session.commit()

        if not parents:
            await message.answer(
                "⚠️ Siz hali biron bir ota-ona hisobiga ulanmagansiz!\n"
                "Iltimos, ota-onangizdan ulanish kodini so'rang."
            )
            return

        # Prepare alert text
        battery_info = f"{latest_loc.battery_level}%" if latest_loc and latest_loc.battery_level else "Noma'lum"
        loc_coords = f"{latest_loc.latitude}, {latest_loc.longitude}" if latest_loc else "Mavjud emas"
        maps_link = (
            f"https://www.google.com/maps/search/?api=1&query={latest_loc.latitude},{latest_loc.longitude}"
            if latest_loc else "Joylashuv hali yozilmagan"
        )

        parent_alert = (
            f"🚨🚨🚨 **FAVQULODDA VAZIYAT (SOS)!** 🚨🚨🚨\n\n"
            f"Farzandingiz **{child_name}** yordam so'ramoqda!\n\n"
            f"🕒 Vaqt: **{now_str}**\n"
            f"🔋 Batareya: **{battery_info}**\n"
            f"📍 Oxirgi koordinata: `{loc_coords}`\n"
            f"🗺 Xaritada: [Google Maps orqali ko'rish]({maps_link})\n\n"
            f"⚠️ **DIQQAT:** Farzandingiz bilan zudlik bilan bog'laning yoki xaritada tekshiring!"
        )

        for parent in parents:
            try:
                await bot.send_message(
                    chat_id=parent.id,
                    text=parent_alert,
                    reply_markup=get_sos_alert_parent_keyboard(child_id, parent.id),
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

        # Respond to child
        await message.answer(
            "🚨 **SOS SIGNALI OTA-ONANGIZGA YUBORILDI!**\n\n"
            "Iltimos, xotirjam bo'ling. Ota-onangizga xabar va oxirgi joylashuvingiz yetkazildi.\n\n"
            "🎙 **Hoziroq qisqa ovozli xabar (golos) yozing yoki rasm yuboring** — biz uni darhol ota-onangizga yetkazamiz!",
            parse_mode="Markdown"
        )


@router.callback_query(F.data.startswith("resolve_sos:"))
async def on_resolve_sos(callback: CallbackQuery, bot: Bot):
    """Parent clears the SOS emergency state."""
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
            except Exception as e:
                logger.error(f"Failed to notify child of SOS resolution: {e}")

    await callback.message.edit_text("✅ SOS signali bekor qilindi va xavfsizlik holati tiklandi.")
    await callback.answer()


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
                logger.error(f"Failed to forward child media to parent {parent.id}: {e}")

        await message.answer("✅ Xabaringiz ota-onangizga yetkazildi.")


# ---------------------------------------------------------
# Check-in Handler ("✅ Yetib keldim")
# ---------------------------------------------------------

@router.message(F.text.startswith("✅ Yetib keldim"))
async def on_checkin_pressed(message: Message, bot: Bot):
    """Child checks in to notify parents they have arrived safely."""
    child_id = message.from_user.id
    child_name = message.from_user.full_name
    now_str = datetime.now().strftime("%H:%M")

    async with AsyncSessionLocal() as session:
        parents = await crud.get_parents_for_child(session, child_id)
        latest_loc = await crud.get_latest_location(session, child_id)

        if not parents:
            await message.answer("Siz hali biron bir ota-onaga ulanmagansiz.")
            return

        for parent in parents:
            try:
                checkin_text = (
                    f"✅ **YETIB KELDIM (Check-in)!**\n\n"
                    f"Farzandingiz **{child_name}** manzilga eson-omon yetib kelganini xabar qildi.\n"
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

        # Send action buttons for each pending task so child can submit
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

        # Notify parent with approval keyboard
        approval_text = (
            f"🔔 **Vazifa bajarildi deb bildirildi!**\n\n"
            f"👤 Farzand: **{child_name}**\n"
            f"📌 Vazifa: **{task.title}**\n"
            f"💰 Mukofot: **{task.reward_coins}** tanga\n\n"
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

        # Notify child
        try:
            await bot.send_message(
                chat_id=task.child_id,
                text=(
                    f"🎉🎉 **TABRIKLAYMIZ!** 🎉🎉\n\n"
                    f"Ota-onangiz **'{task.title}'** vazifangizni tasdiqladi!\n"
                    f"💰 Balansingizga: **+{task.reward_coins} tanga** qo'shildi!\n"
                    f"Hozirgi umumiy balansingiz: **{new_balance:,} tanga**."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify child of approved task: {e}")

        await session.commit()

    await callback.message.edit_text(
        f"✅ **Vazifa tasdiqlandi!**\n\n"
        f"Farzandingiz hisobiga **+{task.reward_coins} tanga** muvaffaqiyatli o'tkazildi."
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
            await message.answer("Sizga hali hech qanday farzand ulanmagan. Avval '➕ Farzand ulash (Kodi)' tugmasidan foydalaning.")
            return

        tasks = await crud.get_tasks_for_parent(session, parent_id)
        text = f"📋 **Vazifalar boshqaruvi**\n\nJami yaratilgan vazifalar: {len(tasks)} ta.\n\n"

        for child in children:
            child_tasks = [t for t in tasks if t.child_id == child.id and t.status in ["pending", "completed_by_child"]]
            text += f"👤 **{child.full_name}** (Balans: {child.virtual_balance:,} tanga)\n"
            if child_tasks:
                for t in child_tasks:
                    status_text = "⏳ Tekshiruvda" if t.status == "completed_by_child" else "▫️ Bajarilmoqda"
                    text += f"  - {t.title} ({t.reward_coins} tanga) [{status_text}]\n"
            else:
                text += "  - Faol vazifalar yo'q\n"
            text += "\n"

        # Show buttons to create task for each child
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
        task = await crud.create_task(
            session=session,
            parent_id=parent_id,
            child_id=child_id,
            title=title,
            reward_coins=reward
        )
        child = await crud.get_user(session, child_id)
        await session.commit()

        # Notify child about the new challenge
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
            logger.error(f"Failed to notify child about new task: {e}")

    await state.clear()
    await message.answer(
        f"✅ **Vazifa muvaffaqiyatli saqlandi va farzandga yuborildi!**\n\n"
        f"📌 Nomi: **{title}**\n"
        f"💰 Mukofot: **{reward:,} tanga**",
        reply_markup=get_parent_main_keyboard(parent_id),
        parse_mode="Markdown"
    )


# ---------------------------------------------------------
# Geofencing Safe Zones Management
# ---------------------------------------------------------

@router.message(F.text == "🛡 Xavfsiz hududlar")
async def on_safe_zones_menu(message: Message):
    """Parent opens safe zones menu."""
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        fences = await crud.get_geofences_for_parent(session, parent_id)
        text = (
            f"🛡 **Xavfsiz hududlar (Geofences)**\n\n"
            f"Faol hududlar soni: **{len(fences)} ta**.\n\n"
            f"Farzandingiz ushbu hududlarga kirganda yoki ulardan chiqqanda sizga darhol xabar keladi."
        )
        await message.answer(
            text,
            reply_markup=get_geofences_menu_keyboard(fences),
            parse_mode="Markdown"
        )


@router.callback_query(F.data == "add_new_geofence")
async def on_add_new_geofence_click(callback: CallbackQuery, state: FSMContext):
    """Start geofence creation flow."""
    await state.set_state(GeofenceAddState.waiting_for_name)
    await callback.message.answer(
        "🏷 **Yangi xavfsiz hudud nomini kiriting:**\n(Masalan: _'Maktab №12'_, _'Uyimiz'_, _'Ingliz tili kursi'_)",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(GeofenceAddState.waiting_for_name)
async def on_geofence_name_entered(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(GeofenceAddState.waiting_for_location)

    await message.answer(
        f"📍 **'{name}' uchun joylashuvni yuboring:**\n\n"
        f"Buning uchun Telegram'dagi qisqich (📎) tugmasini bosing va **Joylashuv (Location)** yuboring, "
        f"yoki xaritadan nuqtani tanlab tashlang.",
        parse_mode="Markdown"
    )


@router.message(GeofenceAddState.waiting_for_location, F.location)
async def on_geofence_location_received(message: Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude
    await state.update_data(lat=lat, lon=lon)
    await state.set_state(GeofenceAddState.waiting_for_radius)

    await message.answer(
        "📏 **Xavfsiz hudud radiusini tanlang:**\n(Ushbu masofa markazdan boshlab hisoblanadi)",
        reply_markup=get_radius_selection_keyboard()
    )


@router.callback_query(GeofenceAddState.waiting_for_radius, F.data.startswith("radius:"))
async def on_geofence_radius_selected(callback: CallbackQuery, state: FSMContext):
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
        f"✅ **Yangi xavfsiz hudud saqlandi!**\n\n"
        f"🏷 Nomi: **{name}**\n"
        f"📏 Radiusi: **{int(radius)} metr**\n"
        f"📍 Koordinatalar: `{lat:.5f}, {lon:.5f}`\n\n"
        f"Endi farzandingiz ushbu hududga kirganda yoki chiqqanda xabar beriladi.",
        parse_mode="Markdown"
    )
    await callback.answer("Saqlandi!")


@router.callback_query(F.data.startswith("delete_fence:"))
async def on_delete_geofence(callback: CallbackQuery):
    fence_id = int(callback.data.split(":")[1])
    parent_id = callback.from_user.id
    async with AsyncSessionLocal() as session:
        success = await crud.delete_geofence(session, fence_id, parent_id)
        await session.commit()

    if success:
        await callback.answer("Xavfsiz hudud o'chirildi!", show_alert=True)
        await callback.message.delete()
    else:
        await callback.answer("O'chirishda xatolik yuz berdi.", show_alert=True)


# ---------------------------------------------------------
# Parent Children List & Pairing Code Display
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
    parent_id = callback.from_user.id
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
            reply_markup=get_child_details_keyboard(child_id, parent_id),
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


@router.message(F.text == "🔄 Yangilash / Status")
async def on_status_pressed(message: Message):
    parent_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        children = await crud.get_children_for_parent(session, parent_id)
        fences = await crud.get_geofences_for_parent(session, parent_id)

        text = (
            f"📊 **Tizim holati (Real-time Status)**\n\n"
            f"👨‍👩‍👧 Ota-ona: **{message.from_user.full_name}**\n"
            f"👶 Ulangan farzandlar: **{len(children)} nafar**\n"
            f"🛡 Xavfsiz hududlar: **{len(fences)} ta**\n"
            f"⚡ Server holati: **Ishlayapti (Online)**\n\n"
            f"Xaritani ochish uchun pastdagi **'🗺 Xaritada ko'rish (Web App)'** tugmasini bosing."
        )
        await message.answer(text, parse_mode="Markdown")


@router.message(F.text.startswith("🗺 Xaritada ko'rish"))
async def on_open_map_pressed(message: Message):
    parent_id = message.from_user.id
    webapp_url = f"{settings.RESOLVED_WEBAPP_URL}/child_security_site/index.html?parent_id={parent_id}"

    if webapp_url.startswith("https://"):
        await message.answer(
            "🗺 **Interaktiv Xaritani ochish:**",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🗺 Xaritani ochish", web_app=WebAppInfo(url=webapp_url))]]
            ),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            f"🗺 **Interaktiv Xarita Dashboardi:**\n\n"
            f"Xaritani brauzerda ochish uchun quyidagi havolani bosing:\n"
            f"👉 [Xaritani ochish]({webapp_url})\n\n"
            f"🔗 To'g'ridan-to'g'ri havola: `{webapp_url}`\n\n"
            f"💡 _Telegram ilovasi ichida to'g'ridan-to'g'ri ochilishi uchun HTTPS havola talab qilinadi (Render.com ga qo'yilsa, avtomatik HTTPS bo'ladi)._",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


@router.message(F.text == "ℹ️ Yordam")
async def on_help_pressed(message: Message):
    text = (
        f"📖 **Qo'llanma va Yordam**\n\n"
        f"**Smart Parental Control & Child Location Tracker** farzandingiz xavfsizligini ta'minlash uchun mo'ljallangan:\n\n"
        f"1. **Jonli Xarita:** '🗺 Xaritada ko'rish' tugmasi orqali interaktiv Leaflet xaritasida farzandingiz harakatini real vaqtda kuzating.\n"
        f"2. **Xavfsiz hududlar:** Maktab, uy yoki o'quv markazlarini qo'shing. Farzand bu hududga kirganda yoki chiqqanda darhol bildirishnoma olasiz.\n"
        f"3. **🚨 SOS tugmasi:** Farzand xavf tugmasini bossa, sizga lokatsiya, batareya darajasi va zudlik bilan ovozli/rasmli xabar jo'natiladi.\n"
        f"4. **Batareya nazorati:** Farzand telefoni quvvati 15% dan tushib ketsa, sizga avtomatik eslatma keladi.\n"
        f"5. **Vazifalar va Tangalar:** Farzandingizga foydali vazifalar bering va uning rag'batini oshiring."
    )
    await message.answer(text, parse_mode="Markdown")
