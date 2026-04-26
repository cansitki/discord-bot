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
from typing import TYPE_CHECKING, Any

import discord
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
# Embed formatters
# ---------------------------------------------------------------------------

# Colour constants (GitHub's palette)
COLOUR_BLUE = 0x0366D6     # push
COLOUR_GREEN = 0x238636    # opened / success
COLOUR_PURPLE = 0x8957E5   # closed / merged
COLOUR_RED = 0xCB2431      # failure / timed_out
COLOUR_YELLOW = 0xDBAB09   # other CI conclusions


def _repo_name(payload: dict[str, Any]) -> str:
    """Extract short repo name from payload, e.g. 'my-repo'."""
    return payload.get("repository", {}).get("name", "unknown")


def format_push_embed(payload: dict[str, Any]) -> discord.Embed:
    """Format a ``push`` event as a Discord embed.

    Blue colour. Shows up to 3 commit messages with truncation.
    Footer displays the pusher's name. URL links to the compare diff.
    """
    repo = _repo_name(payload)
    commits = payload.get("commits", [])
    branch_ref = payload.get("ref", "")
    branch = branch_ref.rsplit("/", 1)[-1] if "/" in branch_ref else branch_ref
    count = len(commits)

    title = f"[{repo}] {count} new commit{'s' if count != 1 else ''} to {branch}"
    compare_url = payload.get("compare")

    # Build description: up to 3 commit lines
    lines: list[str] = []
    for commit in commits[:3]:
        sha_short = commit.get("id", "")[:7]
        message = commit.get("message", "").split("\n", 1)[0]  # first line only
        lines.append(f"`{sha_short}` {message}")
    if count > 3:
        lines.append(f"... and {count - 3} more")

    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        colour=COLOUR_BLUE,
        url=compare_url,
    )

    pusher = payload.get("pusher", {}).get("name")
    if pusher:
        embed.set_footer(text=f"Pushed by {pusher}")

    return embed


def format_issues_embed(payload: dict[str, Any]) -> discord.Embed | None:
    """Format an ``issues`` event as a Discord embed.

    Only handles ``opened`` (green) and ``closed`` (purple) actions.
    Returns None for other actions (e.g. ``labeled``, ``assigned``).
    """
    action = payload.get("action")
    if action not in ("opened", "closed"):
        return None

    issue = payload.get("issue", {})
    repo = _repo_name(payload)
    number = issue.get("number", "?")
    issue_title = issue.get("title", "")
    html_url = issue.get("html_url")
    user = issue.get("user", {}).get("login", "unknown")

    colour = COLOUR_GREEN if action == "opened" else COLOUR_PURPLE
    title = f"[{repo}] Issue #{number}: {issue_title}"

    embed = discord.Embed(
        title=title,
        colour=colour,
        url=html_url,
    )
    embed.set_author(name=user)
    return embed


def format_pull_request_embed(payload: dict[str, Any]) -> discord.Embed | None:
    """Format a ``pull_request`` event as a Discord embed.

    Handles ``opened`` (green), ``closed`` (purple). If ``merged`` is True,
    adds a "(merged)" indicator in the title. Returns None for other actions.
    """
    action = payload.get("action")
    if action not in ("opened", "closed"):
        return None

    pr = payload.get("pull_request", {})
    repo = _repo_name(payload)
    number = pr.get("number", "?")
    pr_title = pr.get("title", "")
    html_url = pr.get("html_url")
    user = pr.get("user", {}).get("login", "unknown")
    merged = pr.get("merged", False)

    colour = COLOUR_GREEN if action == "opened" else COLOUR_PURPLE
    merged_text = " (merged)" if merged else ""
    title = f"[{repo}] PR #{number}: {pr_title}{merged_text}"

    embed = discord.Embed(
        title=title,
        colour=colour,
        url=html_url,
    )
    embed.set_author(name=user)
    return embed


def format_check_suite_embed(payload: dict[str, Any]) -> discord.Embed | None:
    """Format a ``check_suite`` event as a Discord embed.

    Only handles ``completed`` action. Colour-coded by conclusion:
    green for success/neutral, red for failure/timed_out, yellow otherwise.
    """
    action = payload.get("action")
    if action != "completed":
        return None

    suite = payload.get("check_suite", {})
    repo = _repo_name(payload)
    conclusion = suite.get("conclusion", "unknown")
    head_branch = suite.get("head_branch", "unknown")
    html_url = suite.get("html_url")

    if conclusion in ("success", "neutral"):
        colour = COLOUR_GREEN
    elif conclusion in ("failure", "timed_out"):
        colour = COLOUR_RED
    else:
        colour = COLOUR_YELLOW

    title = f"[{repo}] CI: {conclusion} on {head_branch}"

    embed = discord.Embed(
        title=title,
        colour=colour,
        url=html_url,
    )
    return embed


# Map event type → formatter function
EVENT_FORMATTERS: dict[str, Any] = {
    "push": format_push_embed,
    "issues": format_issues_embed,
    "pull_request": format_pull_request_embed,
    "check_suite": format_check_suite_embed,
}


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
    6. Extract repository info → 400 if missing.
    7. Format embed for event type → 200 ignored if unrecognised/filtered.
    8. Route embed to matching Discord channels via channel_repos lookup.
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

    # --- Parse payload ---
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        log.warning("Webhook payload is not valid JSON")
        return web.Response(
            text=json.dumps({"error": "invalid JSON payload"}),
            status=400,
            content_type="application/json",
        )

    # --- Repository extraction ---
    repository = payload.get("repository")
    if not repository or "full_name" not in repository:
        log.warning("Webhook payload missing repository.full_name")
        return web.Response(
            text=json.dumps({"error": "missing repository"}),
            status=400,
            content_type="application/json",
        )

    full_name = repository["full_name"]
    parts = full_name.split("/", 1)
    if len(parts) != 2:
        log.warning("Invalid repository full_name format: %s", full_name)
        return web.Response(
            text=json.dumps({"error": "invalid repository format"}),
            status=400,
            content_type="application/json",
        )
    repo_owner, repo_name = parts

    # --- Event formatting ---
    formatter = EVENT_FORMATTERS.get(event_type)
    if formatter is None:
        log.info("Ignoring unrecognised GitHub event type: %s", event_type)
        return web.Response(
            text=json.dumps({"status": "ignored"}),
            status=200,
            content_type="application/json",
        )

    embed = formatter(payload)
    if embed is None:
        log.info("Event %s action filtered (not handled)", event_type)
        return web.Response(
            text=json.dumps({"status": "ignored"}),
            status=200,
            content_type="application/json",
        )

    # --- Channel routing via reverse channel_repos lookup ---
    # Repo names are case-insensitive on GitHub; we store + match lowercase.
    rows = await bot.db.fetchall(
        "SELECT guild_id, channel_id FROM channel_repos WHERE repo_owner = ? AND repo_name = ?",
        (repo_owner.lower(), repo_name.lower()),
    )

    sent_count = 0
    for row in rows:
        channel_id = row["channel_id"]
        channel = bot.get_channel(channel_id)
        if channel is None:
            log.warning(
                "Channel %d not found for repo %s — skipping",
                channel_id,
                full_name,
            )
            continue
        await channel.send(embed=embed)
        sent_count += 1

    log.info(
        "Webhook event %s for %s delivered to %d channel(s)",
        event_type,
        full_name,
        sent_count,
    )
    return web.Response(
        text=json.dumps({"status": "delivered", "channels": sent_count}),
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
