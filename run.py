#@suhanibots

import asyncio
import sys
from datetime import datetime, timezone, timedelta
import pyrogram.utils

# Set minimum channel ID to support newer channels
pyrogram.utils.MIN_CHANNEL_ID = -1009147483647

# Force UTF-8 encoding for Windows terminals to support emojis
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN, OWNER_ID, MONGO_URI, TG_BOT_WORKERS, HIBERNATION_HOURS, LOGGER

log = LOGGER(__name__)


def validate_config():
    """Validate that all required config values are set."""
    errors = []
    if not API_ID or API_ID == 0:
        errors.append("API_ID is not set")
    if not API_HASH:
        errors.append("API_HASH is not set")
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is not set")
    if not OWNER_ID or OWNER_ID == 0:
        errors.append("OWNER_ID is not set")
    if not MONGO_URI:
        errors.append("MONGO_URI is not set")

    if errors:
        for err in errors:
            log.error(f"Config Error: {err}")
        log.error("Please set all required environment variables or update config.py")
        sys.exit(1)


async def bot_hibernation_task(main_bot, worker_engine):
    """Background task to hibernate bots inactive for >48 hours to save RAM."""
    from database.main_db import MainDB
    main_db = MainDB()
    
    while True:
        try:
            await asyncio.sleep(3600)  # Check every hour
            log.info("Running bot hibernation sweep...")
            now = datetime.now(timezone.utc)
            
            active_bots = await main_db.get_all_active_bots()
            
            for bot in active_bots:
                last_active = bot.get("last_active", bot.get("created_at", now))
                
                # Make timezone aware if needed
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                    
                if now - last_active > timedelta(hours=HIBERNATION_HOURS):
                    bot_id = bot["_id"]
                    log.info(f"Hibernating idle bot ID: {bot_id}")
                    
                    # Stop the actual process to free RAM
                    await worker_engine.stop_worker(bot_id)
                    # Mark as offline in DB
                    await main_db.set_bot_active(bot_id, False)
                    
                    # Notify owner individually
                    try:
                        owner_id = bot["owner_id"]
                        await main_bot.send_message(
                            owner_id,
                            f"<b>💤 Bot Hibernated</b>\n\n"
                            f"<blockquote>Your bot @{bot.get('bot_username', 'unknown')} has been automatically turned off to save server resources due to 48 hours of inactivity.\n\n"
                            f"You can wake it up anytime by pressing <b>🟢 Start Bot</b> in the <b>My Bots</b> dashboard!</blockquote>"
                        )
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"Hibernation task error: {e}")


async def web_server():
    """Dummy web server to satisfy Render and Koyeb health checks."""
    try:
        from aiohttp import web
        from config import PORT
        
        async def handle(request):
            return web.Response(text="Bot is running!")
            
        app = web.Application()
        app.router.add_get('/', handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        log.info(f"Web server started on port {PORT} for health checks.")
    except Exception as e:
        log.error(f"Failed to start web server: {e}")

async def main():
    """Main async entry point."""
    log.info("=" * 50)
    log.info("Multi-User FileStore Bot System")
    log.info("=" * 50)

    # Start the web server for Render/Koyeb health checks
    asyncio.create_task(web_server())

    # Validate configuration
    validate_config()

    # Test MongoDB connection
    from database.mongo import get_motor_client
    try:
        motor_client = get_motor_client()
        await motor_client.admin.command("ping")
        log.info("✅ MongoDB connection successful")
    except Exception as e:
        log.error(f"❌ MongoDB connection failed: {e}")
        sys.exit(1)

    # Create and start the main controller bot
    main_bot = None
    try:
        log.info("Starting main controller bot...")
        main_bot = Client(
            name="MainBot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=TG_BOT_WORKERS,
            plugins={"root": "main_bot/plugins"},
        )

        await main_bot.start()

        import utils.helpers
        utils.helpers.main_bot_client = main_bot

        # Set main bot commands
        from pyrogram.types import BotCommand
        await main_bot.set_bot_commands([
            BotCommand("start", "Start the controller / Main menu"),
        ])

        me = await main_bot.get_me()
        log.info(f"Main bot started: @{me.username}")

    except Exception as e:
        log.error(f"Main bot failed to start: {e}")
        log.warning("Workers will still start without the main bot!")
        main_bot = None

    # Stealth integrity check to protect developer credits
    try:
        from main_bot.plugins.start import get_main_menu
        if False:  # integrity check disabled
            sys.exit("Core Integrity Check Failed: Developer credits modified.")
    except SystemExit as e:
        log.error(f"Fatal Error: {e}")
        sys.exit(1)
    except Exception:
        pass

    # Start all worker bots (independent of main bot)
    from worker_bot.engine import worker_engine
    log.info("Starting worker engine...")
    await worker_engine.start_all_workers()

    active = worker_engine.active_count
    log.info(f"Worker engine ready: {active} bots running")
#@suhanibots
    # Notify owner if main bot is alive
    if main_bot:
        try:
            me = await main_bot.get_me()
            await main_bot.send_message(
                OWNER_ID,
                f"<b>All Systems Online!</b>\n\n"
                f"<blockquote>"
                f"Main Bot: @{me.username}\n"
                f"Active Workers: {active}\n"
                f"</blockquote>",
            )
        except Exception:
            pass
#@suhanibots
    log.info("Bot system is fully operational. Press Ctrl+C to stop.")

    # Start hibernation sweep (only if main bot is alive for notifications)
    if main_bot:
        asyncio.create_task(bot_hibernation_task(main_bot, worker_engine))

    # Keep running
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")

    # Cleanup
    await worker_engine.stop_all_workers()
    if main_bot:
        await main_bot.stop()
    log.info("Bot system stopped.")
#@suhanibots

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
#@suhanibots