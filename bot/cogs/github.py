"""GitHub cog — channel-to-repo linking via /link-repo and /unlink-repo.

Lets users bind a Discord channel to a GitHub repository.  The bot
validates the repo exists (via GitHubClient) before storing the
channel_repos binding.  Implements the tool-provider protocol
(get_tools / handle_tool_call) for AICog discovery — currently returns
an empty tool list; S02 adds create_issue.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.github_client import GitHubAPIError, GitHubClient

if TYPE_CHECKING:
    from bot.bot import DiscordBot

log = logging.getLogger(__name__)


class GitHubCog(commands.Cog):
    """Channel-repo linking and GitHub tool provider."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.github_client: GitHubClient | None = None

        # Only initialise GitHubClient when both required config values are present
        if bot.config.github_app_id and bot.config.github_private_key:
            self.github_client = GitHubClient(
                app_id=bot.config.github_app_id,
                private_key=bot.config.github_private_key,
            )
            log.info(
                "GitHubCog initialised with GitHub App (app_id=%s)",
                bot.config.github_app_id,
            )
        else:
            log.warning(
                "GitHubCog initialised without GitHub App — "
                "GITHUB_APP_ID and/or GITHUB_PRIVATE_KEY not configured"
            )

    # ------------------------------------------------------------------
    # Tool-provider protocol (AICog discovery)
    # ------------------------------------------------------------------

    def get_tools(self) -> list[dict[str, Any]]:
        """Return tool schemas this cog provides.

        Empty for S01; S02 adds the create_issue tool.
        """
        return []

    async def handle_tool_call(
        self,
        name: str,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Dispatch a tool call. Returns an error for unknown tools."""
        return f"Unknown tool: {name}"

    # ------------------------------------------------------------------
    # /link-repo
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="link-repo",
        description="Link this channel to a GitHub repository (owner/repo)",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def link_repo(self, ctx: commands.Context, repo: str) -> None:
        """Link the current channel to a GitHub repository.

        Parameters
        ----------
        repo:
            Repository in ``owner/repo`` format.
        """
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id
        channel_id = ctx.channel.id

        # Validate format — must be exactly "owner/repo"
        parts = repo.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            await ctx.send(
                f"❌ Invalid format: `{repo}`. Use `owner/repo` (e.g. `octocat/Hello-World`)."
            )
            return

        owner, repo_name = parts

        # Check GitHub config
        if self.github_client is None:
            await ctx.send(
                "❌ GitHub integration is not configured. "
                "Set GITHUB_APP_ID and GITHUB_PRIVATE_KEY."
            )
            return

        # Validate that the repo exists on GitHub
        try:
            await self.github_client.get_repo(owner, repo_name)
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                await ctx.send(
                    f"❌ Repository `{owner}/{repo_name}` not found or not accessible."
                )
            else:
                log.error(
                    "github_cog.link_repo: API error guild_id=%d channel_id=%d repo=%s status=%d msg=%s",
                    guild_id,
                    channel_id,
                    repo,
                    exc.status_code,
                    exc.message,
                )
                await ctx.send(
                    f"❌ GitHub API error ({exc.status_code}): {exc.message}"
                )
            return

        # Check if channel is already linked
        existing = await self.bot.db.fetchone(
            "SELECT repo_owner, repo_name FROM channel_repos "
            "WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        if existing is not None:
            existing_full = f"{existing['repo_owner']}/{existing['repo_name']}"
            await ctx.send(
                f"❌ This channel is already linked to `{existing_full}`. "
                "Run `/unlink-repo` first."
            )
            return

        # Insert the binding
        await self.bot.db.execute(
            "INSERT INTO channel_repos (guild_id, channel_id, repo_owner, repo_name, linked_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, owner, repo_name, ctx.author.id),
        )

        # Write action_log entry
        await self.bot.db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild_id,
                ctx.author.id,
                "repo_linked",
                f"{owner}/{repo_name}",
                f"channel_id={channel_id}",
            ),
        )

        log.info(
            "github_cog.link_repo: guild_id=%d channel_id=%d repo=%s/%s user_id=%d",
            guild_id,
            channel_id,
            owner,
            repo_name,
            ctx.author.id,
        )

        # Send confirmation embed
        embed = discord.Embed(
            description=f"✅ Linked #{ctx.channel.name} to `{owner}/{repo_name}`",
            colour=discord.Colour.green(),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # /unlink-repo
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="unlink-repo",
        description="Unlink this channel from its GitHub repository",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def unlink_repo(self, ctx: commands.Context) -> None:
        """Unlink the current channel from its GitHub repository."""
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id
        channel_id = ctx.channel.id

        # Check if channel is linked
        existing = await self.bot.db.fetchone(
            "SELECT repo_owner, repo_name FROM channel_repos "
            "WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        if existing is None:
            await ctx.send("❌ This channel is not linked to any repository.")
            return

        repo_full_name = f"{existing['repo_owner']}/{existing['repo_name']}"

        # Delete the binding
        await self.bot.db.execute(
            "DELETE FROM channel_repos WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )

        # Write action_log entry
        await self.bot.db.execute(
            "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                guild_id,
                ctx.author.id,
                "repo_unlinked",
                repo_full_name,
                f"channel_id={channel_id}",
            ),
        )

        log.info(
            "github_cog.unlink_repo: guild_id=%d channel_id=%d repo=%s user_id=%d",
            guild_id,
            channel_id,
            repo_full_name,
            ctx.author.id,
        )

        # Send confirmation embed
        embed = discord.Embed(
            description=f"✅ Unlinked #{ctx.channel.name} from `{repo_full_name}`",
            colour=discord.Colour.green(),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Load the GitHubCog into the bot."""
    await bot.add_cog(GitHubCog(bot))  # type: ignore[arg-type]
