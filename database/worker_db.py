#@suhanibots

from database.mongo import get_db
from config import LOGGER

log = LOGGER(__name__)


class WorkerDB:
    """
    Database operations scoped to a single worker bot.

    All collections are prefixed with the bot_id to ensure
    complete isolation between different user bots.
    """

    def __init__(self, bot_id: int):
        self.bot_id = bot_id
        db = get_db()
        prefix = f"bot_{bot_id}"
        self.users = db[f"{prefix}_users"]
        self.admins = db[f"{prefix}_admins"]
        self.channels = db[f"{prefix}_channels"]
        self.banned = db[f"{prefix}_banned"]
        self.request_fsub = db[f"{prefix}_request_fsub"]
        self.del_timer = db[f"{prefix}_del_timer"]
        self.verify = db[f"{prefix}_verify"]
        # FSub extras (Link-sharing-bot style)
        self.fsub_cfg = db[f"{prefix}_fsub_cfg"]          # auto_check / check_duration settings
        self.fake_links = db[f"{prefix}_fake_links"]      # fake (extra) buttons shown on fsub screen
        self.tutorials = db[f"{prefix}_tutorials"]        # fsub tutorial video

    # =========================================================================
    # USER TRACKING
    # =========================================================================

    async def present_user(self, user_id: int) -> bool:
        """Check if a user exists."""
        return bool(await self.users.find_one({"_id": user_id}))

    async def add_user(self, user_id: int):
        """Add a user to the bot's user list."""
        if not await self.present_user(user_id):
            await self.users.insert_one({"_id": user_id})

    async def full_userbase(self) -> list:
        """Return all user IDs."""
        docs = await self.users.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def del_user(self, user_id: int):
        """Remove a user from the bot's user list."""
        await self.users.delete_one({"_id": user_id})

    async def total_users(self) -> int:
        """Count total users."""
        return await self.users.count_documents({})

    # =========================================================================
    # ADMIN MANAGEMENT
    # =========================================================================

    async def admin_exist(self, admin_id: int) -> bool:
        """Check if a user is an admin."""
        return bool(await self.admins.find_one({"_id": admin_id}))

    async def add_admin(self, admin_id: int):
        """Add an admin."""
        if not await self.admin_exist(admin_id):
            await self.admins.insert_one({"_id": admin_id})

    async def del_admin(self, admin_id: int):
        """Remove an admin."""
        await self.admins.delete_one({"_id": admin_id})

    async def get_all_admins(self) -> list:
        """Return all admin IDs."""
        docs = await self.admins.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    # =========================================================================
    # BAN MANAGEMENT
    # =========================================================================

    async def ban_user_exist(self, user_id: int) -> bool:
        """Check if a user is banned."""
        return bool(await self.banned.find_one({"_id": user_id}))

    async def add_ban_user(self, user_id: int):
        """Ban a user."""
        if not await self.ban_user_exist(user_id):
            await self.banned.insert_one({"_id": user_id})

    async def del_ban_user(self, user_id: int):
        """Unban a user."""
        await self.banned.delete_one({"_id": user_id})

    async def get_ban_users(self) -> list:
        """Return all banned user IDs."""
        docs = await self.banned.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    # =========================================================================
    # AUTO-DELETE TIMER
    # =========================================================================

    async def set_del_timer(self, value: int):
        """Set the auto-delete timer value (seconds)."""
        existing = await self.del_timer.find_one({})
        if existing:
            await self.del_timer.update_one({}, {"$set": {"value": value}})
        else:
            await self.del_timer.insert_one({"value": value})

    async def get_del_timer(self) -> int:
        """Get the auto-delete timer value. Returns 0 if not set."""
        data = await self.del_timer.find_one({})
        return data.get("value", 0) if data else 0

    # =========================================================================
    # FORCE-SUBSCRIBE CHANNEL MANAGEMENT
    # Channel doc: {_id, mode, created_at, custom_name, join_count,
    #               request_count, invite_link, invite_is_request}
    # mode: 'on' = request-based (join request), 'off' = normal force join
    # =========================================================================

    async def channel_exist(self, channel_id: int) -> bool:
        """Check if a force-sub channel exists."""
        return bool(await self.channels.find_one({"_id": channel_id}))

    async def add_channel(self, channel_id: int, mode: str = "on", custom_name: str = None):
        """Add a force-sub channel.

        New channels are added in REQUEST mode ('on') by default (same as
        Link-sharing-bot). Use set_channel_mode() to switch to normal join ('off').
        """
        if not await self.channel_exist(channel_id):
            from datetime import datetime
            doc = {
                "_id": channel_id,
                "mode": mode,
                "created_at": datetime.utcnow(),
                "join_count": 0,
                "request_count": 0,
            }
            if custom_name:
                doc["custom_name"] = custom_name.strip()
            await self.channels.insert_one(doc)

    async def rem_channel(self, channel_id: int):
        """Remove a force-sub channel (and its request-tracking data)."""
        await self.channels.delete_one({"_id": channel_id})
        await self.request_fsub.delete_one({"_id": channel_id})

    async def show_channels(self) -> list:
        """Return all force-sub channel IDs, oldest-added first.

        Order is stable (created_at) so default button names
        (Join channel 01, 02, ...) never shuffle.
        """
        docs = await self.channels.find().sort("created_at", 1).to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def get_channel_mode(self, channel_id: int) -> str:
        """Get a channel's force-sub mode ('on' = request-based, 'off' = force join)."""
        data = await self.channels.find_one({"_id": channel_id})
        return data.get("mode", "off") if data else "off"

    async def set_channel_mode(self, channel_id: int, mode: str):
        """Set a channel's force-sub mode."""
        await self.channels.update_one(
            {"_id": channel_id},
            {"$set": {"mode": mode}},
            upsert=True,
        )

    async def set_channel_mode_all(self, mode: str) -> int:
        """Set mode for ALL fsub channels at once. Returns number of channels matched."""
        if mode not in ("on", "off"):
            return 0
        result = await self.channels.update_many({}, {"$set": {"mode": mode}})
        return result.matched_count

    # ---- custom button name ----

    async def set_channel_name(self, channel_id: int, custom_name: str) -> bool:
        """Set a custom button name for a fsub channel (empty string clears it)."""
        custom_name = (custom_name or "").strip()
        if custom_name:
            res = await self.channels.update_one({"_id": channel_id}, {"$set": {"custom_name": custom_name}})
        else:
            res = await self.channels.update_one({"_id": channel_id}, {"$unset": {"custom_name": ""}})
        return res.matched_count > 0

    async def get_channel_name(self, channel_id: int):
        """Custom button name of a fsub channel (None if not set)."""
        data = await self.channels.find_one({"_id": channel_id})
        return data.get("custom_name") if data else None

    # ---- join / request stats ----

    async def inc_join_count(self, channel_id: int):
        await self.channels.update_one({"_id": channel_id}, {"$inc": {"join_count": 1}})

    async def inc_request_count(self, channel_id: int):
        await self.channels.update_one({"_id": channel_id}, {"$inc": {"request_count": 1}})

    async def get_fsub_stats(self, channel_id: int) -> dict:
        data = await self.channels.find_one({"_id": channel_id})
        return {
            "join_count": data.get("join_count", 0) if data else 0,
            "request_count": data.get("request_count", 0) if data else 0,
        }

    # ---- permanent (non-expiring) invite link cache ----

    async def get_invite_link(self, channel_id: int):
        """Return {'invite_link': str, 'is_request': bool} or None."""
        data = await self.channels.find_one({"_id": channel_id})
        if data and data.get("invite_link"):
            return {
                "invite_link": data["invite_link"],
                "is_request": bool(data.get("invite_is_request", False)),
            }
        return None

    async def save_invite_link(self, channel_id: int, invite_link: str, is_request: bool):
        await self.channels.update_one(
            {"_id": channel_id},
            {"$set": {"invite_link": invite_link, "invite_is_request": bool(is_request)}},
        )

    async def clear_invite_link(self, channel_id: int):
        await self.channels.update_one(
            {"_id": channel_id},
            {"$unset": {"invite_link": "", "invite_is_request": ""}},
        )

    # =========================================================================
    # REQUEST-BASED FORCE-SUB USER TRACKING
    # =========================================================================

    async def req_user(self, channel_id: int, user_id: int):
        """Record that a user sent a join request for a channel."""
        try:
            await self.request_fsub.update_one(
                {"_id": int(channel_id)},
                {"$addToSet": {"user_ids": int(user_id)}},
                upsert=True,
            )
        except Exception as e:
            log.error(f"Failed to add user to request list: {e}")

    async def del_req_user(self, channel_id: int, user_id: int):
        """Remove a user from the join request list for a channel."""
        await self.request_fsub.update_one(
            {"_id": int(channel_id)},
            {"$pull": {"user_ids": int(user_id)}},
        )

    async def req_user_exist(self, channel_id: int, user_id: int) -> bool:
        """Check if a user has a pending join request for a channel."""
        try:
            found = await self.request_fsub.find_one(
                {"_id": int(channel_id), "user_ids": int(user_id)}
            )
            return bool(found)
        except Exception as e:
            log.error(f"Failed to check request list: {e}")
            return False

    # =========================================================================
    # FSUB SMART AUTO-CHECK  (OFF by default)
    # ON -> step-by-step fsub, one channel at a time, auto-advances on join.
    # =========================================================================

    async def get_auto_check(self) -> bool:
        doc = await self.fsub_cfg.find_one({"_id": "auto_check"})
        return bool(doc.get("enabled", False)) if doc else False

    async def set_auto_check(self, enabled: bool):
        await self.fsub_cfg.update_one(
            {"_id": "auto_check"}, {"$set": {"enabled": bool(enabled)}}, upsert=True
        )

    # =========================================================================
    # FSUB CHECK DURATION (cooldown)  (OFF by default)
    # ON + hours -> once a user passes fsub, no re-check for that many hours,
    # even if new fsub channels get added.
    # =========================================================================

    async def get_fsub_check_duration(self) -> dict:
        doc = await self.fsub_cfg.find_one({"_id": "check_duration"})
        if not doc:
            return {"enabled": False, "hours": 0}
        return {"enabled": bool(doc.get("enabled", False)), "hours": int(doc.get("hours", 0) or 0)}

    async def set_fsub_check_duration(self, enabled: bool, hours: int = 0):
        await self.fsub_cfg.update_one(
            {"_id": "check_duration"},
            {"$set": {"enabled": bool(enabled), "hours": int(hours)}},
            upsert=True,
        )

    async def set_fsub_verified(self, user_id: int):
        """Save the time this user last passed the full fsub check."""
        from datetime import datetime
        await self.users.update_one(
            {"_id": int(user_id)}, {"$set": {"fsub_verified_at": datetime.utcnow()}}
        )

    async def get_fsub_verified_at(self, user_id: int):
        user = await self.users.find_one({"_id": int(user_id)})
        return user.get("fsub_verified_at") if user else None

    # =========================================================================
    # FAKE LINKS (extra custom buttons on the fsub screen, with row position)
    # =========================================================================

    async def add_fake_link(self, url: str, button_text: str, row: int = 1) -> bool:
        try:
            last = await self.fake_links.find_one(sort=[("order", -1)])
            order = (last["order"] + 1) if last else 1
            await self.fake_links.insert_one(
                {"url": url, "button_text": button_text, "row": int(row), "order": order}
            )
            return True
        except Exception as e:
            log.error(f"add_fake_link error: {e}")
            return False

    async def get_fake_links(self) -> list:
        try:
            links = []
            async for doc in self.fake_links.find().sort("order", 1):
                links.append({
                    "url": doc["url"],
                    "button_text": doc["button_text"],
                    "row": doc.get("row", 1),
                })
            return links
        except Exception as e:
            log.error(f"get_fake_links error: {e}")
            return []

    async def remove_fake_link_by_index(self, index: int) -> bool:
        """index is 1-based, as shown in /listfakelink."""
        try:
            docs = await self.fake_links.find().sort("order", 1).to_list(length=None)
            if index < 1 or index > len(docs):
                return False
            res = await self.fake_links.delete_one({"_id": docs[index - 1]["_id"]})
            return res.deleted_count > 0
        except Exception as e:
            log.error(f"remove_fake_link_by_index error: {e}")
            return False

    # =========================================================================
    # FSUB TUTORIAL VIDEO
    # =========================================================================

    async def set_tutorial(self, tutorial_type: str, file_id: str):
        await self.tutorials.update_one(
            {"_id": tutorial_type},
            {"$set": {"file_id": file_id, "enabled": True}},
            upsert=True,
        )

    async def get_tutorial(self, tutorial_type: str):
        """Return {'file_id': ...} if set AND enabled, else None."""
        doc = await self.tutorials.find_one({"_id": tutorial_type})
        if doc and doc.get("enabled", False) and doc.get("file_id"):
            return {"file_id": doc["file_id"]}
        return None

    async def toggle_tutorial(self, tutorial_type: str, enabled: bool) -> bool:
        res = await self.tutorials.update_one(
            {"_id": tutorial_type}, {"$set": {"enabled": bool(enabled)}}
        )
        return res.matched_count > 0

    async def get_tutorial_status(self, tutorial_type: str) -> dict:
        doc = await self.tutorials.find_one({"_id": tutorial_type})
        if not doc:
            return {"exists": False, "enabled": False}
        return {"exists": True, "enabled": bool(doc.get("enabled", False))}

    # =========================================================================
    # SHORTENER VERIFICATION TRACKING
    # =========================================================================

    async def set_verified(self, user_id: int):
        """Mark a user as shortener-verified with current timestamp."""
        from datetime import datetime, timezone
        await self.verify.update_one(
            {"_id": user_id},
            {"$set": {"verified_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def is_verified(self, user_id: int, expire_seconds: int) -> bool:
        """Check if a user's verification is still valid."""
        from datetime import datetime, timezone
        doc = await self.verify.find_one({"_id": user_id})
        if not doc:
            return False
        verified_at = doc.get("verified_at")
        if not verified_at:
            return False
        elapsed = (datetime.now(timezone.utc) - verified_at).total_seconds()
        return elapsed < expire_seconds

    # =========================================================================
    # DATA TRANSFER — Copy data from another bot
    # =========================================================================

    async def copy_data_from(self, source_bot_id: int) -> dict:
        """Copy all collections from source_bot_id to this bot. Returns stats."""
        db = get_db()
        source_prefix = f"bot_{source_bot_id}"
        dest_prefix = f"bot_{self.bot_id}"
        stats = {}

        collections = await db.list_collection_names()
        source_collections = [c for c in collections if c.startswith(source_prefix + "_")]

        for src_col_name in source_collections:
            suffix = src_col_name[len(source_prefix):]  # e.g. "_users"
            dest_col_name = f"{dest_prefix}{suffix}"

            src_col = db[src_col_name]
            dest_col = db[dest_col_name]

            docs = await src_col.find().to_list(length=None)
            if docs:
                # Clear destination first to avoid duplicates
                await dest_col.delete_many({})
                await dest_col.insert_many(docs)
                stats[suffix.strip("_")] = len(docs)
                log.info(f"Transferred {len(docs)} docs: {src_col_name} → {dest_col_name}")
            else:
                stats[suffix.strip("_")] = 0

        return stats

    # =========================================================================
    # CLEANUP — Used when deleting a bot
    # =========================================================================

    async def drop_all_collections(self):
        """Drop all collections for this bot. Use when permanently purging a bot."""
        db = get_db()
        prefix = f"bot_{self.bot_id}"
        collections = await db.list_collection_names()
        for col_name in collections:
            if col_name.startswith(prefix + "_"):
                await db.drop_collection(col_name)
                log.info(f"Dropped collection: {col_name}")
