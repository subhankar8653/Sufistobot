# @suhanibots — main_bot/plugins/admin_panel.py
#
# OWNER-only admin panel inside the MAIN controller bot.
#
#   /admin  ->  buttons: ➕ Add Admin · ➖ Remove Admin · 📋 All bots
#
# Admins added here may use the main bot and CREATE clone bots.
# The owner (OWNER_ID) always sees and can open / edit EVERY bot, whoever created it.
# Admins added to one specific clone bot are managed inside that clone (/addadmin)
# or from the bot's dashboard (👥 Admins).

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from config import OWNER_ID, MAIN_ADMIN_IDS, MAX_BOTS_PER_USER, LOGGER
from database.main_db import MainDB
from main_bot.plugins import access

log = LOGGER(__name__)
main_db = MainDB()


def _state():
    from main_bot.plugins.create_bot import _creation_state
    return _creation_state


async def _panel_text_and_markup():
    ids = sorted(set(await main_db.list_main_admins()))
    env_ids = sorted(i for i in MAIN_ADMIN_IDS if i not in ids)
    bots = await main_db.get_all_bots()

    lines = [f"👑 <code>{OWNER_ID}</code> <i>(owner — sees & controls every bot)</i>"]
    lines += [f"🛡 <code>{i}</code>" for i in ids]
    lines += [f"🛡 <code>{i}</code> <i>(from MAIN_ADMINS env)</i>" for i in env_ids]
    if len(lines) == 1:
        lines.append("<i>No admins yet.</i>")

    text = (
        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
        "👥 𝗔𝗗𝗠𝗜𝗡 𝗣𝗔𝗡𝗘𝗟\n"
        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>\n"
        f"<b>Bots:</b> {len(bots)}/{MAX_BOTS_PER_USER}\n\n"
        "<i>Admins can create & manage their own bots. "
        "You can open every bot from 📋 All Bots.</i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Admin", callback_data="madm_add"),
         InlineKeyboardButton("➖ Remove Admin", callback_data="madm_rem")],
        [InlineKeyboardButton("📋 All Bots", callback_data="my_bots")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
    ])
    return text, markup


def _owner_only(query: CallbackQuery) -> bool:
    return query.from_user.id == OWNER_ID


@Client.on_message(filters.command("admin") & filters.private & filters.user(OWNER_ID))
async def admin_panel_cmd(client: Client, message: Message):
    _state().pop(message.from_user.id, None)
    text, markup = await _panel_text_and_markup()
    await message.reply(text, reply_markup=markup)


@Client.on_callback_query(filters.regex(r"^madm_home$"))
async def madm_home(client: Client, query: CallbackQuery):
    if not _owner_only(query):
        return await query.answer("❌ Owner only.", show_alert=True)
    _state().pop(query.from_user.id, None)
    text, markup = await _panel_text_and_markup()
    await query.message.edit_text(text, reply_markup=markup)
    await query.answer()


@Client.on_callback_query(filters.regex(r"^madm_add$"))
async def madm_add(client: Client, query: CallbackQuery):
    if not _owner_only(query):
        return await query.answer("❌ Owner only.", show_alert=True)
    _state()[query.from_user.id] = {"step": "awaiting_main_admin", "data": {}}
    await query.message.edit_text(
        "<b>➕ Add Admin</b>\n\n"
        "<blockquote>Send ONE of these:\n"
        "• the user ID — <code>123456789</code>\n"
        "• the @username\n"
        "• or forward any message of that person</blockquote>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="madm_home")]]),
    )
    await query.answer()


async def handle_main_admin_input(client: Client, message: Message, state: dict):
    uid = message.from_user.id
    if uid != OWNER_ID:
        _state().pop(uid, None)
        return
    target = await access.resolve_user_id(client, message, text=(message.text or "").strip() or None)
    if not target:
        await message.reply(
            "<b>❌ Could not read that user.</b>\n\n"
            "<blockquote>Send the numeric ID, an @username (the person must have opened "
            "this bot once), or forward a message of that person.</blockquote>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="madm_home")]]),
        )
        return
    _state().pop(uid, None)
    if target == OWNER_ID:
        await message.reply(
            "<b>ℹ️ You are the owner — you always have full access.</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="madm_home")]]),
        )
        return
    added = await main_db.add_main_admin(target, uid)
    access._cache["ids"].add(target)
    await message.reply(
        f"<b>✅ <code>{target}</code> is now an admin.</b>\n\n"
        "<blockquote>They can use this bot and create their own clone bots.</blockquote>"
        if added else f"<b>ℹ️ <code>{target}</code> is already an admin.</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Panel", callback_data="madm_home")]]),
    )
    if added:
        try:
            await client.send_message(
                target,
                "<b>✅ You have been approved as admin!</b>\n\n"
                "<blockquote>Send /start to create and manage your bots.</blockquote>",
            )
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^madm_rem$"))
async def madm_rem(client: Client, query: CallbackQuery):
    if not _owner_only(query):
        return await query.answer("❌ Owner only.", show_alert=True)
    ids = sorted(set(await main_db.list_main_admins()))
    if not ids:
        return await query.answer("No admins to remove.", show_alert=True)
    rows = [[InlineKeyboardButton(f"🗑 {i}", callback_data=f"madm_del_{i}")] for i in ids]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="madm_home")])
    await query.message.edit_text(
        "<b>➖ Remove Admin</b>\n\n<blockquote>Tap an admin to remove.\n"
        "Their bots keep running for their users.</blockquote>",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^madm_del_(\d+)$"))
async def madm_del(client: Client, query: CallbackQuery):
    if not _owner_only(query):
        return await query.answer("❌ Owner only.", show_alert=True)
    target = int(query.data.rsplit("_", 1)[1])
    await main_db.remove_main_admin(target)
    access._cache["ids"].discard(target)
    await query.answer(f"✅ {target} removed", show_alert=True)
    text, markup = await _panel_text_and_markup()
    await query.message.edit_text(text, reply_markup=markup)
