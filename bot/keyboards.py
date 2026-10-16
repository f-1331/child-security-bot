from typing import Optional
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from config import settings


def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing role during /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👨‍👩‍👧 Ota-ona (Parent)",
                    callback_data="set_role:parent"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👦 Farzand (Child)",
                    callback_data="set_role:child"
                ),
            ],
        ]
    )


def get_parent_main_keyboard(parent_id: int) -> ReplyKeyboardMarkup:
    """
    Main persistent reply keyboard for Parent.
    Native Telegram-centric: Instant Location, Kerakli joylar, Telemetry, and Tasks.
    """
    keyboard = [
        [
            KeyboardButton(text="📍 Farzandning hozirgi joylashuvi")
        ],
        [
            KeyboardButton(text="📍 Kerakli joylar"),
            KeyboardButton(text="🔋 Batareya va Holat")
        ],
        [
            KeyboardButton(text="👶 Farzandlarim"),
            KeyboardButton(text="➕ Farzand ulash (Kodi)")
        ],
        [
            KeyboardButton(text="📋 Vazifalar va Tangalar"),
            KeyboardButton(text="📊 Bugungi hisobot")
        ],
        [
            KeyboardButton(text="📞 Oila kontaktlari"),
            KeyboardButton(text="ℹ️ Yordam")
        ]
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )


def get_child_main_keyboard() -> ReplyKeyboardMarkup:
    """
    Persistent reply keyboard for Child.
    Includes instant SOS, Check-in, Request Location, and Emergency Call.
    """
    keyboard = [
        [
            KeyboardButton(text="🚨 SOS (Xavf tugmasi)"),
            KeyboardButton(text="✅ Yetib keldim (Check-in)")
        ],
        [
            KeyboardButton(text="📍 Jonli joylashuvimni yuborish", request_location=True),
            KeyboardButton(text="📞 Ota-onamga qo'ng'iroq")
        ],
        [
            KeyboardButton(text="📋 Vazifalarim va Tangalar"),
            KeyboardButton(text="ℹ️ Yordam")
        ]
    ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )


def get_pairing_share_keyboard(bot_username: str, pairing_code: str) -> InlineKeyboardMarkup:
    """Keyboard to share pairing link with child."""
    deep_link = f"https://t.me/{bot_username}?start=pair_{pairing_code}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📲 Farzandga yuborish (Telegram)",
                    url=f"https://t.me/share/url?url={deep_link}&text=Salom!+Ushbu+havola+orqali+Parental+Tracker+botiga+ulanishingiz+mumkin."
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


def get_child_details_keyboard(child_id: int) -> InlineKeyboardMarkup:
    """Action buttons for a specific child."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📍 Hozirgi joylashuvini ko'rish",
                    callback_data=f"locate_child:{child_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Yangi vazifa yuklash",
                    callback_data=f"create_task:{child_id}"
                ),
                InlineKeyboardButton(
                    text="📍 Kerakli joy qo'shish",
                    callback_data=f"add_zone:{child_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔋 Telefon holati (Batareya)",
                    callback_data=f"battery_child:{child_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Farzandlar ro'yxatiga qaytish",
                    callback_data="list_children"
                )
            ]
        ]
    )


def get_location_actions_keyboard(lat: float, lon: float, child_id: int) -> InlineKeyboardMarkup:
    """Interactive navigation buttons sent with child's native location pin."""
    google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    yandex_maps_url = f"https://yandex.com/maps/?pt={lon},{lat}&z=16&l=map"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗺 Google Maps", url=google_maps_url),
                InlineKeyboardButton(text="🗺 Yandex Maps", url=yandex_maps_url),
            ],
            [
                InlineKeyboardButton(text="🔄 Joylashuvni yangilash", callback_data=f"locate_child:{child_id}")
            ]
        ]
    )


def get_child_sos_call_keyboard(parent_phone: Optional[str] = None) -> InlineKeyboardMarkup:
    """Emergency keyboard for child when SOS is pressed."""
    buttons = []
    if parent_phone:
        clean_phone = parent_phone.replace(" ", "").replace("-", "")
        buttons.append([
            InlineKeyboardButton(text="📞 Oyingizga / Dadasiga qo'ng'iroq", url=f"tel:{clean_phone}")
        ])
    buttons.append([
        InlineKeyboardButton(text="✅ Xavf bartaraf bo'ldi (Bekor qilish)", callback_data="child_cancel_sos")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_sos_alert_parent_keyboard(child_id: int, lat: Optional[float] = None, lon: Optional[float] = None) -> InlineKeyboardMarkup:
    """Action buttons sent to parent during an SOS emergency."""
    buttons = []
    if lat is not None and lon is not None:
        google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        buttons.append([
            InlineKeyboardButton(text="🚨 Xaritada ko'rish (Google Maps)", url=google_maps_url)
        ])
    buttons.append([
        InlineKeyboardButton(text="✅ SOS signalini to'xtatish", callback_data=f"resolve_sos:{child_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_task_approval_keyboard(task_id: int, reward_coins: int) -> InlineKeyboardMarkup:
    """Parent keyboard to approve or reject a task completed by child."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Tasdiqlash (+{reward_coins:,} tanga)",
                    callback_data=f"approve_task:{task_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"reject_task:{task_id}"
                )
            ]
        ]
    )


def get_child_task_action_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Child keyboard to mark a task as completed."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Bajarildi deb yuborish",
                    callback_data=f"complete_task:{task_id}"
                )
            ]
        ]
    )


def get_kerakli_joylar_menu_keyboard(fences: list) -> InlineKeyboardMarkup:
    """List existing Kerakli joylar with delete options and an add button."""
    buttons = []
    for fence in fences:
        buttons.append([
            InlineKeyboardButton(
                text=f"📍 {fence.name} ({int(fence.radius_meters)}m)",
                callback_data=f"view_fence:{fence.id}"
            ),
            InlineKeyboardButton(
                text="🗑 O'chirish",
                callback_data=f"delete_fence:{fence.id}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="➕ Yangi kerakli joy qo'shish",
            callback_data="add_new_geofence"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_radius_selection_keyboard() -> InlineKeyboardMarkup:
    """Radius selection for Kerakli joy creation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="100 metr", callback_data="radius:100"),
                InlineKeyboardButton(text="200 metr (Tavsiya)", callback_data="radius:200"),
            ],
            [
                InlineKeyboardButton(text="500 metr", callback_data="radius:500"),
                InlineKeyboardButton(text="1000 metr (1 km)", callback_data="radius:1000"),
            ],
            [
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")
            ]
        ]
    )
