"""
/community command ka engine.

Owner jab `/community @sbbots` chalata hai to bot ke har outgoing message
(main bot + worker bots), caption aur inline button me ye replace hota hai:

  @suhanibots / t.me/SuhaniBots / suhanibots   ->  naya username
  𝗦𝗨𝗛𝗔𝗡𝗜 𝗙𝗜𝗟𝗘𝗦𝗧𝗢𝗥𝗘                          ->  𝗦𝗕 𝗙𝗜𝗟𝗘 𝗦𝗧𝗢𝗥𝗘 (naye username se)

Setting MongoDB me save hoti hai, restart ke baad bhi rehti hai.
"""
import re

from config import LOGGER
from database.mongo import get_db

log = LOGGER(__name__)

_DOC_ID = "community_branding"

# Purana username (case-insensitive, poora username hi match hoga;
# jaise @SuhaniBots_Updates ko ye NAHI chhuega)
_OLD_USERNAME = re.compile(r"(?<![A-Za-z0-9_])suhanibots(?![A-Za-z0-9_])", re.IGNORECASE)
# Purana brand naam: bold unicode wala + simple text wala
_OLD_BRAND = re.compile(
    r"𝗦𝗨𝗛𝗔𝗡𝗜\s*𝗙𝗜𝗟𝗘\s*𝗦𝗧𝗢𝗥𝗘|(?<![A-Za-z])suhani\s*file\s*store(?![A-Za-z])",
    re.IGNORECASE,
)

_state = {"username": None, "brand": None}
_loaded = False


def to_bold(text: str) -> str:
    """Text ko 𝗯𝗼𝗹𝗱 sans-serif unicode me badalta hai (A-Z, a-z, 0-9)."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D5D4 + ord(ch) - ord("A")))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D5EE + ord(ch) - ord("a")))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7EC + ord(ch) - ord("0")))
        else:
            out.append(ch)
    return "".join(out)


def default_brand(username: str) -> str:
    """sbbots -> 𝗦𝗕 𝗙𝗜𝗟𝗘 𝗦𝗧𝗢𝗥𝗘"""
    base = re.sub(r"(?i)bots?$", "", username) or username
    return to_bold(f"{base.upper()} FILE STORE")


def apply(text):
    """Text me replacements lagao. Community set nahi hai to text jaisa ka taisa."""
    if not isinstance(text, str) or not _state["username"]:
        return text
    text = _OLD_USERNAME.sub(_state["username"], text)
    text = _OLD_BRAND.sub(_state["brand"], text)
    return text


def current():
    return dict(_state)


async def load_community():
    """MongoDB se saved setting memory me load karo."""
    global _loaded
    try:
        doc = await get_db()["settings"].find_one({"_id": _DOC_ID})
        _state["username"] = (doc or {}).get("username")
        _state["brand"] = (doc or {}).get("brand")
        _loaded = True
        if _state["username"]:
            log.info(f"Community branding active: @{_state['username']}")
    except Exception as e:
        log.error(f"Community load failed: {e}")


async def set_community(username: str, brand: str | None = None):
    username = username.lstrip("@")
    brand = to_bold(brand.upper()) if brand else default_brand(username)
    await get_db()["settings"].update_one(
        {"_id": _DOC_ID}, {"$set": {"username": username, "brand": brand}}, upsert=True
    )
    _state["username"], _state["brand"] = username, brand
    return dict(_state)


async def reset_community():
    await get_db()["settings"].delete_one({"_id": _DOC_ID})
    _state["username"] = _state["brand"] = None


# --------------------------------------------------------------------------
# Pyrogram patches (ek hi baar lagte hain)
# --------------------------------------------------------------------------
def _install():
    from pyrogram.parser import Parser
    from pyrogram.types import InlineKeyboardButton

    if getattr(Parser, "_community_patched", False):
        return

    _orig_parse = Parser.parse

    async def _parse(self, text, *args, **kwargs):
        global _loaded
        if not _loaded:
            await load_community()
        return await _orig_parse(self, apply(text), *args, **kwargs)

    Parser.parse = _parse
    Parser._community_patched = True

    _orig_btn_init = InlineKeyboardButton.__init__

    def _btn_init(self, *args, **kwargs):
        _orig_btn_init(self, *args, **kwargs)
        try:
            self.text = apply(self.text)
            if getattr(self, "url", None):
                self.url = apply(self.url)
        except Exception:
            pass

    InlineKeyboardButton.__init__ = _btn_init


try:
    _install()
except Exception as _e:  # patch fail ho to bot normal chalega
    log.error(f"Community patch install failed: {_e}")
