import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)

# Known spam, advertisement, search bot, and gambling keywords
AD_SPAM_PATTERNS = [
    r"поисковый\s+бот",
    r"варианты\s+поиска",
    r"персональные\s+данные",
    r"попробуйте\s+бесплатно",
    r"глаз\s+бога",
    r"пробив\s+(по|номера|авто|человека|данных)",
    r"(1xbet|mostbet|melbet|linebet|pin-up|казино|casino|рулетка|ruletka)",
    r"(stavka|stavkalar|stavka\s+qiling|stavka\s+bosing)",
    r"(kripto\s+daromad|kuniga\s+\$?\d+|pul\s+ishlash|oson\s+daromad)",
    r"(подпишись\s+на|kanalga\s+obuna\s+bo'ling|kanalga\s+a'zo\s+bo'l)",
    r"(reklama\s+uchun|reklama\s+tarqatish|reklama\s+xizmati)",
    r"(daromad\s+olishni\s+xohlaysizmi|investitsiya\s+loyihasi)",
    r"(tucosprofit|tucos|must\s+join\s+our\s+channel|to\s+use\s+this\s+bot)",
    r"(obuna\s+bo'ling|kanalimizga\s+qo'shiling|join\s+our\s+channel)",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in AD_SPAM_PATTERNS]

ALLOWED_DOMAINS = [
    "child_security_bot",
    "child_security_owner_bot",
    "child-security-bot.vercel.app",
    "child-security-site.onrender.com",
    "openstreetmap.org",
    "google.com/maps",
]


def is_ad_or_spam(text: str) -> bool:
    """Check if message text contains forbidden advertisements or spam."""
    if not text:
        return False

    # Check compiled regex keywords
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return True

    # Check for external telegram channel promotion invite links
    if "t.me/+" in text or "t.me/joinchat/" in text:
        return True

    # Check external t.me bot/channel links if they are not our bots
    if "t.me/" in text.lower():
        is_safe = any(domain in text.lower() for domain in ALLOWED_DOMAINS)
        if not is_safe:
            return True

    return False


async def delete_after_delay(msg: Message, delay: int = 7):
    """Safely delete auto-reply warning after given delay in seconds."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


async def clean_preceding_messages(bot, chat_id: int, current_mid: int, count: int = 5):
    """Purge preceding messages in case ad was delivered across multiple bubbles."""
    for mid in range(current_mid - 1, max(1, current_mid - count), -1):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


async def purge_chat_history(bot, chat_id: int, start_mid: int, limit: int = 50) -> int:
    """Retroactively delete old clutter, messages, and ads in chat history."""
    deleted = 0
    for mid in range(start_mid, max(1, start_mid - limit), -1):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass
    return deleted


class AntiAdShieldMiddleware(BaseMiddleware):
    """Outer middleware intercepting and auto-deleting all advertisements and spam messages."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event

        # Never filter messages from the project owner
        from_user = message.from_user
        if from_user and (
            from_user.id in (1, 6707839718)
            or (from_user.username and from_user.username.lower().lstrip("@") == "fayzbek1331")
        ):
            return await handler(event, data)

        text_to_check = message.text or message.caption or ""

        # Also inspect forwarded messages from channels
        if message.forward_from_chat or message.forward_origin:
            if is_ad_or_spam(text_to_check):
                logger.warning(
                    f"Blocked forwarded ad message from user {message.from_user.id}: {text_to_check[:60]}..."
                )
                try:
                    await message.delete()
                except Exception:
                    pass

                asyncio.create_task(
                    clean_preceding_messages(message.bot, message.chat.id, message.message_id, 5)
                )

                try:
                    warn = await message.answer(
                        "🚫 <b>Reklama va spam taqiqlangan!</b>\n\n"
                        "Ushbu bot faqat oilaviy xavfsizlik va farzandlar nazorati uchun mo'ljallangan. "
                        "Reklama xabari avtomatik ravishda o'chirildi va hech kimga yuborilmadi.",
                        parse_mode="HTML"
                    )
                    asyncio.create_task(delete_after_delay(warn, 7))
                except Exception:
                    pass
                return

        # Check standard message text
        if is_ad_or_spam(text_to_check):
            logger.warning(
                f"Auto-deleted ad/spam message from user {message.from_user.id}: {text_to_check[:60]}..."
            )
            try:
                await message.delete()
            except Exception:
                pass

            asyncio.create_task(
                clean_preceding_messages(message.bot, message.chat.id, message.message_id, 5)
            )

            try:
                warn = await message.answer(
                    "🚫 <b>Reklama va spam xabarlar taqiqlangan!</b>\n\n"
                    "Ushbu bot faqat oilaviy xavfsizlik uchun mo'ljallangan. "
                    "Yuborilgan reklama xabari avtomatik o'chirildi va hech kimga bormadi.",
                    parse_mode="HTML"
                )
                asyncio.create_task(delete_after_delay(warn, 7))
            except Exception:
                pass
            return

        return await handler(event, data)
