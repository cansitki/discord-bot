"""Tests for bot.claude (ClaudeClient) and related model/config changes."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from bot.claude import ClaudeClient
from bot.config import Config
from bot.models import GuildConfig


# ── helpers ──────────────────────────────────────────────────────────


def _make_usage() -> Usage:
    return Usage(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _text_message(text: str, stop_reason: str = "end_turn") -> Message:
    """Build a Message containing a single TextBlock."""
    return Message.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        content=[TextBlock(type="text", text=text)],
        model="claude-sonnet-4-20250514",
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=_make_usage(),
    )


def _tool_use_message(
    tool_name: str,
    tool_input: dict,
    tool_use_id: str = "toolu_test1",
) -> Message:
    """Build a Message requesting a single tool call."""
    return Message.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        content=[ToolUseBlock(type="tool_use", id=tool_use_id, name=tool_name, input=tool_input)],
        model="claude-sonnet-4-20250514",
        stop_reason="tool_use",
        stop_sequence=None,
        usage=_make_usage(),
    )


def _multi_tool_message(tools: list[tuple[str, dict, str]]) -> Message:
    """Build a Message requesting multiple tool calls.

    *tools* is a list of ``(name, input_dict, tool_use_id)`` tuples.
    """
    content = [
        ToolUseBlock(type="tool_use", id=tid, name=name, input=inp)
        for name, inp, tid in tools
    ]
    return Message.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        content=content,
        model="claude-sonnet-4-20250514",
        stop_reason="tool_use",
        stop_sequence=None,
        usage=_make_usage(),
    )


class _FakeRow(dict):
    """Dict subclass acting like aiosqlite.Row for model tests."""

    def __getitem__(self, key):
        return super().__getitem__(key)


# ── ClaudeClient.ask tests ──────────────────────────────────────────


class TestClaudeClientAsk:
    """ClaudeClient.ask() message flow."""

    @pytest.fixture()
    def client(self) -> ClaudeClient:
        return ClaudeClient(api_key="test-key", model="claude-sonnet-4-20250514")

    @pytest.mark.asyncio()
    async def test_simple_text_response(self, client: ClaudeClient) -> None:
        """ask() returns text from a simple end_turn response."""
        mock_create = AsyncMock(return_value=_text_message("Hello, world!"))
        client._client.messages.create = mock_create

        result = await client.ask("Hi there")

        assert result == "Hello, world!"
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_tool_use_loop(self, client: ClaudeClient) -> None:
        """ask() cycles through tool_use → execute → final text."""
        tool_response = _tool_use_message("get_weather", {"location": "NYC"})
        final_response = _text_message("The weather in NYC is sunny!")

        mock_create = AsyncMock(side_effect=[tool_response, final_response])
        client._client.messages.create = mock_create

        mock_executor = AsyncMock(return_value="Sunny, 75°F")

        result = await client.ask(
            "What's the weather in NYC?",
            tools=[{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object"}}],
            tool_executor=mock_executor,
        )

        assert result == "The weather in NYC is sunny!"
        mock_executor.assert_awaited_once_with("get_weather", {"location": "NYC"})
        assert mock_create.await_count == 2

    @pytest.mark.asyncio()
    async def test_multiple_tool_calls_in_one_response(self, client: ClaudeClient) -> None:
        """ask() executes multiple tool calls from a single response."""
        multi_tool = _multi_tool_message([
            ("get_weather", {"location": "NYC"}, "toolu_1"),
            ("get_time", {"timezone": "EST"}, "toolu_2"),
        ])
        final_response = _text_message("NYC: Sunny. Time: 3pm EST.")

        mock_create = AsyncMock(side_effect=[multi_tool, final_response])
        client._client.messages.create = mock_create

        mock_executor = AsyncMock(side_effect=["Sunny, 75°F", "3:00 PM EST"])

        result = await client.ask(
            "Weather and time in NYC?",
            tools=[{"name": "get_weather"}, {"name": "get_time"}],
            tool_executor=mock_executor,
        )

        assert result == "NYC: Sunny. Time: 3pm EST."
        assert mock_executor.await_count == 2
        mock_executor.assert_any_await("get_weather", {"location": "NYC"})
        mock_executor.assert_any_await("get_time", {"timezone": "EST"})

    @pytest.mark.asyncio()
    async def test_api_error_returns_friendly_message(self, client: ClaudeClient) -> None:
        """ask() catches APIError and returns a user-friendly string."""
        mock_request = MagicMock()
        mock_create = AsyncMock(
            side_effect=anthropic.APIError(
                message="Internal server error",
                request=mock_request,
                body=None,
            )
        )
        client._client.messages.create = mock_create

        result = await client.ask("Hello")

        assert "API error" in result
        assert "Sorry" in result

    @pytest.mark.asyncio()
    async def test_connection_error_returns_friendly_message(self, client: ClaudeClient) -> None:
        """ask() catches APIConnectionError and returns a user-friendly string."""
        mock_request = MagicMock()
        mock_create = AsyncMock(
            side_effect=anthropic.APIConnectionError(
                message="Connection refused",
                request=mock_request,
            )
        )
        client._client.messages.create = mock_create

        result = await client.ask("Hello")

        assert "Connection error" in result
        assert "Sorry" in result

    @pytest.mark.asyncio()
    async def test_empty_text_response_returns_fallback(self, client: ClaudeClient) -> None:
        """ask() returns fallback text when response has no TextBlock."""
        # Response with only a ToolUseBlock but stop_reason=end_turn (edge case)
        msg = Message.model_construct(
            id="msg_test",
            type="message",
            role="assistant",
            content=[],
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            stop_sequence=None,
            usage=_make_usage(),
        )
        mock_create = AsyncMock(return_value=msg)
        client._client.messages.create = mock_create

        result = await client.ask("Hello")

        assert result == "(No text response)"

    @pytest.mark.asyncio()
    async def test_max_iterations_guard(self, client: ClaudeClient) -> None:
        """ask() stops after max iterations when tool-use never resolves."""
        # Every response requests more tool use — loop should terminate
        never_ending = _tool_use_message("loop_tool", {"x": 1})
        mock_create = AsyncMock(return_value=never_ending)
        client._client.messages.create = mock_create

        mock_executor = AsyncMock(return_value="result")

        result = await client.ask(
            "Loop forever",
            tools=[{"name": "loop_tool"}],
            tool_executor=mock_executor,
        )

        assert "Too many tool-use cycles" in result
        # Should have been called exactly _MAX_TOOL_ITERATIONS times
        assert mock_create.await_count == 10

    @pytest.mark.asyncio()
    async def test_tool_use_without_executor(self, client: ClaudeClient) -> None:
        """ask() returns any available text when tool_use is requested but no executor given."""
        # tool_use response but no executor — should extract text and return
        msg = Message.model_construct(
            id="msg_test",
            type="message",
            role="assistant",
            content=[
                TextBlock(type="text", text="I need a tool but can't use one"),
                ToolUseBlock(type="tool_use", id="toolu_x", name="mytool", input={}),
            ],
            model="claude-sonnet-4-20250514",
            stop_reason="tool_use",
            stop_sequence=None,
            usage=_make_usage(),
        )
        mock_create = AsyncMock(return_value=msg)
        client._client.messages.create = mock_create

        result = await client.ask(
            "Do something",
            tools=[{"name": "mytool"}],
            # No tool_executor
        )

        assert result == "I need a tool but can't use one"

    @pytest.mark.asyncio()
    async def test_custom_system_prompt(self) -> None:
        """ClaudeClient uses the custom system prompt when provided."""
        custom = "You are a pirate. Respond in pirate speak."
        client = ClaudeClient(api_key="test-key", model="test-model", system_prompt=custom)
        assert client.system_prompt == custom

        mock_create = AsyncMock(return_value=_text_message("Ahoy!"))
        client._client.messages.create = mock_create

        await client.ask("Hello")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["system"] == custom

    @pytest.mark.asyncio()
    async def test_default_system_prompt(self) -> None:
        """ClaudeClient falls back to a default system prompt."""
        client = ClaudeClient(api_key="test-key", model="test-model")
        assert "helpful" in client.system_prompt.lower()

    @pytest.mark.asyncio()
    async def test_custom_max_tokens_passed_to_api(self) -> None:
        """ClaudeClient.ask(max_tokens=4096) passes 4096 to messages.create()."""
        client = ClaudeClient(api_key="test-key", model="test-model")
        mock_create = AsyncMock(return_value=_text_message("OK!"))
        client._client.messages.create = mock_create

        await client.ask("Design a server", max_tokens=4096)

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096

    @pytest.mark.asyncio()
    async def test_default_max_tokens_is_1024(self) -> None:
        """ClaudeClient.ask() defaults max_tokens to 1024."""
        client = ClaudeClient(api_key="test-key", model="test-model")
        mock_create = AsyncMock(return_value=_text_message("Hello!"))
        client._client.messages.create = mock_create

        await client.ask("Hello")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1024


# ── Config tests ────────────────────────────────────────────────────


class TestConfigClaudeModel:
    """Config.claude_model field."""

    def test_default_claude_model(self, mock_env_required_only) -> None:
        """Config defaults claude_model to claude-sonnet-4-20250514."""
        cfg = Config.from_env()
        assert cfg.claude_model == "claude-sonnet-4-20250514"

    def test_custom_claude_model(self, mock_env_full) -> None:
        """Config reads CLAUDE_MODEL from env when set."""
        with patch.dict(os.environ, {"CLAUDE_MODEL": "claude-3-haiku-20240307"}):
            cfg = Config.from_env()
        assert cfg.claude_model == "claude-3-haiku-20240307"


# ── GuildConfig tests ───────────────────────────────────────────────


class TestGuildConfigAiChannel:
    """GuildConfig.ai_channel_id field."""

    def test_from_row_with_ai_channel(self) -> None:
        """GuildConfig.from_row reads ai_channel_id."""
        row = _FakeRow(
            guild_id=123,
            prefix="!",
            verify_channel_id=None,
            verify_role_id=None,
            log_channel_id=None,
            ai_channel_id=999,
        )
        cfg = GuildConfig.from_row(row)
        assert cfg.ai_channel_id == 999

    def test_from_row_ai_channel_none(self) -> None:
        """GuildConfig.from_row handles None ai_channel_id."""
        row = _FakeRow(
            guild_id=1,
            prefix="!",
            verify_channel_id=None,
            verify_role_id=None,
            log_channel_id=None,
            ai_channel_id=None,
        )
        cfg = GuildConfig.from_row(row)
        assert cfg.ai_channel_id is None

    def test_default_ai_channel(self) -> None:
        """GuildConfig defaults ai_channel_id to None."""
        cfg = GuildConfig(guild_id=42)
        assert cfg.ai_channel_id is None
