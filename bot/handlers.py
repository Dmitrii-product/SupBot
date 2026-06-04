"""Telegram handlers (aiogram 3.x).

Customer flow: text -> RAG -> reply, or hand off to the shop's manager.
Manager flow (only from the shop's manager_chat_id):
  /reply <user_id> <text>   relay an answer to the customer through the bot
  /resume <user_id>         clear escalation; bot resumes auto-replies
"""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot import config, rag, store, translit

router = Router()
log = logging.getLogger(__name__)

GREETING = (
    "Assalomu alaykum! 👋 Savolingizni yozing — narx, o'lcham, mavjudlik bo'yicha "
    "yordam beraman.\n\nЗдравствуйте! Напишите ваш вопрос — помогу с ценой, "
    "размером и наличием."
)
HANDED_OFF = (
    "Bir lahza — menejerimiz tez orada javob beradi. 🙌\n"
    "Секунду — наш менеджер скоро ответит."
)


def _shop_for(bot: Bot, shops: dict) -> dict | None:
    return shops.get(bot.token)


def _wants_human(text: str) -> bool:
    folded = translit.fold(text)
    return any(translit.fold(kw) in folded for kw in config.ESCALATE_KEYWORDS)


@router.message(CommandStart())
async def on_start(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if shop and message.chat.id == shop["manager_chat_id"]:
        await message.answer(
            "Manager console.\n"
            "/reply <user_id> <text> — answer a customer\n"
            "/resume <user_id> — let the bot take over again"
        )
        return
    await message.answer(GREETING)


@router.message(Command("reply"))
async def on_reply(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop or message.chat.id != shop["manager_chat_id"]:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /reply <user_id> <text>")
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await message.answer("user_id must be a number.")
        return
    try:
        await bot.send_message(user_id, parts[2])
        await message.answer(f"Sent to {user_id}. ✅")
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Could not send: {e}")


@router.message(Command("resume"))
async def on_resume(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop or message.chat.id != shop["manager_chat_id"]:
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /resume <user_id>")
        return
    store.clear_escalated(shop["shop_id"], int(parts[1]))
    await message.answer(f"Bot resumed for {parts[1]}. 🤖")


@router.message(F.text)
async def on_text(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop:
        return
    # Ignore the manager's free-form chatter (commands handled above).
    if message.chat.id == shop["manager_chat_id"]:
        return

    shop_id, user_id, text = shop["shop_id"], message.from_user.id, message.text

    # If a human already owns this conversation, stay silent.
    if store.is_escalated(shop_id, user_id):
        return

    if _wants_human(text):
        await _handoff(message, bot, shop, reason="customer asked for a human")
        return

    reply = await asyncio.to_thread(rag.answer, shop_id, text)
    if reply == rag.ESCALATE:
        await _handoff(message, bot, shop, reason="bot was not confident")
        return
    await message.answer(reply)


async def _handoff(message: Message, bot: Bot, shop: dict, reason: str):
    store.set_escalated(shop["shop_id"], message.from_user.id)
    await message.answer(HANDED_OFF)
    u = message.from_user
    note = (
        f"🔔 Handoff ({reason})\n"
        f"From: {u.full_name} @{u.username or '—'} (id {u.id})\n"
        f"Message: {message.text}\n\n"
        f"Reply with:  /reply {u.id} your answer"
    )
    try:
        await bot.send_message(shop["manager_chat_id"], note)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not notify manager for %s: %s", shop["shop_id"], e)
