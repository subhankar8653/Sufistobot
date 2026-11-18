# @suhanibots — worker_bot/admin_cmds.py
#
# Admin management INSIDE every clone (worker) bot.
#
#   /addadmin <user_id | @username>   (or reply to / forward the person's message)
#   /deladmin <user_id | @username>   (or reply to / forward the person's message)
#   /admins                           list owner + admins
#   /version                          quick self-check (which code is answering this bot)
#
# Who may do what:
#   * bot OWNER (the person who created the clone) and the super owner (OWNER_ID)
#       -> /addadmin, /deladmin, /admins, /version
#   * admins of this bot  -> /admins, /version
#   * everybody else      -> no reply at all
#
# Every change is written to BOTH places that hold admins:
#   1. the bot's own collection  (WorkerDB.add_admin / del_admin)  -> used by the bot itself
#   2. the registry doc          (MainDB.add_bot_admin / remove_bot_admin)
#      -> makes the bot show up in the admin's "My Bots" inside the main bot

import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import LOGGER, OWNER_ID
from database.main_db import MainDB

log = LOGGER(__name__)
main_db = MainDB()

_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")

CODE_VERSION = "2026-11-fix1"


def _forwarded_user_id(msg: Message):
    """User id of the ORIGINAL sender of a forwarded message (None if hidden / not forwarded)."""
    if msg is None:
        return None
    origin = getattr(msg, "forward_origin", None)          # newer pyrogram / kurigram
    if origin is not None:
        user = getattr(origin, "sender_user", None)
        if user is not None:
            return user.id
    user = getattr(msg, "forward_from", None)              # older pyrogram
    if user is not None:
        return user.id
    return None


async def _resolve_target(client: Client, message: Message):
    """Return (user_id | None, error_text | None)."""
    # 1) /cmd <id or @username>
    if len(message.command) > 1:
        raw = message.command[1].strip()
        if raw.lstrip("-").isdigit():
            return int(raw), None
        m = _USERNAME_RE.match(raw)
        if m:
            try:
                user = await client.get_users(m.group(1))
                return user.id, None
            except Exception:
                return None, (
                    "I can't find that username. Ask the person to press /start in this bot first, "
                    "or use their numeric ID (get it from @userinfobot)."
                )
        return None, "That is not a valid user ID or @username."

    # 2) the command message is itself a reply to somebody
    reply = message.reply_to_message
    if reply is not None:
        fwd = _forwarded_user_id(reply)
        if fwd:
            return fwd, None
        if reply.from_user is not None and not reply.from_user.is_bot:
            return reply.from_user.id, None

    return None, None


def setup_admin_cmds(app: Client, worker_db, bot_id: int, owner_id: int, is_admin):
    """Register /addadmin /deladmin /admins /version on one worker bot."""

    def can_grant(uid: int) -> bool:
        return uid == owner_id or (bool(OWNER_ID) and uid == OWNER_ID)

    @app.on_message(filters.command("addadmin") & filters.private)
    async def _add_admin(client: Client, message: Message):
        if message.from_user is None:
            return
        uid = message.from_user.id
        if not can_grant(uid):
            if await is_admin(uid):
                await message.reply("<b>❌ Only the bot owner can add or remove admins.</b>")
            return  # strangers get no reply

        target, err = await _resolve_target(client, message)
        if not target:
            await message.reply(
                (f"<b>❌ {err}</b>\n\n" if err else "")
                + "<b>How to add an admin:</b>\n"
                "<blockquote>• <code>/addadmin 123456789</code>\n"
                "• <code>/addadmin @username</code>\n"
                "• reply to the person's message with <code>/addadmin</code>\n"
                "• or reply to a message they forwarded</blockquote>"
            )
            return

        if can_grant(target):
            await message.reply("<b>ℹ️ That user is the owner — owners always have full access.</b>")
            return

        if await worker_db.admin_exist(target):
            await message.reply(f"<b>ℹ️ <code>{target}</code> is already an admin of this bot.</b>")
            return

        await worker_db.add_admin(target)
        try:
            await main_db.add_bot_admin(bot_id, target)   # shows up in the admin's "My Bots"
        except Exception as e:
            log.error(f"add_bot_admin registry sync failed (bot {bot_id}): {e}")

        await message.reply(
            f"<b>✅ <code>{target}</code> is now an admin!</b>\n\n"
            "<blockquote>They can use /genlink, /batch, /custom_batch, /flink, /ban, /unban, "
            "/broadcast and the fsub admin commands in this bot.</blockquote>"
        )
        try:
            await client.send_message(
                target,
                "<b>✅ You have been made an admin of this bot!</b>\n\n"
                "<blockquote>Send /help or use /genlink to create file links.</blockquote>",
            )
        except Exception:
            pass  # they have not started the bot yet — no problem

    @app.on_message(filters.command("deladmin") & filters.private)
    async def _del_admin(client: Client, message: Message):
        if message.from_user is None:
            return
        uid = message.from_user.id
        if not can_grant(uid):
            if await is_admin(uid):
                await message.reply("<b>❌ Only the bot owner can add or remove admins.</b>")
            return

        target, err = await _resolve_target(client, message)
        if not target:
            await message.reply(
                (f"<b>❌ {err}</b>\n\n" if err else "")
                + "<b>Usage:</b> <code>/deladmin 123456789</code> (or @username, or reply to the person)"
            )
            return

        if can_grant(target):
            await message.reply("<b>❌ The owner can't be removed.</b>")
            return

        if not await worker_db.admin_exist(target):
            await message.reply(f"<b>ℹ️ <code>{target}</code> is not an admin of this bot.</b>")
            return

        await worker_db.del_admin(target)
        try:
            await main_db.remove_bot_admin(bot_id, target)
        except Exception as e:
            log.error(f"remove_bot_admin registry sync failed (bot {bot_id}): {e}")
        await message.reply(f"<b>✅ <code>{target}</code> removed from admins.</b>")

    @app.on_message(filters.command("admins") & filters.private)
    async def _list_admins(client: Client, message: Message):
        if message.from_user is None:
            return
        uid = message.from_user.id
        if not (can_grant(uid) or await is_admin(uid)):
            return
        admins = await worker_db.get_all_admins()
        lines = ["<b>👥 Admins of this bot</b>\n", f"👑 <code>{owner_id}</code> (owner)"]
        lines += [f"• <code>{a}</code>" for a in admins if a != owner_id]
        if not admins:
            lines.append("\n<i>No extra admins yet.</i>")
        if can_grant(uid):
            lines.append(
                "\n<i>Add: /addadmin &lt;id&gt; · Remove: /deladmin &lt;id&gt;</i>"
            )
        await message.reply("\n".join(lines))

    @app.on_message(filters.command("version") & filters.private)
    async def _version(client: Client, message: Message):
        if message.from_user is None or not (can_grant(message.from_user.id) or await is_admin(message.from_user.id)):
            return
        try:
            chans = await worker_db.show_channels()
        except Exception:
            chans = []
        me = await client.get_me()
        await message.reply(
            "<b>🛠 Bot self-check</b>\n\n"
            f"<blockquote>Bot: @{me.username} (<code>{me.id}</code>)\n"
            f"Code version: <code>{CODE_VERSION}</code>\n"
            f"Force-sub channels set: <b>{len(chans)}</b></blockquote>\n"
            "<i>If this command does not answer, this bot is NOT running on this server's code.</i>"
        )
