"""Tests for bot.cogs.ai (AICog) — routing, chunking, error handling, /ai-channel."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from bot.cogs.ai import AICog
from bot.config import Config


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def mock_config(tmp_path: Path) -> Config:
    """Return a Config instance for testing (no real secrets)."""
    return Config(
        discord_bot_token="test-token-do-not-use",
        anthropic_api_key="test-api-key-do-not-use",
        database_path=str(tmp_path / "test.db"),
        command_prefix="!",
        claude_model="claude-sonnet-4-20250514",
    )


@pytest.fixture()
def mock_bot(mock_config: Config) -> MagicMock:
    """Create a mock DiscordBot with the attributes AICog needs."""
    bot = MagicMock()
    bot.config = mock_config
    bot.user = MagicMock(spec=discord.ClientUser)
    bot.user.id = 123456789
    bot.user.bot = True
    bot.db = AsyncMock()
    # By default no optional cogs are loaded — empty cogs dict
    bot.cogs = {}
    bot.get_cog = MagicMock(return_value=None)
    return bot


@pytest.fixture()
def cog(mock_bot: MagicMock) -> AICog:
    """Create an AICog with a mocked bot and mocked ClaudeClient."""
    with patch("bot.cogs.ai.ClaudeClient") as MockClaude:
        mock_claude_instance = AsyncMock()
        MockClaude.return_value = mock_claude_instance
        ai_cog = AICog(mock_bot)
    return ai_cog


def _make_message(
    *,
    author_id: int = 987654321,
    author_bot: bool = False,
    guild_id: int | None = 111222333,
    channel_id: int = 444555666,
    content: str = "Hello bot!",
    mentions: list | None = None,
) -> MagicMock:
    """Build a mock discord.Message with the given properties."""
    msg = MagicMock(spec=discord.Message)
    msg.author = MagicMock(spec=discord.Member)
    msg.author.id = author_id
    msg.author.bot = author_bot
    msg.content = content

    if guild_id is not None:
        msg.guild = MagicMock(spec=discord.Guild)
        msg.guild.id = guild_id
    else:
        msg.guild = None

    msg.channel = MagicMock(spec=discord.TextChannel)
    msg.channel.id = channel_id
    msg.channel.send = AsyncMock()
    msg.channel.typing = MagicMock(return_value=AsyncMock())

    msg.mentions = mentions or []

    return msg


# ── Routing tests ────────────────────────────────────────────────────


class TestMessageRouting:
    """AICog.on_message routes only bot-directed messages to Claude."""

    @pytest.mark.asyncio()
    async def test_mention_routes_to_claude(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Message mentioning the bot → Claude is called."""
        message = _make_message(
            content=f"<@{mock_bot.user.id}> What is Python?",
            mentions=[mock_bot.user],
        )
        # Not an AI channel
        mock_bot.db.fetchone = AsyncMock(return_value=None)

        cog.claude_client.ask = AsyncMock(return_value="Python is a programming language.")

        await cog.on_message(message)

        cog.claude_client.ask.assert_awaited_once()
        # The mention should be stripped from the content sent to Claude
        call_args = cog.claude_client.ask.call_args[0][0]
        assert f"<@{mock_bot.user.id}>" not in call_args
        assert "What is Python?" in call_args
        message.channel.send.assert_awaited_once_with("Python is a programming language.")

    @pytest.mark.asyncio()
    async def test_ai_channel_routes_to_claude(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Message in AI channel → Claude is called."""
        message = _make_message(content="Tell me a joke")
        # Configure the channel as the AI channel
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: message.channel.id if key == "ai_channel_id" else None
        mock_bot.db.fetchone = AsyncMock(return_value=mock_row)

        cog.claude_client.ask = AsyncMock(return_value="Why did the chicken cross the road?")

        await cog.on_message(message)

        cog.claude_client.ask.assert_awaited_once()
        call_args = cog.claude_client.ask.call_args
        assert call_args[0][0] == "Tell me a joke"
        message.channel.send.assert_awaited_once_with("Why did the chicken cross the road?")

    @pytest.mark.asyncio()
    async def test_non_directed_message_ignored(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Message not mentioning bot and not in AI channel → Claude NOT called."""
        message = _make_message(content="Just chatting with friends")
        # Not an AI channel
        mock_bot.db.fetchone = AsyncMock(return_value=None)

        cog.claude_client.ask = AsyncMock()

        await cog.on_message(message)

        cog.claude_client.ask.assert_not_awaited()
        message.channel.send.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_bot_own_message_ignored(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Bot's own message → ignored (no self-reply loop)."""
        message = _make_message()
        message.author = mock_bot.user

        cog.claude_client.ask = AsyncMock()

        await cog.on_message(message)

        cog.claude_client.ask.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_other_bot_message_ignored(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Other bot's message → ignored."""
        message = _make_message(author_bot=True)

        cog.claude_client.ask = AsyncMock()

        await cog.on_message(message)

        cog.claude_client.ask.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_dm_message_ignored(self, cog: AICog, mock_bot: MagicMock) -> None:
        """DM message (no guild) → ignored."""
        message = _make_message(guild_id=None)

        cog.claude_client.ask = AsyncMock()

        await cog.on_message(message)

        cog.claude_client.ask.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_typing_indicator_shown(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Typing indicator is shown during Claude call."""
        message = _make_message(
            content=f"<@{mock_bot.user.id}> hi",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hello!")

        await cog.on_message(message)

        # typing() context manager should have been entered
        message.channel.typing.assert_called_once()

    @pytest.mark.asyncio()
    async def test_mention_stripped_with_exclamation(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Bot mention with nickname format <@!id> is stripped."""
        message = _make_message(
            content=f"<@!{mock_bot.user.id}> What's up?",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Not much!")

        await cog.on_message(message)

        call_args = cog.claude_client.ask.call_args[0][0]
        assert f"<@!{mock_bot.user.id}>" not in call_args
        assert "What's up?" in call_args

    @pytest.mark.asyncio()
    async def test_empty_content_after_mention_sends_default(self, cog: AICog, mock_bot: MagicMock) -> None:
        """When content is empty after stripping mention, a default prompt is used."""
        message = _make_message(
            content=f"<@{mock_bot.user.id}>",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hi there!")

        await cog.on_message(message)

        call_args = cog.claude_client.ask.call_args[0][0]
        assert call_args == "Hello!"


# ── Response chunking tests ──────────────────────────────────────────


class TestResponseChunking:
    """AICog._chunk_response splits text at Discord's 2000-char limit."""

    def test_short_response_single_chunk(self) -> None:
        """Response ≤2000 chars → single chunk."""
        text = "Hello, world!"
        chunks = AICog._chunk_response(text)
        assert chunks == ["Hello, world!"]

    def test_exactly_2000_chars_single_chunk(self) -> None:
        """Response of exactly 2000 chars → single chunk."""
        text = "a" * 2000
        chunks = AICog._chunk_response(text)
        assert len(chunks) == 1
        assert len(chunks[0]) == 2000

    def test_long_response_multiple_chunks(self) -> None:
        """Response >2000 chars → multiple chunks, each ≤2000."""
        # Create text with paragraph breaks that exceeds 2000 chars
        paragraphs = ["This is paragraph number {}.".format(i) * 10 for i in range(20)]
        text = "\n\n".join(paragraphs)
        assert len(text) > 2000

        chunks = AICog._chunk_response(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 2000

    def test_split_prefers_paragraph_boundaries(self) -> None:
        """Chunks split on paragraph boundaries when possible."""
        para1 = "a" * 900
        para2 = "b" * 900
        para3 = "c" * 900
        text = f"{para1}\n\n{para2}\n\n{para3}"
        assert len(text) > 2000

        chunks = AICog._chunk_response(text)
        # First chunk should contain para1 and para2 (900 + 2 + 900 = 1802 ≤ 2000)
        assert para1 in chunks[0]
        assert para2 in chunks[0]
        # Third paragraph should be in the second chunk
        assert para3 in chunks[1]

    def test_single_paragraph_over_2000_splits_on_newlines(self) -> None:
        """Single paragraph >2000 chars splits on newline boundaries."""
        lines = ["Line number {} with some content here.".format(i) for i in range(80)]
        text = "\n".join(lines)
        assert len(text) > 2000
        # No paragraph breaks — force newline splitting
        assert "\n\n" not in text

        chunks = AICog._chunk_response(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 2000

    def test_single_line_over_2000_hard_splits(self) -> None:
        """Single line >2000 chars hard-splits at 2000."""
        text = "word " * 500  # 2500 chars
        assert len(text) > 2000
        assert "\n" not in text.strip()

        chunks = AICog._chunk_response(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 2000

    def test_no_empty_chunks(self) -> None:
        """Chunking never produces empty strings."""
        text = "Hello\n\n\n\nWorld\n\n\n\n" + "x" * 2500
        chunks = AICog._chunk_response(text)
        for chunk in chunks:
            assert chunk  # not empty

    def test_all_content_preserved(self) -> None:
        """All original content is present across the chunks."""
        paragraphs = [f"Paragraph {i}: " + "x" * 150 for i in range(20)]
        text = "\n\n".join(paragraphs)
        chunks = AICog._chunk_response(text)
        # Rejoin and verify all paragraphs are present
        rejoined = "\n\n".join(chunks) if len(chunks) > 1 else chunks[0]
        for para in paragraphs:
            # Content should be somewhere in the output (may have different separators)
            assert para in rejoined or para in "".join(chunks)


# ── Error handling tests ─────────────────────────────────────────────


class TestErrorHandling:
    """AICog handles errors gracefully — no crashes, user-visible error messages."""

    @pytest.mark.asyncio()
    async def test_exception_sends_error_message(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Claude client exception → error message sent to channel, no crash."""
        message = _make_message(
            content=f"<@{mock_bot.user.id}> hi",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(side_effect=RuntimeError("Something broke"))

        # Should NOT raise
        await cog.on_message(message)

        # Should have sent an error message to the channel
        message.channel.send.assert_awaited()
        error_msg = message.channel.send.call_args[0][0]
        assert "sorry" in error_msg.lower() or "wrong" in error_msg.lower()

    @pytest.mark.asyncio()
    async def test_no_db_ai_channel_check_returns_false(self, cog: AICog, mock_bot: MagicMock) -> None:
        """When db is None, AI channel check returns False (no crash)."""
        mock_bot.db = None
        message = _make_message(content="Hello")
        # No mention either
        cog.claude_client.ask = AsyncMock()

        await cog.on_message(message)

        cog.claude_client.ask.assert_not_awaited()


# ── /ai-channel command tests ───────────────────────────────────────


class TestAiChannelCommand:
    """/ai-channel command sets and clears the AI channel."""

    @pytest.mark.asyncio()
    async def test_set_channel(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Setting a channel updates guild_config."""
        ctx = MagicMock(spec=commands.Context)
        ctx.guild = MagicMock(spec=discord.Guild)
        ctx.guild.id = 111222333
        ctx.send = AsyncMock()

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 777888999
        channel.mention = "#ai-chat"

        # Call the underlying callback directly to bypass HybridCommand descriptor
        await cog.ai_channel.callback(cog, ctx, channel=channel)

        mock_bot.db.execute.assert_awaited_once()
        call_args = mock_bot.db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "ai_channel_id" in sql
        assert channel.id in params
        assert ctx.guild.id in params
        ctx.send.assert_awaited_once()
        sent_text = ctx.send.call_args[0][0]
        assert "#ai-chat" in sent_text

    @pytest.mark.asyncio()
    async def test_clear_channel(self, cog: AICog, mock_bot: MagicMock) -> None:
        """Clearing the channel sets ai_channel_id to NULL."""
        ctx = MagicMock(spec=commands.Context)
        ctx.guild = MagicMock(spec=discord.Guild)
        ctx.guild.id = 111222333
        ctx.send = AsyncMock()

        # Call the underlying callback directly to bypass HybridCommand descriptor
        await cog.ai_channel.callback(cog, ctx, channel=None)

        mock_bot.db.execute.assert_awaited_once()
        call_args = mock_bot.db.execute.call_args
        sql = call_args[0][0]
        assert "NULL" in sql
        ctx.send.assert_awaited_once()
        sent_text = ctx.send.call_args[0][0]
        assert "cleared" in sent_text.lower()


# ── Module setup test ────────────────────────────────────────────────


class TestSetup:
    """Module-level setup function loads the cog."""

    @pytest.mark.asyncio()
    async def test_setup_adds_cog(self) -> None:
        """setup() calls bot.add_cog with an AICog instance."""
        from bot.cogs.ai import setup

        mock_bot = MagicMock()
        mock_bot.config = Config(
            discord_bot_token="test-token",
            anthropic_api_key="test-key",
            database_path=":memory:",
            command_prefix="!",
            claude_model="claude-sonnet-4-20250514",
        )
        mock_bot.add_cog = AsyncMock()

        await setup(mock_bot)

        mock_bot.add_cog.assert_awaited_once()
        added_cog = mock_bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, AICog)


# ── Tool-provider protocol tests ────────────────────────────────────


def _make_protocol_cog(tool_defs: list[dict[str, Any]], name: str = "MockCog") -> MagicMock:
    """Create a mock cog implementing the tool-provider protocol."""
    cog = MagicMock()
    cog.__class__.__name__ = name
    cog.get_tools = MagicMock(return_value=tool_defs)
    cog.handle_tool_call = AsyncMock(return_value="Tool executed.")
    return cog


class TestToolProviderProtocol:
    """AICog discovers tools from any cog implementing get_tools/handle_tool_call."""

    @pytest.mark.asyncio()
    async def test_tools_passed_when_protocol_cog_loaded(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """When a cog with get_tools()/handle_tool_call() is loaded, tools are passed to Claude."""
        from bot.cogs.server_design import PROPOSE_TOOL

        design_cog = _make_protocol_cog([PROPOSE_TOOL], "ServerDesignCog")
        mock_bot.cogs = {"AICog": cog, "ServerDesignCog": design_cog}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> set up a gaming server",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Sure, I'll design that!")

        await cog.on_message(message)

        cog.claude_client.ask.assert_awaited_once()
        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert call_kwargs["tools"] == [PROPOSE_TOOL]
        assert call_kwargs["tool_executor"] is not None
        assert callable(call_kwargs["tool_executor"])

    @pytest.mark.asyncio()
    async def test_no_tools_when_no_protocol_cogs(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """When only AICog itself is in cogs dict (no protocol cogs), no tools are passed."""
        mock_bot.cogs = {"AICog": cog}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> hello",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hi there!")

        await cog.on_message(message)

        cog.claude_client.ask.assert_awaited_once()
        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert call_kwargs.get("tools") is None
        assert call_kwargs.get("tool_executor") is None

    @pytest.mark.asyncio()
    async def test_multiple_cogs_tools_combined(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """Tools from multiple protocol cogs are combined into one list."""
        tool_a = {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}}
        tool_b = {"name": "tool_b", "description": "Tool B", "input_schema": {"type": "object", "properties": {}}}

        cog_a = _make_protocol_cog([tool_a], "CogA")
        cog_b = _make_protocol_cog([tool_b], "CogB")
        mock_bot.cogs = {"AICog": cog, "CogA": cog_a, "CogB": cog_b}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> hi",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hello!")

        await cog.on_message(message)

        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert len(call_kwargs["tools"]) == 2
        tool_names = {t["name"] for t in call_kwargs["tools"]}
        assert tool_names == {"tool_a", "tool_b"}

    @pytest.mark.asyncio()
    async def test_tool_executor_dispatches_to_correct_cog(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """Tool executor routes calls to the cog that owns the tool name."""
        tool_a = {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}}
        tool_b = {"name": "tool_b", "description": "Tool B", "input_schema": {"type": "object", "properties": {}}}

        cog_a = _make_protocol_cog([tool_a], "CogA")
        cog_a.handle_tool_call = AsyncMock(return_value="Result from CogA")
        cog_b = _make_protocol_cog([tool_b], "CogB")
        cog_b.handle_tool_call = AsyncMock(return_value="Result from CogB")
        mock_bot.cogs = {"AICog": cog, "CogA": cog_a, "CogB": cog_b}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> test",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="OK")

        await cog.on_message(message)

        executor = cog.claude_client.ask.call_args.kwargs["tool_executor"]

        result_a = await executor("tool_a", {"key": "val"})
        assert result_a == "Result from CogA"
        cog_a.handle_tool_call.assert_awaited_once_with("tool_a", {"key": "val"}, message)
        cog_b.handle_tool_call.assert_not_awaited()

        result_b = await executor("tool_b", {"key2": "val2"})
        assert result_b == "Result from CogB"
        cog_b.handle_tool_call.assert_awaited_once_with("tool_b", {"key2": "val2"}, message)

    @pytest.mark.asyncio()
    async def test_tool_executor_unknown_tool(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """Tool executor returns error string for unknown tool names."""
        tool_a = {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}}
        cog_a = _make_protocol_cog([tool_a], "CogA")
        mock_bot.cogs = {"AICog": cog, "CogA": cog_a}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> test",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="OK")

        await cog.on_message(message)

        executor = cog.claude_client.ask.call_args.kwargs["tool_executor"]
        result = await executor("nonexistent_tool", {})
        assert "Unknown tool" in result
        assert "nonexistent_tool" in result

    @pytest.mark.asyncio()
    async def test_max_tokens_4096_when_tools_present(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """max_tokens is set to 4096 when any cog provides tools."""
        tool_a = {"name": "tool_a", "description": "Tool A", "input_schema": {"type": "object", "properties": {}}}
        cog_a = _make_protocol_cog([tool_a], "CogA")
        mock_bot.cogs = {"AICog": cog, "CogA": cog_a}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> test",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="OK")

        await cog.on_message(message)

        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio()
    async def test_max_tokens_default_when_no_tools(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """max_tokens stays at default 1024 when no cog provides tools."""
        mock_bot.cogs = {"AICog": cog}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> hello",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hi!")

        await cog.on_message(message)

        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio()
    async def test_cog_without_protocol_ignored(
        self, cog: AICog, mock_bot: MagicMock
    ) -> None:
        """Cogs without get_tools/handle_tool_call are ignored in tool discovery."""
        plain_cog = MagicMock()
        # No get_tools or handle_tool_call attributes
        del plain_cog.get_tools
        del plain_cog.handle_tool_call
        mock_bot.cogs = {"AICog": cog, "PlainCog": plain_cog}

        message = _make_message(
            content=f"<@{mock_bot.user.id}> hello",
            mentions=[mock_bot.user],
        )
        mock_bot.db.fetchone = AsyncMock(return_value=None)
        cog.claude_client.ask = AsyncMock(return_value="Hi!")

        await cog.on_message(message)

        call_kwargs = cog.claude_client.ask.call_args.kwargs
        assert call_kwargs.get("tools") is None
