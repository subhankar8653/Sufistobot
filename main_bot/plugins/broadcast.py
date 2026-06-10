from pyrogram import Client, filters
from pyrogram.types import Message
import asyncio
from config import OWNER_ID, LOGGER
from database.main_db import MainDB
#@suhanibots
log = LOGGER(__name__)
main_db = MainDB()
#@suhanibots
@Client.on_message(filters.command("broadcast") & filters.private & filters.user(OWNER_ID))
async def main_bot_broadcast(client: Client, message: Message):
    if not message.reply_to_message:
        await message.reply("<b>❌ Please reply to a message to broadcast.</b>")
        return

    b_msg = await message.reply("<b>⏳ Preparing broadcast...</b>")
    users = await main_db.get_all_users()
    
    if not users:
        await b_msg.edit("<b>❌ No users found in database.</b>")
        return

    await b_msg.edit(f"<b>⏳ Broadcasting to {len(users)} users...</b>")
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            await message.reply_to_message.copy(user_id)
            success += 1
            await asyncio.sleep(0.1) # Small delay to avoid flooding
        except Exception as e:
            failed += 1
            log.error(f"Broadcast failed for user {user_id}: {e}")
            
    await b_msg.edit(
        f"<b>✅ Broadcast Completed</b>\n\n"
        f"<b>Total Users:</b> {len(users)}\n"
        f"<b>Success:</b> {success}\n"
        f"<b>Failed:</b> {failed}"
    )
#@suhanibots