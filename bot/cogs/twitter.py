"""Twitter/X feed watcher — polls watched profiles and posts new tweets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.models import TwitterFeed

if TYPE_CHECKING:
    from bot.bot import DiscordBot

log = logging.getLogger(__name__)

try:
    from twikit import Client as TwikitClient

    TWIKIT_AVAILABLE = True
except ImportError:
    TWIKIT_AVAILABLE = False
    TwikitClient = None  # type: ignore[assignment,misc]


class TwitterCog(commands.Cog):
    """Watch Twitter/X profiles and post new tweets to Discord channels."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self._client: TwikitClient | None = None  # type: ignore[assignment]
        self._authenticated = False

    async def cog_load(self) -> None:
        if not TWIKIT_AVAILABLE:
            log.warning("twikit not installed — TwitterCog disabled")
            return
        cfg = self.bot.config
        has_cookie_auth = cfg.twitter_auth_token and cfg.twitter_ct0
        has_password_auth = cfg.twitter_username and cfg.twitter_password
        if not (has_cookie_auth or has_password_auth):
            log.info("Twitter credentials not configured — TwitterCog polling disabled")
            return
        self._client = TwikitClient("en-US")
        await self._try_auth()
        self.poll_feeds.start()
        log.info("Twitter feed poller started (5 min interval)")

    async def cog_unload(self) -> None:
        if self.poll_feeds.is_running():
            self.poll_feeds.cancel()

    # ── Authentication ───────────────────────────────────────────────

    async def _try_auth(self) -> None:
        """Authenticate using cookie env vars, saved cookie file, or password login."""
        if self._client is None:
            return

        cfg = self.bot.config
        if cfg.twitter_auth_token and cfg.twitter_ct0:
            try:
                self._client.set_cookies({
                    "auth_token": cfg.twitter_auth_token,
                    "ct0": cfg.twitter_ct0,
                })
                self._authenticated = True
                log.info("Twitter authenticated via cookie env vars")
                return
            except Exception:
                log.warning("Failed to set Twitter cookies from env, trying fallbacks")

        cookies_path = cfg.twitter_cookies_path
        if Path(cookies_path).exists():
            try:
                self._client.load_cookies(cookies_path)
                self._authenticated = True
                log.info("Twitter cookies loaded from %s", cookies_path)
                return
            except Exception:
                log.warning("Failed to load Twitter cookies, will re-login")

        await self._login()

    async def _login(self) -> None:
        if self._client is None:
            return
        cfg = self.bot.config
        try:
            await self._client.login(
                auth_info_1=cfg.twitter_username,
                auth_info_2=cfg.twitter_email,
                password=cfg.twitter_password,
            )
            Path(cfg.twitter_cookies_path).parent.mkdir(parents=True, exist_ok=True)
            self._client.save_cookies(cfg.twitter_cookies_path)
            self._authenticated = True
            log.info("Twitter login successful, cookies saved")
        except Exception:
            log.exception("Twitter login failed")
            self._authenticated = False

    # ── Polling loop ─────────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def poll_feeds(self) -> None:
        if not self._authenticated or self._client is None:
            return
        db = self.bot.db
        if db is None:
            return

        rows = await db.fetchall("SELECT * FROM twitter_feeds")
        feeds = [TwitterFeed.from_row(r) for r in rows]

        for feed in feeds:
            try:
                await self._check_feed(feed)
            except Exception as exc:
                if "unauthorized" in str(exc).lower() or "401" in str(exc):
                    log.warning("Twitter auth expired, re-authenticating")
                    await self._try_auth()
                    if self._authenticated:
                        try:
                            await self._check_feed(feed)
                        except Exception:
                            log.exception("Feed check failed after re-auth: @%s", feed.twitter_handle)
                else:
                    log.exception("Error checking feed: @%s", feed.twitter_handle)

    @poll_feeds.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_feed(self, feed: TwitterFeed) -> None:
        """Fetch latest tweets for a feed and post any new ones."""
        if self._client is None:
            return
        user = await self._client.get_user_by_screen_name(feed.twitter_handle)
        tweets = await user.get_tweets("Tweets", count=5)

        new_tweets = []
        for tweet in tweets:
            if feed.last_tweet_id and int(tweet.id) <= int(feed.last_tweet_id):
                break
            new_tweets.append(tweet)

        if not new_tweets:
            return

        new_tweets.reverse()

        channel = self.bot.get_channel(feed.channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        for tweet in new_tweets:
            url = f"https://fxtwitter.com/{feed.twitter_handle}/status/{tweet.id}"
            await channel.send(
                f"**@{feed.twitter_handle}** posted:\n{url}"
            )

        latest_id = new_tweets[-1].id
        db = self.bot.db
        if db is not None:
            await db.execute(
                "UPDATE twitter_feeds SET last_tweet_id = ? WHERE id = ?",
                (str(latest_id), feed.id),
            )

    # ── Slash commands ───────────────────────────────────────────────

    @app_commands.command(name="watch", description="Watch a Twitter/X profile in this channel")
    @app_commands.describe(handle="Twitter/X username (without @)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def watch(self, interaction: discord.Interaction, handle: str) -> None:
        handle = handle.lstrip("@").lower()
        if not handle:
            await interaction.response.send_message("Please provide a valid handle.", ephemeral=True)
            return

        db = self.bot.db
        if db is None:
            await interaction.response.send_message("Database not available.", ephemeral=True)
            return

        existing = await db.fetchone(
            "SELECT id FROM twitter_feeds WHERE guild_id = ? AND channel_id = ? AND twitter_handle = ?",
            (interaction.guild_id, interaction.channel_id, handle),
        )
        if existing:
            await interaction.response.send_message(
                f"Already watching **@{handle}** in this channel.", ephemeral=True
            )
            return

        await db.execute(
            "INSERT INTO twitter_feeds (guild_id, channel_id, twitter_handle, created_by) VALUES (?, ?, ?, ?)",
            (interaction.guild_id, interaction.channel_id, handle, interaction.user.id),
        )

        embed = discord.Embed(
            title="Twitter Feed Added",
            description=f"Now watching **@{handle}** in this channel.\nNew tweets will appear here every ~5 minutes.",
            colour=discord.Colour.blue(),
        )
        await interaction.response.send_message(embed=embed)

        if not TWIKIT_AVAILABLE or not self._authenticated:
            await interaction.followup.send(
                "Note: Twitter client is not authenticated. "
                "Feeds are saved but won't poll until credentials are configured.",
                ephemeral=True,
            )

        await self._log_action(
            interaction.guild_id, interaction.user.id, "twitter_watch", handle
        )

    @app_commands.command(name="unwatch", description="Stop watching a Twitter/X profile in this channel")
    @app_commands.describe(handle="Twitter/X username (without @)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unwatch(self, interaction: discord.Interaction, handle: str) -> None:
        handle = handle.lstrip("@").lower()
        db = self.bot.db
        if db is None:
            await interaction.response.send_message("Database not available.", ephemeral=True)
            return

        cursor = await db.execute(
            "DELETE FROM twitter_feeds WHERE guild_id = ? AND channel_id = ? AND twitter_handle = ?",
            (interaction.guild_id, interaction.channel_id, handle),
        )
        if cursor.rowcount == 0:
            await interaction.response.send_message(
                f"Not watching **@{handle}** in this channel.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Twitter Feed Removed",
            description=f"Stopped watching **@{handle}** in this channel.",
            colour=discord.Colour.orange(),
        )
        await interaction.response.send_message(embed=embed)
        await self._log_action(
            interaction.guild_id, interaction.user.id, "twitter_unwatch", handle
        )

    @app_commands.command(name="watchlist", description="List all watched Twitter/X profiles in this server")
    async def watchlist(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        if db is None:
            await interaction.response.send_message("Database not available.", ephemeral=True)
            return

        rows = await db.fetchall(
            "SELECT twitter_handle, channel_id FROM twitter_feeds WHERE guild_id = ? ORDER BY twitter_handle",
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message("No watched Twitter profiles in this server.", ephemeral=True)
            return

        lines = [f"**@{r['twitter_handle']}** in <#{r['channel_id']}>" for r in rows]
        embed = discord.Embed(
            title="Watched Twitter Profiles",
            description="\n".join(lines),
            colour=discord.Colour.blue(),
        )
        status = "active" if self._authenticated else "inactive (no credentials)"
        embed.set_footer(text=f"Polling: {status} | Interval: 5 min")
        await interaction.response.send_message(embed=embed)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _log_action(self, guild_id: int, user_id: int, action: str, target: str) -> None:
        db = self.bot.db
        if db is None:
            return
        await db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, action, target),
        )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(TwitterCog(bot))
