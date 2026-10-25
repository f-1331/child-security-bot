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
        # If it contains t.me/ but none of our allowed domains, check if it's an ad
        if not is_safe and ("bot?start=" in text.lower() or "channel" in text.lower()):
            for kw in ["podpiska", "obuna", "kanal", "qidiruv", "bot"]:
                if kw in text.lower():
                    return True

    return False


async def delete_after_delay(msg: Message, delay: int = 7):
    """Safely delete auto-reply warning after given delay in seconds."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


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
