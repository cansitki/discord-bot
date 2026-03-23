"""Async wrapper around the Anthropic SDK for Claude conversations.

Owns the ``AsyncAnthropic`` client instance and provides a single
``ask()`` entry-point that handles the tool-use loop internally.

Supports two authentication modes:
- **API key**: Traditional ``ANTHROPIC_API_KEY`` (``x-api-key`` header).
- **OAuth**: Claude Pro/Max subscription tokens (Bearer auth), matching
  the same OAuth flow used by Claude Code / pi SDK. OAuth tokens are
  auto-refreshed when expired.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import anthropic
from anthropic.types import TextBlock, ToolUseBlock

if TYPE_CHECKING:
    from bot.oauth import OAuthManager

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful Discord bot assistant. Be concise, friendly, "
    "and helpful. Use Discord markdown formatting when appropriate. "
    "You can help with general questions, translate text between languages, "
    "summarize channel conversations, and summarize content from URLs."
)

_MAX_TOOL_ITERATIONS = 10

# Claude Code version to mimic for OAuth requests (stealth mode)
_CLAUDE_CODE_VERSION = "2.1.62"


def _is_oauth_token(api_key: str) -> bool:
    """Check if a token is an Anthropic OAuth token."""
    return "sk-ant-oat" in api_key


def _create_anthropic_client(api_key: str) -> anthropic.AsyncAnthropic:
    """Create an AsyncAnthropic client with the right auth mode.

    - OAuth tokens (``sk-ant-oat*``): Bearer auth with Claude Code headers.
    - API keys: Standard ``x-api-key`` auth.
    """
    if _is_oauth_token(api_key):
        return anthropic.AsyncAnthropic(
            api_key=None,
            auth_token=api_key,
            default_headers={
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                "user-agent": f"claude-cli/{_CLAUDE_CODE_VERSION}",
                "x-app": "cli",
            },
        )
    return anthropic.AsyncAnthropic(api_key=api_key)


class ClaudeClient:
    """Thin async wrapper around the Anthropic Messages API.

    Parameters
    ----------
    api_key:
        Anthropic API key or OAuth access token. Never logged.
        May be empty/None when ``oauth_manager`` is provided.
    model:
        Model identifier passed to every ``messages.create`` call.
    system_prompt:
        Optional system prompt; defaults to a brief bot personality.
    oauth_manager:
        Optional OAuthManager for auto-refreshing OAuth tokens.
        When set, the client prefers OAuth tokens over the static api_key.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        system_prompt: str | None = None,
        oauth_manager: OAuthManager | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._api_key = api_key
        self._oauth_manager = oauth_manager
        self._client = _create_anthropic_client(api_key) if api_key else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _resolve_client(self) -> anthropic.AsyncAnthropic:
        """Resolve the Anthropic client, preferring OAuth with auto-refresh.

        Priority:
        1. OAuth token from OAuthManager (refreshed automatically)
        2. Static API key provided at construction
        """
        if self._oauth_manager is not None:
            try:
                access_token = await self._oauth_manager.get_access_token()
                if access_token is not None:
                    # Rebuild client if the token changed
                    if self._client is None or self._api_key != access_token:
                        self._api_key = access_token
                        self._client = _create_anthropic_client(access_token)
                    return self._client
            except Exception:
                logger.warning(
                    "claude._resolve_client: OAuth token resolution failed, falling back to API key"
                )

        if self._client is None:
            raise RuntimeError(
                "No Claude authentication configured. "
                "Set ANTHROPIC_API_KEY or run /claude-login."
            )
        return self._client

    async def ask(
        self,
        message: str,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Send *message* to Claude and return the final text response.

        When *tools* and *tool_executor* are provided, the method runs a
        tool-use loop: if Claude responds with ``stop_reason="tool_use"``,
        each requested tool call is forwarded to *tool_executor*, and the
        results are sent back.  The loop repeats until Claude returns a
        final text response or ``_MAX_TOOL_ITERATIONS`` is reached.

        Errors from the Anthropic SDK are caught and returned as
        user-friendly error strings — callers never see unhandled
        exceptions from this method.
        """
        logger.info(
            "claude.ask: model=%s message_len=%d tools=%d",
            self.model,
            len(message),
            len(tools) if tools else 0,
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": message},
        ]

        try:
            # Resolve client (may refresh OAuth tokens)
            await self._resolve_client()
            return await self._run_message_loop(messages, tools, tool_executor, max_tokens)
        except RuntimeError as exc:
            logger.error("claude.ask: no auth configured: %s", exc)
            return "Sorry, no Claude authentication is configured. Use `/claude-login` or set ANTHROPIC_API_KEY."
        except anthropic.APIConnectionError as exc:
            logger.error(
                "claude.ask: connection error model=%s error=%s",
                self.model,
                type(exc).__name__,
            )
            return "Sorry, I couldn't reach the AI service. Please try again later. (Connection error)"
        except anthropic.APIError as exc:
            logger.error(
                "claude.ask: api error model=%s status=%s error=%s",
                self.model,
                getattr(exc, "status_code", "unknown"),
                type(exc).__name__,
            )
            return "Sorry, I couldn't process that request. (API error)"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_message_loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]] | None,
        max_tokens: int = 1024,
    ) -> str:
        """Drive the create → tool-use → create cycle."""
        for iteration in range(_MAX_TOOL_ITERATIONS):
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=self.system_prompt,
                messages=messages,
                tools=tools or [],
            )

            if response.stop_reason == "tool_use":
                if tool_executor is None:
                    logger.warning(
                        "claude: tool_use requested but no executor provided"
                    )
                    return self._extract_text(response.content)

                logger.info(
                    "claude: tool_use iteration=%d",
                    iteration + 1,
                )

                # Append the full assistant response (including tool-use blocks)
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if isinstance(block, ToolUseBlock):
                        result = await tool_executor(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})
                continue

            # end_turn or any other stop_reason → extract text and return
            text = self._extract_text(response.content)
            return text

        # Exhausted iterations without a final text response
        logger.warning(
            "claude: max tool-use iterations reached (%d)", _MAX_TOOL_ITERATIONS
        )
        return "Sorry, I couldn't complete that request. (Too many tool-use cycles)"

    @staticmethod
    def _extract_text(content: list[Any]) -> str:
        """Pull text from ``TextBlock`` items, returning fallback if empty."""
        parts = [block.text for block in content if isinstance(block, TextBlock)]
        return "\n".join(parts) if parts else "(No text response)"
