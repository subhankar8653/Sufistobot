# @suhanibots — Open Source flink_logic.py (replaces pyarmor obfuscated version)

import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from utils.helpers import encode, get_message_id, send_main_log
from database.main_db import MainDB
from config import BACKEND_API_URL, LOGGER, BACKEND_API_SECRET

log = LOGGER(__name__)
main_db = MainDB()

# ─────────────────────────────────────────────
#  CANCEL button markup
# ─────────────────────────────────────────────
FLINK_CANCEL_MARKUP = InlineKeyboardMarkup([
    [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="flink:cancel")]
])


def setup_flink(app: Client, worker_db, log_channel_id: int, is_admin_func):
    """Register /flink handlers on the given worker app."""

    _waiting = {}   # user_id -> asyncio.Future

    # ── wait helper ──────────────────────────────────────────────────────────
    async def wait_for_input(user_id: int, timeout: int = 300):
        fut = asyncio.get_event_loop().create_future()
        _waiting[user_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            _waiting.pop(user_id, None)

    # ── permanent-link helper (same logic as link_gen) ────────────────────────
    async def process_link(encoded: str, user_id: int, bot_id: int) -> str:
        bot = await main_db.get_bot(bot_id)
        me = await app.get_me()
        base = f"https://t.me/{me.username}?start={encoded}"

        if not bot:
            return base

        settings = bot.get("settings", {})
        if settings.get("permanent_link") and BACKEND_API_URL:
            try:
                import aiohttp
                headers = {"Authorization": f"Bearer {BACKEND_API_SECRET}"}
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{BACKEND_API_URL}/api/link",
                        json={"token": encoded, "userId": str(bot["owner_id"])},
                        headers=headers,
                    ) as resp:
                        data = await resp.json()
                        if data.get("success"):
                            return f"{BACKEND_API_URL}/?url={encoded}"
            except Exception as e:
                log.error(f"Permanent Link API Error: {e}")

        return base

    # ── input catcher (runs before /flink handler so group=1) ────────────────
    @app.on_message(
        filters.private & ~filters.command(
            ["start", "genlink", "batch", "custom_batch", "flink", "done"]
        ),
        group=3,
    )
    async def flink_input_catcher(client: Client, message: Message):
        uid = message.from_user.id
        if uid in _waiting and not _waiting[uid].done():
            _waiting[uid].set_result(message)
            message.stop_propagation()

    @app.on_message(filters.command("done") & filters.private, group=1)
    async def flink_done_catcher(client: Client, message: Message):
        uid = message.from_user.id
        if uid in _waiting and not _waiting[uid].done():
            _waiting[uid].set_result(message)
            message.stop_propagation()

    # ── /flink main handler ───────────────────────────────────────────────────
    @app.on_message(filters.command("flink") & filters.private)
    async def handle_flink(client: Client, message: Message):
        if not await is_admin_func(message.from_user.id):
            return await message.reply(
                "<b>❌ Tᴜᴍʜᴇ ɪs ᴄᴏᴍᴍᴀɴᴅ ᴋᴀ ᴀᴄᴄᴇss ɴᴀʜɪ ʜᴀɪ.</b>"
            )

        user_id = message.from_user.id

        # ── Step 1: collect all files ────────────────────────────────────────
        await message.reply(
            "<b>📂 Fʟɪɴᴋ Gᴇɴᴇʀᴀᴛᴏʀ\n\n"
            "Sᴇɴᴅ ᴀʟʟ ᴍᴇssᴀɢᴇs / ꜰɪʟᴇs ᴏɴᴇ ʙʏ ᴏɴᴇ.\n"
            "Sᴇɴᴅ /done ᴡʜᴇɴ ꜰɪɴɪsʜᴇᴅ.</b>",
            reply_markup=FLINK_CANCEL_MARKUP,
        )

        collected = []   # list of (msg_id, caption_or_name)

        while True:
            rcv = await wait_for_input(user_id)

            # timeout or cancel
            if rcv is None or rcv == "CANCEL":
                return await message.reply(
                    "<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ / Tɪᴍᴇᴅ Oᴜᴛ...</i></b>"
                )

            # /done received
            is_done = (
                (hasattr(rcv, "command") and rcv.command and rcv.command[0] == "done")
                or (
                    hasattr(rcv, "text")
                    and rcv.text
                    and rcv.text.split("@")[0].strip() == "/done"
                )
            )

            if is_done:
                if not collected:
                    await message.reply("<b>❌ Kᴏɪ ꜰɪʟᴇ ɴᴀʜɪ ᴍɪʟɪ. Pʜɪʀ sᴇ ᴛʀʏ ᴋᴀʀᴏ.</b>")
                    return
                break

            # process received message
            msg_id = await get_message_id(client, rcv, log_channel_id)
            if msg_id:
                # grab a display name for this file
                label = ""
                if rcv.caption:
                    label = rcv.caption.split("\n")[0][:80]
                elif rcv.document and rcv.document.file_name:
                    label = rcv.document.file_name[:80]
                elif rcv.video and rcv.video.file_name:
                    label = rcv.video.file_name[:80]
                elif rcv.audio and rcv.audio.file_name:
                    label = rcv.audio.file_name[:80]
                else:
                    label = f"File {len(collected) + 1}"

                collected.append((msg_id, label))
                await rcv.reply(
                    f"<b>✅ Aᴅᴅᴇᴅ ({len(collected)}): <code>{label}</code></b>",
                    quote=True,
                )
            else:
                await rcv.reply(
                    "<b>❌ Tʜɪs ᴍᴇssᴀɢᴇ ᴘʀᴏᴄᴇss ɴᴀʜɪ ʜᴜᴀ. Sᴋɪᴘᴘɪɴɢ...</b>",
                    quote=True,
                )

        # ── Step 2: ask for a title ──────────────────────────────────────────
        await message.reply(
            "<b>📝 Aʙ ɪs ʟɪsᴛ ᴋᴀ ᴛɪᴛʟᴇ ᴛʏᴘᴇ ᴋᴀʀᴏ:\n"
            "(ʏᴀ /skip ʙʜᴇᴊᴏ ᴀɢᴀʀ ᴛɪᴛʟᴇ ɴᴀʜɪ ᴄʜᴀʜɪʏᴇ)</b>",
            reply_markup=FLINK_CANCEL_MARKUP,
        )

        title_msg = await wait_for_input(user_id)
        if title_msg is None or title_msg == "CANCEL":
            return await message.reply(
                "<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ / Tɪᴍᴇᴅ Oᴜᴛ...</i></b>"
            )

        title_text = ""
        if hasattr(title_msg, "text") and title_msg.text:
            if title_msg.text.strip().lower() not in ["/skip", "/skip@" + (await client.get_me()).username.lower()]:
                title_text = title_msg.text.strip()

        # ── Step 3: generate individual links + build formatted message ───────
        wait_msg = await message.reply("<b>⏳ Lɪɴᴋs ɢᴇɴᴇʀᴀᴛᴇ ʜᴏ ʀᴀʜᴇ ʜᴀɪɴ...</b>")

        me = await client.get_me()
        lines = []

        if title_text:
            lines.append(f"<b>🎬 {title_text}</b>\n")

        for idx, (msg_id, label) in enumerate(collected, start=1):
            encoded = await encode(f"get-{msg_id * abs(log_channel_id)}")
            link = await process_link(encoded, user_id, me.id)
            lines.append(f"{idx}. <a href='{link}'>{label}</a>")

        formatted_text = "\n".join(lines)

        # Telegram message limit safety
        if len(formatted_text) > 4000:
            formatted_text = formatted_text[:4000] + "\n...(truncated)"

        await message.reply(
            f"<b>✅ Fʟɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ ({len(collected)} ꜰɪʟᴇs):</b>\n\n{formatted_text}",
            disable_web_page_preview=True,
        )
        await wait_msg.delete()

        # ── log ──────────────────────────────────────────────────────────────
        log_msg = (
            f"<b>🔗 Fʟɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
            f"<b>• Bᴏᴛ:</b> @{me.username}\n"
            f"<b>• Bᴏᴛ ID:</b> <code>{me.id}</code>\n"
            f"<b>• Oᴡɴᴇʀ:</b> <code>{user_id}</code>\n"
            f"<b>• Fɪʟᴇs:</b> <code>{len(collected)}</code>\n"
            f"<b>• Mᴇᴛʜᴏᴅ:</b> <code>/flink</code>"
        )
        await send_main_log(client, log_msg)

    # ── cancel callback ───────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex(r"^flink:cancel$"))
    async def flink_cancel_cb(client: Client, query: CallbackQuery):
        uid = query.from_user.id
        if uid in _waiting and not _waiting[uid].done():
            _waiting[uid].set_result("CANCEL")
        await query.answer("Cancelled")

# @suhanibots
