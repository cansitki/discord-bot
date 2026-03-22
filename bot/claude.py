"""Async wrapper around the Anthropic SDK for Claude conversations.

Owns the ``AsyncAnthropic`` client instance and provides a single
``ask()`` entry-point that handles the tool-use loop internally.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic
from anthropic.types import TextBlock, ToolUseBlock

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful Discord bot assistant. Be concise, friendly, "
    "and helpful. Use Discord markdown formatting when appropriate. "
    "You can help with general questions, translate text between languages, "
    "summarize channel conversations, and summarize content from URLs."
)

_MAX_TOOL_ITERATIONS = 10


class ClaudeClient:
    """Thin async wrapper around the Anthropic Messages API.

    Parameters
    ----------
    api_key:
        Anthropic API key.  Never logged.
    model:
        Model identifier passed to every ``messages.create`` call.
    system_prompt:
        Optional system prompt; defaults to a brief bot personality.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
            return await self._run_message_loop(messages, tools, tool_executor, max_tokens)
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
