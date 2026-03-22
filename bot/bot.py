"""Discord bot subclass with async initialization and clean shutdown."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from bot.config import Config
from bot.database import DatabaseManager

log = logging.getLogger(__name__)


class DiscordBot(commands.Bot):
    """Bot subclass that wires config, database, and cog loading.

    Attributes:
        config: Frozen Config dataclass with validated env vars.
        db: DatabaseManager instance, created in setup_hook.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.db: DatabaseManager | None = None
        self._on_ready_fired = False

        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.guild_messages = True

        super().__init__(
            command_prefix=config.command_prefix,
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """Connect database, run migrations, load cogs, sync command tree."""
        # Database initialization
        self.db = DatabaseManager(self.config.database_path)
        await self.db.connect()
        await self.db.run_migrations("migrations")
        log.info("Database ready: %s", self.config.database_path)

        # Load cogs
        await self.load_extension("bot.cogs.ping")
        await self.load_extension("bot.cogs.verification")
        await self.load_extension("bot.cogs.ai")
        await self.load_extension("bot.cogs.server_design")
        await self.load_extension("bot.cogs.assistant")
        log.info("Cogs loaded: ping, verification, ai, server_design, assistant")

        # Register dynamic items so persistent buttons survive restarts
        from bot.cogs.verification import ApproveButton, DenyButton
        from bot.cogs.server_design import DesignApproveButton, DesignCancelButton

        self.add_dynamic_items(ApproveButton, DenyButton, DesignApproveButton, DesignCancelButton)

        # Sync command tree — guild-specific for fast dev, global for prod
        dev_guild_id = os.getenv("DEV_GUILD_ID")
        if dev_guild_id:
            guild = discord.Object(id=int(dev_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Command tree synced to dev guild: %s", dev_guild_id)
        else:
            await self.tree.sync()
            log.info("Command tree synced globally")

    async def on_ready(self) -> None:
        """Log bot identity on first ready event; ignore reconnect re-fires."""
        if self._on_ready_fired:
            return
        self._on_ready_fired = True

        user = self.user
        guild_count = len(self.guilds)
        db_status = "connected" if self.db and self.db._conn else "disconnected"

        print(f"Bot online: {user} (guilds: {guild_count}, db: {db_status})")
        log.info(
            "Ready: %s#%s | guilds=%d | db=%s",
            user.name if user else "unknown",
            user.discriminator if user else "0",
            guild_count,
            db_status,
        )

    async def on_message(self, message: discord.Message) -> None:
        """Ignore own messages, then route to command processing."""
        if message.author == self.user:
            return
        await self.process_commands(message)

    async def close(self) -> None:
        """Close database connection, then shut down the bot."""
        if self.db is not None:
            await self.db.close()
            log.info("Database closed during shutdown")
        await super().close()
