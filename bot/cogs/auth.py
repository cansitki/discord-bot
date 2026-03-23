"""Auth cog — OAuth login/logout commands for Claude authentication.

Provides ``/claude-login`` and ``/claude-logout`` slash commands that let
the bot operator authenticate with their Claude Pro/Max account via OAuth,
eliminating the need for an ``ANTHROPIC_API_KEY``.

The login flow matches Claude Code (pi SDK):
1. Bot generates a PKCE authorization URL
2. User opens the URL in their browser and authorizes
3. User pastes the callback code into the bot
4. Bot exchanges the code for access + refresh tokens
5. Tokens are stored in SQLite and auto-refreshed
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from bot.oauth import OAuthError

if TYPE_CHECKING:
    from bot.bot import DiscordBot

logger = logging.getLogger(__name__)


class AuthCog(commands.Cog):
    """Claude OAuth login/logout commands."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="claude-login",
        description="Authenticate the bot with your Claude Pro/Max account via OAuth",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def claude_login(self, ctx: commands.Context) -> None:
        """Start the Claude OAuth login flow.

        Only server administrators can run this. The flow sends the auth URL
        as an ephemeral DM to avoid leaking it to the channel.
        """
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        if self.bot.oauth_manager is None:
            await ctx.send("OAuth is not available (database not initialized).")
            return

        # Check if already authenticated
        if await self.bot.oauth_manager.has_tokens():
            await ctx.send(
                "✅ Claude OAuth is already configured. "
                "Use `/claude-logout` first if you want to re-authenticate.",
                ephemeral=True,
            )
            return

        # Start OAuth flow
        flow_id, auth_url = self.bot.oauth_manager.start_flow()

        # Build the instruction embed
        embed = discord.Embed(
            title="🔐 Claude OAuth Login",
            description=(
                "Authenticate the bot with your Claude Pro/Max account.\n\n"
                "**Step 1:** Click the link below to authorize:\n"
                f"[Open Claude Authorization]({auth_url})\n\n"
                "**Step 2:** After authorizing, you'll see a code like:\n"
                "`abc123#xyz789`\n\n"
                "**Step 3:** Copy that code and run:\n"
                f"`/claude-callback code:<paste_code_here>`"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="This link expires after one use. Only the code holder can complete login.")

        # Store flow_id in a way the callback can retrieve it.
        # We stash it on the cog instance keyed by guild_id.
        if not hasattr(self, "_active_flows"):
            self._active_flows: dict[int, str] = {}
        self._active_flows[ctx.guild.id] = flow_id

        # Send as ephemeral so only the admin sees the auth URL
        await ctx.send(embed=embed, ephemeral=True)

        logger.info(
            "auth.claude_login: flow started guild_id=%d user_id=%d",
            ctx.guild.id,
            ctx.author.id,
        )

    @commands.hybrid_command(
        name="claude-callback",
        description="Complete Claude OAuth login with the authorization code",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def claude_callback(self, ctx: commands.Context, code: str) -> None:
        """Complete the OAuth flow with the authorization code.

        Args:
            code: The authorization code from the Claude callback (format: code#state).
        """
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        if self.bot.oauth_manager is None:
            await ctx.send("OAuth is not available (database not initialized).")
            return

        # Retrieve the pending flow
        active_flows = getattr(self, "_active_flows", {})
        flow_id = active_flows.pop(ctx.guild.id, None)

        if flow_id is None:
            await ctx.send(
                "❌ No pending login flow found. Run `/claude-login` first.",
                ephemeral=True,
            )
            return

        # Try to delete the message containing the code (it's a secret)
        try:
            if ctx.interaction is None:
                # Prefix command — delete the user's message to hide the code
                await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        # Exchange the code for tokens
        try:
            await self.bot.oauth_manager.complete_flow(flow_id, code)
        except OAuthError as exc:
            await ctx.send(
                f"❌ Login failed: {exc}\n\nPlease run `/claude-login` to try again.",
                ephemeral=True,
            )
            logger.warning(
                "auth.claude_callback: flow failed guild_id=%d error=%s",
                ctx.guild.id,
                exc,
            )
            return

        # Log the action
        if self.bot.db is not None:
            await self.bot.db.execute(
                "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (ctx.guild.id, ctx.author.id, "claude_oauth_login", "bot", "OAuth tokens acquired"),
            )

        await ctx.send(
            "✅ Claude OAuth login successful! The bot is now authenticated "
            "with your Claude account. API key is no longer required.",
            ephemeral=True,
        )

        logger.info(
            "auth.claude_callback: login complete guild_id=%d user_id=%d",
            ctx.guild.id,
            ctx.author.id,
        )

    @commands.hybrid_command(
        name="claude-logout",
        description="Remove stored Claude OAuth tokens",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def claude_logout(self, ctx: commands.Context) -> None:
        """Remove stored OAuth tokens. The bot will fall back to ANTHROPIC_API_KEY."""
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        if self.bot.oauth_manager is None:
            await ctx.send("OAuth is not available (database not initialized).")
            return

        has_tokens = await self.bot.oauth_manager.has_tokens()
        if not has_tokens:
            await ctx.send("No OAuth tokens are stored.", ephemeral=True)
            return

        await self.bot.oauth_manager.clear_tokens()

        # Log the action
        if self.bot.db is not None:
            await self.bot.db.execute(
                "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (ctx.guild.id, ctx.author.id, "claude_oauth_logout", "bot", "OAuth tokens cleared"),
            )

        fallback = " Falling back to API key." if self.bot.config.anthropic_api_key else ""
        await ctx.send(
            f"✅ Claude OAuth tokens have been removed.{fallback}",
            ephemeral=True,
        )

        logger.info(
            "auth.claude_logout: tokens cleared guild_id=%d user_id=%d",
            ctx.guild.id,
            ctx.author.id,
        )

    @commands.hybrid_command(
        name="claude-status",
        description="Check Claude authentication status",
    )
    @commands.guild_only()
    async def claude_status(self, ctx: commands.Context) -> None:
        """Show current Claude authentication method and status."""
        if self.bot.oauth_manager is None:
            await ctx.send("OAuth is not available.", ephemeral=True)
            return

        has_oauth = await self.bot.oauth_manager.has_tokens()
        has_api_key = bool(self.bot.config.anthropic_api_key)

        if has_oauth:
            method = "🔐 OAuth (Claude Pro/Max account)"
            # Try to get a valid token to check expiry
            try:
                token = await self.bot.oauth_manager.get_access_token()
                status = "✅ Active" if token else "⚠️ Token expired"
            except Exception:
                status = "⚠️ Token refresh failed — run `/claude-login` to re-authenticate"
        elif has_api_key:
            method = "🔑 API Key (ANTHROPIC_API_KEY)"
            status = "✅ Configured"
        else:
            method = "❌ None"
            status = "Not authenticated — run `/claude-login` or set ANTHROPIC_API_KEY"

        embed = discord.Embed(
            title="Claude Authentication Status",
            color=discord.Color.green() if has_oauth or has_api_key else discord.Color.red(),
        )
        embed.add_field(name="Method", value=method, inline=False)
        embed.add_field(name="Status", value=status, inline=False)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the AuthCog into the bot."""
    await bot.add_cog(AuthCog(bot))  # type: ignore[arg-type]
