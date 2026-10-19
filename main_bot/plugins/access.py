#@suhanibots
"""
Main-bot access control.

Only the OWNER (OWNER_ID) and the people the owner approved may use the MAIN bot
(open the panel, create bots, manage bots). Everybody else gets NO response at all:
their messages, button taps and inline queries are silently dropped before any other
handler sees them.

This only guards the main controller bot. The worker bots people create stay public
(their end users are never affected).

Owner commands (in the main bot):
    /addadmin <user_id>     (or reply to the person's message)
    /deladmin <user_id>     (or reply)
    /adminlist
"""

import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineQuery, Message

from config import OWNER_ID, MAIN_ADMIN_IDS, LOGGER
from database.main_db import MainDB

log = LOGGER(__name__)
main_db = MainDB()

_CACHE_TTL = 15  # seconds; /addadmin and /deladmin update the cache instantly
_cache = {"t": 0.0, "ids": set()}


async def _approved_ids() -> set:
    now = time.monotonic()
    if now - _cache["t"] > _CACHE_TTL:
        try:
            _cache["ids"] = set(await main_db.list_main_admins())
        except Exception as e:
            log.error(f"access: could not load approved admins: {e}")  # keep the old list
        _cache["t"] = now
    return _cache["ids"]


async def is_authorized(user_id: int) -> bool:
    """OWNER_ID, MAIN_ADMINS env, or an admin added with /addadmin."""
    if user_id == OWNER_ID or user_id in MAIN_ADMIN_IDS:
        return True
    return user_id in await _approved_ids()


# =============================================================================
# THE GATE — runs before every other handler (group -100)
# =============================================================================

@Client.on_message(group=-100)
async def _gate_message(client: Client, message: Message):
    user = message.from_user
    if user is None:          # channel posts / service messages: not our business
        return
    if not await is_authorized(user.id):
        message.stop_propagation()   # silence: no reply, no error, nothing


@Client.on_callback_query(group=-100)
async def _gate_callback(client: Client, query: CallbackQuery):
    if not await is_authorized(query.from_user.id):
        try:
            await query.answer()      # empty answer: just stops the loading spinner, shows nothing
        except Exception:
            pass
        query.stop_propagation()


@Client.on_inline_query(group=-100)
async def _gate_inline(client: Client, query: InlineQuery):
    if not await is_authorized(query.from_user.id):
        query.stop_propagation()


# =============================================================================
# OWNER COMMANDS
# =============================================================================

def _target_id(message: Message):
    """User id from '/cmd 123456' or from the replied-to message."""
    if len(message.command) > 1:
        raw = message.command[1].strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
        return None
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    return None


@Client.on_message(filters.command("addadmin") & filters.private & filters.user(OWNER_ID))
async def add_admin_cmd(client: Client, message: Message):
    target = _target_id(message)
    if not target:
        await message.reply(
            "<b>Usage:</b> <code>/addadmin 123456789</code>\n"
            "<i>or reply to that person's message with /addadmin</i>"
        )
        return
    if target == OWNER_ID:
        await message.reply("<b>ℹ️ You are the owner — you always have access.</b>")
        return
    added = await main_db.add_main_admin(target, message.from_user.id)
    _cache["ids"].add(target)
    await message.reply(
        f"<b>✅ <code>{target}</code> can now use this bot and create bots.</b>"
        if added else f"<b>ℹ️ <code>{target}</code> already has access.</b>"
    )
    if added:
        try:
            await client.send_message(
                target,
                "<b>✅ You have been approved!</b>\n\n"
                "<blockquote>You can now use this bot. Send /start to begin.</blockquote>",
            )
        except Exception:
            pass  # they never opened the bot yet — they can simply send /start


@Client.on_message(filters.command("deladmin") & filters.private & filters.user(OWNER_ID))
async def del_admin_cmd(client: Client, message: Message):
    target = _target_id(message)
    if not target:
        await message.reply("<b>Usage:</b> <code>/deladmin 123456789</code>")
        return
    if target == OWNER_ID:
        await message.reply("<b>❌ The owner can't be removed.</b>")
        return
    if target in MAIN_ADMIN_IDS:
        await message.reply(
            "<b>⚠️ This ID is set in the <code>MAIN_ADMINS</code> environment variable.</b>\n"
            "Remove it there to revoke access."
        )
        return
    removed = await main_db.remove_main_admin(target)
    _cache["ids"].discard(target)
    await message.reply(
        f"<b>✅ <code>{target}</code> removed. They get no response from this bot anymore.</b>\n"
        "<i>Bots they already created keep running for their users.</i>"
        if removed else f"<b>ℹ️ <code>{target}</code> was not an approved admin.</b>"
    )


@Client.on_message(filters.command("adminlist") & filters.private & filters.user(OWNER_ID))
async def admin_list_cmd(client: Client, message: Message):
    ids = sorted(set(await main_db.list_main_admins()))
    env_ids = sorted(MAIN_ADMIN_IDS)
    lines = ["<b>👥 Approved admins</b>\n"]
    lines.append(f"👑 <code>{OWNER_ID}</code> (owner)")
    lines += [f"• <code>{i}</code>" for i in ids]
    lines += [f"• <code>{i}</code> <i>(from MAIN_ADMINS env)</i>" for i in env_ids if i not in ids]
    if len(lines) == 2:
        lines.append("\n<i>No one else yet. Add with /addadmin</i>")
    await message.reply("\n".join(lines))
