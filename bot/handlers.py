"""Telegram handlers (aiogram 3.x).

Customer flow:  text → RAG → reply, or escalate to human manager.
Manager flow (only from the shop's manager_chat_id):
  /customers              — inline list of active escalations
  Tap "Open chat"         — view history and set as active chat
  Free text (when active) — forwarded directly to that customer
  Tap "Resume AI"         — hand conversation back to the bot
  /reply <id> <text>      — manual relay (fallback)
  /resume <id>            — manual resume (fallback)
"""
import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import config, rag, store, translit

router = Router()
log = logging.getLogger(__name__)

GREETING = (
    "Assalomu alaykum! 👋 Savolingizni yozing — narx, o'lcham, mavjudlik bo'yicha "
    "yordam beraman.\n\nЗравствуйте! Напишите ваш вопрос — помогу с ценой, "
    "размером и наличием."
)
HANDED_OFF = (
    "Bir lahza — menejerimiz tez orada javob beradi. 🙌\n"
    "Секунду — наш менеджер скоро ответит."
)
BOT_RESUMED = (
    "Bot yana ishga tushdi! Savollaringizni yozing. 🤖\n"
    "Бот снова подключён! Пишите ваши вопросы."
)
OFF_TOPIC_REPLY = (
    "Kechirasiz, bu savol do'konimiz doirasidan tashqarida — men faqat mahsulotlar, "
    "narxlar va buyurtmalar haqida yordam bera olaman. 🙏\n\n"
    "Извините, этот вопрос выходит за рамки нашего магазина — я могу помочь только "
    "с товарами, ценами и заказами."
)

# manager_chat_id → currently active customer user_id (in-memory, resets on restart)
_active: dict[int, int] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shop_for(bot: Bot, shops: dict) -> dict | None:
    return shops.get(bot.token)


def _wants_human(text: str) -> bool:
    folded = translit.fold(text)
    return any(translit.fold(kw) in folded for kw in config.ESCALATE_KEYWORDS)


def _e(text: str | None) -> str:
    """HTML-escape a string for safe use in parse_mode=HTML messages."""
    return html.escape(str(text or ""))


def _resume_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Resume AI", callback_data=f"mgr:resume:{user_id}"),
        InlineKeyboardButton(text="← Customers", callback_data="mgr:back"),
    ]])


def _customer_list_kb(contacts: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"💬 {c['full_name']}", callback_data=f"mgr:pick:{c['user_id']}"
            ),
            InlineKeyboardButton(
                text="✅ AI", callback_data=f"mgr:resume:{c['user_id']}"
            ),
        ]
        for c in contacts
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_customer_list(contacts: list[dict]) -> str:
    if not contacts:
        return "No active escalations. All chats handled by the bot. ✅"
    lines = [f"<b>Active chats — {len(contacts)}</b>"]
    for c in contacts:
        uname = f" @{_e(c['username'])}" if c["username"] else ""
        last = _e(c["last_msg"] or "")
        if len(last) > 60:
            last = last[:60] + "…"
        lines.append(f"\n👤 <b>{_e(c['full_name'])}</b>{uname}\n<i>{last}</i>")
    return "\n".join(lines)


def _fmt_history(history: list[tuple[str, str]], customer_name: str) -> str:
    lines = [f"💬 <b>{_e(customer_name)}</b>\n"]
    for role, text in history:
        if role == "customer":
            lines.append(f"👤 {_e(text)}")
        elif role == "manager":
            lines.append(f"💼 <b>You:</b> {_e(text)}")
        else:
            lines.append(f"🤖 {_e(text)}")
    lines.append("\n<i>Type to reply — your messages go directly to this customer.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Customer-facing handlers
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def on_start(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop:
        return
    if message.chat.id == shop["manager_chat_id"]:
        await _send_customer_list(message, shop)
        return
    await message.answer(GREETING)


@router.message(F.text)
async def on_text(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop:
        return

    shop_id = shop["shop_id"]

    # --- Manager free text: forward to active customer ---
    if message.chat.id == shop["manager_chat_id"]:
        active_user = _active.get(message.chat.id)
        if active_user:
            try:
                await bot.send_message(active_user, message.text)
                store.log_message(shop_id, active_user, "manager", message.text)
                await message.answer("✅ Sent.", reply_markup=_resume_kb(active_user))
            except Exception as e:  # noqa: BLE001
                await message.answer(f"Could not send: {e}")
        else:
            await message.answer("No active chat selected.\nUse /customers to pick one.")
        return

    # --- Customer flow ---
    user_id = message.from_user.id
    store.upsert_contact(shop_id, user_id, message.from_user.full_name, message.from_user.username)
    store.log_message(shop_id, user_id, "customer", message.text)

    if store.is_escalated(shop_id, user_id):
        u = message.from_user
        fwd = f"💬 <b>{_e(u.full_name)}</b> @{_e(u.username)} (id <code>{u.id}</code>):\n{_e(message.text)}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💬 Open chat", callback_data=f"mgr:pick:{u.id}"),
            InlineKeyboardButton(text="✅ Resume AI", callback_data=f"mgr:resume:{u.id}"),
        ]])
        try:
            await bot.send_message(shop["manager_chat_id"], fwd, reply_markup=kb, parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            log.warning("Could not forward to manager for %s: %s", shop_id, e)
        return

    if _wants_human(message.text):
        await _handoff(message, bot, shop, reason="customer asked for a human")
        return

    reply = await asyncio.to_thread(rag.answer, shop_id, message.text)
    if reply == rag.ESCALATE:
        await _handoff(message, bot, shop, reason="bot was not confident")
        return
    if reply == rag.OFFTOPIC:
        await message.answer(OFF_TOPIC_REPLY)
        return
    store.log_message(shop_id, user_id, "bot", reply)
    await message.answer(reply)


async def _handoff(message: Message, bot: Bot, shop: dict, reason: str):
    store.set_escalated(shop["shop_id"], message.from_user.id)
    await message.answer(HANDED_OFF)
    u = message.from_user
    note = (
        f"🔔 <b>Handoff</b> ({_e(reason)})\n"
        f"From: <b>{_e(u.full_name)}</b> @{_e(u.username)} (id <code>{u.id}</code>)\n"
        f"Message: <i>{_e(message.text)}</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Open chat", callback_data=f"mgr:pick:{u.id}"),
        InlineKeyboardButton(text="✅ Resume AI", callback_data=f"mgr:resume:{u.id}"),
    ]])
    try:
        await bot.send_message(shop["manager_chat_id"], note, reply_markup=kb, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        log.warning("Could not notify manager for %s: %s", shop["shop_id"], e)


# ---------------------------------------------------------------------------
# Manager commands
# ---------------------------------------------------------------------------

@router.message(Command("customers"))
async def on_customers(message: Message, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop or message.chat.id != shop["manager_chat_id"]:
        return
    await _send_customer_list(message, shop)


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
        store.log_message(shop["shop_id"], user_id, "manager", parts[2])
        await message.answer(f"Sent to {user_id}. ✅", reply_markup=_resume_kb(user_id))
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
    user_id = int(parts[1])
    store.clear_escalated(shop["shop_id"], user_id)
    if _active.get(message.chat.id) == user_id:
        del _active[message.chat.id]
    try:
        await bot.send_message(user_id, BOT_RESUMED)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not notify customer %s on resume: %s", user_id, e)
    await message.answer(f"Bot resumed for {user_id}. 🤖")


# ---------------------------------------------------------------------------
# Manager inline-keyboard callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("mgr:pick:"))
async def on_pick_customer(callback: CallbackQuery, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop or callback.message.chat.id != shop["manager_chat_id"]:
        await callback.answer()
        return
    user_id = int(callback.data.split(":")[2])
    _active[callback.message.chat.id] = user_id

    contacts = store.get_escalated_contacts(shop["shop_id"])
    contact = next((c for c in contacts if c["user_id"] == user_id), None)
    name = contact["full_name"] if contact else str(user_id)

    history = store.get_history(shop["shop_id"], user_id, limit=20)
    text = _fmt_history(history, name)
    await callback.message.edit_text(text, reply_markup=_resume_kb(user_id), parse_mode="HTML")
    await callback.answer(f"Now chatting with {name}")


@router.callback_query(F.data.startswith("mgr:resume:"))
async def on_resume_customer(callback: CallbackQuery, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop or callback.message.chat.id != shop["manager_chat_id"]:
        await callback.answer()
        return
    user_id = int(callback.data.split(":")[2])
    store.clear_escalated(shop["shop_id"], user_id)
    if _active.get(callback.message.chat.id) == user_id:
        del _active[callback.message.chat.id]
    try:
        await bot.send_message(user_id, BOT_RESUMED)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not notify customer %s on resume: %s", user_id, e)
    await callback.answer("AI resumed ✅")
    await _refresh_customer_list(callback, shop)


@router.callback_query(F.data == "mgr:back")
async def on_back(callback: CallbackQuery, bot: Bot, shops: dict):
    shop = _shop_for(bot, shops)
    if not shop:
        await callback.answer()
        return
    if callback.message.chat.id in _active:
        del _active[callback.message.chat.id]
    await _refresh_customer_list(callback, shop)
    await callback.answer()


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

async def _send_customer_list(message: Message, shop: dict) -> None:
    contacts = store.get_escalated_contacts(shop["shop_id"])
    text = _fmt_customer_list(contacts)
    kb = _customer_list_kb(contacts) if contacts else None
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _refresh_customer_list(callback: CallbackQuery, shop: dict) -> None:
    contacts = store.get_escalated_contacts(shop["shop_id"])
    text = _fmt_customer_list(contacts)
    kb = _customer_list_kb(contacts) if contacts else None
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
