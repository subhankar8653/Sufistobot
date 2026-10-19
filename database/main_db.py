#@suhanibots
"""
main_db.py — Database operations for the main controller bot.

Manages the registry of user-created bots and main bot users.
"""

from datetime import datetime, timezone
from database.mongo import get_db
from config import LOGGER
#@suhanibots
log = LOGGER(__name__)

#@suhanibots
class MainDB:
    """Database operations for the main controller bot."""

    def __init__(self):
        db = get_db()
        self.bots = db["registered_bots"]
        self.users = db["main_users"]
        self.cooldowns = db["creation_cooldowns"]

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

    async def add_user(self, user_id: int) -> bool:
        """Add a user to the main bot's user list. Returns True if new."""
        existing = await self.users.find_one({"_id": user_id})
        if existing:
            return False
        await self.users.insert_one({
            "_id": user_id,
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def user_exists(self, user_id: int) -> bool:
        """Check if a user exists in the main bot's user list."""
        return bool(await self.users.find_one({"_id": user_id}))

    async def get_all_users(self) -> list:
        """Return all user IDs."""
        docs = await self.users.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def total_users(self) -> int:
        """Return total user count."""
        return await self.users.count_documents({})

    async def ban_user_exist(self, user_id: int) -> bool:
        doc = await self.users.find_one({"_id": user_id})
        return doc.get("banned", False) if doc else False

    async def add_ban_user(self, user_id: int):
        await self.users.update_one({"_id": user_id}, {"$set": {"banned": True}}, upsert=True)

    async def del_ban_user(self, user_id: int):
        await self.users.update_one({"_id": user_id}, {"$unset": {"banned": ""}})

    # =========================================================================
    # BOT REGISTRATION
    # =========================================================================

    async def add_bot(
        self,
        bot_id: int,
        owner_id: int,
        bot_token_encrypted: str,
        bot_username: str,
        log_channel_id: int,
    ) -> dict:
        """Register a new user-created bot."""
        # If this bot is being re-registered, keep its admins and the history of
        # its older log channels (old links must keep working).
        prev = await self.bots.find_one({"_id": bot_id})
        previous_channels = list(prev.get("previous_log_channels", [])) if prev else []
        if prev and prev.get("log_channel_id") and prev["log_channel_id"] != log_channel_id:
            if prev["log_channel_id"] not in previous_channels:
                previous_channels.append(prev["log_channel_id"])
        previous_channels = [c for c in previous_channels if c != log_channel_id]
        doc = {
            "admin_ids": list(prev.get("admin_ids", [])) if prev else [],
            "previous_log_channels": previous_channels,
            "_id": bot_id,
            "owner_id": owner_id,
            "bot_token_encrypted": bot_token_encrypted,
            "bot_username": bot_username,
            "log_channel_id": log_channel_id,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "settings": {
                "auto_delete_time": 0,
                "protect_content": False,
                "custom_caption": "",
                "start_pic": "",
                "start_message": "",
                "force_pic": "",
                "permanent_link": False,
            },
            "shortener": {
                "enabled": False,
                "api_key_encrypted": "",
                "domain": "",
            },
        }
        await self.bots.replace_one({"_id": bot_id}, doc, upsert=True)
        log.info(f"Bot @{bot_username} (ID:{bot_id}) registered by user {owner_id}")
        return doc

    async def get_bot(self, bot_id: int) -> dict | None:
        """Get a registered bot by its bot user ID."""
        return await self.bots.find_one({"_id": bot_id})

    async def get_bot_by_token(self, token_encrypted: str) -> dict | None:
        """Get a bot by its encrypted token."""
        return await self.bots.find_one({"bot_token_encrypted": token_encrypted})

    # ---- access model -------------------------------------------------------
    # A user can see/manage a bot if they are its OWNER or one of its ADMINS.
    # `admin_ids` on the registry doc mirrors the per-bot admins collection so
    # one query can list every bot a user can access.

    @staticmethod
    def can_manage(bot: dict, user_id: int) -> bool:
        """Owner or admin of this bot."""
        if not bot:
            return False
        return bot.get("owner_id") == user_id or user_id in (bot.get("admin_ids") or [])

    @staticmethod
    def is_owner(bot: dict, user_id: int) -> bool:
        return bool(bot) and bot.get("owner_id") == user_id

    async def get_user_bots(self, user_id: int) -> list:
        """ALL active (non-deleted) bots the user owns OR administers."""
        cursor = self.bots.find({
            "$or": [{"owner_id": user_id}, {"admin_ids": user_id}],
            "is_deleted": {"$ne": True},
        })
        return await cursor.to_list(length=None)

    async def count_user_bots(self, user_id: int) -> int:
        """Universal bot count for the 5-bot limit: owned + administered
        (so adding a second account as admin can never be used to dodge the limit)."""
        return await self.bots.count_documents({
            "$or": [{"owner_id": user_id}, {"admin_ids": user_id}],
            "is_deleted": {"$ne": True},
        })

    async def add_bot_admin(self, bot_id: int, admin_id: int):
        await self.bots.update_one({"_id": bot_id}, {"$addToSet": {"admin_ids": int(admin_id)}})

    async def remove_bot_admin(self, bot_id: int, admin_id: int):
        await self.bots.update_one({"_id": bot_id}, {"$pull": {"admin_ids": int(admin_id)}})

    async def sync_bot_admins(self, bot_id: int):
        """Rebuild admin_ids of one bot from its per-bot admins collection."""
        db = get_db()
        docs = await db[f"bot_{bot_id}_admins"].find().to_list(length=None)
        ids = [d["_id"] for d in docs if isinstance(d.get("_id"), int)]
        await self.bots.update_one({"_id": bot_id}, {"$set": {"admin_ids": ids}})

    async def sync_admin_ids(self):
        """Startup backfill: make sure every bot's admin_ids matches reality."""
        try:
            async for bot in self.bots.find({}, {"_id": 1}):
                await self.sync_bot_admins(bot["_id"])
        except Exception as e:
            log.error(f"sync_admin_ids failed: {e}")

    async def get_deleted_user_bots(self, owner_id: int) -> list:
        """Get all soft-deleted bots owned by a user (for data transfer)."""
        cursor = self.bots.find({"owner_id": owner_id, "is_deleted": True})
        return await cursor.to_list(length=None)

    async def get_all_active_bots(self) -> list:
        """Get all active bots for worker engine startup."""
        cursor = self.bots.find({"is_active": True})
        return await cursor.to_list(length=None)

    async def delete_bot(self, bot_id: int) -> bool:
        """Soft-delete a bot (keeps data for transfer)."""
        result = await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"is_deleted": True, "is_active": False}},
        )
        if result.modified_count > 0:
            log.info(f"Bot ID:{bot_id} soft-deleted (data preserved)")
            return True
        return False

    async def purge_bot(self, bot_id: int) -> bool:
        """Permanently delete a bot record from the registry."""
        result = await self.bots.delete_one({"_id": bot_id})
        if result.deleted_count > 0:
            log.info(f"Bot ID:{bot_id} permanently purged")
            return True
        return False

    async def set_bot_active(self, bot_id: int, active: bool):
        """Enable or disable a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"is_active": active}},
        )

    async def update_log_channel(self, bot_id: int, channel_id: int):
        """Change a bot's log channel. The OLD channel id is remembered, because
        every file link encodes (message_id x channel_id) and old links must keep
        resolving against the channel they were created for."""
        doc = await self.bots.find_one({"_id": bot_id})
        old = doc.get("log_channel_id") if doc else None
        await self.bots.update_one({"_id": bot_id}, {"$set": {"log_channel_id": channel_id}})
        await self.bots.update_one({"_id": bot_id}, {"$pull": {"previous_log_channels": channel_id}})
        if old and old != channel_id:
            await self.bots.update_one({"_id": bot_id}, {"$addToSet": {"previous_log_channels": old}})

    async def add_old_log_channels(self, bot_id: int, channel_ids: list):
        """Register older log channels so links made for them still work."""
        doc = await self.bots.find_one({"_id": bot_id})
        cur = doc.get("log_channel_id") if doc else None
        ids = [int(c) for c in channel_ids if c and int(c) != cur]
        if ids:
            await self.bots.update_one(
                {"_id": bot_id}, {"$addToSet": {"previous_log_channels": {"$each": ids}}}
            )

    async def get_old_log_channels(self, bot_id: int) -> list:
        doc = await self.bots.find_one({"_id": bot_id})
        return list(doc.get("previous_log_channels", [])) if doc else []

    async def update_last_active(self, bot_id: int):
        """Update the last_active timestamp of a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"last_active": datetime.now(timezone.utc)}},
        )

    # =========================================================================
    # BOT SETTINGS
    # =========================================================================

    async def update_setting(self, bot_id: int, key: str, value):
        """Update a single setting for a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {f"settings.{key}": value}},
        )

    async def get_settings(self, bot_id: int) -> dict:
        """Get all settings for a bot."""
        doc = await self.get_bot(bot_id)
        return doc.get("settings", {}) if doc else {}

    # =========================================================================
    # SHORTENER CONFIG
    # =========================================================================

    async def update_shortener(self, bot_id: int, key: str, value):
        """Update a shortener setting for a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {f"shortener.{key}": value}},
        )

    async def get_shortener(self, bot_id: int) -> dict:
        """Get shortener config for a bot."""
        doc = await self.get_bot(bot_id)
        return doc.get("shortener", {}) if doc else {}

    # =========================================================================
    # RATE LIMITING / COOLDOWN
    # =========================================================================

    async def set_cooldown(self, user_id: int):
        """Set a bot creation cooldown timestamp for a user."""
        await self.cooldowns.replace_one(
            {"_id": user_id},
            {"_id": user_id, "timestamp": datetime.now(timezone.utc)},
            upsert=True,
        )

    async def get_cooldown(self, user_id: int) -> datetime | None:
        """Get the last bot creation timestamp for a user."""
        doc = await self.cooldowns.find_one({"_id": user_id})
        return doc["timestamp"] if doc else None
#@suhanibots