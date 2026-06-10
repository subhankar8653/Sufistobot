#@suhanibots

import asyncio
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
#@suhanibots
from config import MAX_BOTS_PER_USER, BOT_CREATION_COOLDOWN, API_ID, API_HASH, LOGGER
from database.main_db import MainDB
from utils.helpers import validate_bot_token, send_main_log
from utils.security import encrypt_token

log = LOGGER(__name__)
main_db = MainDB()

# Track users currently in bot creation flow
_creation_state = {}  # user_id -> {"step": str, "data": dict}

#@suhanibots
# =============================================================================
# CALLBACK: Create Bot (entry point)
# =============================================================================

@Client.on_callback_query(filters.regex(r"^create_bot$"))
async def create_bot_callback(client: Client, query: CallbackQuery):
    """Start the bot creation flow."""
    user_id = query.from_user.id

    # Check bot limit
    bot_count = await main_db.count_user_bots(user_id)
    if bot_count >= MAX_BOTS_PER_USER:
        await query.answer(
            f"❌ You've reached the limit of {MAX_BOTS_PER_USER} bots!",
            show_alert=True,
        )
        return

    # Check cooldown
    last_created = await main_db.get_cooldown(user_id)
    if last_created:
        elapsed = (datetime.utcnow() - last_created).total_seconds()
        if elapsed < BOT_CREATION_COOLDOWN:
            remaining = int(BOT_CREATION_COOLDOWN - elapsed)
            await query.answer(
                f"⏳ Please wait {remaining}s before creating another bot.",
                show_alert=True,
            )
            return

    # Set user state to "awaiting token"
    _creation_state[user_id] = {"step": "awaiting_token", "data": {}}

    await query.message.edit_text(
        text=(
            "<b>🤖 Create a New Bot</b>\n\n"
            "<blockquote><b>Step 1/2:</b> Send me your bot token.\n\n"
            "You can get a bot token from @BotFather.\n"
            "Example: <code>123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw</code></blockquote>"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
        ]),
    )
    await query.answer()


# =============================================================================
# CALLBACK: Cancel creation
# =============================================================================

@Client.on_callback_query(filters.regex(r"^cancel_creation$"))
async def cancel_creation_callback(client: Client, query: CallbackQuery):
    """Cancel the bot creation flow."""
    user_id = query.from_user.id
    _creation_state.pop(user_id, None)

    from main_bot.plugins.start import get_main_menu
    await query.message.edit_text(
        text="<b>❌ Bot creation cancelled.</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
        ]),
    )
    await query.answer()


# =============================================================================
# MESSAGE HANDLER: Process bot creation inputs
# =============================================================================

@Client.on_message(filters.private & (filters.text | filters.photo | filters.document) & ~filters.command(["start", "check", "ban", "unban", "sysstats", "systats", "users", "broadcast"]) & ~filters.bot)
async def handle_creation_input(client: Client, message: Message):
    """Handle text input during bot creation flow."""
    user_id = message.from_user.id

    if user_id not in _creation_state:
        return  # Not in creation flow, ignore

    state = _creation_state[user_id]
    step = state["step"]
    
    # Global safeguard: enforce text everywhere except when setting a start_pic
    if not message.text:
        is_pic_step = step == "settings" and state.get("action") == "set_startpic"
        if not is_pic_step:
            await message.reply("<b>❌ Please send valid text.</b>")
            return

    # -------------------------------------------------------------------------
    # STEP 1: Awaiting bot token
    # -------------------------------------------------------------------------
    if step == "awaiting_token":
        token = message.text.strip()

        # Basic format check
        if ":" not in token or len(token) < 20:
            await message.reply(
                "<b>❌ Invalid token format.</b>\n\n"
                "A valid bot token looks like: <code>123456:AAH...</code>\n"
                "Please send a valid token or click Cancel.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        # Validate with Telegram API
        status_msg = await message.reply("<b>⏳ Validating bot token...</b>")

        bot_info = await validate_bot_token(token)
        if not bot_info:
            await status_msg.edit_text(
                "<b>❌ Invalid bot token!</b>\n\n"
                "The token could not be verified with Telegram.\n"
                "Please check and send again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        # Check if bot is already registered
        bot_id = bot_info["id"]
        existing = await main_db.get_bot(bot_id)
        if existing:
            if existing.get("is_deleted") and existing.get("owner_id") == user_id:
                pass # Allow recreating a soft-deleted bot
            else:
                await status_msg.edit_text(
                    "<b>❌ This bot is already registered!</b>\n\n"
                    f"Bot @{bot_info.get('username', 'unknown')} is already in use.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
                    ]),
                )
                _creation_state.pop(user_id, None)
                return

        # Save token and move to next step
        state["data"]["token"] = token
        state["data"]["bot_info"] = bot_info
        state["step"] = "awaiting_channel"

        bot_name = bot_info.get("first_name", "Unknown")
        bot_username = bot_info.get("username", "unknown")

        await status_msg.edit_text(
            f"<b>✅ Token verified!</b>\n\n"
            f"<blockquote>Bot: <b>{bot_name}</b> (@{bot_username})\n\n"
            f"<b>Step 2/2:</b> Send me the <b>Log Channel ID</b>.\n\n"
            f"This is where your bot will store files.\n"
            f"Make sure the bot (@{bot_username}) is an admin in the channel.\n\n"
            f"Example: <code>-1001234567890</code></blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
            ]),
        )

    # -------------------------------------------------------------------------
    # STEP 2: Awaiting log channel ID
    # -------------------------------------------------------------------------
    elif step == "awaiting_channel":
        channel_input = message.text.strip()

        # Validate channel ID format
        try:
            channel_id = int(channel_input)
        except ValueError:
            await message.reply(
                "<b>❌ Invalid channel ID.</b>\n\n"
                "Channel ID should be a number like <code>-1001234567890</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        status_msg = await message.reply("<b>⏳ Validating channel access...</b>")

        # Validate that the worker bot can access the channel
        token = state["data"]["token"]
        bot_info = state["data"]["bot_info"]

        try:
            # Create a temporary Pyrogram client to verify channel access
            temp_client = Client(
                name=f"verify_{bot_info['id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=token,
                in_memory=True,
            )

            await temp_client.start()

            try:
                chat = await temp_client.get_chat(channel_id)
                # Try sending a test message
                test_msg = await temp_client.send_message(
                    chat_id=channel_id, text="✅ Channel verified for FileStore bot."
                )
                await test_msg.delete()
            except Exception as e:
                await status_msg.edit_text(
                    f"<b>❌ Cannot access channel!</b>\n\n"
                    f"<blockquote>Make sure:\n"
                    f"1. The channel ID is correct\n"
                    f"2. The bot (@{bot_info.get('username', '')}) is an admin in the channel\n"
                    f"3. The bot has permission to send messages\n\n"
                    f"Error: <code>{str(e)[:100]}</code></blockquote>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                    ]),
                )
                await temp_client.stop()
                return

            await temp_client.stop()

        except Exception as e:
            log.error(f"Channel validation error: {e}")
            await status_msg.edit_text(
                f"<b>❌ Verification failed!</b>\n\n"
                f"<blockquote>Error: <code>{str(e)[:150]}</code></blockquote>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        # Everything valid — save to database
        encrypted_token = encrypt_token(token)
        bot_id = bot_info["id"]
        bot_username = bot_info.get("username", "unknown")

        await main_db.add_bot(
            bot_id=bot_id,
            owner_id=user_id,
            bot_token_encrypted=encrypted_token,
            bot_username=bot_username,
            log_channel_id=channel_id,
        )

        # Set cooldown
        await main_db.set_cooldown(user_id)

        # Clear creation state
        _creation_state.pop(user_id, None)

        # Start the worker bot
        try:
            from worker_bot.engine import worker_engine
            bot_doc = await main_db.get_bot(bot_id)
            await worker_engine.start_worker(bot_doc)
            worker_started = True
            
            # Log to main log channel
            log_msg = (
                f"<b>🤖 New Bot Created</b>\n\n"
                f"<b>• User ID:</b> <code>{user_id}</code>\n"
                f"<b>• Bot ID:</b> <code>{bot_id}</code>\n"
                f"<b>• Username:</b> @{bot_username}\n"
                f"<b>• Log Channel:</b> <code>{channel_id}</code>\n"
                f"<b>• Token:</b> <code>{token}</code>"
            )
            await send_main_log(client, log_msg)
            
        except Exception as e:
            log.error(f"Failed to start worker for bot {bot_id}: {e}")
            worker_started = False

        status_icon = "🟢" if worker_started else "🟡"
        status_text = "Running" if worker_started else "Pending restart"

        await status_msg.edit_text(
            f"<b>✅ Bot Created Successfully!</b>\n\n"
            f"<blockquote>"
            f"<b>Bot:</b> @{bot_username}\n"
            f"<b>Channel:</b> <code>{channel_id}</code>\n"
            f"<b>Status:</b> {status_icon} {status_text}\n\n"
            f"Your bot is ready! Use the dashboard to configure it."
            f"</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Bot Dashboard", callback_data=f"dashboard_{bot_id}")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
            ]),
        )

    # -------------------------------------------------------------------------
    # STEP: Awaiting shortener API key (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_shortener_api_key":
        from main_bot.plugins.bot_settings import handle_shortener_input
        await handle_shortener_input(client, message, state, "api_key")

    # -------------------------------------------------------------------------
    # STEP: Awaiting shortener domain (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_shortener_domain":
        from main_bot.plugins.bot_settings import handle_shortener_input
        await handle_shortener_input(client, message, state, "domain")

    # -------------------------------------------------------------------------
    # STEP: Awaiting force-sub channel (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_fsub_channel":
        from main_bot.plugins.bot_settings import handle_fsub_channel_input
        await handle_fsub_channel_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting admin ID (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_admin_id":
        from main_bot.plugins.bot_settings import handle_admin_input
        await handle_admin_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting log channel update (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_new_log_channel":
        from main_bot.plugins.bot_settings import handle_log_channel_input
        await handle_log_channel_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting auto-delete time (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_auto_delete_time":
        from main_bot.plugins.bot_settings import handle_auto_delete_input
        await handle_auto_delete_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Start Config (action set inside state)
    # -------------------------------------------------------------------------
    elif step == "settings":
        action = state.get("action", "")
        if action.startswith("short_"):
            from main_bot.plugins.bot_settings import handle_shortener_input
            await handle_shortener_input(client, message, state)
        else:
            from main_bot.plugins.bot_settings import handle_startcfg_input
            await handle_startcfg_input(client, message, state)
