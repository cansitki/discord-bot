"""Assistant cog — general-purpose Claude tools for channel summaries and URL fetching.

Provides two Claude tools:
- ``summarize_channel``: fetches recent channel messages for Claude to summarize
- ``fetch_url``: fetches a URL's text content for Claude to summarize

Implements the **tool-provider protocol** (``get_tools()`` and
``handle_tool_call()``) so that ``ai.py`` can generically discover and
dispatch tools from any cog.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
import httpx
from discord.ext import commands

log = logging.getLogger("assistant")

# ---------------------------------------------------------------------------
# Tool schemas — Anthropic tool format for Claude
# ---------------------------------------------------------------------------

SUMMARIZE_CHANNEL_TOOL: dict[str, Any] = {
    "name": "summarize_channel",
    "description": (
        "Fetch the most recent messages from a Discord channel so you can "
        "summarize the conversation. Use this when the user asks for a "
        "channel summary, recap, or wants to know what was discussed recently."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": (
                    "The Discord channel ID to summarize. If not provided, "
                    "the current channel is used."
                ),
            },
        },
        "required": [],
    },
}

FETCH_URL_TOOL: dict[str, Any] = {
    "name": "fetch_url",
    "description": (
        "Fetch the text content of a URL so you can summarize or answer "
        "questions about it. Use this when the user pastes a link and asks "
        "what it's about, or asks you to read/summarize a web page."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from.",
            },
        },
        "required": ["url"],
    },
}

# Maximum characters of page content to return to Claude
_MAX_CONTENT_CHARS = 10_000

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class AssistantCog(commands.Cog):
    """General-purpose assistant tools — channel summaries and URL fetching."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Tool-provider protocol --------------------------------------------

    def get_tools(self) -> list[dict[str, Any]]:
        """Return tool schemas this cog provides."""
        return [SUMMARIZE_CHANNEL_TOOL, FETCH_URL_TOOL]

    async def handle_tool_call(
        self,
        name: str,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Dispatch a tool call to the correct handler.

        Returns the tool result as a string.  Unknown tool names return
        an error string (never raises).
        """
        if name == "summarize_channel":
            return await self._handle_summarize_channel(tool_input, message)
        elif name == "fetch_url":
            return await self._handle_fetch_url(tool_input)
        else:
            log.warning("assistant.handle_tool_call: unknown tool=%s", name)
            return f"Unknown tool: {name}"

    # -- Summarize channel -------------------------------------------------

    async def _handle_summarize_channel(
        self,
        tool_input: dict[str, Any],
        message: discord.Message,
    ) -> str:
        """Fetch recent messages from a channel and return formatted text.

        Claude will produce the actual summary in its response — this
        method just provides the raw message history.
        """
        channel_id_str = tool_input.get("channel_id")

        if channel_id_str:
            try:
                channel = self.bot.get_channel(int(channel_id_str))
            except (ValueError, TypeError):
                log.warning(
                    "assistant.summarize_channel: invalid channel_id=%s",
                    channel_id_str,
                )
                return "Invalid channel ID provided."
        else:
            channel = message.channel

        if channel is None:
            log.info("assistant.summarize_channel: channel not found")
            return "Could not find that channel."

        try:
            messages: list[discord.Message] = []
            async for msg in channel.history(limit=50):  # type: ignore[union-attr]
                messages.append(msg)

            if not messages:
                return "The channel is empty or I don't have access."

            # history() returns newest-first; reverse for chronological order
            messages.reverse()

            lines = [
                f"[{msg.author.display_name}]: {msg.content}"
                for msg in messages
            ]

            log.info(
                "assistant.summarize_channel: channel_id=%s messages=%d",
                channel.id,
                len(messages),
            )
            return "\n".join(lines)

        except discord.Forbidden:
            log.warning(
                "assistant.summarize_channel: forbidden channel_id=%s",
                channel.id,
            )
            return "I don't have permission to read that channel's history."

    # -- Fetch URL ---------------------------------------------------------

    async def _handle_fetch_url(self, tool_input: dict[str, Any]) -> str:
        """Fetch a URL's text content for Claude to summarize.

        Validates the URL scheme, checks content type, strips HTML tags,
        and caps the response at ``_MAX_CONTENT_CHARS`` characters.
        """
        url = tool_input.get("url", "")

        # Validate URL scheme
        if not url.startswith(("http://", "https://")):
            log.info("assistant.fetch_url: invalid scheme url=%s", url[:100])
            return "Invalid URL — only http:// and https:// URLs are supported."

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": "DiscordBot/1.0"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Check content type
                content_type = response.headers.get("content-type", "")
                if not any(
                    t in content_type
                    for t in ("text/html", "text/plain", "application/json", "text/xml")
                ):
                    log.info(
                        "assistant.fetch_url: unsupported content_type=%s url=%s",
                        content_type,
                        url[:100],
                    )
                    return "I can only read text/HTML content from URLs."

                text = response.text

                # Cap at max characters
                text = text[:_MAX_CONTENT_CHARS]

                # Strip HTML tags and collapse whitespace
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\s+", " ", text).strip()

                log.info(
                    "assistant.fetch_url: success url=%s chars=%d",
                    url[:100],
                    len(text),
                )
                return text

        except httpx.TimeoutException:
            log.warning("assistant.fetch_url: timeout url=%s", url[:100])
            return "The URL took too long to respond."
        except httpx.HTTPStatusError as exc:
            log.warning(
                "assistant.fetch_url: http_error url=%s status=%d",
                url[:100],
                exc.response.status_code,
            )
            return f"The URL returned an error (status {exc.response.status_code})."
        except httpx.RequestError:
            log.warning("assistant.fetch_url: request_error url=%s", url[:100])
            return "Could not fetch the URL."
        except Exception:
            log.exception("assistant.fetch_url: unexpected error url=%s", url[:100])
            return "Something went wrong fetching that URL."


async def setup(bot: commands.Bot) -> None:
    """Load the AssistantCog into the bot."""
    await bot.add_cog(AssistantCog(bot))
