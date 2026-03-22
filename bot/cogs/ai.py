"""AI cog — routes messages to Claude and delivers responses.

Listens for ``on_message`` events and routes to Claude when the bot is
mentioned or the message is in the guild's configured AI channel.
Handles typing indicator, response chunking (Discord 2000-char limit),
error display, and the ``/ai-channel`` admin command.

Any cog that implements the **tool-provider protocol** (``get_tools()``
and ``handle_tool_call()``) automatically has its tools included in
Claude API calls.  Claude decides autonomously when to use them based
on the user's intent.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.claude import ClaudeClient

if TYPE_CHECKING:
    from bot.bot import DiscordBot

logger = logging.getLogger(__name__)


class AICog(commands.Cog):
    """Discord-facing integration that routes messages to Claude."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.claude_client = ClaudeClient(
            api_key=bot.config.anthropic_api_key,
            model=bot.config.claude_model,
        )

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Route bot-directed messages to Claude."""
        # Guard: ignore own messages
        if message.author == self.bot.user:
            return

        # Guard: ignore other bots
        if message.author.bot:
            return

        # Guard: ignore DMs (no guild)
        if message.guild is None:
            return

        # Determine if message is bot-directed
        is_mention = self.bot.user in message.mentions
        is_ai_channel = await self._is_ai_channel(
            message.guild.id, message.channel.id
        )

        if not is_mention and not is_ai_channel:
            return

        logger.info(
            "ai_cog.route: guild_id=%d channel_id=%d user_id=%d is_mention=%s is_ai_channel=%s",
            message.guild.id,
            message.channel.id,
            message.author.id,
            is_mention,
            is_ai_channel,
        )

        # Strip bot mention from content so Claude sees clean text
        clean_content = message.content
        if is_mention and self.bot.user:
            clean_content = re.sub(
                rf"<@!?{self.bot.user.id}>",
                "",
                clean_content,
            ).strip()

        # If content is empty after stripping, provide a default prompt
        if not clean_content:
            clean_content = "Hello!"

        # Build tool list and executor from any cog implementing the
        # tool-provider protocol (get_tools / handle_tool_call)
        tools: list[dict[str, Any]] = []
        tool_cogs: list[Any] = []
        for cog_instance in self.bot.cogs.values():
            if cog_instance is self:
                continue  # skip AICog itself
            if hasattr(cog_instance, "get_tools") and hasattr(cog_instance, "handle_tool_call"):
                cog_tools = cog_instance.get_tools()
                if cog_tools:
                    tools.extend(cog_tools)
                    tool_cogs.append(cog_instance)

        tool_executor = None
        max_tokens = 1024
        if tools:
            max_tokens = 4096

            async def _tool_executor(
                tool_name: str, tool_input: dict[str, Any]
            ) -> str:
                for tc in tool_cogs:
                    tool_names = [t["name"] for t in tc.get_tools()]
                    if tool_name in tool_names:
                        return await tc.handle_tool_call(tool_name, tool_input, message)
                return f"Unknown tool: {tool_name}"

            tool_executor = _tool_executor
        else:
            tools = None  # type: ignore[assignment]

        try:
            async with message.channel.typing():
                response = await self.claude_client.ask(
                    clean_content,
                    tools=tools,
                    tool_executor=tool_executor,
                    max_tokens=max_tokens,
                )

            # Send response using chunking helper
            chunks = self._chunk_response(response)
            for chunk in chunks:
                await message.channel.send(chunk)

            logger.info(
                "ai_cog.response: guild_id=%d channel_id=%d chunks=%d total_len=%d",
                message.guild.id,
                message.channel.id,
                len(chunks),
                len(response),
            )
        except Exception:
            logger.exception(
                "ai_cog.error: guild_id=%d channel_id=%d user_id=%d",
                message.guild.id,
                message.channel.id,
                message.author.id,
            )
            try:
                await message.channel.send(
                    "Sorry, something went wrong while processing your message."
                )
            except Exception:
                logger.exception("ai_cog.error: failed to send error message")

    # ------------------------------------------------------------------
    # AI channel lookup
    # ------------------------------------------------------------------

    async def _is_ai_channel(self, guild_id: int, channel_id: int) -> bool:
        """Check if *channel_id* is the configured AI channel for *guild_id*."""
        if self.bot.db is None:
            return False

        row = await self.bot.db.fetchone(
            "SELECT ai_channel_id FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        if row is None:
            return False

        return row["ai_channel_id"] == channel_id

    # ------------------------------------------------------------------
    # Response chunking
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_response(text: str) -> list[str]:
        """Split *text* into chunks of ≤2000 chars for Discord.

        Splitting priority:
        1. Paragraph boundaries (``\\n\\n``)
        2. Newline boundaries (``\\n``)
        3. Word boundaries (space)
        4. Hard-cut at 2000 chars (last resort)
        """
        if len(text) <= 2000:
            return [text]

        chunks: list[str] = []
        paragraphs = text.split("\n\n")
        current = ""

        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para

            if len(candidate) <= 2000:
                current = candidate
                continue

            # Current chunk is full — flush it if non-empty
            if current:
                chunks.append(current)
                current = ""

            # This paragraph itself may exceed 2000 chars
            if len(para) <= 2000:
                current = para
            else:
                # Split on newline boundaries
                lines = para.split("\n")
                for line in lines:
                    line_candidate = f"{current}\n{line}" if current else line

                    if len(line_candidate) <= 2000:
                        current = line_candidate
                        continue

                    if current:
                        chunks.append(current)
                        current = ""

                    if len(line) <= 2000:
                        current = line
                    else:
                        # Hard-split at 2000 chars, prefer word boundary
                        while len(line) > 2000:
                            split_at = line.rfind(" ", 0, 2000)
                            if split_at == -1:
                                split_at = 2000
                            chunks.append(line[:split_at])
                            line = line[split_at:].lstrip()
                        if line:
                            current = line

        # Flush remaining content
        if current:
            chunks.append(current)

        return chunks

    # ------------------------------------------------------------------
    # /ai-channel command
    # ------------------------------------------------------------------

    @commands.hybrid_command(
        name="ai-channel",
        description="Set or clear the AI response channel for this server",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def ai_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        """Set or clear the AI channel. Only users with Manage Channels can use this."""
        if ctx.guild is None or self.bot.db is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id

        if channel is not None:
            await self.bot.db.execute(
                "INSERT INTO guild_config (guild_id, prefix, ai_channel_id) "
                "VALUES (?, '!', ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET ai_channel_id = excluded.ai_channel_id",
                (guild_id, channel.id),
            )
            logger.info(
                "ai_cog.ai_channel: guild_id=%d set ai_channel_id=%d",
                guild_id,
                channel.id,
            )
            await ctx.send(f"AI channel set to {channel.mention}.")
        else:
            await self.bot.db.execute(
                "INSERT INTO guild_config (guild_id, prefix, ai_channel_id) "
                "VALUES (?, '!', NULL) "
                "ON CONFLICT(guild_id) DO UPDATE SET ai_channel_id = NULL",
                (guild_id,),
            )
            logger.info(
                "ai_cog.ai_channel: guild_id=%d cleared ai_channel_id",
                guild_id,
            )
            await ctx.send("AI channel cleared. I'll only respond to mentions now.")


async def setup(bot: commands.Bot) -> None:
    """Load the AICog into the bot (called by ``load_extension``)."""
    await bot.add_cog(AICog(bot))  # type: ignore[arg-type]
