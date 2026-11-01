from typing import Any, Dict, List, Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def get_owner_main_keyboard(is_owner: bool = True) -> ReplyKeyboardMarkup:
    """Main dashboard keyboard for the owner bot."""
    keyboard = [
        [
            KeyboardButton(text="📊 Umumiy Statistika"),
            KeyboardButton(text="📡 Jonli GPS Radar"),
        ],
        [
            KeyboardButton(text="👥 Barcha foydalanuvchilar"),
            KeyboardButton(text="👥 Faol foydalanuvchilar"),
        ],
        [
            KeyboardButton(text="🔍 ID bo'yicha tekshirish"),
            KeyboardButton(text="🔑 Magic Login Link"),
        ],
        [
            KeyboardButton(text="🚨 Faol SOS signallari"),
            KeyboardButton(text="🔋 Batareya & Qurilmalar radari"),
        ],
        [
            KeyboardButton(text="🏆 TOP Balanslar & Tangalar"),
            KeyboardButton(text="🛡️ Xavfsizlik & Anti-Spam"),
        ],
        [
            KeyboardButton(text="👥 Adminlarni boshqarish"),
            KeyboardButton(text="📢 Hammaga xabar (Broadcast)"),
        ],
        [
            KeyboardButton(text="🌐 Server & Diagnostika"),
            KeyboardButton(text="🗄️ Neon DB Holati"),
        ],
        [
            KeyboardButton(text="🎁 Bolalarga bonus (Airdrop)"),
            KeyboardButton(text="📱 Ilova havolalari (APK)"),
        ],
        [
            KeyboardButton(text="💾 Baza zaxirasi (Backup)"),
            KeyboardButton(text="🔄 Barchada botni reset qilish"),
        ],
        [
            KeyboardButton(text="🧹 Baza tozalash (Optimize)"),
            KeyboardButton(text="📑 Server loglari"),
        ],
        [
            KeyboardButton(text="ℹ️ Yordam & Buyruqlar"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Boshqaruv menyusidan tanlang...",
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Cancel operation keyboard."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_user_dossier_inline_keyboard(
    user_id: int,
    is_sos: bool = False,
    has_location: bool = False,
    role: str = "child",
) -> InlineKeyboardMarkup:
    """Action buttons for an inspected user dossier."""
    buttons = []

    # Row 1: Map & Direct Message
    row1 = []
    if has_location:
        row1.append(
            InlineKeyboardButton(
                text="📍 Xaritada ko'rish",
                callback_data=f"adm_loc:{user_id}",
            )
        )
    row1.append(
        InlineKeyboardButton(
            text="✉️ Xabar yuborish",
            callback_data=f"adm_msg:{user_id}",
        )
    )
    buttons.append(row1)

    # Row 2: Code regeneration & SOS resolution
    row2 = [
        InlineKeyboardButton(
            text="🔄 Kodni yangilash",
            callback_data=f"adm_code:{user_id}",
        )
    ]
    if is_sos:
        row2.append(
            InlineKeyboardButton(
                text="🚨 SOS ni bekor qilish",
                callback_data=f"adm_sos_off:{user_id}",
            )
        )
    buttons.append(row2)

    # Row 3: Device Control (for children)
    if role == "child":
        buttons.append([
            InlineKeyboardButton(text="🔊 Sirena chalish", callback_data=f"adm_siren:{user_id}"),
            InlineKeyboardButton(text="🔑 Magic WebApp Login", callback_data=f"adm_magic:{user_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🛑 Ilovalarni bloklash", callback_data=f"adm_lock:{user_id}"),
            InlineKeyboardButton(text="✅ Ilovalarni ochish", callback_data=f"adm_unlock:{user_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="⏱ Ekran vaqtini ko'rish", callback_data=f"adm_screen:{user_id}"),
            InlineKeyboardButton(text="🔑 PIN-kodni tiklash", callback_data=f"adm_reset_pin:{user_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="💰 +100 tanga", callback_data=f"adm_coin:{user_id}:100"),
            InlineKeyboardButton(text="💰 +500 tanga", callback_data=f"adm_coin:{user_id}:500"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🔑 Magic WebApp Login", callback_data=f"adm_magic:{user_id}"),
        ])

    # Row 4: Unlink & Back
    buttons.append([
        InlineKeyboardButton(
            text="🔗 Bog'lanishlarni tozalash (Unlink)",
            callback_data=f"adm_unlink_ask:{user_id}",
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            text="🔙 Asosiy menyu",
            callback_data="adm_back_main",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_unlink_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Confirm user unlinking."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚠️ Ha, barcha aloqalarni uzish",
                    callback_data=f"adm_unlink_do:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=f"adm_inspect:{user_id}",
                )
            ],
        ]
    )


def get_admin_management_keyboard() -> InlineKeyboardMarkup:
    """Admin management menu keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Yangi Admin qo'shish",
                    callback_data="adm_add_admin_prompt",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Adminlar ro'yxati",
                    callback_data="adm_list_admins",
                ),
                InlineKeyboardButton(
                    text="🗑 Adminni o'chirish",
                    callback_data="adm_remove_admin_list",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Asosiy menyu",
                    callback_data="adm_back_main",
                )
            ],
        ]
    )


def get_broadcast_target_keyboard() -> InlineKeyboardMarkup:
    """Select broadcast audience."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👨‍👩‍👧 Faqat Ota-onalarga",
                    callback_data="bc_target:parent",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👦 Faqat Farzandlarga",
                    callback_data="bc_target:child",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Barcha foydalanuvchilarga",
                    callback_data="bc_target:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="adm_back_main",
                )
            ],
        ]
    )


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm broadcast delivery."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Tasdiqlash va Yuborish",
                    callback_data="bc_confirm_send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="adm_back_main",
                )
            ],
        ]
    )


def get_airdrop_preset_keyboard() -> InlineKeyboardMarkup:
    """Airdrop coin amount selection keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎁 +100 tanga (≈1,000 so'm)", callback_data="ad_amt:100"),
                InlineKeyboardButton(text="🎁 +500 tanga (≈5,000 so'm)", callback_data="ad_amt:500"),
            ],
            [
                InlineKeyboardButton(text="🎉 +1,000 tanga (≈10,000 so'm)", callback_data="ad_amt:1000"),
                InlineKeyboardButton(text="👑 +5,000 tanga (≈50,000 so'm)", callback_data="ad_amt:5000"),
            ],
            [
                InlineKeyboardButton(text="✍️ Boshqa miqdor kiritish", callback_data="ad_amt_custom"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_back_main"),
            ],
        ]
    )


def get_shield_management_keyboard() -> InlineKeyboardMarkup:
    """Anti-Spam & Firewall Shield management keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Holatni yangilash", callback_data="adm_refresh_shield"),
                InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="adm_back_main"),
            ],
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
                    callback_data="adm_back_main"
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
                    callback_data="adm_back_main"
                )
            ]
        ]
    )


def get_users_directory_inline_keyboard(
    user_items: List[Dict[str, Any]],
    page: int,
    total_pages: int,
    role_filter: str = "all",
    search_query: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Build rich inline keyboard for the bot users directory.
    Includes user selection buttons, 1-tap direct WebApp ghost login,
    role filters, pagination, and search controls.
    """
    buttons = []

    # 1. User selector rows: [Role + Name/Username (ID)] and [🚀 Kirish (Ghost Login)]
    for idx, item in enumerate(user_items, start=1):
        uid = item.get("id")
        raw_name = (item.get("name") or "").strip()
        uname = (item.get("username") or "").strip().lstrip("@")
        role = item.get("role") or "parent"
        role_ico = "👦" if role == "child" else "👨‍👩‍👧"
        magic_url = item.get("magic_url")

        # Format label prioritizing @username or clean full name, never clipped
        if uname:
            display_label = f"@{uname}"
        elif raw_name and raw_name.lower() not in ("none", "nomsiz", "foydalanuvchi"):
            display_label = raw_name.split()[0]
        else:
            display_label = "Foydalanuvchi"

        btn_title = f"{role_ico} {idx}. {display_label} (ID: {uid})"
        row = [
            InlineKeyboardButton(
                text=btn_title,
                callback_data=f"usr_sel:{uid}"
            )
        ]
        if magic_url:
            row.append(
                InlineKeyboardButton(
                    text="🚀 Kirish (WebApp)",
                    url=magic_url
                )
            )
        else:
            row.append(
                InlineKeyboardButton(
                    text="🔑 Kirish link",
                    callback_data=f"usr_magic:{uid}"
                )
            )
        buttons.append(row)

    # 2. Filter tabs: All, Parent, Child, Banned
    filter_tabs = [
        InlineKeyboardButton(
            text="✅ Barcha" if role_filter == "all" else "🌐 Barcha",
            callback_data="usr_flt:all"
        ),
        InlineKeyboardButton(
            text="✅ Ota-ona" if role_filter == "parent" else "👨‍👩‍👧 Ota-ona",
            callback_data="usr_flt:parent"
        ),
        InlineKeyboardButton(
            text="✅ Bolalar" if role_filter == "child" else "👦 Bolalar",
            callback_data="usr_flt:child"
        ),
        InlineKeyboardButton(
            text="✅ Blok" if role_filter == "banned" else "🚫 Blok",
            callback_data="usr_flt:banned"
        ),
    ]
    buttons.append(filter_tabs)

    # 3. Pagination controls: [◀️ Oldingi] [📄 X/Y] [Keyingi ▶️]
    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Oldingi",
                callback_data=f"usr_page:{page - 1}:{role_filter}"
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"📄 {page}/{total_pages}",
            callback_data=f"usr_noop:{page}"
        )
    )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Keyingi ▶️",
                callback_data=f"usr_page:{page + 1}:{role_filter}"
            )
        )
    buttons.append(nav_row)

    # 4. Search and Refresh
    action_row = [
        InlineKeyboardButton(text="🔍 Qidirish", callback_data="usr_search"),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"usr_page:{page}:{role_filter}"),
    ]
    if search_query:
        action_row.append(
            InlineKeyboardButton(text="❌ Qidiruvni tozalash", callback_data="usr_clear_search")
        )
    buttons.append(action_row)

    # 5. Back to Main Dashboard
    buttons.append([
        InlineKeyboardButton(text="🔙 Boshqaruv paneli", callback_data="adm_back_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


