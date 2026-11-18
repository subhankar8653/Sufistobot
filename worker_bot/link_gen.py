# @suhanibots — link_gen.py (rebuilt: /genlink, /batch, /custom_batch)
#
# Link format (must match worker_bot/engine.py -> /start decoder):
#   single : get-{msg_id * abs(log_channel_id)}
#   batch  : get-{first_id * abs(log_channel_id)}-{last_id * abs(log_channel_id)}

import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import BACKEND_API_URL, BACKEND_API_SECRET, LOGGER
from database.main_db import MainDB
from utils.helpers import encode, get_message_id, send_main_log

log = LOGGER(__name__)
main_db = MainDB()

_NO_ACCESS = "<b>❌ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴅᴍɪɴ ᴏꜰ ᴛʜɪs ʙᴏᴛ.</b>"
_CANCELLED = "<b><i>🆑 ᴏᴘᴇʀᴀᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ / ᴛɪᴍᴇᴅ ᴏᴜᴛ...</i></b>"
_INVALID = (
    "<b>❌ ᴇʀʀᴏʀ\n\n"
    "ᴛʜɪs ᴍᴇssᴀɢᴇ ᴄᴏᴜʟᴅ ɴᴏᴛ ʙᴇ ᴘʀᴏᴄᴇssᴇᴅ. "
    "ꜰᴏʀᴡᴀʀᴅ ᴀ ᴘᴏsᴛ ꜰʀᴏᴍ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ, sᴇɴᴅ ᴀ ꜰɪʟᴇ, ᴏʀ sᴇɴᴅ ᴀ ᴘᴏsᴛ ʟɪɴᴋ.</b>"
)

_COMMANDS = ["start", "genlink", "batch", "custom_batch", "flink", "ban", "unban", "broadcast",
             "addadmin", "deladmin", "admins", "version", "oldchannel"]


def setup_link_gen(app: Client, log_channel_id: int, is_admin_func):
    """Register /genlink, /batch and /custom_batch handlers on a worker app."""

    _waiting: dict[int, asyncio.Future] = {}  # user_id -> Future(Message)

    # ── helpers ──────────────────────────────────────────────────────────────
    async def wait_for_input(user_id: int, timeout: int = 300):
        fut = asyncio.get_event_loop().create_future()
        _waiting[user_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            _waiting.pop(user_id, None)

    def _is_cmd(msg: Message, name: str) -> bool:
        txt = (msg.text or "").strip().lower()
        return txt == f"/{name}" or txt.startswith(f"/{name}@")

    async def process_link(client: Client, encoded: str, owner_check_id: int) -> str:
        """Build the final link (plain t.me link, or permanent link if enabled)."""
        me = await client.get_me()
        base = f"https://t.me/{me.username}?start={encoded}"

        try:
            bot = await main_db.get_bot(me.id)
        except Exception as e:
            log.error(f"link_gen: get_bot failed: {e}")
            bot = None
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

    async def ask_for_post(client: Client, message: Message, prompt: str):
        """Ask for a post until a valid one arrives. Returns (rcv, msg_id) or (None, None)."""
        user_id = message.from_user.id
        await message.reply(prompt)
        while True:
            rcv = await wait_for_input(user_id)
            if rcv is None or _is_cmd(rcv, "cancel"):
                await message.reply(_CANCELLED)
                return None, None
            msg_id = await get_message_id(client, rcv, log_channel_id)
            if msg_id:
                return rcv, msg_id
            await rcv.reply(_INVALID, quote=True)

    async def _log(client: Client, user_id: int, method: str, count: int = 1):
        try:
            me = await client.get_me()
            await send_main_log(
                client,
                f"<b>🔗 ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
                f"<b>• ʙᴏᴛ:</b> @{me.username}\n"
                f"<b>• ʙᴏᴛ ɪᴅ:</b> <code>{me.id}</code>\n"
                f"<b>• ᴜsᴇʀ:</b> <code>{user_id}</code>\n"
                f"<b>• ꜰɪʟᴇs:</b> <code>{count}</code>\n"
                f"<b>• ᴍᴇᴛʜᴏᴅ:</b> <code>{method}</code>",
            )
        except Exception as e:
            log.error(f"link_gen log failed: {e}")

    # ── input catcher (only acts while a command flow is waiting) ────────────
    @app.on_message(filters.private & ~filters.command(_COMMANDS), group=2)
    async def linkgen_input_catcher(client: Client, message: Message):
        if not message.from_user:
            return
        uid = message.from_user.id
        fut = _waiting.get(uid)
        if fut is not None and not fut.done():
            fut.set_result(message)
            message.stop_propagation()

    # ── /genlink ─────────────────────────────────────────────────────────────
    @app.on_message(filters.command("genlink") & filters.private)
    async def genlink_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        if not await is_admin_func(user_id):
            return await message.reply(_NO_ACCESS)

        rcv, msg_id = await ask_for_post(
            client,
            message,
            "<b>📎 ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ᴏʀ sᴇɴᴅ ᴛʜᴇ ꜰɪʟᴇ.\n"
            "(/cancel ᴛᴏ ᴀʙᴏʀᴛ)</b>",
        )
        if not msg_id:
            return

        encoded = await encode(f"get-{msg_id * abs(log_channel_id)}")
        link = await process_link(client, encoded, user_id)
        await rcv.reply(
            f"<b>✅ ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʟɪɴᴋ:</b>\n\n<code>{link}</code>",
            quote=True,
            disable_web_page_preview=True,
        )
        await _log(client, user_id, "/genlink", 1)

    # ── /batch ───────────────────────────────────────────────────────────────
    @app.on_message(filters.command("batch") & filters.private)
    async def batch_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        if not await is_admin_func(user_id):
            return await message.reply(_NO_ACCESS)

        _, first_id = await ask_for_post(
            client,
            message,
            "<b>📎 ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ ꜰɪʀsᴛ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ (ᴏʀ sᴇɴᴅ ɪᴛs ʟɪɴᴋ).\n"
            "(/cancel ᴛᴏ ᴀʙᴏʀᴛ)</b>",
        )
        if not first_id:
            return

        rcv, last_id = await ask_for_post(
            client,
            message,
            "<b>📎 ɴᴏᴡ ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ ʟᴀsᴛ ᴍᴇssᴀɢᴇ ꜰʀᴏᴍ ᴛʜᴇ ʟᴏɢ ᴄʜᴀɴɴᴇʟ (ᴏʀ sᴇɴᴅ ɪᴛs ʟɪɴᴋ).</b>",
        )
        if not last_id:
            return

        ch = abs(log_channel_id)
        encoded = await encode(f"get-{first_id * ch}-{last_id * ch}")
        link = await process_link(client, encoded, user_id)
        total = abs(last_id - first_id) + 1
        await rcv.reply(
            f"<b>✅ ʜᴇʀᴇ ɪs ʏᴏᴜʀ ʟɪɴᴋ ({total} ᴍᴇssᴀɢᴇs):</b>\n\n<code>{link}</code>",
            quote=True,
            disable_web_page_preview=True,
        )
        await _log(client, user_id, "/batch", total)

    # ── /custom_batch ────────────────────────────────────────────────────────
    @app.on_message(filters.command("custom_batch") & filters.private)
    async def custom_batch_cmd(client: Client, message: Message):
        user_id = message.from_user.id
        if not await is_admin_func(user_id):
            return await message.reply(_NO_ACCESS)

        await message.reply(
            "<b>📂 sᴇɴᴅ ᴀʟʟ ᴍᴇssᴀɢᴇs / ꜰɪʟᴇs ᴏɴᴇ ʙʏ ᴏɴᴇ.\n"
            "sᴇɴᴅ /done ᴡʜᴇɴ ꜰɪɴɪsʜᴇᴅ, ᴏʀ /cancel ᴛᴏ ᴀʙᴏʀᴛ.</b>"
        )

        ids: list[int] = []
        while True:
            rcv = await wait_for_input(user_id)
            if rcv is None or _is_cmd(rcv, "cancel"):
                return await message.reply(_CANCELLED)
            if _is_cmd(rcv, "done"):
                if not ids:
                    await message.reply("<b>❌ ɴᴏ ꜰɪʟᴇs ᴀᴅᴅᴇᴅ ʏᴇᴛ. sᴇɴᴅ ᴀᴛ ʟᴇᴀsᴛ ᴏɴᴇ.</b>")
                    continue
                break

            msg_id = await get_message_id(client, rcv, log_channel_id)
            if msg_id:
                ids.append(msg_id)
                await rcv.reply(f"<b>✅ ᴀᴅᴅᴇᴅ ({len(ids)})</b>", quote=True)
            else:
                await rcv.reply(_INVALID, quote=True)

        # Group sorted unique ids into contiguous runs -> one link per run
        ids = sorted(set(ids))
        runs: list[tuple[int, int]] = []
        start = prev = ids[0]
        for i in ids[1:]:
            if i == prev + 1:
                prev = i
                continue
            runs.append((start, prev))
            start = prev = i
        runs.append((start, prev))

        ch = abs(log_channel_id)
        lines = []
        for n, (a, b) in enumerate(runs, start=1):
            if a == b:
                encoded = await encode(f"get-{a * ch}")
                count = 1
            else:
                encoded = await encode(f"get-{a * ch}-{b * ch}")
                count = b - a + 1
            link = await process_link(client, encoded, user_id)
            lines.append(f"{n}. ({count} ꜰɪʟᴇs) <code>{link}</code>")

        text = "<b>✅ ʜᴇʀᴇ ᴀʀᴇ ʏᴏᴜʀ ʟɪɴᴋs:</b>\n\n" + "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        await message.reply(text, disable_web_page_preview=True)
        await _log(client, user_id, "/custom_batch", len(ids))


# @suhanibots
