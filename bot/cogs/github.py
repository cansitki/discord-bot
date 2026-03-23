"""GitHub cog — channel-to-repo linking and natural language issue creation.

Lets users bind a Discord channel to a GitHub repository.  Provides a
``create_issue`` Claude tool: when invoked, the cog renders a preview
embed with Approve / Cancel DynamicItem buttons.  On approval, the
issue is created via ``GitHubClient.create_issue()`` and an action_log
entry is written.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord
from discord import Interaction
from discord.ext import commands

from bot.github_client import GitHubAPIError, GitHubClient

if TYPE_CHECKING:
    from bot.bot import DiscordBot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema — Anthropic tool format for Claude
# ---------------------------------------------------------------------------

CREATE_ISSUE_TOOL: dict[str, Any] = {
    "name": "create_issue",
    "description": (
        "Create a GitHub issue in the repository linked to this channel. "
        "Use when a user describes a bug, feature request, or task that "
        "should be tracked as a GitHub issue."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "A concise issue title",
            },
            "body": {
                "type": "string",
                "description": "Detailed issue description in markdown",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels to apply (e.g. 'bug', 'enhancement')",
            },
        },
        "required": ["title", "body"],
    },
}


# ---------------------------------------------------------------------------
# DynamicItem buttons — survive bot restarts
# ---------------------------------------------------------------------------


class IssueApproveButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"issue:approve:(?P<guild_id>\d+):(?P<msg_id>\d+)",
):
    """Green approve button for issue creation previews."""

    def __init__(self, guild_id: int, msg_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Approve",
                style=discord.ButtonStyle.green,
                custom_id=f"issue:approve:{guild_id}:{msg_id}",
            )
        )
        self.guild_id = guild_id
        self.msg_id = msg_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> IssueApproveButton:
        guild_id = int(match.group("guild_id"))
        msg_id = int(match.group("msg_id"))
        return cls(guild_id, msg_id)

    async def callback(self, interaction: Interaction) -> None:
        """Look up pending issue, verify requester, create on GitHub."""
        assert interaction.guild is not None
        assert interaction.user is not None

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        cog: GitHubCog | None = bot.get_cog("GitHubCog")  # type: ignore[assignment]

        if cog is None:
            await interaction.response.send_message(
                "GitHub issue system is not available.", ephemeral=True
            )
            return

        key = (self.guild_id, self.msg_id)
        pending = cog._pending_issues.get(key)

        if pending is None:
            await interaction.response.send_message(
                "This issue preview has expired.", ephemeral=True
            )
            return

        # Only the original requester can approve
        if interaction.user.id != pending["user_id"]:
            await interaction.response.send_message(
                "Only the requester can approve this issue.",
                ephemeral=True,
            )
            return

        # Remove from pending
        del cog._pending_issues[key]

        log.info(
            "github_cog.approve: guild_id=%s user_id=%s msg_id=%s title=%s",
            self.guild_id,
            interaction.user.id,
            self.msg_id,
            pending["title"],
        )

        try:
            result = await cog.github_client.create_issue(
                owner=pending["repo_owner"],
                repo=pending["repo_name"],
                title=pending["title"],
                body=pending["body"],
                labels=pending["labels"] or None,
            )
            issue_number = result["number"]
            html_url = result["html_url"]

            # Write action_log entry
            db = bot.db  # type: ignore[attr-defined]
            await db.execute(
                "INSERT INTO action_log (guild_id, user_id, action_type, target, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    self.guild_id,
                    interaction.user.id,
                    "issue_created",
                    f"{pending['repo_owner']}/{pending['repo_name']}#{issue_number}",
                    html_url,
                ),
            )

            await interaction.response.edit_message(
                content=f"✅ Issue created: [{pending['title']}]({html_url})",
                embed=None,
                view=None,
            )

        except GitHubAPIError as exc:
            log.error(
                "github_cog.approve: create_issue failed guild_id=%s status=%d msg=%s",
                self.guild_id,
                exc.status_code,
                exc.message,
            )
            await interaction.response.edit_message(
                content=f"❌ Failed to create issue: {exc.message} (HTTP {exc.status_code})",
                embed=None,
                view=None,
            )


class IssueCancelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"issue:cancel:(?P<guild_id>\d+):(?P<msg_id>\d+)",
):
    """Red cancel button for issue creation previews."""

    def __init__(self, guild_id: int, msg_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Cancel",
                style=discord.ButtonStyle.red,
                custom_id=f"issue:cancel:{guild_id}:{msg_id}",
            )
        )
        self.guild_id = guild_id
        self.msg_id = msg_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> IssueCancelButton:
        guild_id = int(match.group("guild_id"))
        msg_id = int(match.group("msg_id"))
        return cls(guild_id, msg_id)

    async def callback(self, interaction: Interaction) -> None:
        """Remove the pending issue and update the message."""
        assert interaction.guild is not None
        assert interaction.user is not None

        bot: commands.Bot = interaction.client  # type: ignore[assignment]
        cog: GitHubCog | None = bot.get_cog("GitHubCog")  # type: ignore[assignment]

        if cog is not None:
            key = (self.guild_id, self.msg_id)
            cog._pending_issues.pop(key, None)

        log.info(
            "github_cog.cancel: guild_id=%s user_id=%s msg_id=%s",
            self.guild_id,
            interaction.user.id,
            self.msg_id,
        )

        await interaction.response.edit_message(
            content="❌ Issue creation cancelled.",
            embed=None,
            view=None,
        )


def make_issue_view(guild_id: int, msg_id: int) -> discord.ui.View:
    """Create a persistent View with Approve/Cancel buttons."""
    view = discord.ui.View(timeout=None)
    view.add_item(IssueApproveButton(guild_id, msg_id))
    view.add_item(IssueCancelButton(guild_id, msg_id))
    return view


class GitHubCog(commands.Cog):
    """Channel-repo linking and GitHub tool provider."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.github_client: GitHubClient | None = None
        self._pending_issues: dict[tuple[int, int], dict[str, Any]] = {}

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

        Returns the create_issue tool when github_client is configured,
        otherwise an empty list.
        """
        if self.github_client is not None:
            return [CREATE_ISSUE_TOOL]
        return []

    async def handle_tool_call(
        self,
        name: str,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Dispatch a tool call to the correct handler."""
        if name == "create_issue":
            return await self._handle_create_issue(tool_input, message)
        return f"Unknown tool: {name}"

    # ------------------------------------------------------------------
    # Issue creation handler
    # ------------------------------------------------------------------

    async def _handle_create_issue(
        self,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Look up linked repo, render preview embed, store pending issue.

        Returns a confirmation string for Claude to relay.
        """
        if message.guild is None:
            return "This command can only be used in a server."

        guild_id = message.guild.id
        channel_id = message.channel.id

        # Look up the channel's linked repo
        row = await self.bot.db.fetchone(
            "SELECT repo_owner, repo_name FROM channel_repos "
            "WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        if row is None:
            return (
                "This channel isn't linked to a GitHub repo. "
                "Use /link-repo to set one up."
            )

        repo_owner = row["repo_owner"]
        repo_name = row["repo_name"]

        title = tool_input["title"]
        body = tool_input["body"]
        labels = tool_input.get("labels", [])

        # Build preview embed
        display_body = body if len(body) <= 200 else body[:200] + "..."
        embed = discord.Embed(
            title=title,
            description=display_body,
            colour=discord.Colour.blue(),
        )
        embed.add_field(
            name="Labels",
            value=", ".join(labels) if labels else "None",
            inline=False,
        )
        embed.set_footer(text=f"Repository: {repo_owner}/{repo_name}")

        # Send embed — need the sent message ID for button custom_ids
        sent = await message.channel.send(embed=embed)

        # Store pending issue
        self._pending_issues[(guild_id, sent.id)] = {
            "title": title,
            "body": body,
            "labels": labels,
            "repo_owner": repo_owner,
            "repo_name": repo_name,
            "user_id": message.author.id,
        }

        # Attach buttons with correct message ID
        view = make_issue_view(guild_id, sent.id)
        await sent.edit(view=view)

        log.info(
            "github_cog.create_issue: guild_id=%d channel_id=%d title=%s repo=%s/%s",
            guild_id,
            channel_id,
            title,
            repo_owner,
            repo_name,
        )

        return (
            f"I've posted an issue preview for **{title}** targeting "
            f"`{repo_owner}/{repo_name}`. Click **Approve** to create "
            f"it on GitHub or **Cancel** to discard."
        )

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
