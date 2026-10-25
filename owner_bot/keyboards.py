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
            KeyboardButton(text="🔍 ID bo'yicha tekshirish"),
        ],
        [
            KeyboardButton(text="🚨 Faol SOS signallari"),
            KeyboardButton(text="👥 Adminlarni boshqarish"),
        ],
        [
            KeyboardButton(text="📢 Hammaga xabar (Broadcast)"),
            KeyboardButton(text="🌐 Server & Xavfsizlik"),
        ],
        [
            KeyboardButton(text="📜 So'nggi foydalanuvchilar"),
            KeyboardButton(text="📑 Server loglari"),
        ],
        [
            KeyboardButton(text="💾 Baza zaxirasi (Backup)"),
            KeyboardButton(text="ℹ️ Yordam"),
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

    # Row 3: Coins bonus (for children)
    if role == "child":
        buttons.append([
            InlineKeyboardButton(text="💰 +100 tanga", callback_data=f"adm_coin:{user_id}:100"),
            InlineKeyboardButton(text="💰 +500 tanga", callback_data=f"adm_coin:{user_id}:500"),
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
