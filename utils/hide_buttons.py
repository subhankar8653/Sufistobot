"""
Main bot ke /start menu se "Updates" aur "Developer" URL buttons hata deta hai.

start.py Pyarmor se protected hai, isliye us file ko edit nahi kiya ja sakta.
Ye patch InlineKeyboardMarkup banate waqt un do buttons ko filter kar deta hai,
lekin SIRF tab jab keyboard main_bot/plugins/start.py se ban raha ho
(worker bots ke custom buttons pe koi asar nahi).
"""
import sys

import pyrogram.types as _t

# Small-caps / fancy letters ko normal a-z me convert karne ke liye
_SMALL_CAPS = {
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g",
    "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n",
    "ᴏ": "o", "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u",
    "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}
_BLOCKED_WORDS = ("update", "developer")  # "updates" bhi isme aa jata hai


def _norm(text):
    text = "".join(_SMALL_CAPS.get(ch, ch) for ch in (text or ""))
    return "".join(ch for ch in text.lower() if ch.isalpha())


def _called_from_start():
    """Check karo ki keyboard main_bot/plugins/start.py se ban raha hai ya nahi."""
    try:
        frame = sys._getframe(2)
        for _ in range(6):
            if frame is None:
                break
            name = frame.f_globals.get("__name__", "")
            fname = (frame.f_code.co_filename or "").replace("\\", "/")
            if name == "main_bot.plugins.start" or fname.endswith("main_bot/plugins/start.py"):
                return True
            frame = frame.f_back
    except Exception:
        pass
    return False


def _is_blocked(btn):
    if not getattr(btn, "url", None):
        return False
    text = _norm(getattr(btn, "text", ""))
    return any(w in text for w in _BLOCKED_WORDS)


_orig_init = _t.InlineKeyboardMarkup.__init__


def _patched_init(self, inline_keyboard, *args, **kwargs):
    try:
        if _called_from_start():
            inline_keyboard = [
                row for row in (
                    [b for b in row if not _is_blocked(b)] for row in inline_keyboard
                ) if row
            ]
    except Exception:
        pass  # kuch bhi gadbad ho to original keyboard hi chalega
    _orig_init(self, inline_keyboard, *args, **kwargs)


_t.InlineKeyboardMarkup.__init__ = _patched_init
