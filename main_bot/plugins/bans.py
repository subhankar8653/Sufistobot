from pyrogram import Client, filters
from pyrogram.types import Message
from config import OWNER_ID
from database.main_db import MainDB
#@suhanibots
main_db = MainDB()
#@suhanibots
@Client.on_message(filters.command("ban") & filters.private & filters.user(OWNER_ID))
async def ban_user_main(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("<b>Usage:</b> <code>/ban [user_id]</code>")
        return
    
    try:
        target_id = int(message.command[1])
        await main_db.add_ban_user(target_id)
        await message.reply(f"<b>✅ User {target_id} has been banned from the main bot.</b>")
    except ValueError:
        await message.reply("<b>❌ Invalid User ID.</b>")

@Client.on_message(filters.command("unban") & filters.private & filters.user(OWNER_ID))
async def unban_user_main(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply("<b>Usage:</b> <code>/unban [user_id]</code>")
        return
    
    try:
        target_id = int(message.command[1])
        await main_db.del_ban_user(target_id)
        await message.reply(f"<b>✅ User {target_id} has been unbanned from the main bot.</b>")
    except ValueError:
        await message.reply("<b>❌ Invalid User ID.</b>")
#@suhanibots