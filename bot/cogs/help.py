"""Help cog — comprehensive command listing.

Provides a ``/help`` slash command that displays all available bot commands
grouped by category with descriptions, permissions, and usage hints.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from bot.bot import DiscordBot

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command metadata — grouped by category
# ---------------------------------------------------------------------------

COMMAND_CATEGORIES: list[dict] = [
    {
        "name": "🤖 AI & Chat",
        "commands": [
            {
                "name": "/ask",
                "description": "Ask the AI a question directly",
                "permissions": "Everyone",
            },
            {
                "name": "/ai-channel",
                "description": "Set or clear the channel where the bot responds to all messages",
                "permissions": "Manage Channels",
            },
            {
                "name": "@mention",
                "description": "Mention the bot in any channel to ask it a question",
                "permissions": "Everyone",
            },
        ],
    },
    {
        "name": "🔐 Authentication",
        "commands": [
            {
                "name": "/claude-login",
                "description": "Authenticate the bot with your Claude Pro/Max account via OAuth",
                "permissions": "Administrator",
            },
            {
                "name": "/claude-callback",
                "description": "Complete Claude OAuth login with the authorization code",
                "permissions": "Administrator",
            },
            {
                "name": "/claude-logout",
                "description": "Remove stored Claude OAuth tokens",
                "permissions": "Administrator",
            },
            {
                "name": "/claude-status",
                "description": "Check Claude authentication status",
                "permissions": "Everyone",
            },
            {
                "name": "/github-login",
                "description": "Connect a GitHub Personal Access Token for GitHub integration",
                "permissions": "Administrator",
            },
            {
                "name": "/github-logout",
                "description": "Remove stored GitHub Personal Access Token",
                "permissions": "Administrator",
            },
            {
                "name": "/github-status",
                "description": "Check GitHub integration authentication status",
                "permissions": "Everyone",
            },
        ],
    },
    {
        "name": "🐙 GitHub Integration",
        "commands": [
            {
                "name": "/link-repo",
                "description": "Link a Discord channel to a GitHub repository (owner/repo)",
                "permissions": "Manage Channels",
            },
            {
                "name": "/unlink-repo",
                "description": "Unlink the current channel from its GitHub repository",
                "permissions": "Manage Channels",
            },
            {
                "name": "/repo-status",
                "description": "Show open PRs and recent commits for the linked repository",
                "permissions": "Everyone",
            },
        ],
    },
    {
        "name": "🛡️ Verification",
        "commands": [
            {
                "name": "/verify-setup",
                "description": "Set up the member verification gate for this server",
                "permissions": "Administrator",
            },
        ],
    },
    {
        "name": "🏗️ Server Design",
        "commands": [
            {
                "name": "AI Tool",
                "description": "Ask the AI to design your server layout (e.g. \"set up a gaming server\")",
                "permissions": "Everyone (approve requires Admin)",
            },
        ],
    },
    {
        "name": "🔧 Utility",
        "commands": [
            {
                "name": "/ping",
                "description": "Check bot latency",
                "permissions": "Everyone",
            },
            {
                "name": "/help",
                "description": "Show this help message",
                "permissions": "Everyone",
            },
        ],
    },
]


class HelpCog(commands.Cog):
    """Comprehensive help command that lists all bot features."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="help",
        description="Show all available bot commands and features",
    )
    @commands.guild_only()
    async def help_command(self, ctx: commands.Context) -> None:
        """Display an embed listing every bot command grouped by category."""
        embed = discord.Embed(
            title="📖 Bot Commands",
            description=(
                "Here are all available commands and features. "
                "You can also **@mention** me in any channel or use the "
                "configured AI channel to chat directly."
            ),
            colour=discord.Colour.blurple(),
        )

        for category in COMMAND_CATEGORIES:
            lines: list[str] = []
            for cmd in category["commands"]:
                perm = cmd["permissions"]
                perm_badge = f" 🔒 *{perm}*" if perm != "Everyone" else ""
                lines.append(f"**{cmd['name']}** — {cmd['description']}{perm_badge}")

            embed.add_field(
                name=category["name"],
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(
            text="💡 Tip: Commands marked with 🔒 require specific permissions."
        )

        await ctx.send(embed=embed, ephemeral=True)

        log.info(
            "help.show: guild_id=%d user_id=%d",
            ctx.guild.id if ctx.guild else 0,
            ctx.author.id,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the HelpCog into the bot."""
    await bot.add_cog(HelpCog(bot))  # type: ignore[arg-type]
