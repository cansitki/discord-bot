"""Discord bot subclass with async initialization and clean shutdown."""

from __future__ import annotations

import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from bot.config import Config
from bot.database import DatabaseManager
from bot.oauth import OAuthManager

log = logging.getLogger(__name__)


class DiscordBot(commands.Bot):
    """Bot subclass that wires config, database, and cog loading.

    Attributes:
        config: Frozen Config dataclass with validated env vars.
        db: DatabaseManager instance, created in setup_hook.
        oauth_manager: OAuthManager for Claude OAuth auth, created in setup_hook.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.db: DatabaseManager | None = None
        self.oauth_manager: OAuthManager | None = None
        self._on_ready_fired = False
        self._webhook_runner: web.AppRunner | None = None

        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.guild_messages = True

        super().__init__(
            command_prefix=config.command_prefix,
            intents=intents,
            help_command=None,  # Disable built-in help; we use /help from HelpCog
        )

    async def setup_hook(self) -> None:
        """Connect database, run migrations, load cogs, sync command tree."""
        # Database initialization
        self.db = DatabaseManager(self.config.database_path)
        await self.db.connect()
        await self.db.run_migrations("migrations")
        log.info("Database ready: %s", self.config.database_path)

        # OAuth manager (for Claude OAuth authentication)
        self.oauth_manager = OAuthManager(self.db)

        # Load cogs
        await self.load_extension("bot.cogs.ping")
        await self.load_extension("bot.cogs.verification")
        await self.load_extension("bot.cogs.ai")
        await self.load_extension("bot.cogs.server_design")
        await self.load_extension("bot.cogs.assistant")
        await self.load_extension("bot.cogs.auth")
        await self.load_extension("bot.cogs.github")
        await self.load_extension("bot.cogs.github_auth")
        await self.load_extension("bot.cogs.help")
        await self.load_extension("bot.cogs.twitter")
        log.info("Cogs loaded: ping, verification, ai, server_design, assistant, auth, github, github_auth, help, twitter")

        # Register dynamic items so persistent buttons survive restarts
        from bot.cogs.verification import ApproveButton, DenyButton
        from bot.cogs.server_design import DesignApproveButton, DesignCancelButton
        from bot.cogs.github import IssueApproveButton, IssueCancelButton

        self.add_dynamic_items(
            ApproveButton, DenyButton,
            DesignApproveButton, DesignCancelButton,
            IssueApproveButton, IssueCancelButton,
        )

        # Sync command tree — guild-specific for fast dev, global for prod
        dev_guild_id = os.getenv("DEV_GUILD_ID")
        if dev_guild_id:
            guild = discord.Object(id=int(dev_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("Command tree synced to dev guild: %s (global cleared)", dev_guild_id)
        else:
            await self.tree.sync()
            log.info("Command tree synced globally")

        # Webhook server — start only when secret is configured
        if self.config.github_webhook_secret:
            from bot.webhook import create_webhook_app

            self._webhook_app = create_webhook_app(self)
            self._webhook_runner = web.AppRunner(self._webhook_app)
            await self._webhook_runner.setup()
            port = int(os.getenv("PORT", "8080"))
            site = web.TCPSite(self._webhook_runner, "0.0.0.0", port)
            await site.start()
            log.info("Webhook server started on port %d", port)
        else:
            log.info("Webhook server not started — GITHUB_WEBHOOK_SECRET not configured")

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
        """Close database connection, shut down webhook server, then shut down the bot."""
        if self._webhook_runner is not None:
            await self._webhook_runner.cleanup()
            log.info("Webhook server stopped")
        if self.db is not None:
            await self.db.close()
            log.info("Database closed during shutdown")
        await super().close()
