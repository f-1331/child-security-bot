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
    Includes Telegram WebApp button pointing to the Leaflet map dashboard.
    """
    webapp_url = f"{settings.RESOLVED_WEBAPP_URL}/child_security_site/index.html?parent_id={parent_id}"

    # Telegram strictly enforces HTTPS for WebAppInfo; for HTTP use standard button
    if webapp_url.startswith("https://"):
        map_btn = KeyboardButton(
            text="🗺 Xaritada ko'rish (Web App)",
            web_app=WebAppInfo(url=webapp_url)
        )
    else:
        map_btn = KeyboardButton(
            text="🗺 Xaritada ko'rish (Web App)"
        )

    keyboard = [
        [map_btn],
        [
            KeyboardButton(text="👶 Farzandlarim"),
            KeyboardButton(text="➕ Farzand ulash (Kodi)")
        ],
        [
            KeyboardButton(text="🛡 Xavfsiz hududlar"),
            KeyboardButton(text="📋 Vazifalar va Tangalar")
        ],
        [
            KeyboardButton(text="🔄 Yangilash / Status"),
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
    Includes instant SOS button, Check-in, Request Location, and Tasks.
    """
    keyboard = [
        [
            KeyboardButton(text="🚨 SOS (Xavf tugmasi)"),
            KeyboardButton(text="✅ Yetib keldim (Check-in)")
        ],
        [
            KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True),
            KeyboardButton(text="📋 Vazifalarim va Tangalar")
        ],
        [
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


def get_child_details_keyboard(child_id: int, parent_id: int) -> InlineKeyboardMarkup:
    """Action buttons for a specific child."""
    webapp_url = f"{settings.RESOLVED_WEBAPP_URL}/child_security_site/index.html?parent_id={parent_id}&child_id={child_id}"
    
    if webapp_url.startswith("https://"):
        view_btn = InlineKeyboardButton(
            text="🗺 Xaritada jonli ko'rish",
            web_app=WebAppInfo(url=webapp_url)
        )
    else:
        view_btn = InlineKeyboardButton(
            text="🗺 Xaritada ko'rish (Havola)",
            url=webapp_url
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [view_btn],
            [
                InlineKeyboardButton(
                    text="➕ Yangi vazifa yuklash",
                    callback_data=f"create_task:{child_id}"
                ),
                InlineKeyboardButton(
                    text="🛡 Safe Zone qo'shish",
                    callback_data=f"add_zone:{child_id}"
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


def get_task_approval_keyboard(task_id: int, reward_coins: int) -> InlineKeyboardMarkup:
    """Parent keyboard to approve or reject a task completed by child."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Tasdiqlash (+{reward_coins} tanga)",
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


def get_sos_alert_parent_keyboard(child_id: int, parent_id: int) -> InlineKeyboardMarkup:
    """Action buttons sent to parent during an SOS emergency."""
    webapp_url = f"{settings.RESOLVED_WEBAPP_URL}/child_security_site/index.html?parent_id={parent_id}&child_id={child_id}"
    
    if webapp_url.startswith("https://"):
        view_btn = InlineKeyboardButton(
            text="🗺 Jonli xaritada ko'rish",
            web_app=WebAppInfo(url=webapp_url)
        )
    else:
        view_btn = InlineKeyboardButton(
            text="🗺 Jonli xarita (Havola)",
            url=webapp_url
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [view_btn],
            [
                InlineKeyboardButton(
                    text="✅ SOS signalini to'xtatish",
                    callback_data=f"resolve_sos:{child_id}"
                )
            ]
        ]
    )


def get_geofences_menu_keyboard(fences: list) -> InlineKeyboardMarkup:
    """List existing geofences with delete options and an add button."""
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
            text="➕ Yangi xavfsiz hudud qo'shish",
            callback_data="add_new_geofence"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_radius_selection_keyboard() -> InlineKeyboardMarkup:
    """Radius selection for safe zone creation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="100 metr", callback_data="radius:100"),
                InlineKeyboardButton(text="200 metr", callback_data="radius:200"),
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
