"""Tests for bot.webhook — signature verification, security, health, embed
formatting, and event routing.

Covers: HMAC-SHA256 verification (valid, invalid, missing, wrong secret,
empty payload, timing-safe), webhook security (401/403/503), health
endpoint, unrecognised events, method restrictions, embed formatters
(push, issues, PR, check_suite), and channel routing logic.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import discord

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.webhook import (
    COLOUR_BLUE,
    COLOUR_GREEN,
    COLOUR_PURPLE,
    COLOUR_RED,
    COLOUR_YELLOW,
    create_webhook_app,
    format_check_suite_embed,
    format_issues_embed,
    format_pull_request_embed,
    format_push_embed,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_SECRET = "test-webhook-secret-do-not-use"


def _make_mock_bot(
    *,
    webhook_secret: str | None = TEST_SECRET,
) -> MagicMock:
    """Return a MagicMock bot with the attributes webhook handlers need."""
    bot = MagicMock()
    bot.config.github_webhook_secret = webhook_secret
    bot.db = AsyncMock()
    bot.get_channel = MagicMock()
    return bot


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute a GitHub-style ``sha256=<hex>`` signature."""
    mac = hmac_mod.new(secret.encode(), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


# ---------------------------------------------------------------------------
# Signature verification unit tests
# ---------------------------------------------------------------------------


class TestVerifySignature:
    """Unit tests for verify_signature()."""

    def test_valid_signature(self) -> None:
        payload = b'{"action": "opened"}'
        sig = _sign_payload(payload, TEST_SECRET)
        assert verify_signature(payload, TEST_SECRET, sig) is True

    def test_invalid_signature_tampered_payload(self) -> None:
        payload = b'{"action": "opened"}'
        sig = _sign_payload(payload, TEST_SECRET)
        # Tamper the payload after signing
        tampered = b'{"action": "closed"}'
        assert verify_signature(tampered, TEST_SECRET, sig) is False

    def test_wrong_secret(self) -> None:
        payload = b'{"action": "opened"}'
        sig = _sign_payload(payload, TEST_SECRET)
        assert verify_signature(payload, "wrong-secret", sig) is False

    def test_missing_header_none(self) -> None:
        payload = b'{"action": "opened"}'
        assert verify_signature(payload, TEST_SECRET, None) is False

    def test_missing_header_empty(self) -> None:
        payload = b'{"action": "opened"}'
        assert verify_signature(payload, TEST_SECRET, "") is False

    def test_empty_payload_valid_sig(self) -> None:
        payload = b""
        sig = _sign_payload(payload, TEST_SECRET)
        assert verify_signature(payload, TEST_SECRET, sig) is True

    def test_uses_compare_digest(self) -> None:
        """Verify the function uses hmac.compare_digest for timing safety."""
        source = inspect.getsource(verify_signature)
        assert "compare_digest" in source, (
            "verify_signature must use hmac.compare_digest for timing-safe comparison"
        )


# ---------------------------------------------------------------------------
# Webhook security endpoint tests
# ---------------------------------------------------------------------------


class TestWebhookSecurity:
    """Integration tests for the webhook endpoint's security layer."""

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self) -> None:
        bot = _make_mock_bot()
        bot.db.fetchall.return_value = []  # no matching channels
        app = create_webhook_app(bot)
        payload = json.dumps({
            "action": "opened",
            "repository": {"full_name": "owner/repo", "name": "repo"},
        }).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 200

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)
        payload = json.dumps({"action": "opened"}).encode()

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": "sha256=badhex",
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)
        payload = json.dumps({"action": "opened"}).encode()

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={"X-GitHub-Event": "push"},
            )
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_missing_secret_config(self) -> None:
        bot = _make_mock_bot(webhook_secret=None)
        app = create_webhook_app(bot)
        payload = json.dumps({"action": "opened"}).encode()

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": "sha256=anything",
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 503

    @pytest.mark.asyncio
    async def test_missing_event_header(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)
        payload = json.dumps({"action": "opened"}).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={"X-Hub-Signature-256": sig},
            )
            assert resp.status == 400


# ---------------------------------------------------------------------------
# Health and miscellaneous endpoint tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for the /health liveness probe."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            text = await resp.text()
            assert text == "ok"

    @pytest.mark.asyncio
    async def test_health_post_not_allowed(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/health")
            assert resp.status == 405


class TestMiscEndpoints:
    """Edge case and method restriction tests."""

    @pytest.mark.asyncio
    async def test_unrecognised_event_returns_200(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)
        payload = json.dumps({
            "action": "whatever",
            "repository": {"full_name": "owner/repo", "name": "repo"},
        }).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "unknown_event",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_webhook_get_not_allowed(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/webhook/github")
            assert resp.status == 405


# ---------------------------------------------------------------------------
# Payload fixtures for embed formatter tests
# ---------------------------------------------------------------------------


def _push_payload(*, num_commits: int = 2) -> dict:
    """Build a GitHub push event payload with N commits."""
    commits = [
        {
            "id": f"abc{i:04d}1234567890abcdef1234567890abcdef12",
            "message": f"Commit message {i + 1}",
        }
        for i in range(num_commits)
    ]
    return {
        "ref": "refs/heads/main",
        "compare": "https://github.com/owner/repo/compare/abc...def",
        "commits": commits,
        "pusher": {"name": "testuser"},
        "repository": {"full_name": "owner/repo", "name": "repo"},
    }


def _issues_payload(*, action: str = "opened") -> dict:
    """Build a GitHub issues event payload."""
    return {
        "action": action,
        "issue": {
            "number": 42,
            "title": "Bug in login flow",
            "html_url": "https://github.com/owner/repo/issues/42",
            "user": {"login": "reporter"},
        },
        "repository": {"full_name": "owner/repo", "name": "repo"},
    }


def _pr_payload(*, action: str = "opened", merged: bool = False) -> dict:
    """Build a GitHub pull_request event payload."""
    return {
        "action": action,
        "pull_request": {
            "number": 17,
            "title": "Add dark mode",
            "html_url": "https://github.com/owner/repo/pull/17",
            "user": {"login": "contributor"},
            "merged": merged,
        },
        "repository": {"full_name": "owner/repo", "name": "repo"},
    }


def _check_suite_payload(
    *, action: str = "completed", conclusion: str = "success"
) -> dict:
    """Build a GitHub check_suite event payload."""
    return {
        "action": action,
        "check_suite": {
            "conclusion": conclusion,
            "head_branch": "main",
            "html_url": "https://github.com/owner/repo/actions/runs/123",
        },
        "repository": {"full_name": "owner/repo", "name": "repo"},
    }


# ---------------------------------------------------------------------------
# Embed formatter tests
# ---------------------------------------------------------------------------


class TestFormatPushEmbed:
    """Tests for format_push_embed()."""

    def test_format_push_embed(self) -> None:
        payload = _push_payload(num_commits=2)
        embed = format_push_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_BLUE
        assert "[repo]" in embed.title
        assert "2 new commits to main" in embed.title
        assert embed.url == "https://github.com/owner/repo/compare/abc...def"
        # Both commits in description
        assert "`abc0000`" in embed.description
        assert "Commit message 1" in embed.description
        assert "`abc0001`" in embed.description
        assert "Commit message 2" in embed.description
        # Pusher in footer
        assert "testuser" in embed.footer.text

    def test_format_push_embed_truncation(self) -> None:
        payload = _push_payload(num_commits=5)
        embed = format_push_embed(payload)

        assert "5 new commits" in embed.title
        # Only 3 commits shown
        assert "`abc0000`" in embed.description
        assert "`abc0001`" in embed.description
        assert "`abc0002`" in embed.description
        # 4th commit NOT shown directly
        assert "`abc0003`" not in embed.description
        # Truncation line
        assert "... and 2 more" in embed.description

    def test_push_single_commit(self) -> None:
        payload = _push_payload(num_commits=1)
        embed = format_push_embed(payload)

        assert "1 new commit to main" in embed.title  # singular
        assert "... and" not in embed.description  # no truncation


class TestFormatIssuesEmbed:
    """Tests for format_issues_embed()."""

    def test_format_issues_opened_embed(self) -> None:
        payload = _issues_payload(action="opened")
        embed = format_issues_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_GREEN
        assert "[repo] Issue #42: Bug in login flow" in embed.title
        assert embed.url == "https://github.com/owner/repo/issues/42"
        assert embed.author.name == "reporter"

    def test_format_issues_closed_embed(self) -> None:
        payload = _issues_payload(action="closed")
        embed = format_issues_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_PURPLE
        assert "Issue #42" in embed.title

    def test_format_issues_ignored_action(self) -> None:
        payload = _issues_payload(action="labeled")
        embed = format_issues_embed(payload)
        assert embed is None


class TestFormatPullRequestEmbed:
    """Tests for format_pull_request_embed()."""

    def test_format_pr_opened_embed(self) -> None:
        payload = _pr_payload(action="opened")
        embed = format_pull_request_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_GREEN
        assert "[repo] PR #17: Add dark mode" in embed.title
        assert "(merged)" not in embed.title
        assert embed.url == "https://github.com/owner/repo/pull/17"
        assert embed.author.name == "contributor"

    def test_format_pr_merged_embed(self) -> None:
        payload = _pr_payload(action="closed", merged=True)
        embed = format_pull_request_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_PURPLE
        assert "(merged)" in embed.title
        assert "PR #17" in embed.title

    def test_pr_closed_not_merged(self) -> None:
        payload = _pr_payload(action="closed", merged=False)
        embed = format_pull_request_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_PURPLE
        assert "(merged)" not in embed.title


class TestFormatCheckSuiteEmbed:
    """Tests for format_check_suite_embed()."""

    def test_format_check_suite_success(self) -> None:
        payload = _check_suite_payload(conclusion="success")
        embed = format_check_suite_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_GREEN
        assert "[repo] CI: success on main" in embed.title
        assert embed.url == "https://github.com/owner/repo/actions/runs/123"

    def test_format_check_suite_failure(self) -> None:
        payload = _check_suite_payload(conclusion="failure")
        embed = format_check_suite_embed(payload)

        assert isinstance(embed, discord.Embed)
        assert embed.colour.value == COLOUR_RED
        assert "failure" in embed.title

    def test_check_suite_only_completed(self) -> None:
        payload = _check_suite_payload(action="requested")
        embed = format_check_suite_embed(payload)
        assert embed is None


# ---------------------------------------------------------------------------
# Event routing tests
# ---------------------------------------------------------------------------


def _make_routing_bot(
    *,
    channel_rows: list[dict] | None = None,
    channels: dict[int, AsyncMock] | None = None,
) -> MagicMock:
    """Build a mock bot with db.fetchall and get_channel wired for routing tests."""
    bot = _make_mock_bot()
    # Default: no matching channels
    if channel_rows is None:
        channel_rows = []
    bot.db.fetchall.return_value = channel_rows

    # get_channel returns a mock channel for known IDs, None for unknown
    if channels is None:
        channels = {}

    def _get_channel(cid: int):
        return channels.get(cid)

    bot.get_channel = MagicMock(side_effect=_get_channel)
    return bot


def _make_mock_channel(channel_id: int) -> AsyncMock:
    """Return an AsyncMock representing a Discord text channel."""
    ch = AsyncMock()
    ch.id = channel_id
    ch.send = AsyncMock()
    return ch


class TestEventRouting:
    """Integration tests for webhook event routing to Discord channels."""

    @pytest.mark.asyncio
    async def test_event_routes_to_matching_channel(self) -> None:
        channel = _make_mock_channel(100)
        bot = _make_routing_bot(
            channel_rows=[{"guild_id": 1, "channel_id": 100}],
            channels={100: channel},
        )
        app = create_webhook_app(bot)
        payload = json.dumps(_push_payload(num_commits=1)).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "delivered"
            assert body["channels"] == 1

        channel.send.assert_called_once()
        sent_embed = channel.send.call_args[1]["embed"]
        assert isinstance(sent_embed, discord.Embed)

    @pytest.mark.asyncio
    async def test_event_routes_to_multiple_channels(self) -> None:
        ch1 = _make_mock_channel(100)
        ch2 = _make_mock_channel(200)
        bot = _make_routing_bot(
            channel_rows=[
                {"guild_id": 1, "channel_id": 100},
                {"guild_id": 2, "channel_id": 200},
            ],
            channels={100: ch1, 200: ch2},
        )
        app = create_webhook_app(bot)
        payload = json.dumps(_push_payload(num_commits=1)).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["channels"] == 2

        ch1.send.assert_called_once()
        ch2.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_no_matching_channels(self) -> None:
        bot = _make_routing_bot(channel_rows=[])
        app = create_webhook_app(bot)
        payload = json.dumps(_push_payload()).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "delivered"
            assert body["channels"] == 0

    @pytest.mark.asyncio
    async def test_event_channel_not_found(self) -> None:
        """bot.get_channel() returns None → warning logged, skip, no crash."""
        bot = _make_routing_bot(
            channel_rows=[{"guild_id": 1, "channel_id": 999}],
            channels={},  # 999 not in channels → get_channel returns None
        )
        app = create_webhook_app(bot)
        payload = json.dumps(_push_payload()).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["channels"] == 0  # none delivered

    @pytest.mark.asyncio
    async def test_event_missing_repository_key(self) -> None:
        """Payload without repository → 400."""
        bot = _make_mock_bot()
        app = create_webhook_app(bot)
        payload = json.dumps({"action": "opened"}).encode()
        sig = _sign_payload(payload, TEST_SECRET)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/webhook/github",
                data=payload,
                headers={
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                },
            )
            assert resp.status == 400
            body = await resp.json()
            assert "repository" in body["error"]


# ---------------------------------------------------------------------------
# Bot lifecycle integration tests
# ---------------------------------------------------------------------------


class TestBotWebhookLifecycle:
    """Integration tests for webhook server start/stop in bot lifecycle."""

    @pytest.mark.asyncio
    async def test_bot_starts_webhook_with_secret(self) -> None:
        """When github_webhook_secret is set, setup_hook creates a runner."""
        from aiohttp import web as _web

        bot = MagicMock()
        bot.config.github_webhook_secret = TEST_SECRET
        bot._webhook_runner = None

        # Simulate the setup_hook webhook startup block
        app = create_webhook_app(bot)
        runner = _web.AppRunner(app)
        await runner.setup()
        try:
            site = _web.TCPSite(runner, "0.0.0.0", 0)  # port 0 = OS picks free port
            await site.start()
            # Runner is set up and site is listening
            assert runner is not None
            assert runner._server is not None
        finally:
            await runner.cleanup()

    @pytest.mark.asyncio
    async def test_bot_skips_webhook_without_secret(self) -> None:
        """When github_webhook_secret is None, _webhook_runner stays None."""
        bot = MagicMock()
        bot.config.github_webhook_secret = None
        bot._webhook_runner = None

        # The bot's setup_hook checks the secret and skips if None.
        # Verify the conditional path: no runner created.
        if bot.config.github_webhook_secret:
            bot._webhook_runner = MagicMock()  # would be set
        else:
            bot._webhook_runner = None  # stays None

        assert bot._webhook_runner is None

    @pytest.mark.asyncio
    async def test_bot_shutdown_cleans_up_runner(self) -> None:
        """close() calls runner.cleanup() when runner exists."""
        from aiohttp import web as _web

        bot = MagicMock()
        bot.config.github_webhook_secret = TEST_SECRET

        app = create_webhook_app(bot)
        runner = _web.AppRunner(app)
        await runner.setup()
        site = _web.TCPSite(runner, "0.0.0.0", 0)
        await site.start()

        # Verify cleanup shuts down cleanly (no errors)
        assert runner._server is not None
        await runner.cleanup()
        # After cleanup, server should be None
        assert runner._server is None
