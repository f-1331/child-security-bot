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
                InlineKeyboardButton(
                    text="👦 Farzand (Child)",
                    callback_data="set_role:child"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Android ilovasi (.apk)",
                    callback_data="download_android"
                ),
                InlineKeyboardButton(
                    text="🍎 iPhone / iPad (iOS)",
                    callback_data="download_ios"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💻 Noutbuk dasturi (PC / Mac / Linux)",
                    callback_data="download_desktop"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌐 Web App (Sayt)ni ochish",
                    web_app=WebAppInfo(url=settings.MINI_APP_URL)
                )
            ]
        ]
    )


def get_parent_main_keyboard(parent_id: int) -> ReplyKeyboardMarkup:
    """
    Main persistent reply keyboard for Parent.
    Includes Mobile Apps (Android/iOS), Laptop App (PC/Mac/Linux), Instant Location, Send Live Location, Web App Mini App, Kerakli joylar, Telemetry, and Tasks.
    """
    keyboard = [
        [
            KeyboardButton(text="📱 Telefon uchun ilova (Android & iOS)"),
            KeyboardButton(text="💻 Noutbuk uchun dastur"),
        ],
        [
            KeyboardButton(text="🤖 Android ilovasi (APK)"),
            KeyboardButton(text="🍎 iPhone / iOS (O'rnatish)"),
        ],
        [
            KeyboardButton(text="📍 Farzandning hozirgi joylashuvi"),
            KeyboardButton(text="🗺️ Jonli Xarita (Web App)", web_app=WebAppInfo(url=f"{settings.RESOLVED_WEBAPP_URL}?role=parent&parent_id={parent_id}"))
        ],
        [
            KeyboardButton(text="🎙 Atrof ovozi («Звук вокруг»)"),
            KeyboardButton(text="🚨 Masofaviy Siren (Qidiruv)"),
        ],
        [
            KeyboardButton(text="📱 Ilovalar & Ekran tahlili"),
            KeyboardButton(text="🔋 Batareya va Holat")
        ],
        [
            KeyboardButton(text="📚 Maktab rejimi (Dars vaqti)"),
            KeyboardButton(text="💬 Farzandga ko'rsatma")
        ],
        [
            KeyboardButton(text="📍 Kerakli joylar"),
            KeyboardButton(text="📋 Vazifalar va Tangalar")
        ],
        [
            KeyboardButton(text="👶 Farzandlarim"),
            KeyboardButton(text="➕ Farzand ulash (Kodi)")
        ],
        [
            KeyboardButton(text="👨‍👩‍👧 Ikkinchi ota-onani taklif qilish"),
            KeyboardButton(text="🔑 Saytga kirish kodi")
        ],
        [
            KeyboardButton(text="👟 Kunlik harakat maqsadi"),
            KeyboardButton(text="📊 Bugungi hisobot")
        ],
        [
            KeyboardButton(text="💰 Tangalar kursini sozlash"),
            KeyboardButton(text="📞 Oila kontaktlari")
        ],
        [
            KeyboardButton(text="🔄 Rolni o'zgartirish"),
            KeyboardButton(text="ℹ️ Yordam")
        ],
        [
            KeyboardButton(text="🔔 Xabarnomalar sozlamasi"),
            KeyboardButton(text="⚙️ Sozlamalar / Chiqish")
        ]
    ]

    # Maxsus: faqat tizim asoschisi @fayzbek1331 (ID 6707839718) uchun Asoschi boshqaruv tugmasi
    if parent_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
        keyboard.insert(0, [KeyboardButton(text="👑 Asoschi (Owner) Boshqaruv")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )


def get_child_main_keyboard(child_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    """
    Persistent reply keyboard for Child.
    Includes Parent App, Child App, Laptop App, instant Location sharing, instant SOS, Check-in, Web App, and Emergency Call.
    """
    child_param = f"&child_id={child_id}" if child_id else ""
    keyboard = [
        [
            KeyboardButton(text="📱 Telefon uchun ilova (Android & iOS)"),
            KeyboardButton(text="💻 Noutbuk uchun dastur"),
        ],
        [
            KeyboardButton(text="🤖 Android ilovasi (APK)"),
            KeyboardButton(text="🍎 iPhone / iOS (O'rnatish)"),
        ],
        [
            KeyboardButton(text="📍 Jonli joylashuvimni yuborish", request_location=True),
            KeyboardButton(text="🚨 SOS (Xavf tugmasi)")
        ],
        [
            KeyboardButton(text="📱 Web App (Sayt)", web_app=WebAppInfo(url=f"{settings.RESOLVED_WEBAPP_URL}?role=child{child_param}")),
            KeyboardButton(text="💬 Ota-onaga tezkor xabar")
        ],
        [
            KeyboardButton(text="✅ Yetib keldim (Check-in)"),
            KeyboardButton(text="📞 Ota-onamga qo'ng'iroq")
        ],
        [
            KeyboardButton(text="📋 Vazifalarim va Tangalar"),
            KeyboardButton(text="👨‍👩‍👧 Ota-onamni taklif qilish")
        ],
        [
            KeyboardButton(text="🔄 Rolni o'zgartirish"),
            KeyboardButton(text="ℹ️ Yordam")
        ],
        [
            KeyboardButton(text="🔔 Xabarnomalar sozlamasi"),
            KeyboardButton(text="⚙️ Sozlamalar / Chiqish")
        ]
    ]

    # Maxsus: faqat tizim asoschisi @fayzbek1331 (ID 6707839718) uchun Asoschi boshqaruv tugmasi
    if child_id and child_id in (getattr(settings, "OWNER_ID", 1), 1, 6707839718):
        keyboard.insert(0, [KeyboardButton(text="👑 Asoschi (Owner) Boshqaruv")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True
    )


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Settings menu keyboard with logout/role switch option."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 Saytga kirish kodi",
                    callback_data="show_web_access_code"
                ),
                InlineKeyboardButton(
                    text="💰 Tangalar kursini sozlash",
                    callback_data="coin_rate_start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Xabarnomalar sozlamasi (Mute)",
                    callback_data="parent_notifications_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Rolni almashtirish",
                    callback_data="role_switch_start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚪 Hisobdan chiqish (2-bosqichli)",
                    callback_data="logout_start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Yopish",
                    callback_data="logout_cancel"
                )
            ]
        ]
    )


def get_coin_rate_preset_keyboard() -> InlineKeyboardMarkup:
    """Preset options for coin rate."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪙 1,000 tanga = 10,000 so'm (Standart)",
                    callback_data="set_coin_rate:1000:10000"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🪙 1,000 = 5,000 so'm",
                    callback_data="set_coin_rate:1000:5000"
                ),
                InlineKeyboardButton(
                    text="🪙 1,000 = 20,000 so'm",
                    callback_data="set_coin_rate:1000:20000"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ O'z qiymatimni kiritish",
                    callback_data="custom_coin_rate"
                ),
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="cancel_action"
                ),
            ]
        ]
    )


def get_child_role_switch_request_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for child to confirm sending role switch request to parent."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📩 Ota-onamga so'rov yuborish",
                    callback_data="child_request_role_switch"
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


def get_parent_role_switch_approval_keyboard(child_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for parent to approve or reject child's role switch request."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ruxsat berish (Rolni almashtirish)",
                    callback_data=f"approve_role_switch:{child_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"reject_role_switch:{child_id}"
                )
            ]
        ]
    )


def get_logout_step1_keyboard() -> InlineKeyboardMarkup:
    """Step 1 confirmation for account logout / role switch."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ 1-bosqich: Chiqishni davom ettirish",
                    callback_data="logout_step1"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Bekor qilish (Hisobda qolish)",
                    callback_data="logout_cancel"
                )
            ]
        ]
    )


def get_logout_step2_keyboard(confirm_code: str) -> InlineKeyboardMarkup:
    """Step 2 final verification keyboard with randomized security code."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔴 HA, BUTUNLAY CHIQISH ({confirm_code})",
                    callback_data=f"logout_step2:{confirm_code}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 YO'Q, BEKOR QILISH",
                    callback_data="logout_cancel"
                )
            ]
        ]
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
    """Action buttons for a specific child including offline controls and SMS fallback."""
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
                    text="📞 Farzand bilan bog'lanish (Qo'ng'iroq)",
                    callback_data=f"contact_child:{child_id}"
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
                ),
                InlineKeyboardButton(
                    text="📱 Ilovalar & Ekran",
                    callback_data=f"st_child:{child_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📴 Internetni Cheklash (Offline)",
                    callback_data=f"toggle_net_lock:{child_id}"
                ),
                InlineKeyboardButton(
                    text="✉️ Oflayn SMS Buyruqlar",
                    callback_data=f"sms_controls:{child_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Farzandni hisobdan o'chirish",
                    callback_data=f"delete_child_confirm:{child_id}"
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


def get_location_actions_keyboard(lat: float, lon: float, child_id: int, is_offline: bool = False, child_phone: Optional[str] = None) -> InlineKeyboardMarkup:
    """Interactive navigation buttons sent with child's native location pin."""
    google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    yandex_maps_url = f"https://yandex.com/maps/?pt={lon},{lat}&z=16&l=map"

    rows = [
        [
            InlineKeyboardButton(text="🗺 Google Maps", url=google_maps_url),
            InlineKeyboardButton(text="🗺 Yandex Maps", url=yandex_maps_url),
        ],
        [
            InlineKeyboardButton(text="🗺 Harakat marshruti", web_app=WebAppInfo(url=f"{settings.RESOLVED_WEBAPP_URL}?child_id={child_id}&show_route=1")),
            InlineKeyboardButton(text="🔁 Qayta joylar", web_app=WebAppInfo(url=f"{settings.RESOLVED_WEBAPP_URL}?child_id={child_id}&show_repeats=1")),
        ]
    ]
    if is_offline and child_phone:
        clean_p = child_phone.strip()
        rows.append([
            InlineKeyboardButton(text="✉️ 1-Tap SMS: Oflayn GPS so'rash", url=f"sms:{clean_p}?body=%23GPS:1234")
        ])
    elif is_offline:
        rows.append([
            InlineKeyboardButton(text="✉️ Internetsiz SMS Buyruqlar", callback_data=f"sms_controls:{child_id}")
        ])
    rows.append([
        InlineKeyboardButton(text="🔄 Joylashuvni yangilash", callback_data=f"locate_child:{child_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_contact_request_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard prompting parent to link their phone number for SOS emergency calls."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨‍👩‍👧 Ota-ona ilovasi (APK)"), KeyboardButton(text="👦 Farzand ilovasi (APK)")],
            [KeyboardButton(text="📱 Raqamimni ulash (Kontaktni yuborish)", request_contact=True)],
            [KeyboardButton(text="⏭ Keyinroq ulash")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_child_sos_call_keyboard(parent_phone: Optional[str] = None, parent_tg_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Emergency keyboard for child when SOS is pressed."""
    buttons = []
    if parent_tg_id:
        buttons.append([
            InlineKeyboardButton(text="💬 Ota-ona bilan Telegramda bog'lanish", url=f"tg://user?id={parent_tg_id}")
        ])
    buttons.append([
        InlineKeyboardButton(text="✅ Xavf bartaraf bo'ldi (Bekor qilish)", callback_data="child_cancel_sos")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_sos_alert_parent_keyboard(child_id: int, lat: Optional[float] = None, lon: Optional[float] = None) -> InlineKeyboardMarkup:
    """Action buttons sent to parent during an SOS emergency."""
    buttons = []
    if settings.MINI_APP_URL:
        buttons.append([
            InlineKeyboardButton(text="📱 WebApp Xaritasida Ko'rish", web_app=WebAppInfo(url=settings.MINI_APP_URL))
        ])
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


def get_child_task_action_keyboard(
    task_id: int,
    status: str = "pending",
    promised_time: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Child interactive keyboard for task:
    1. '👁 Ko'rdim' button (notifies parent that child saw the task).
    2. '⏰ Shu vaqtda bajaraman' button (choose time commitment).
    3. '📸 Bajarildi (Rasm yuborish)' button (submitting proof photo).
    """
    rows = []

    # 1. Seen status / button
    if status == "pending":
        rows.append([
            InlineKeyboardButton(
                text="👁 Ko'rdim",
                callback_data=f"task_seen:{task_id}"
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="✅ Ko'rildi",
                callback_data="noop"
            )
        ])

    # 2. Schedule commitment button
    if promised_time:
        rows.append([
            InlineKeyboardButton(
                text=f"⏰ Vaqt belgilandi: {promised_time}",
                callback_data=f"task_sched_menu:{task_id}"
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="⏰ Shu vaqtda bajaraman",
                callback_data=f"task_sched_menu:{task_id}"
            )
        ])

    # 3. Completion / Proof upload button
    if status == "completed_by_child":
        rows.append([
            InlineKeyboardButton(
                text="⏳ Ota-ona tekshirmoqda...",
                callback_data="noop"
            )
        ])
    elif status == "approved":
        rows.append([
            InlineKeyboardButton(
                text="🏆 Vazifa tasdiqlangan!",
                callback_data="noop"
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="📸 Bajarildi (Rasm yuborish)",
                callback_data=f"task_submit_photo:{task_id}"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_task_schedule_options_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Options for child to pick when they plan to execute the task."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡️ Hozir bajaraman", callback_data=f"task_sched:{task_id}:Hozir"),
                InlineKeyboardButton(text="⏰ 15 daqiqada", callback_data=f"task_sched:{task_id}:15 daqiqadan keyin")
            ],
            [
                InlineKeyboardButton(text="⏰ 30 daqiqada", callback_data=f"task_sched:{task_id}:30 daqiqadan keyin"),
                InlineKeyboardButton(text="⏰ 1 soatda", callback_data=f"task_sched:{task_id}:1 soatdan keyin")
            ],
            [
                InlineKeyboardButton(text="🌙 Bugun soat 18:00 da", callback_data=f"task_sched:{task_id}:Bugun soat 18:00 da"),
                InlineKeyboardButton(text="📅 Ertaga", callback_data=f"task_sched:{task_id}:Ertaga")
            ],
            [
                InlineKeyboardButton(text="🔙 Ortga", callback_data=f"task_back:{task_id}")
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
    """Radius selection for Kerakli joy creation with flexible distance presets."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="50 metr", callback_data="radius:50"),
                InlineKeyboardButton(text="100 metr", callback_data="radius:100"),
                InlineKeyboardButton(text="200 metr (Tavsiya)", callback_data="radius:200"),
            ],
            [
                InlineKeyboardButton(text="300 metr", callback_data="radius:300"),
                InlineKeyboardButton(text="500 metr", callback_data="radius:500"),
                InlineKeyboardButton(text="1000 metr (1 km)", callback_data="radius:1000"),
            ],
            [
                InlineKeyboardButton(text="2000 metr (2 km)", callback_data="radius:2000"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")
            ]
        ]
    )


def get_empty_children_kb() -> InlineKeyboardMarkup:
    """Inline keyboard offered when parent has 0 paired children."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧪 Namuna farzand bilan sinash (1-bosish)",
                    callback_data="link_demo_child"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Farzand ulash (Kodi)",
                    callback_data="show_pairing_code_callback"
                )
            ]
        ]
    )


def get_school_mode_inline_keyboard(child_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Toggle School Mode directly from Telegram inline button."""
    btn_text = "🔴 O'chirish (Cheklovlarni yechish)" if is_active else "🟢 Faollashtirish (Dars vaqti)"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=btn_text, callback_data=f"toggle_school:{child_id}:{0 if is_active else 1}")
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="cancel_action")
            ]
        ]
    )


def get_parent_directive_inline_keyboard(child_id: int) -> InlineKeyboardMarkup:
    """Quick 1-tap parental directive choices."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Tezroq uyga qayt", callback_data=f"p_dir:{child_id}:return_home"),
                InlineKeyboardButton(text="📞 Menga qo'ng'iroq qil", callback_data=f"p_dir:{child_id}:call_me"),
            ],
            [
                InlineKeyboardButton(text="🍱 Dori / Ovqat vaqti", callback_data=f"p_dir:{child_id}:take_medicine"),
                InlineKeyboardButton(text="❤️ Seni yaxshi ko'ramiz", callback_data=f"p_dir:{child_id}:cheer"),
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="cancel_action")
            ]
        ]
    )


def get_child_quick_signals_inline_keyboard() -> InlineKeyboardMarkup:
    """Quick 1-tap signal choices for child in Telegram bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎒 Maktabdaman", callback_data="c_sig:school_arrived"),
                InlineKeyboardButton(text="🏠 Uydaman", callback_data="c_sig:home_arrived"),
            ],
            [
                InlineKeyboardButton(text="🚗 Olib keting", callback_data="c_sig:pickup_me"),
                InlineKeyboardButton(text="⚽ Mashg'ulotdaman", callback_data="c_sig:training"),
            ],
            [
                InlineKeyboardButton(text="🪫 Zaryadim kam", callback_data="c_sig:low_battery"),
                InlineKeyboardButton(text="❓ Qayerdasiz?", callback_data="c_sig:where_are_you"),
            ],
            [
                InlineKeyboardButton(text="🔙 Yopish", callback_data="cancel_action")
            ]
        ]
    )


def get_owner_panel_inline_keyboard() -> InlineKeyboardMarkup:
    """Special owner dashboard keyboard inside main bot for @fayzbek1331."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Umumiy Statistika", callback_data="own_main:stats"),
                InlineKeyboardButton(text="📡 Jonli GPS Radar", callback_data="own_main:radar"),
            ],
            [
                InlineKeyboardButton(text="🚨 Faol SOS signallari", callback_data="own_main:sos_list"),
                InlineKeyboardButton(text="🔋 Batareya & Holat", callback_data="own_main:battery"),
            ],
            [
                InlineKeyboardButton(text="🔍 Foydalanuvchini tekshirish", callback_data="own_main:inspect"),
                InlineKeyboardButton(text="🔑 Magic WebApp Login", callback_data="own_main:magic"),
            ],
            [
                InlineKeyboardButton(text="👥 Adminlarni boshqarish", callback_data="own_main:admins"),
                InlineKeyboardButton(text="🔔 Xabarnomalar nazorati", callback_data="own_main:notifications"),
            ],
            [
                InlineKeyboardButton(text="🔄 Barchada botni reset qilish", callback_data="own_main:reset_menu"),
            ],
            [
                InlineKeyboardButton(text="👨‍👩‍👧 Ota-ona bo'lib kirish", callback_data="own_switch:parent"),
                InlineKeyboardButton(text="👦 Farzand bo'lib kirish", callback_data="own_switch:child"),
            ],
            [
                InlineKeyboardButton(text="🤖 Maxsus Owner Botga o'tish", url="https://t.me/child_security_owner_bot"),
            ],
            [
                InlineKeyboardButton(text="🔙 Menyuga qaytish", callback_data="own_main:close"),
            ]
        ]
    )


def get_reset_options_keyboard() -> InlineKeyboardMarkup:
    """Interactive reset menu for owner."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Barchada menyu va tugmalarni yangilash",
                    callback_data="global_reset:keyboards"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Kesh va faol SOS signallarini tozalash",
                    callback_data="global_reset:cache_sos"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Barchani boshlang'ich holatga qaytarish (Factory Reset)",
                    callback_data="global_reset:ask_factory"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Bekor qilish",
                    callback_data="own_main:stats"
                )
            ]
        ]
    )


def get_factory_reset_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation for dangerous global factory reset."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 HA, BARCHANI TO'LIQ RESET QILISH!",
                    callback_data="global_reset:confirm_factory"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="own_main:stats"
                )
            ]
        ]
    )



def get_ambient_sound_inline_keyboard(child_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for selecting ambient sound duration."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ 15 soniya", callback_data=f"rec_ambient:{child_id}:15"),
                InlineKeyboardButton(text="⏱ 30 soniya", callback_data=f"rec_ambient:{child_id}:30"),
            ],
            [
                InlineKeyboardButton(text="🎧 60 soniya", callback_data=f"rec_ambient:{child_id}:60"),
            ],
            [
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_action")
            ]
        ]
    )


def format_parent_notification_settings_html(settings_dict: dict[str, bool]) -> str:
    """Format HTML report of parent's personal notification preferences."""
    sos_on = settings_dict.get("notify_sos", True)
    msg_on = settings_dict.get("notify_messages", True)
    geo_on = settings_dict.get("notify_geofence", True)

    sos_badge = "🟢 <b>YOQILGAN (Xabarlar keladi)</b>" if sos_on else "🔴 <b>OʻCHIRILGAN (Mute qilingan)</b>"
    msg_badge = "🟢 <b>YOQILGAN (Xabarlar keladi)</b>" if msg_on else "🔴 <b>OʻCHIRILGAN (Mute qilingan)</b>"
    geo_badge = "🟢 <b>YOQILGAN (Signallar keladi)</b>" if geo_on else "🔴 <b>OʻCHIRILGAN (Mute qilingan)</b>"

    return (
        "🔔 <b>XABARNOMALAR VA BILDIRISHNOMALAR SOZLAMASI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Farzandingizdan keladigan bildirishnomalar oqimini mustaqil boshqaring:</i>\n\n"
        f"🚨 <b>SOS xavf signallari:</b>\n"
        f"Holat: {sos_badge}\n"
        "<i>(Farzand favqulodda SOS bosganda keladigan shoshilinch xabar va sirenalar)</i>\n\n"
        f"💬 <b>Boladan keladigan xabarlar:</b>\n"
        f"Holat: {msg_badge}\n"
        "<i>(Tezkor signallar: «Maktabdaman», «Uyga ketyapman», topshiriq hisobotlari)</i>\n\n"
        f"📍 <b>Joylashuv va Geozona xabarlari:</b>\n"
        f"Holat: {geo_badge}\n"
        "<i>(Maktabga kirish/chiqish, xavfli hudud, quvvat tugashi)</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <i>Kerakli tugmani bosing va 1-bosishda xabarlarni yoqing/o'chiring:</i>"
    )


def get_parent_notifications_inline_keyboard(settings_dict: dict[str, bool]) -> InlineKeyboardMarkup:
    """Keyboard for parent toggling personal notifications."""
    sos_on = settings_dict.get("notify_sos", True)
    msg_on = settings_dict.get("notify_messages", True)
    geo_on = settings_dict.get("notify_geofence", True)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🟢 YOQILGAN' if sos_on else '🔴 OʻCHIRILGAN'} | 🚨 SOS xavf",
                    callback_data="toggle_parent_notif:sos"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🟢 YOQILGAN' if msg_on else '🔴 OʻCHIRILGAN'} | 💬 Boladan xabarlar",
                    callback_data="toggle_parent_notif:messages"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🟢 YOQILGAN' if geo_on else '🔴 OʻCHIRILGAN'} | 📍 Geozona signallari",
                    callback_data="toggle_parent_notif:geofence"
                )
            ],
            [
                InlineKeyboardButton(text="🟢 Barchasini yoqish", callback_data="toggle_parent_notif:all_on"),
                InlineKeyboardButton(text="🔴 Barchasini o'chirish (Mute)", callback_data="toggle_parent_notif:all_off")
            ],
            [
                InlineKeyboardButton(text="🔙 Sozlamalarga qaytish", callback_data="back_to_settings")
            ]
        ]
    )
