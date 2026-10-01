import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNER_ID
from utils import community

USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")

# NOTE: Is file ke messages me purana username/brand mat likhna,
# kyunki har outgoing message me replacement lagta hai.


@Client.on_message(filters.command("community") & filters.private & filters.user(OWNER_ID))
async def community_cmd(client: Client, message: Message):
    args = message.command[1:]
    cur = community.current()

    if not args:
        status = f"@{cur['username']}" if cur["username"] else "Not set"
        await message.reply(
            "<b>📢 Community Branding</b>\n\n"
            f"<b>Current:</b> <code>{status}</code>\n\n"
            "<b>Usage:</b>\n"
            "<code>/community @newusername</code>\n"
            "<code>/community @newusername MY BRAND NAME</code>\n"
            "<code>/community reset</code>"
        )
        return

    if args[0].lower() in ("reset", "off", "clear"):
        await community.reset_community()
        await message.reply("<b>✅ Community branding reset ho gayi.</b>")
        return

    m = USERNAME_RE.match(args[0])
    if not m:
        await message.reply("<b>❌ Invalid username.</b> Example: <code>/community @sbbots</code>")
        return

    username = m.group(1)
    brand = " ".join(args[1:]).strip() or None
    state = await community.set_community(username, brand)

    await message.reply(
        "<b>✅ Community update ho gaya!</b>\n\n"
        f"<b>Username:</b> @{state['username']}\n"
        f"<b>Link:</b> https://t.me/{state['username']}\n"
        f"<b>Brand:</b> {state['brand']}\n\n"
        "<i>Ab se sab jagah (main bot + worker bots) purana username aur brand "
        "naye se replace hoke dikhega.</i>"
    )
