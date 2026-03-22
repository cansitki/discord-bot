"""Tests for bot.cogs.assistant — tool schemas, channel summary, URL fetch, protocol."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx
import pytest
from discord.ext import commands

from bot.cogs.assistant import (
    FETCH_URL_TOOL,
    SUMMARIZE_CHANNEL_TOOL,
    AssistantCog,
    _MAX_CONTENT_CHARS,
)
from bot.config import Config


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def mock_bot() -> MagicMock:
    """Create a mock bot with the attributes AssistantCog needs."""
    bot = MagicMock(spec=commands.Bot)
    bot.get_channel = MagicMock(return_value=None)
    return bot


@pytest.fixture()
def cog(mock_bot: MagicMock) -> AssistantCog:
    """Create an AssistantCog with a mocked bot."""
    return AssistantCog(mock_bot)


def _make_message(
    *,
    channel_id: int = 444555666,
    guild_id: int = 111222333,
) -> MagicMock:
    """Build a mock discord.Message."""
    msg = MagicMock(spec=discord.Message)
    msg.channel = MagicMock(spec=discord.TextChannel)
    msg.channel.id = channel_id
    msg.channel.history = MagicMock()

    if guild_id:
        msg.guild = MagicMock(spec=discord.Guild)
        msg.guild.id = guild_id
    else:
        msg.guild = None

    return msg


def _make_discord_message(
    *,
    author_name: str = "Alice",
    content: str = "Hello!",
) -> MagicMock:
    """Build a mock discord.Message returned from channel.history()."""
    msg = MagicMock(spec=discord.Message)
    msg.author = MagicMock()
    msg.author.display_name = author_name
    msg.content = content
    return msg


def _make_httpx_response(
    *,
    status_code: int = 200,
    text: str = "<html><body>Hello World</body></html>",
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    """Build a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    response.headers = {"content-type": content_type}
    response.raise_for_status = MagicMock()
    return response


# ── Tool schema tests ────────────────────────────────────────────────


class TestToolSchemas:
    """Tool schema dicts conform to Anthropic tool format."""

    def test_summarize_channel_has_required_keys(self) -> None:
        assert "name" in SUMMARIZE_CHANNEL_TOOL
        assert "description" in SUMMARIZE_CHANNEL_TOOL
        assert "input_schema" in SUMMARIZE_CHANNEL_TOOL

    def test_summarize_channel_name(self) -> None:
        assert SUMMARIZE_CHANNEL_TOOL["name"] == "summarize_channel"

    def test_summarize_channel_schema_has_channel_id(self) -> None:
        schema = SUMMARIZE_CHANNEL_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "channel_id" in schema["properties"]

    def test_fetch_url_has_required_keys(self) -> None:
        assert "name" in FETCH_URL_TOOL
        assert "description" in FETCH_URL_TOOL
        assert "input_schema" in FETCH_URL_TOOL

    def test_fetch_url_name(self) -> None:
        assert FETCH_URL_TOOL["name"] == "fetch_url"

    def test_fetch_url_schema_requires_url(self) -> None:
        schema = FETCH_URL_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "url" in schema["required"]


# ── Protocol tests ───────────────────────────────────────────────────


class TestToolProviderProtocol:
    """AssistantCog implements get_tools() and handle_tool_call() protocol."""

    def test_get_tools_returns_both_schemas(self, cog: AssistantCog) -> None:
        tools = cog.get_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"summarize_channel", "fetch_url"}

    @pytest.mark.asyncio()
    async def test_handle_tool_call_dispatches_summarize(
        self, cog: AssistantCog, mock_bot: MagicMock
    ) -> None:
        """handle_tool_call routes 'summarize_channel' to the handler."""
        message = _make_message()

        # Set up channel with messages
        mock_msg = _make_discord_message(author_name="Bob", content="Hi there")

        async def _fake_history(limit=50):
            for m in [mock_msg]:
                yield m

        message.channel.history = _fake_history

        result = await cog.handle_tool_call("summarize_channel", {}, message)
        assert "[Bob]: Hi there" in result

    @pytest.mark.asyncio()
    async def test_handle_tool_call_dispatches_fetch_url(
        self, cog: AssistantCog
    ) -> None:
        """handle_tool_call routes 'fetch_url' to the handler."""
        message = _make_message()
        mock_resp = _make_httpx_response(text="<p>Test content</p>")

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog.handle_tool_call(
                "fetch_url", {"url": "https://example.com"}, message
            )

        assert "Test content" in result

    @pytest.mark.asyncio()
    async def test_handle_tool_call_unknown_tool(self, cog: AssistantCog) -> None:
        """handle_tool_call returns error for unknown tool names."""
        message = _make_message()
        result = await cog.handle_tool_call("nonexistent_tool", {}, message)
        assert "Unknown tool" in result


# ── Summarize channel tests ──────────────────────────────────────────


class TestSummarizeChannel:
    """_handle_summarize_channel covers normal, empty, not-found, Forbidden."""

    @pytest.mark.asyncio()
    async def test_normal_channel_with_messages(
        self, cog: AssistantCog, mock_bot: MagicMock
    ) -> None:
        """Channel with messages returns formatted text."""
        message = _make_message()

        msg1 = _make_discord_message(author_name="Alice", content="Hello!")
        msg2 = _make_discord_message(author_name="Bob", content="How are you?")

        # history() returns newest first, so msg2 comes before msg1
        async def _fake_history(limit=50):
            for m in [msg2, msg1]:
                yield m

        channel = MagicMock()
        channel.id = 999
        channel.history = _fake_history
        mock_bot.get_channel = MagicMock(return_value=channel)

        result = await cog._handle_summarize_channel(
            {"channel_id": "999"}, message
        )

        # Should be in chronological order (reversed from newest-first)
        lines = result.split("\n")
        assert lines[0] == "[Alice]: Hello!"
        assert lines[1] == "[Bob]: How are you?"

    @pytest.mark.asyncio()
    async def test_empty_channel(
        self, cog: AssistantCog, mock_bot: MagicMock
    ) -> None:
        """Empty channel returns appropriate message."""
        message = _make_message()

        async def _fake_history(limit=50):
            return
            yield  # make it an async generator

        channel = MagicMock()
        channel.id = 999
        channel.history = _fake_history
        mock_bot.get_channel = MagicMock(return_value=channel)

        result = await cog._handle_summarize_channel(
            {"channel_id": "999"}, message
        )
        assert "empty" in result.lower() or "access" in result.lower()

    @pytest.mark.asyncio()
    async def test_channel_not_found(
        self, cog: AssistantCog, mock_bot: MagicMock
    ) -> None:
        """Channel not found returns error."""
        message = _make_message()
        mock_bot.get_channel = MagicMock(return_value=None)

        result = await cog._handle_summarize_channel(
            {"channel_id": "999999"}, message
        )
        assert "Could not find" in result

    @pytest.mark.asyncio()
    async def test_discord_forbidden(
        self, cog: AssistantCog, mock_bot: MagicMock
    ) -> None:
        """discord.Forbidden → returns permission error."""
        message = _make_message()

        async def _raise_forbidden(limit=50):
            raise discord.Forbidden(MagicMock(), "Missing permissions")
            yield  # pragma: no cover

        channel = MagicMock()
        channel.id = 999
        channel.history = _raise_forbidden
        mock_bot.get_channel = MagicMock(return_value=channel)

        result = await cog._handle_summarize_channel(
            {"channel_id": "999"}, message
        )
        assert "permission" in result.lower()

    @pytest.mark.asyncio()
    async def test_default_channel_when_no_channel_id(
        self, cog: AssistantCog
    ) -> None:
        """When no channel_id in input, uses message.channel."""
        message = _make_message()

        msg1 = _make_discord_message(author_name="Eve", content="Testing")

        async def _fake_history(limit=50):
            for m in [msg1]:
                yield m

        message.channel.history = _fake_history
        message.channel.id = 444555666

        result = await cog._handle_summarize_channel({}, message)
        assert "[Eve]: Testing" in result

    @pytest.mark.asyncio()
    async def test_invalid_channel_id(
        self, cog: AssistantCog
    ) -> None:
        """Invalid channel_id (not a number) returns error."""
        message = _make_message()
        result = await cog._handle_summarize_channel(
            {"channel_id": "not-a-number"}, message
        )
        assert "Invalid" in result or "Could not find" in result


# ── Fetch URL tests ──────────────────────────────────────────────────


class TestFetchUrl:
    """_handle_fetch_url covers success, errors, and edge cases."""

    @pytest.mark.asyncio()
    async def test_normal_html_page(self, cog: AssistantCog) -> None:
        """Normal HTML page returns stripped text content."""
        mock_resp = _make_httpx_response(
            text="<html><body><h1>Title</h1><p>Content here</p></body></html>"
        )

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://example.com"})

        assert "Title" in result
        assert "Content here" in result
        # HTML tags should be stripped
        assert "<html>" not in result
        assert "<p>" not in result

    @pytest.mark.asyncio()
    async def test_timeout(self, cog: AssistantCog) -> None:
        """Timeout returns appropriate message."""
        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(
                side_effect=httpx.TimeoutException("timed out")
            )
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://slow.example.com"})

        assert "too long" in result.lower()

    @pytest.mark.asyncio()
    async def test_non_http_url(self, cog: AssistantCog) -> None:
        """Non-HTTP URL returns validation error."""
        result = await cog._handle_fetch_url({"url": "ftp://files.example.com"})
        assert "http" in result.lower()

    @pytest.mark.asyncio()
    async def test_empty_url(self, cog: AssistantCog) -> None:
        """Empty URL returns validation error."""
        result = await cog._handle_fetch_url({"url": ""})
        assert "http" in result.lower() or "Invalid" in result

    @pytest.mark.asyncio()
    async def test_non_text_content_type(self, cog: AssistantCog) -> None:
        """Non-text content type returns appropriate message."""
        mock_resp = _make_httpx_response(
            text="binary data",
            content_type="application/pdf",
        )

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://example.com/file.pdf"})

        assert "text/HTML" in result or "text" in result.lower()

    @pytest.mark.asyncio()
    async def test_large_response_truncated(self, cog: AssistantCog) -> None:
        """Response exceeding _MAX_CONTENT_CHARS is truncated."""
        large_text = "a" * (_MAX_CONTENT_CHARS + 5000)
        mock_resp = _make_httpx_response(
            text=large_text,
            content_type="text/plain",
        )

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://example.com/long"})

        assert len(result) <= _MAX_CONTENT_CHARS

    @pytest.mark.asyncio()
    async def test_http_error_status(self, cog: AssistantCog) -> None:
        """HTTP error status returns status error message."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404

        error = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_resp
        )

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            mock_resp.raise_for_status = MagicMock(side_effect=error)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://example.com/missing"})

        assert "error" in result.lower()
        assert "404" in result

    @pytest.mark.asyncio()
    async def test_request_error(self, cog: AssistantCog) -> None:
        """Network request error returns appropriate message."""
        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(
                side_effect=httpx.RequestError("Connection refused")
            )
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://down.example.com"})

        assert "Could not fetch" in result

    @pytest.mark.asyncio()
    async def test_plain_text_content_type_accepted(self, cog: AssistantCog) -> None:
        """text/plain content type is accepted."""
        mock_resp = _make_httpx_response(
            text="Plain text content here",
            content_type="text/plain",
        )

        with patch("bot.cogs.assistant.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await cog._handle_fetch_url({"url": "https://example.com/text.txt"})

        assert "Plain text content here" in result


# ── Module setup test ────────────────────────────────────────────────


class TestSetup:
    """Module-level setup function loads the cog."""

    @pytest.mark.asyncio()
    async def test_setup_adds_cog(self) -> None:
        """setup() calls bot.add_cog with an AssistantCog instance."""
        from bot.cogs.assistant import setup

        mock_bot = MagicMock(spec=commands.Bot)
        mock_bot.add_cog = AsyncMock()

        await setup(mock_bot)

        mock_bot.add_cog.assert_awaited_once()
        added_cog = mock_bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, AssistantCog)
