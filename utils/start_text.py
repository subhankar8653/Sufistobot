# @suhanibots — utils/start_text.py
#
# Guards the plain /start welcome message of worker (clone) bots.
#
# Some bots have an old "force-sub warning" text saved as their *start message*
# (e.g. copied from another bot / old data transfer):
#     "You haven't joined all channels yet. Please be sure to join all the
#      channels provided below, then try again.. ! Facing problems, use: /help"
# A user who already passed force-sub must never see that. looks_like_fsub_notice()
# detects such text (also in small-caps / fancy letters) so the bot can fall back
# to the normal welcome message instead.

_SMALL_CAPS = {
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g",
    "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n",
    "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u",
    "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}

_MARKERS = (
    "haventjoinedallchannels",
    "havenotjoinedallchannels",
    "joinallthechannelsprovided",
    "tryagainfacingproblems",
    "facingproblemsuse",
)


def _norm(text: str) -> str:
    out = []
    for ch in text or "":
        ch = _SMALL_CAPS.get(ch, ch)
        # bold / italic / mono "math alphanumeric" letters -> plain a-z
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D7FF:
            import unicodedata
            ch = unicodedata.normalize("NFKC", ch)
        if ch.isalpha():
            out.append(ch.lower())
    return "".join(out)


def looks_like_fsub_notice(text: str) -> bool:
    """True if `text` is a leftover 'you have not joined all channels' notice."""
    n = _norm(text)
    return bool(n) and any(m in n for m in _MARKERS)


def default_welcome(mention: str, bot_username: str) -> str:
    return (
        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
        "👋 𝗪𝗘𝗟𝗖𝗢𝗠𝗘\n"
        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>ʜᴇʏ {mention}, ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ <b>@{bot_username}</b>!\n\n"
        "ɪ ᴀᴍ ᴀ ꜰɪʟᴇ sᴛᴏʀᴇ ʙᴏᴛ. ᴏᴘᴇɴ ᴀ sʜᴀʀᴇ ʟɪɴᴋ ꜰʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ "
        "ᴀɴᴅ ɪ ᴡɪʟʟ sᴇɴᴅ ʏᴏᴜ ᴛʜᴇ ꜰɪʟᴇs ʀɪɢʜᴛ ʜᴇʀᴇ.\n\n"
        "✅ ʏᴏᴜ ᴀʀᴇ ᴀʟʟ sᴇᴛ — ɴᴏ ᴍᴏʀᴇ ᴄʜᴀɴɴᴇʟs ᴛᴏ ᴊᴏɪɴ.\n"
        "ɴᴇᴇᴅ ʜᴇʟᴘ? ᴜsᴇ /help</blockquote>"
    )


HELP_USER = (
    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
    "📖 𝗛𝗘𝗟𝗣\n"
    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
    "<blockquote>◈ ᴛᴀᴘ ᴀ ꜰɪʟᴇ ʟɪɴᴋ ꜰʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ꜰɪʟᴇs.\n"
    "◈ ꜰɪʟᴇs ᴍᴀʏ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ — sᴀᴠᴇ ᴏʀ ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇᴍ ꜰɪʀsᴛ.\n"
    "◈ ʟɪɴᴋ ᴅɪᴅɴ'ᴛ ᴡᴏʀᴋ? ᴏᴘᴇɴ ɪᴛ ᴀɢᴀɪɴ ᴏʀ ᴀsᴋ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ᴀᴅᴍɪɴ.</blockquote>"
)

HELP_ADMIN_EXTRA = (
    "\n\n<blockquote><b>👑 Admin commands</b>\n"
    "/genlink · /batch · /custom_batch · /flink\n"
    "/ban · /unban · /broadcast\n"
    "/addadmin · /deladmin · /admins (owner)\n"
    "/auto_check · /setfakelink · /version</blockquote>"
)
