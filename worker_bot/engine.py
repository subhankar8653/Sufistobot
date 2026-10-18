#@suhanibots

import asyncio
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatJoinRequest,
)
#@suhanibots
from config import API_ID, API_HASH, LOGGER, OWNER_ID
from database.main_db import MainDB
from database.worker_db import WorkerDB
from utils.security import decrypt_token
from utils.helpers import encode, decode, get_messages, get_message_id, get_exp_time
#@suhanibotss
log = LOGGER(__name__)
main_db = MainDB()

#@suhanibots
class WorkerEngine:
    """
    Manages multiple Pyrogram bot clients concurrently.

    Each user-created bot runs as its own Pyrogram Client instance
    with dynamically registered handlers.
    """

    def __init__(self):
        self.workers: dict[int, Client] = {}  # bot_id -> Client
        self._lock = asyncio.Lock()

    async def start_all_workers(self):
        """Start all active bots from the database."""
        bots = await main_db.get_all_active_bots()
        log.info(f"Starting {len(bots)} worker bots...")

        # Start workers in parallel batches for speed
        BATCH_SIZE = 50
        for i in range(0, len(bots), BATCH_SIZE):
            batch = bots[i:i + BATCH_SIZE]
            tasks = []
            for bot_doc in batch:
                tasks.append(self._safe_start_worker(bot_doc))
            await asyncio.gather(*tasks)
            if i + BATCH_SIZE < len(bots):
                log.info(f"  Started {min(i + BATCH_SIZE, len(bots))}/{len(bots)} bots...")

        log.info(f"Worker engine: {len(self.workers)} bots running")

    async def _safe_start_worker(self, bot_doc: dict):
        """Start a worker with error handling (used in parallel batches)."""
        try:
            await self.start_worker(bot_doc)
        except Exception as e:
            bot_id = bot_doc.get("_id", "?")
            log.error(f"Failed to start worker bot {bot_id}: {e}")

    async def start_worker(self, bot_doc: dict):
        """Start a single worker bot."""
        bot_id = bot_doc["_id"]

        async with self._lock:
            if bot_id in self.workers:
                log.warning(f"Worker {bot_id} already running, skipping")
                return

        try:
            token = decrypt_token(bot_doc["bot_token_encrypted"])
        except Exception as e:
            log.error(f"Cannot decrypt token for bot {bot_id}: {e}")
            raise RuntimeError(f"Cannot decrypt bot token: {e}")

        log_channel_id = bot_doc["log_channel_id"]
        owner_id = bot_doc["owner_id"]
        bot_username = bot_doc.get("bot_username", "unknown")
        worker_db = WorkerDB(bot_id)

        # Increased worker threads to handle concurrent requests without freezing
        app = Client(
            name=f"worker_{bot_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
            in_memory=True,
            workers=4,
        )

        # Global middleware to update last_active
        async def update_activity_middleware(client, update):
            asyncio.create_task(main_db.update_last_active(bot_id))
            raise ContinuePropagation

        app.add_handler(MessageHandler(update_activity_middleware), group=-1)
        app.add_handler(CallbackQueryHandler(update_activity_middleware), group=-1)

        from worker_bot.link_gen import setup_link_gen

        # We need a small wrapper to pass `is_admin` to flink logic properly
        # since it's defined lower down, but we can just define a helper.
        async def flink_is_admin(uid: int) -> bool:
            return uid == owner_id or uid == OWNER_ID or await worker_db.admin_exist(uid)

        setup_link_gen(app, log_channel_id, flink_is_admin)

        try:
            from worker_bot.flink_logic import setup_flink
            setup_flink(app, worker_db, log_channel_id, flink_is_admin)
        except Exception as _flink_err:
            log.warning(f"flink_logic load failed (flink disabled): {_flink_err}")

        # =====================================================================
        # REGISTER HANDLERS — Each handler is a closure that captures bot_doc
        # =====================================================================

        # ----- Helper: Check if user is admin -----
        async def is_admin(user_id: int) -> bool:
            return user_id == owner_id or user_id == OWNER_ID or await worker_db.admin_exist(user_id)

        # ----- Advanced Force-Subscribe (Link-sharing-bot style) -----
        # Handles: request/normal modes, custom button names, permanent links,
        # fake links, auto-check (step-by-step), check duration, stats, tutorial.
        from worker_bot.fsub_logic import setup_fsub
        fsub = setup_fsub(app, worker_db, bot_id, owner_id, is_admin)

        # =====================================================================
        # HANDLER: /start
        # =====================================================================

        @app.on_message(filters.command("start") & filters.private)
        async def worker_start(client: Client, message: Message):
            user_id = message.from_user.id
            current_bot_doc = await main_db.get_bot(bot_id) or bot_doc

            # Track user
            if not await worker_db.present_user(user_id):
                await worker_db.add_user(user_id)
                
                if log_channel_id:
                    log_text = (
                        f"<b>#NewUser</b>\n\n"
                        f"<b>Iᴅ</b> - <code>{user_id}</code>\n"
                        f"<b>Nᴀᴍᴇ</b> - {message.from_user.first_name}\n"
                        f"<b>username</b> - @{message.from_user.username or 'N/A'}"
                    )
                    try:
                        await client.send_message(chat_id=log_channel_id, text=log_text)
                    except Exception as e:
                        log.error(f"Failed to send #NewUser log to {log_channel_id}: {e}")

            # Check ban
            banned = await worker_db.get_ban_users()
            if user_id in banned:
                await message.reply(
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "⛔ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘𝗗\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    "<blockquote>ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ꜰʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.</blockquote>"
                )
                return

            # Check force-sub (shows the fsub screen itself when needed)
            if not await fsub.gate(client, message, worker_start):
                return

            # Handle /start verify — mark user as verified via shortener
            text = message.text
            if len(message.command) > 1 and message.command[1] == "verify":
                await worker_db.set_verified(user_id)
                await message.reply(
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    "<blockquote>ʏᴏᴜ ᴀʀᴇ ɴᴏᴡ ᴠᴇʀɪꜰɪᴇᴅ!\n"
                    "ʏᴏᴜ ᴄᴀɴ ɴᴏᴡ ᴀᴄᴄᴇss ꜰɪʟᴇs.\n\n"
                    "ᴛᴀᴘ ʏᴏᴜʀ ᴏʀɪɢɪɴᴀʟ ʟɪɴᴋ ᴀɢᴀɪɴ.</blockquote>"
                )
                return

            # Check for deep link (file retrieval)
            if len(text) > 7:
                try:
                    base64_string = text.split(" ", 1)[1]
                except IndexError:
                    return

                string = await decode(base64_string)
                argument = string.split("-")

                ids = []
                if len(argument) == 3:
                    try:
                        start = int(int(argument[1]) / abs(log_channel_id))
                        end = int(int(argument[2]) / abs(log_channel_id))
                        ids = list(range(start, end + 1)) if start <= end else list(range(start, end - 1, -1))
                    except Exception as e:
                        log.error(f"Error decoding IDs: {e}")
                        return
                elif len(argument) == 2:
                    try:
                        ids = [int(int(argument[1]) / abs(log_channel_id))]
                    except Exception as e:
                        log.error(f"Error decoding ID: {e}")
                        return

                if not ids:
                    return

                # ---- SHORTENER VERIFICATION GATE ----
                shortener_cfg = current_bot_doc.get("shortener", {})
                if shortener_cfg.get("enabled") and shortener_cfg.get("domain") and shortener_cfg.get("api_key_encrypted"):
                    expire_secs = shortener_cfg.get("verify_expire", 86400)
                    is_admin_user = await is_admin(user_id)

                    if not is_admin_user and not await worker_db.is_verified(user_id, expire_secs):
                        # Build verify URL — shorten the bot's start link so user must visit shortener
                        me = await client.get_me()
                        verify_url = f"https://t.me/{me.username}?start=verify"
                        from utils.shortener import shorten_url
                        shortened = await shorten_url(
                            verify_url,
                            shortener_cfg["api_key_encrypted"],
                            shortener_cfg["domain"],
                        )

                        expire_hrs = expire_secs // 3600
                        buttons = [
                            [InlineKeyboardButton(
                                "🔗 ᴠᴇʀɪꜰʏ", url=shortened
                            )],
                        ]

                        # Tutorial button if enabled
                        tut_link = shortener_cfg.get("tutorial_link", "")
                        tut_enabled = shortener_cfg.get("tutorial_enabled", False)
                        if tut_enabled and tut_link:
                            buttons.append([
                                InlineKeyboardButton("📹 ᴛᴜᴛᴏʀɪᴀʟ", url=tut_link)
                            ])

                        # Try Again button to re-check after visiting
                        buttons.append([
                            InlineKeyboardButton(
                                "✅ ɪ ʜᴀᴠᴇ ᴠᴇʀɪꜰɪᴇᴅ",
                                url=f"https://t.me/{me.username}?start={base64_string}",
                            )
                        ])

                        await message.reply(
                            "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                            "🔗 𝗩𝗘𝗥𝗜𝗙𝗜𝗖𝗔𝗧𝗜𝗢𝗡 𝗥𝗘𝗤𝗨𝗜𝗥𝗘𝗗\n"
                            "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                            f"<blockquote>ʜᴇʏ {message.from_user.mention},\n\n"
                            f"ᴛᴀᴘ <b>🔗 ᴠᴇʀɪꜰʏ</b> ᴀɴᴅ ᴄᴏᴍᴘʟᴇᴛᴇ ᴛʜᴇ\n"
                            f"sʜᴏʀᴛ ʟɪɴᴋ ᴛᴏ ᴜɴʟᴏᴄᴋ ꜰɪʟᴇs.\n\n"
                            f"ᴀꜰᴛᴇʀ ᴠᴇʀɪꜰʏɪɴɢ, ᴛᴀᴘ\n"
                            f"<b>✅ ɪ ʜᴀᴠᴇ ᴠᴇʀɪꜰɪᴇᴅ</b>.\n\n"
                            f"◈ ᴠᴀʟɪᴅ ꜰᴏʀ: <b>{expire_hrs}h</b></blockquote>",
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        return

                # Only show loading for large batches (>5 files)
                temp_msg = None
                if len(ids) > 5:
                    temp_msg = await message.reply(
                        "<b>⏳ ʟᴏᴀᴅɪɴɢ ʏᴏᴜʀ ꜰɪʟᴇs...</b>"
                    )
                try:
                    messages = await get_messages(client, log_channel_id, ids)
                except Exception as e:
                    await message.reply(
                        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                        "❌ 𝗘𝗥𝗥𝗢𝗥\n"
                        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                        "<blockquote>sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ ᴡʜɪʟᴇ ꜰᴇᴛᴄʜɪɴɢ ʏᴏᴜʀ ꜰɪʟᴇs.</blockquote>"
                    )
                    log.error(f"Error getting messages: {e}")
                    return
                finally:
                    if temp_msg:
                        try:
                            await temp_msg.delete()
                        except Exception:
                            pass

                settings = current_bot_doc.get("settings", {})
                protect_content = settings.get("protect_content", False)
                custom_caption = settings.get("custom_caption", "")

                from utils.caption_logic import get_file_details, format_caption

                sent_msgs = []
                for msg in messages:
                    if msg.empty:
                        continue
                        
                    # Apply custom caption formatting
                    caption_text = ""
                    original_caption = msg.caption.html if msg.caption else ""
                    if custom_caption and getattr(msg, "media", None):
                        file_details = get_file_details(msg)
                        formatted_custom = format_caption(custom_caption, file_details)
                        if original_caption:
                            caption_text = f"{original_caption}\n\n{formatted_custom}"
                        else:
                            caption_text = formatted_custom
                    else:
                        caption_text = original_caption
                        
                    try:
                        copied = await msg.copy(
                            chat_id=user_id,
                            caption=caption_text if caption_text else None,
                            parse_mode=ParseMode.HTML,
                            protect_content=protect_content,
                        )
                        sent_msgs.append(copied)
                        await asyncio.sleep(0)
                    except Exception as e:
                        log.error(f"Failed to copy message: {e}")

                # Auto-delete
                del_timer = await worker_db.get_del_timer()
                if del_timer > 0 and sent_msgs:
                    notification = await message.reply(
                        f"<b>⏱ These files will be auto-deleted in {get_exp_time(del_timer)}.\n"
                        f"Save or forward them before deletion!</b>"
                    )

                    reload_url = f"https://t.me/{(await client.get_me()).username}?start={message.command[1]}" if len(message.command) > 1 else None

                    asyncio.create_task(
                        _schedule_delete(client, sent_msgs, notification, del_timer, reload_url)
                    )

            else:
                # Normal /start - welcome message
                settings = current_bot_doc.get("settings", {})
                start_pic = settings.get("start_pic", "")
                start_message = settings.get("start_message", "")

                if not start_message:
                    start_message = (
                        f"<blockquote>ᴡᴇʟᴄᴏᴍᴇ {message.from_user.mention}!\n\n"
                        f"ɪ ᴄᴀɴ sᴛᴏʀᴇ ꜰɪʟᴇs ᴀɴᴅ sʜᴀʀᴇ ᴛʜᴇᴍ\n"
                        f"ᴠɪᴀ sᴘᴇᴄɪᴀʟ ʟɪɴᴋs.\n\n"
                        f"ᴘᴏᴡᴇʀᴇᴅ ʙʏ @suhanibots</blockquote>"
                    )
                else:
                    try:
                        me = await client.get_me()
                        start_message = start_message.format(
                            mention=message.from_user.mention,
                            first=message.from_user.first_name,
                            last=message.from_user.last_name or "",
                            id=user_id,
                            bot_mention=f"@{me.username}",
                            username=message.from_user.username or "",
                        )
                    except (KeyError, IndexError, ValueError):
                        # If custom message has unknown placeholders, just send it raw
                        pass

                if start_pic and start_pic.lower() not in ["none", "ɴᴏɴᴇ", "0"]:
                    await message.reply_photo(photo=start_pic, caption=start_message)
                else:
                    await message.reply(start_message)

        # =====================================================================
        # HANDLER: /ban & /unban (admin only)
        # =====================================================================

        @app.on_message(filters.command("ban") & filters.private)
        async def handle_ban(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            if len(message.command) < 2:
                await message.reply("<b>Usage:</b> <code>/ban [user_id]</code>")
                return
            try:
                target_id = int(message.command[1])
                await worker_db.add_ban_user(target_id)
                await message.reply(f"<b>✅ User {target_id} has been banned from this bot!</b>")
            except ValueError:
                await message.reply("<b>❌ Invalid User ID.</b>")

        @app.on_message(filters.command("unban") & filters.private)
        async def handle_unban(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            if len(message.command) < 2:
                await message.reply("<b>Usage:</b> <code>/unban [user_id]</code>")
                return
            try:
                target_id = int(message.command[1])
                await worker_db.del_ban_user(target_id)
                await message.reply(f"<b>✅ User {target_id} has been unbanned from this bot!</b>")
            except ValueError:
                await message.reply("<b>❌ Invalid User ID.</b>")

        # =====================================================================
        # HANDLER: /broadcast (admin only)
        # =====================================================================

        @app.on_message(filters.command("broadcast") & filters.private)
        async def handle_worker_broadcast(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            if not message.reply_to_message:
                await message.reply("<b>❌ Please reply to a message to broadcast.</b>")
                return

            b_msg = await message.reply("<b>⏳ Preparing broadcast...</b>")
            users = await worker_db.full_userbase()
            
            if not users:
                await b_msg.edit("<b>❌ No users found in database.</b>")
                return

            await b_msg.edit(f"<b>⏳ Broadcasting to {len(users)} users...</b>")
            
            success = 0
            failed = 0
            
            for uid in users:
                try:
                    await message.reply_to_message.copy(uid)
                    success += 1
                    await asyncio.sleep(0.1) # Small delay
                except Exception as e:
                    failed += 1
                    log.error(f"Worker broadcast failed for user {uid}: {e}")
                    
            await b_msg.edit(
                f"<b>✅ Broadcast Completed</b>\n\n"
                f"<b>Total Users:</b> {len(users)}\n"
                f"<b>Success:</b> {success}\n"
                f"<b>Failed:</b> {failed}"
            )

        # =====================================================================
        # START THE CLIENT
        # =====================================================================

        try:
            await app.start()
            app.set_parse_mode(ParseMode.HTML)

            # Set bot commands in the Telegram menu
            from pyrogram.types import BotCommand
            await app.set_bot_commands([
                BotCommand("start", "Start the bot / Retrieve files"),
                BotCommand("genlink", "Generate link for a single post"),
                BotCommand("batch", "Generate link for multiple posts"),
                BotCommand("custom_batch", "Generate link for custom posts"),
                BotCommand("flink", "Formatted links generator"),
                BotCommand("broadcast", "Broadcast a message (Admin)"),
                BotCommand("auto_check", "Fsub step-by-step mode on/off (Admin)"),
                BotCommand("setfakelink", "Add a fake fsub button (Admin)"),
                BotCommand("listfakelink", "List fake fsub buttons (Admin)"),
                BotCommand("deletefakelink", "Delete a fake fsub button (Admin)"),
                BotCommand("set_tutorial_fsub", "Set fsub tutorial video (Admin)"),
                BotCommand("fsub_tutorial", "Fsub tutorial on/off (Admin)"),
            ])

            me = await app.get_me()
            log.info(f"Worker started: @{me.username} (ID: {bot_id})")

            async with self._lock:
                self.workers[bot_id] = app

        except Exception as e:
            log.error(f"Failed to start worker {bot_id}: {e}")
            raise

    async def stop_worker(self, bot_id: int):
        """Stop a single worker bot."""
        async with self._lock:
            app = self.workers.pop(bot_id, None)

        if app:
            try:
                # Prevent app.stop() from hanging forever if there's a connection issue
                await asyncio.wait_for(app.stop(block=False), timeout=3.0)
                log.info(f"Worker stopped: {bot_id}")
            except Exception as e:
                log.error(f"Error stopping worker {bot_id}: {e}")

    async def stop_all_workers(self):
        """Stop all running worker bots."""
        async with self._lock:
            bot_ids = list(self.workers.keys())

        for bot_id in bot_ids:
            await self.stop_worker(bot_id)

        log.info("All workers stopped")

    def get_worker(self, bot_id: int) -> Client | None:
        """Get a running worker client by bot_id."""
        return self.workers.get(bot_id)

    @property
    def active_count(self) -> int:
        """Number of currently running workers."""
        return len(self.workers)

#@suhanibots
# =============================================================================
# AUTO-DELETE HELPER
# =============================================================================
#@suhanibots
async def _schedule_delete(
    client: Client,
    messages: list,
    notification: Message,
    delay: int,
    reload_url: str | None,
):
    """Schedule auto-deletion of messages after a delay."""
    await asyncio.sleep(delay)

    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            log.error(f"Error deleting message {msg.id}: {e}")

    try:
        keyboard = None
        if reload_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Get File Again", url=reload_url)]
            ])

        await notification.edit(
            "<b>🗑 Your files have been auto-deleted.</b>\n\n"
            "<blockquote>Click below to retrieve them again.</blockquote>",
            reply_markup=keyboard,
        )
    except Exception as e:
        log.error(f"Error updating deletion notification: {e}")


# =============================================================================
# GLOBAL WORKER ENGINE SINGLETON
# =============================================================================

worker_engine = WorkerEngine()
#@suhanibots