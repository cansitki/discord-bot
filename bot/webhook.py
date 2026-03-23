"""GitHub webhook receiver with HMAC-SHA256 signature verification.

Provides an aiohttp web application that receives GitHub webhook POST
requests, verifies their signatures, and dispatches events to Discord
channels.  Created via ``create_webhook_app(bot)`` factory; the bot
reference is stored on ``app["bot"]``.

Routes:
    POST /webhook/github  — GitHub webhook endpoint
    GET  /health          — Liveness probe (returns 200 "ok")
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from bot.bot import DiscordBot

log = logging.getLogger("bot.webhook")

# Typed application key — avoids NotAppKeyWarning
bot_key: web.AppKey[DiscordBot] = web.AppKey("bot", t=object)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_signature(
    payload_body: bytes, secret: str, signature_header: str | None
) -> bool:
    """Verify an HMAC-SHA256 signature from GitHub.

    Uses ``hmac.compare_digest`` for timing-safe comparison to prevent
    timing side-channel attacks.

    Args:
        payload_body: Raw request body bytes.
        secret: Webhook secret configured in the GitHub App.
        signature_header: Value of the ``X-Hub-Signature-256`` header,
            expected format ``sha256=<hex>``.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature_header:
        return False

    expected = (
        "sha256="
        + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


async def handle_health(request: web.Request) -> web.Response:
    """Liveness probe — always returns 200 "ok"."""
    return web.Response(text="ok", status=200)


async def handle_webhook(request: web.Request) -> web.Response:
    """Process an incoming GitHub webhook event.

    Verification flow:
    1. Read raw body.
    2. Check ``X-Hub-Signature-256`` header → 401 if missing.
    3. Check webhook secret is configured → 503 if None.
    4. Verify HMAC signature → 403 if invalid.
    5. Check ``X-GitHub-Event`` header → 400 if missing.
    6. Log event type and return 200 (event dispatch added in T02).
    """
    body = await request.read()

    # --- Signature header ---
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        log.warning("Webhook request missing X-Hub-Signature-256 header")
        return web.Response(
            text=json.dumps({"error": "missing signature"}),
            status=401,
            content_type="application/json",
        )

    # --- Webhook secret ---
    bot: DiscordBot = request.app[bot_key]
    secret = bot.config.github_webhook_secret
    if secret is None:
        log.error(
            "GITHUB_WEBHOOK_SECRET not configured — rejecting all webhook requests"
        )
        return web.Response(
            text=json.dumps({"error": "webhook secret not configured"}),
            status=503,
            content_type="application/json",
        )

    # --- Signature verification ---
    if not verify_signature(body, secret, signature):
        log.warning("Webhook signature verification failed")
        return web.Response(
            text=json.dumps({"error": "invalid signature"}),
            status=403,
            content_type="application/json",
        )

    # --- Event type ---
    event_type = request.headers.get("X-GitHub-Event")
    if not event_type:
        log.warning("Webhook request missing X-GitHub-Event header")
        return web.Response(
            text=json.dumps({"error": "missing event type"}),
            status=400,
            content_type="application/json",
        )

    # Stub: log event and return accepted (routing added in T02)
    log.info("Received GitHub webhook event: %s", event_type)
    return web.Response(
        text=json.dumps({"status": "accepted"}),
        status=200,
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_webhook_app(bot: DiscordBot) -> web.Application:
    """Create and configure the aiohttp webhook application.

    The bot instance is stored on ``app["bot"]`` so that request handlers
    can access ``bot.db``, ``bot.get_channel()``, and ``bot.config``.

    Args:
        bot: The running DiscordBot instance.

    Returns:
        Configured ``aiohttp.web.Application`` ready to be served.
    """
    app = web.Application()
    app[bot_key] = bot

    app.router.add_post("/webhook/github", handle_webhook)
    app.router.add_get("/health", handle_health)

    log.info("Webhook application created with routes: POST /webhook/github, GET /health")
    return app
