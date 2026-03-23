"""Tests for bot.webhook — signature verification, security, and health.

Covers: HMAC-SHA256 verification (valid, invalid, missing, wrong secret,
empty payload, timing-safe), webhook security (401/403/503), health
endpoint, unrecognised events, and method restrictions.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.webhook import create_webhook_app, verify_signature


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
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "accepted"

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
        payload = json.dumps({"action": "whatever"}).encode()
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
            assert body["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_webhook_get_not_allowed(self) -> None:
        bot = _make_mock_bot()
        app = create_webhook_app(bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/webhook/github")
            assert resp.status == 405
