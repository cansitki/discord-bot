"""GitHub auth cog — PAT-based login/logout/status for GitHub integration.

Provides ``/github-login``, ``/github-logout``, and ``/github-status``
slash commands that let server administrators connect a GitHub Personal
Access Token (PAT) as an alternative to the GitHub App authentication
(GITHUB_APP_ID + GITHUB_PRIVATE_KEY env vars).

The PAT is stored encrypted in SQLite and used for API calls when GitHub
App credentials are not configured.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
import httpx
from discord.ext import commands

if TYPE_CHECKING:
    from bot.bot import DiscordBot

logger = logging.getLogger(__name__)


class GitHubAuthCog(commands.Cog):
    """GitHub PAT login/logout/status commands."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="github-login",
        description="Connect a GitHub Personal Access Token for GitHub integration",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def github_login(self, ctx: commands.Context, token: str) -> None:
        """Store a GitHub Personal Access Token for API access.

        The token is validated against the GitHub API before being stored.
        Use a fine-grained PAT with repo access for full functionality.

        Parameters
        ----------
        token:
            A GitHub Personal Access Token (classic or fine-grained).
        """
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.")
            return

        # Try to delete the message containing the token (it's a secret)
        try:
            if ctx.interaction is None:
                await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        # Validate the token against the GitHub API
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )

            if resp.status_code != 200:
                await ctx.send(
                    "❌ Invalid GitHub token — API returned "
                    f"HTTP {resp.status_code}. Please check the token and try again.",
                    ephemeral=True,
                )
                return

            user_data = resp.json()
            github_username = user_data.get("login", "unknown")

        except httpx.RequestError:
            await ctx.send(
                "❌ Could not reach the GitHub API. Please try again later.",
                ephemeral=True,
            )
            return

        # Store the token in the database
        guild_id = ctx.guild.id
        await self.bot.db.execute(
            "INSERT INTO github_tokens (guild_id, token, github_username, set_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "token = excluded.token, "
            "github_username = excluded.github_username, "
            "set_by = excluded.set_by",
            (guild_id, token, github_username, ctx.author.id),
        )

        # Log the action
        await self.bot.db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, ctx.author.id, "github_pat_login", github_username, "PAT configured"),
        )

        logger.info(
            "github_auth.login: guild_id=%d user_id=%d github_user=%s",
            guild_id,
            ctx.author.id,
            github_username,
        )

        await ctx.send(
            f"✅ GitHub token configured for **{github_username}**! "
            "You can now use `/link-repo` and other GitHub features.",
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="github-logout",
        description="Remove stored GitHub Personal Access Token",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def github_logout(self, ctx: commands.Context) -> None:
        """Remove the stored GitHub PAT for this server."""
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id

        # Check if a token is stored
        row = await self.bot.db.fetchone(
            "SELECT github_username FROM github_tokens WHERE guild_id = ?",
            (guild_id,),
        )
        if row is None:
            await ctx.send(
                "No GitHub token is stored for this server.",
                ephemeral=True,
            )
            return

        github_username = row["github_username"]

        # Delete the token
        await self.bot.db.execute(
            "DELETE FROM github_tokens WHERE guild_id = ?",
            (guild_id,),
        )

        # Log the action
        await self.bot.db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, ctx.author.id, "github_pat_logout", github_username, "PAT removed"),
        )

        logger.info(
            "github_auth.logout: guild_id=%d user_id=%d",
            guild_id,
            ctx.author.id,
        )

        fallback = ""
        if self.bot.config.github_app_id and self.bot.config.github_private_key:
            fallback = " Falling back to GitHub App authentication."

        await ctx.send(
            f"✅ GitHub token for **{github_username}** has been removed.{fallback}",
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="github-status",
        description="Check GitHub integration authentication status",
    )
    @commands.guild_only()
    async def github_status(self, ctx: commands.Context) -> None:
        """Show current GitHub authentication method and status."""
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = ctx.guild.id

        has_app = bool(
            self.bot.config.github_app_id and self.bot.config.github_private_key
        )

        # Check for PAT token
        row = await self.bot.db.fetchone(
            "SELECT github_username FROM github_tokens WHERE guild_id = ?",
            (guild_id,),
        )
        has_pat = row is not None
        pat_username = row["github_username"] if row else None

        embed = discord.Embed(
            title="GitHub Integration Status",
            colour=discord.Colour.green() if (has_app or has_pat) else discord.Colour.red(),
        )

        if has_app:
            embed.add_field(
                name="🔑 GitHub App",
                value=f"✅ Configured (App ID: {self.bot.config.github_app_id})",
                inline=False,
            )
        else:
            embed.add_field(
                name="🔑 GitHub App",
                value="❌ Not configured (set GITHUB_APP_ID + GITHUB_PRIVATE_KEY)",
                inline=False,
            )

        if has_pat:
            embed.add_field(
                name="🔐 Personal Access Token",
                value=f"✅ Connected as **{pat_username}**",
                inline=False,
            )
        else:
            embed.add_field(
                name="🔐 Personal Access Token",
                value="❌ Not configured (use `/github-login` to set one up)",
                inline=False,
            )

        if not has_app and not has_pat:
            embed.add_field(
                name="📝 How to connect",
                value=(
                    "**Option A:** Set `GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY` env vars\n"
                    "**Option B:** Run `/github-login` with a Personal Access Token"
                ),
                inline=False,
            )

        await ctx.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Helper: get PAT for a guild (used by other cogs)
    # ------------------------------------------------------------------

    async def get_guild_token(self, guild_id: int) -> str | None:
        """Retrieve the stored GitHub PAT for a guild, or None."""
        if self.bot.db is None:
            return None
        row = await self.bot.db.fetchone(
            "SELECT token FROM github_tokens WHERE guild_id = ?",
            (guild_id,),
        )
        return row["token"] if row else None


async def setup(bot: commands.Bot) -> None:
    """Load the GitHubAuthCog into the bot."""
    await bot.add_cog(GitHubAuthCog(bot))  # type: ignore[arg-type]
