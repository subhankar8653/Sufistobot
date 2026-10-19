#@suhanibots
"""
Advanced Force-Subscribe (FSub) for worker bots.

Feature set (ported from Link-sharing-bot):
  * Per-channel mode: Request (join request) / Normal (force join)
  * Numbered default buttons  ->  "⚡ Join channel 01 ⚡" / "⚡ Request channel 01 ⚡"
    or a custom button name per channel
  * PERMANENT invite links (created once, cached, never expire / rotate)
  * Fake links (extra custom buttons with row position)
  * Smart Auto-Check  -> step-by-step fsub, one channel at a time,
    auto-advances the moment the user joins / requests
  * Check Duration (cooldown) -> pass once, skip re-check for N hours
  * Join / request stats per channel
  * Tutorial video when user does not join within 30s
  * Parallel membership check (fast)

Everything is per worker bot (isolated through WorkerDB).
"""

import asyncio
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config as _cfg
from config import LOGGER, OWNER_ID
from database.main_db import MainDB

log = LOGGER(__name__)
main_db = MainDB()

_JOINED = {
    ChatMemberStatus.OWNER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}

# Smart auto-check timings
AUTO_CHECK_FAST_INTERVAL = 2      # first 30s: check every 2s (join/request detected instantly)
AUTO_CHECK_FAST_WINDOW = 30
AUTO_CHECK_POLL_INTERVAL = 5      # afterwards: every 5s
AUTO_CHECK_MAX_DURATION = 900     # stop polling after 15 minutes

TUTORIAL_WAIT = 30                # seconds before tutorial video is sent
TUTORIAL_DELETE_AFTER = 600       # tutorial video auto-delete (seconds)

FSUB_DURATION_OPTIONS = [
    ("12 Hours", 12),
    ("24 Hours", 24),
    ("48 Hours", 48),
    ("7 Days", 168),
]


def tz_offset() -> timedelta:
    return timedelta(minutes=int(getattr(_cfg, "FSUB_TZ_OFFSET_MIN", 330)))


def tz_label() -> str:
    mins = int(getattr(_cfg, "FSUB_TZ_OFFSET_MIN", 330))
    sign = "+" if mins >= 0 else "-"
    return f"UTC{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def fmt_local(dt_utc: datetime) -> str:
    """Naive-UTC datetime -> readable local time (FSUB_TZ_OFFSET_MIN)."""
    return (dt_utc + tz_offset()).strftime("%d %b %Y, %I:%M %p")


def parse_remove_time(text: str):
    """Parse owner input into a naive-UTC datetime (or None if unreadable / in the past).

    Accepts:  6h | 1d 12h | 90m          (from now)
              12:00 | 6:30 pm            (next time it is that clock time, local TZ)
              25-12-2026 18:30           (exact local date + time)
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    now = datetime.utcnow()
    result = None
    if re.fullmatch(r"(?:\s*\d+\s*[dhm]\s*)+", t):
        mins = sum(int(n) * {"d": 1440, "h": 60, "m": 1}[u] for n, u in re.findall(r"(\d+)\s*([dhm])", t))
        result = now + timedelta(minutes=mins) if mins > 0 else None
    else:
        for fmt in ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d-%m-%Y %I:%M %p"):
            try:
                result = datetime.strptime(t, fmt) - tz_offset()
                break
            except ValueError:
                continue
        if result is None:
            for fmt in ("%H:%M", "%I:%M %p", "%I %p"):
                try:
                    hm = datetime.strptime(t, fmt)
                except ValueError:
                    continue
                now_local = now + tz_offset()
                cand = now_local.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
                if cand <= now_local:
                    cand += timedelta(days=1)
                result = cand - tz_offset()
                break
    if result is None or result <= now:
        return None
    return result


def _is_real_pic(value) -> bool:
    return bool(value) and str(value).lower() not in ("none", "ɴᴏɴᴇ", "0")


async def revoke_fsub_link(client: Client, worker_db, channel_id: int):
    """Call when a channel is removed from fsub: revoke the permanent invite link
    on Telegram and clear it from the DB. Never raises."""
    try:
        cached = await worker_db.get_invite_link(channel_id)
        if cached and cached.get("invite_link") and client is not None:
            try:
                await client.revoke_chat_invite_link(channel_id, cached["invite_link"])
            except Exception:
                pass  # already invalid, or bot no longer admin
        await worker_db.clear_invite_link(channel_id)
    except Exception:
        pass


class FsubManager:
    def __init__(self, app: Client, worker_db, bot_id: int, owner_id: int, is_admin):
        self.app = app
        self.db = worker_db
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.is_admin = is_admin  # async (user_id) -> bool

        self.contexts: dict[int, dict] = {}            # uid -> stepwise session
        self.poll_tasks: dict[int, asyncio.Task] = {}  # uid -> auto-check poll task
        self.link_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.chat_cache: dict[int, object] = {}
        self.awaiting_tut: dict[int, float] = {}       # uid -> expiry (tutorial upload)
        self._maint_task = None

    # ------------------------------------------------------------------
    # auto remove / request target / link rotation
    # ------------------------------------------------------------------

    @staticmethod
    def limit_progress(doc: dict) -> int:
        """New requests (request mode) or joins (normal mode) since the target was set."""
        if doc.get("mode", "off") == "on":
            return doc.get("request_count", 0) - doc.get("limit_base_req", 0)
        return doc.get("join_count", 0) - doc.get("limit_base_join", 0)

    async def _notify_owner(self, client: Client, text: str):
        try:
            from utils import helpers as _h
            senders = [getattr(_h, "main_bot_client", None), client]
        except Exception:
            senders = [client]
        for sender in senders:
            if not sender:
                continue
            try:
                await sender.send_message(self.owner_id, text)
                return
            except Exception:
                continue

    async def remove_channel_now(self, client: Client, cid: int, reason: str) -> bool:
        """Remove a channel from fsub AND expire its invite link."""
        doc = await self.db.get_channel_doc(cid)
        if not doc:
            return False
        chat = await self._chat(client, cid)
        title = getattr(chat, "title", None) or str(cid)
        await revoke_fsub_link(client, self.db, cid)
        await self.db.rem_channel(cid)
        self.chat_cache.pop(cid, None)
        log.info(f"[fsub] bot {self.bot_id}: channel {cid} auto-removed ({reason})")
        await self._notify_owner(
            client,
            "<b>🗑 Channel auto-removed from Fsub</b>\n\n"
            f"<blockquote><b>Channel:</b> {title}\n<code>{cid}</code>\n"
            f"<b>Reason:</b> {reason}\n\n"
            "🔗 Its invite link has been expired.</blockquote>",
        )
        return True

    async def check_limits(self, client: Client, cid: int) -> bool:
        """Remove the channel if its request/join target is reached. True if removed."""
        doc = await self.db.get_channel_doc(cid)
        if not doc:
            return False
        limit = doc.get("request_limit") or 0
        if limit and self.limit_progress(doc) >= limit:
            kind = "requests" if doc.get("mode", "off") == "on" else "joins"
            return await self.remove_channel_now(client, cid, f"target of {limit} {kind} reached")
        return False

    async def rotate_link(self, client: Client, cid: int) -> bool:
        """Create a fresh invite link, then expire the previous one."""
        async with self.link_locks[cid]:
            old = await self.db.get_invite_link(cid)
            if not old:
                return False
            is_request = (await self.db.get_channel_mode(cid)) == "on"
            inv = None
            for _ in range(3):
                try:
                    inv = await client.create_chat_invite_link(chat_id=cid, creates_join_request=is_request)
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 2)
                except Exception as e:
                    log.error(f"[fsub] rotate_link({cid}) create failed: {e}")
                    return False
            if inv is None:
                return False
            await self.db.save_invite_link(cid, inv.invite_link, is_request)
            try:
                await client.revoke_chat_invite_link(cid, old["invite_link"])
            except Exception:
                pass
            log.info(f"[fsub] bot {self.bot_id}: rotated invite link of {cid}")
            return True

    async def maintenance_tick(self, client: Client):
        now = datetime.utcnow()
        for doc in await self.db.get_all_channel_docs():
            cid = doc["_id"]
            try:
                remove_at = doc.get("remove_at")
                if remove_at and now >= remove_at:
                    await self.remove_channel_now(client, cid, "scheduled removal time reached")
                    continue
                limit = doc.get("request_limit") or 0
                if limit and self.limit_progress(doc) >= limit:
                    await self.check_limits(client, cid)
                    continue
                hours = doc.get("rotate_hours") or 0
                if hours and doc.get("invite_link"):
                    marks = [m for m in (doc.get("invite_created_at"), doc.get("rotate_since")) if m]
                    if marks and now - max(marks) >= timedelta(hours=hours):
                        await self.rotate_link(client, cid)
            except Exception as e:
                log.error(f"[fsub] maintenance error for {cid}: {e}")

    async def _maintenance_loop(self):
        await asyncio.sleep(20)
        while True:
            try:
                if getattr(self.app, "is_connected", True):
                    await self.maintenance_tick(self.app)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"[fsub] maintenance loop error: {e}")
            await asyncio.sleep(60)

    def start_maintenance(self):
        if self._maint_task is None or self._maint_task.done():
            self._maint_task = asyncio.create_task(self._maintenance_loop())

    def stop(self):
        """Called when the worker bot stops."""
        if self._maint_task and not self._maint_task.done():
            self._maint_task.cancel()
        for t in list(self.poll_tasks.values()):
            t.cancel()

    # ------------------------------------------------------------------
    # low level helpers
    # ------------------------------------------------------------------

    async def _settings(self) -> dict:
        try:
            doc = await main_db.get_bot(self.bot_id)
            return (doc or {}).get("settings", {}) or {}
        except Exception:
            return {}

    async def _get_member(self, client: Client, cid: int, uid: int):
        for attempt in range(3):
            try:
                return await client.get_chat_member(cid, uid)
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
        return await client.get_chat_member(cid, uid)

    async def _chat(self, client: Client, cid: int):
        if cid in self.chat_cache:
            return self.chat_cache[cid]
        try:
            for attempt in range(3):
                try:
                    chat = await client.get_chat(cid)
                    self.chat_cache[cid] = chat
                    return chat
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
        except Exception as e:
            log.warning(f"[fsub] get_chat failed for {cid}: {e}")
        return None

    def _bypass(self, uid: int) -> bool:
        return uid == self.owner_id or uid == OWNER_ID

    # ------------------------------------------------------------------
    # join status (single source of truth)
    # ------------------------------------------------------------------

    async def channel_joined(self, client: Client, uid: int, cid: int) -> bool:
        """True if user is a member of the channel, or (request mode) has a
        pending join request for it."""
        try:
            m = await self._get_member(client, cid, uid)
            if m.status in _JOINED:
                try:
                    if await self.db.req_user_exist(cid, uid):
                        await self.db.del_req_user(cid, uid)
                except Exception:
                    pass
                return True
            # left / banned / restricted -> treat like "not participant"
            raise UserNotParticipant()
        except UserNotParticipant:
            if await self.db.get_channel_mode(cid) == "on":
                return await self.db.req_user_exist(cid, uid)
            return False
        except Exception as e:
            log.error(f"[fsub] channel_joined error ({cid}): {e}")
            return False

    async def is_subscribed(self, client: Client, uid: int) -> bool:
        channels = await self.db.show_channels()
        if not channels or self._bypass(uid):
            return True

        # Check-duration (cooldown)
        try:
            cfg = await self.db.get_fsub_check_duration()
        except Exception:
            cfg = {"enabled": False, "hours": 0}
        duration_on = bool(cfg.get("enabled")) and cfg.get("hours", 0) > 0
        if duration_on:
            try:
                verified_at = await self.db.get_fsub_verified_at(uid)
            except Exception:
                verified_at = None
            if verified_at and (datetime.utcnow() - verified_at) < timedelta(hours=cfg["hours"]):
                return True

        # Parallel check of all channels
        results = await asyncio.gather(
            *[self.channel_joined(client, uid, cid) for cid in channels],
            return_exceptions=True,
        )
        for cid, res in zip(channels, results):
            ok = res if isinstance(res, bool) else False
            if not ok:
                if await self.db.get_channel_mode(cid) == "on":
                    # request mode: one more quick check (request may have just landed)
                    if await self.channel_joined(client, uid, cid):
                        continue
                return False

        if duration_on:
            try:
                await self.db.set_fsub_verified(uid)
            except Exception as e:
                log.error(f"[fsub] set_fsub_verified error: {e}")
        return True

    # ------------------------------------------------------------------
    # permanent invite link
    # ------------------------------------------------------------------

    async def permanent_link(self, client: Client, cid: int, is_request: bool):
        """ONE permanent link per channel (no expiry, no rotation). Re-created only
        when the mode (join <-> request) changes."""
        async with self.link_locks[cid]:
            old = await self.db.get_invite_link(cid)
            if old and old.get("invite_link"):
                if old.get("is_request") == is_request:
                    return old["invite_link"]
                try:
                    await client.revoke_chat_invite_link(cid, old["invite_link"])
                except Exception:
                    pass
            for attempt in range(3):
                try:
                    inv = await client.create_chat_invite_link(
                        chat_id=cid, creates_join_request=is_request
                    )  # no expire_date on purpose -> permanent
                    await self.db.save_invite_link(cid, inv.invite_link, is_request)
                    return inv.invite_link
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 2)
                except Exception as e:
                    log.error(f"[fsub] create_chat_invite_link({cid}) failed: {e}")
                    return None
            return None

    async def _channel_link(self, client: Client, cid: int, mode: str):
        chat = await self._chat(client, cid)
        # public channel in normal mode -> plain t.me link; otherwise permanent invite link
        if chat is not None and getattr(chat, "username", None) and mode != "on":
            return f"https://t.me/{chat.username}"
        return await self.permanent_link(client, cid, is_request=(mode == "on"))

    # ------------------------------------------------------------------
    # buttons / captions
    # ------------------------------------------------------------------

    async def _channel_button(self, client: Client, cid: int, position: int, stepwise: bool = False):
        try:
            mode = await self.db.get_channel_mode(cid)
            link = await self._channel_link(client, cid, mode)
            if not link:
                return None
            custom = await self.db.get_channel_name(cid)
            label = "Request" if mode == "on" else "Join"
            if stepwise:
                text = f"🔴 {custom}" if custom else f"🔴 {label} channel {position:02d}"
            else:
                text = custom if custom else f"⚡ {label} channel {position:02d} ⚡"
            return InlineKeyboardButton(text=text, url=link)
        except Exception as e:
            log.error(f"[fsub] button error {cid}: {e}")
            return None

    async def _build_all_buttons(self, client: Client, uid: int, start_param):
        """Normal (all-at-once) fsub screen: fake links + every pending channel + Joined."""
        buttons = []
        channels = await self.db.show_channels()
        position_map = {cid: i for i, cid in enumerate(channels, start=1)}

        # fake links (row positioning)
        if channels:
            for fake in await self.db.get_fake_links():
                pos = max(0, min(int(fake.get("row", 1)) - 1, len(buttons)))
                buttons.insert(pos, [InlineKeyboardButton(text=fake["button_text"], url=fake["url"])])

        pending = 0
        for cid in channels:
            if await self.channel_joined(client, uid, cid):
                continue  # already joined / request sent -> button hidden
            btn = await self._channel_button(client, cid, position_map[cid])
            if btn:
                buttons.append([btn])
                pending += 1

        if pending == 0:
            return None

        if start_param:
            try:
                me = await client.get_me()
                buttons.append([InlineKeyboardButton(
                    text="• Jᴏɪɴᴇᴅ •",
                    url=f"https://t.me/{me.username}?start={start_param}",
                )])
            except Exception:
                pass
        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def _all_caption(mention: str) -> str:
        return (
            "<b>━━━━━━━━━━━━━━━━━━━━━\n"
            "🔒 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗\n"
            "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<blockquote>ʜᴇʏ {mention},\n\n"
            "ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ / ʀᴇǫᴜᴇsᴛ ᴛʜᴇ\n"
            "ᴄʜᴀɴɴᴇʟs ʙᴇʟᴏᴡ, ᴛʜᴇɴ ᴛᴀᴘ <b>• Jᴏɪɴᴇᴅ •</b>.</blockquote>"
        )

    @staticmethod
    def _step_caption(mention: str, step_num: int, total: int) -> str:
        return (
            "<b>━━━━━━━━━━━━━━━━━━━━━\n"
            "🔒 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗\n"
            "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<blockquote>ʜᴇʏ {mention},\n"
            "ᴊᴏɪɴ / ʀᴇǫᴜᴇsᴛ ᴛʜᴇ ᴄʜᴀɴɴᴇʟs sᴛᴇᴘ-ʙʏ-sᴛᴇᴘ.\n"
            f"ᴛᴏᴛᴀʟ sᴛᴇᴘs: {total:02d}</blockquote>\n\n"
            f"📍 <b>sᴛᴇᴘ {step_num:02d}/{total:02d}</b>\n"
            "<blockquote>🔴 ᴊᴏɪɴ ᴏʀ ʀᴇǫᴜᴇsᴛ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ʙᴇʟᴏᴡ — ᴛʜᴇ ɴᴇxᴛ sᴛᴇᴘ ᴀᴘᴘᴇᴀʀs "
            "ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.\n"
            "ᴛᴀᴘ 🔵 ʙᴜᴛᴛᴏɴ ᴛᴏ ᴄʜᴇᴄᴋ ɪɴsᴛᴀɴᴛʟʏ.</blockquote>"
        )

    async def _send_ui(self, message: Message, caption: str, markup):
        settings = await self._settings()
        pic = settings.get("force_pic", "")
        if _is_real_pic(pic):
            try:
                return await message.reply_photo(photo=pic, caption=caption, reply_markup=markup)
            except Exception as e:
                log.warning(f"[fsub] force_pic send failed, falling back to text: {e}")
        return await message.reply(caption, reply_markup=markup)

    # ------------------------------------------------------------------
    # PUBLIC ENTRY: gate
    # ------------------------------------------------------------------

    async def gate(self, client: Client, message: Message, deliver) -> bool:
        """Call at the start of /start. Returns True if the user may continue.
        Otherwise the fsub screen has been shown and False is returned.

        deliver: async (client, message) -> original handler, re-run automatically
        when a step-by-step (auto-check) session completes.
        """
        uid = message.from_user.id
        if await self.is_subscribed(client, uid):
            return True

        try:
            auto_on = await self.db.get_auto_check()
        except Exception:
            auto_on = False

        if auto_on:
            self.contexts[uid] = {
                "message": message, "deliver": deliver,
                "step_msg": None, "shown_step": None,
                "channel_order": [], "total_steps": 0,
            }
            await self._show_stepwise(client, message, uid)
            self._start_poll(client, uid)
        else:
            await self._show_all(client, message, uid)
        return False

    # ------------------------------------------------------------------
    # normal (all-at-once) screen
    # ------------------------------------------------------------------

    async def _show_all(self, client: Client, message: Message, uid: int):
        start_param = message.command[1] if getattr(message, "command", None) and len(message.command) > 1 else None
        markup = await self._build_all_buttons(client, uid, start_param)
        if markup is None:
            await message.reply("<b>❌ No valid channels found. Please contact the bot owner.</b>")
            return
        await self._send_ui(message, self._all_caption(message.from_user.mention), markup)
        asyncio.create_task(self._tutorial_if_not_joined(client, uid))

    # ------------------------------------------------------------------
    # step-by-step (smart auto-check) flow
    # ------------------------------------------------------------------

    async def _fresh_pending_order(self, client: Client, uid: int) -> list:
        pending = []
        for cid in await self.db.show_channels():
            if not await self.channel_joined(client, uid, cid):
                pending.append(cid)
        return pending

    async def _pending_within(self, client: Client, uid: int, order: list) -> list:
        pending = []
        for cid in order:
            if not await self.channel_joined(client, uid, cid):
                pending.append(cid)
        return pending

    async def _build_next_step(self, client: Client, mention: str, order: list, pending: list, total: int):
        for cid in pending:
            step_num = order.index(cid) + 1
            btn = await self._channel_button(client, cid, step_num, stepwise=True)
            if btn:
                markup = InlineKeyboardMarkup([
                    [btn],
                    [InlineKeyboardButton("🔵 Mᴀɪɴᴇ Jᴏɪɴ Kᴀʀ Lɪʏᴀ", callback_data="fsub_stepcheck")],
                ])
                return step_num, self._step_caption(mention, step_num, total), markup
        return None

    async def _show_stepwise(self, client: Client, message: Message, uid: int):
        try:
            order = await self._fresh_pending_order(client, uid)
            ctx = self.contexts.get(uid)
            if ctx is None:
                return
            ctx["channel_order"] = order
            ctx["total_steps"] = len(order)
            status = await self._refresh_step(client, uid)
            if status == "error":
                await message.reply("<b>❌ No valid channels found. Please contact the bot owner.</b>")
            asyncio.create_task(self._tutorial_if_not_joined(client, uid))
        except Exception as e:
            log.error(f"[fsub] _show_stepwise error: {e}")

    async def _finish_stepwise(self, client: Client, uid: int):
        ctx = self.contexts.pop(uid, None)
        if not ctx:
            return
        step_msg = ctx.get("step_msg")
        if step_msg:
            try:
                await step_msg.delete()
            except Exception:
                pass
        message, deliver = ctx.get("message"), ctx.get("deliver")
        if message is None or deliver is None:
            return
        try:
            await deliver(client, message)
        except Exception as e:
            log.error(f"[fsub] deliver after stepwise failed for {uid}: {e}")

    async def _refresh_step(self, client: Client, uid: int) -> str:
        """Return: finished | advanced | same | no_context | error"""
        ctx = self.contexts.get(uid)
        if not ctx:
            return "no_context"
        order = ctx.get("channel_order") or []
        total = ctx.get("total_steps", len(order))
        if not order:
            # nothing pending at session start -> user already completed everything
            await self._finish_stepwise(client, uid)
            return "finished"

        pending = await self._pending_within(client, uid, order)
        if not pending:
            await self._finish_stepwise(client, uid)
            return "finished"

        orig = ctx.get("message")
        if orig is None:
            return "error"
        built = await self._build_next_step(client, orig.from_user.mention, order, pending, total)
        if built is None:
            return "error"
        step_num, caption, markup = built

        if ctx.get("shown_step") == step_num:
            return "same"

        old = ctx.get("step_msg")
        if old is not None:
            try:
                await old.delete()
            except Exception as e:
                log.error(f"[fsub] old step msg delete failed for {uid}: {e}")
        try:
            new_msg = await self._send_ui(orig, caption, markup)
            ctx["step_msg"] = new_msg
            ctx["shown_step"] = step_num
            return "advanced"
        except Exception as e:
            log.error(f"[fsub] step send failed for {uid}: {e}")
            return "error"

    def _start_poll(self, client: Client, uid: int):
        existing = self.poll_tasks.get(uid)
        if existing and not existing.done():
            return
        self.poll_tasks[uid] = asyncio.create_task(self._poll_loop(client, uid))

    async def _poll_loop(self, client: Client, uid: int):
        elapsed = 0
        try:
            while elapsed < AUTO_CHECK_MAX_DURATION:
                interval = AUTO_CHECK_FAST_INTERVAL if elapsed < AUTO_CHECK_FAST_WINDOW else AUTO_CHECK_POLL_INTERVAL
                await asyncio.sleep(interval)
                elapsed += interval
                try:
                    if not await self.db.get_auto_check():
                        return
                    status = await self._refresh_step(client, uid)
                    if status in ("finished", "no_context"):
                        return
                except Exception as e:
                    log.error(f"[fsub] poll error for {uid}: {e}")
                    return
        finally:
            self.poll_tasks.pop(uid, None)

    async def notify_event(self, client: Client, uid: int):
        """Real-time trigger (join / join request detected) -> advance step instantly."""
        if uid not in self.contexts:
            return
        try:
            await self._refresh_step(client, uid)
        except Exception as e:
            log.error(f"[fsub] notify_event error for {uid}: {e}")

    # ------------------------------------------------------------------
    # tutorial video
    # ------------------------------------------------------------------

    async def _tutorial_if_not_joined(self, client: Client, uid: int):
        tutorial = await self.db.get_tutorial("fsub")
        if not tutorial:
            return
        await asyncio.sleep(TUTORIAL_WAIT)
        try:
            for cid in await self.db.show_channels():
                if not await self.channel_joined(client, uid, cid):
                    break
            else:
                return  # joined everything within 30s -> no video
        except Exception as e:
            log.error(f"[fsub] tutorial check error for {uid}: {e}")
            return
        try:
            sent = await client.send_video(
                chat_id=uid,
                video=tutorial["file_id"],
                caption=(
                    "<b>📹 ʜᴏᴡ ᴛᴏ ᴊᴏɪɴ?</b>\n\n"
                    "<blockquote>ʟᴏᴏᴋs ʟɪᴋᴇ ʏᴏᴜ ᴀʀᴇ ꜰᴀᴄɪɴɢ ᴀɴ ɪssᴜᴇ ᴊᴏɪɴɪɴɢ.\n"
                    "ᴡᴀᴛᴄʜ ᴛʜᴇ ᴛᴜᴛᴏʀɪᴀʟ ᴀɴᴅ ꜰᴏʟʟᴏᴡ ᴛʜᴇ sᴛᴇᴘs! 👆\n\n"
                    "⏰ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ 10 ᴍɪɴᴜᴛᴇs.</blockquote>"
                ),
            )

            async def _later():
                await asyncio.sleep(TUTORIAL_DELETE_AFTER)
                try:
                    await sent.delete()
                except Exception:
                    pass

            asyncio.create_task(_later())
        except Exception as e:
            log.error(f"[fsub] tutorial send failed for {uid}: {e}")

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------

    def register(self):
        app = self.app
        mgr = self

        # ---- admin filter ----
        async def _admin_check(_, __, m):
            try:
                return bool(m.from_user) and await mgr.is_admin(m.from_user.id)
            except Exception:
                return False

        admin_f = filters.create(_admin_check)

        # ---- step "I have joined" button ----
        @app.on_callback_query(filters.regex(r"^fsub_stepcheck$"))
        async def _stepcheck_cb(client: Client, cq: CallbackQuery):
            uid = cq.from_user.id
            try:
                status = await mgr._refresh_step(client, uid)
                if status == "finished":
                    await cq.answer("🟢 All steps complete! Sending your content...")
                elif status == "advanced":
                    await cq.answer("🟢 Step complete! Next step is shown.")
                elif status == "same":
                    await cq.answer(
                        "🔴 You haven't joined / requested the channel above yet. Please do that first!",
                        show_alert=True,
                    )
                elif status == "no_context":
                    await cq.answer("⚠️ Session expired, please send /start again.", show_alert=True)
                else:
                    await cq.answer("⚠️ Error, please try again in a moment.", show_alert=True)
            except Exception as e:
                log.error(f"[fsub] stepcheck error: {e}")
                try:
                    await cq.answer("⚠️ Error, try again.", show_alert=True)
                except Exception:
                    pass

        # ---- join request tracking ----
        @app.on_chat_join_request()
        async def _join_request(client: Client, request: ChatJoinRequest):
            cid, uid = request.chat.id, request.from_user.id
            try:
                if cid in await mgr.db.show_channels():
                    # Record only — never auto-approve (owner approves manually)
                    await mgr.db.req_user(cid, uid)
                    await mgr.db.inc_request_count(cid)
                    await mgr.check_limits(client, cid)       # target reached -> auto-remove + expire link
                    asyncio.create_task(mgr.notify_event(client, uid))
            except Exception as e:
                log.error(f"[fsub] join_request handler error: {e}")

        # ---- member joined / approved ----
        @app.on_chat_member_updated(filters.group | filters.channel)
        async def _member_updated(client: Client, update):
            try:
                if not update.new_chat_member:
                    return
                user, chat = update.new_chat_member.user, update.chat
                old = update.old_chat_member.status if update.old_chat_member else None
                new = update.new_chat_member.status
                if new in _JOINED and old not in _JOINED:
                    if chat.id not in await mgr.db.show_channels():
                        return
                    asyncio.create_task(mgr.notify_event(client, user.id))
                    await mgr.db.inc_join_count(chat.id)
                    await mgr.check_limits(client, chat.id)
                    if await mgr.db.req_user_exist(chat.id, user.id):
                        await mgr.db.del_req_user(chat.id, user.id)
                        asyncio.create_task(mgr._approved_dm(client, user.id, chat))
            except Exception as e:
                log.error(f"[fsub] member_updated handler error: {e}")

        # ---- /auto_check ----
        @app.on_message(filters.command("auto_check") & filters.private & admin_f)
        async def _auto_check_cmd(client: Client, message: Message):
            args = message.text.split(None, 1)
            if len(args) < 2 or args[1].strip().lower() not in ("on", "off"):
                cur = await mgr.db.get_auto_check()
                await message.reply(
                    f"<b>Fsub Smart Auto-Check:</b> {'✅ ON' if cur else '❌ OFF'}\n\n"
                    "<b>Usage:</b>\n"
                    "<code>/auto_check on</code> — channels are shown step-by-step, the next step "
                    "appears automatically and the content is sent as soon as everything is joined\n"
                    "<code>/auto_check off</code> — all channels at once, user taps Joined"
                )
                return
            enabled = args[1].strip().lower() == "on"
            await mgr.db.set_auto_check(enabled)
            await message.reply(
                f"<b>✅ Fsub Smart Auto-Check {'ON' if enabled else 'OFF'}!</b>"
            )

        # ---- fake links ----
        @app.on_message(filters.command("setfakelink") & filters.private & admin_f)
        async def _set_fake(client: Client, message: Message):
            parts = message.text.split(maxsplit=2)
            if len(parts) < 3:
                await message.reply(
                    "<b>❌ Wrong format!</b>\n\n"
                    "<code>/setfakelink [URL] [Button Text] [Row]</code>\n\n"
                    "<b>Examples:</b>\n"
                    "<code>/setfakelink https://t.me/channel Join My Channel</code>\n"
                    "<code>/setfakelink https://t.me/channel2 Another Button 2</code>\n\n"
                    "<i>Row is optional (default 1). Add as many buttons as you like. "
                    "See them with /listfakelink</i>"
                )
                return
            url, rest = parts[1], parts[2]
            row = 1
            rp = rest.rsplit(maxsplit=1)
            if len(rp) == 2 and rp[1].isdigit():
                text, row = rp[0], max(1, int(rp[1]))
            else:
                text = rest
            if not url.startswith(("http://", "https://", "t.me/")):
                await message.reply(
                    "<b>❌ Invalid URL!</b>\n\nURL must start with "
                    "<code>http://</code>, <code>https://</code> or <code>t.me/</code>."
                )
                return
            if url.startswith("t.me/"):
                url = "https://" + url
            if await mgr.db.add_fake_link(url, text, row):
                total = len(await mgr.db.get_fake_links())
                await message.reply(
                    "<b>✅ Fake link added!</b>\n\n"
                    f"<b>Button:</b> <code>{text}</code>\n"
                    f"<b>URL:</b> <code>{url}</code>\n"
                    f"<b>Row:</b> <code>{row}</code>\n\n"
                    f"<i>Total fake links: {total}. See /listfakelink</i>"
                )
            else:
                await message.reply("<b>❌ Could not save the fake link. Try again.</b>")

        @app.on_message(filters.command("listfakelink") & filters.private & admin_f)
        async def _list_fake(client: Client, message: Message):
            fakes = await mgr.db.get_fake_links()
            if not fakes:
                await message.reply(
                    "<b>No fake links set.</b>\n\n"
                    "<code>/setfakelink https://t.me/channel Button Text [row]</code>"
                )
                return
            lines = ["<b>🔗 Fake Links:</b>\n"]
            for i, f in enumerate(fakes, 1):
                lines.append(
                    f"<b>{i}.</b> <code>{f['button_text']}</code>\n"
                    f"   🔗 <code>{f['url']}</code>\n"
                    f"   📍 Row: <code>{f['row']}</code>"
                )
            lines.append("\n<i>Delete: /deletefakelink (number)</i>")
            await message.reply("\n".join(lines), disable_web_page_preview=True)

        @app.on_message(filters.command("deletefakelink") & filters.private & admin_f)
        async def _del_fake(client: Client, message: Message):
            if len(message.command) < 2 or not message.command[1].isdigit():
                await message.reply(
                    "<b>❌ Usage:</b> <code>/deletefakelink [number]</code>\n\n"
                    "Get the number from <code>/listfakelink</code>."
                )
                return
            if await mgr.db.remove_fake_link_by_index(int(message.command[1])):
                await message.reply("<b>✅ Fake link deleted!</b>")
            else:
                await message.reply("<b>❌ Invalid number.</b> Check /listfakelink.")

        # ---- tutorial video ----
        @app.on_message(filters.command("set_tutorial_fsub") & filters.private & admin_f)
        async def _set_tut(client: Client, message: Message):
            mgr.awaiting_tut[message.from_user.id] = time.time() + 300
            await message.reply(
                "<b>📹 Fsub Tutorial</b>\n\n"
                "<blockquote>Send me the video you want to show users who don't join "
                "within 30 seconds.\n\n⏳ You have 5 minutes.</blockquote>"
            )

        async def _tut_wait(_, __, m):
            try:
                if not m.from_user:
                    return False
                exp = mgr.awaiting_tut.get(m.from_user.id)
                if not exp:
                    return False
                if time.time() > exp:
                    mgr.awaiting_tut.pop(m.from_user.id, None)
                    return False
                return not (m.text and m.text.startswith("/"))
            except Exception:
                return False

        @app.on_message(filters.private & filters.create(_tut_wait), group=-2)
        async def _tut_receive(client: Client, message: Message):
            uid = message.from_user.id
            file_id = None
            if message.video:
                file_id = message.video.file_id
            elif message.document:
                file_id = message.document.file_id
            elif message.animation:
                file_id = message.animation.file_id
            if not file_id:
                await message.reply("<b>❌ Send only a video or document!</b>\nTry /set_tutorial_fsub again.")
                mgr.awaiting_tut.pop(uid, None)
                message.stop_propagation()
                return
            mgr.awaiting_tut.pop(uid, None)
            await mgr.db.set_tutorial("fsub", file_id)
            try:
                await message.delete()
            except Exception:
                pass
            await client.send_message(
                uid,
                "<b>✅ Fsub tutorial video saved!</b>\n\n"
                "<blockquote>Users who don't join within 30 seconds will get this video "
                "(auto-deleted after 10 min).\n\n"
                "📌 Tutorial is <b>ON</b>. Turn off: <code>/fsub_tutorial off</code></blockquote>",
            )
            message.stop_propagation()

        @app.on_message(filters.command("fsub_tutorial") & filters.private & admin_f)
        async def _tut_toggle(client: Client, message: Message):
            args = message.command
            status = await mgr.db.get_tutorial_status("fsub")
            if len(args) < 2 or args[1].lower() not in ("on", "off"):
                await message.reply(
                    "<b>📹 Fsub Tutorial Status</b>\n\n"
                    f"<blockquote>Video: {'✅ Set' if status['exists'] else '❌ Not set'}\n"
                    f"Status: {'✅ ON' if status['enabled'] else '❌ OFF'}\n\n"
                    "Usage: <code>/fsub_tutorial on</code> | <code>/fsub_tutorial off</code></blockquote>"
                )
                return
            enabled = args[1].lower() == "on"
            if enabled and not status["exists"]:
                await message.reply("<b>❌ Set the video first:</b> <code>/set_tutorial_fsub</code>")
                return
            ok = await mgr.db.toggle_tutorial("fsub", enabled)
            await message.reply(
                f"<b>{'✅' if enabled else '❌'} Fsub Tutorial {'ON' if enabled else 'OFF'}!</b>"
                if ok else "<b>❌ Not updated. Set the video first with /set_tutorial_fsub.</b>"
            )

    # ------------------------------------------------------------------

    async def _approved_dm(self, client: Client, uid: int, chat):
        """Owner approved a user's join request in a fsub channel -> tell the user."""
        try:
            sent = await client.send_message(
                uid,
                "<b>✅ Your request was accepted!</b>\n\n"
                f"<blockquote><b>Channel:</b> {chat.title}\n\n"
                "You can now go back and use your link.\n"
                "<i>⚠️ This message auto-deletes in 5 minutes.</i></blockquote>",
            )
            await asyncio.sleep(300)
            await sent.delete()
        except Exception:
            pass


def setup_fsub(app: Client, worker_db, bot_id: int, owner_id: int, is_admin) -> FsubManager:
    """Create the FsubManager for one worker bot and register all its handlers."""
    mgr = FsubManager(app, worker_db, bot_id, owner_id, is_admin)
    mgr.register()
    app._fsub_mgr = mgr
    try:
        mgr.start_maintenance()
    except RuntimeError:
        pass  # no running loop (never the case inside the worker engine)
    return mgr
