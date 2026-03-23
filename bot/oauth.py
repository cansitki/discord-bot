"""Anthropic OAuth flow for Claude Pro/Max subscriptions.

Implements the same PKCE-based authorization code flow used by Claude Code
(pi SDK). Users authenticate with their Claude account and the bot uses
their subscription for API calls — no ANTHROPIC_API_KEY needed.

Flow:
1. Generate PKCE verifier + challenge
2. Build authorization URL → user opens in browser
3. User authorizes and gets a ``code#state`` callback value
4. Exchange code for access_token + refresh_token
5. Store tokens in SQLite, auto-refresh when expired
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Anthropic OAuth endpoints (same as Claude Code / pi SDK)
_CLIENT_ID = base64.b64decode(
    "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
).decode()
_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
_SCOPES = "org:create_api_key user:profile user:inference"

# Token refresh buffer: refresh 5 minutes before expiry
_REFRESH_BUFFER_SECONDS = 5 * 60


@dataclass
class OAuthTokens:
    """OAuth token set stored per-user or globally."""

    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp (seconds)

    @property
    def is_expired(self) -> bool:
        """True when the access token has expired or is within the refresh buffer."""
        return time.time() >= self.expires_at

    @property
    def is_oauth_token(self) -> bool:
        """True when the access token is an Anthropic OAuth token."""
        return "sk-ant-oat" in self.access_token


def generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code verifier and S256 challenge.

    Returns:
        (verifier, challenge) — both base64url-encoded strings.
    """
    verifier_bytes = os.urandom(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")

    challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")

    return verifier, challenge


def build_auth_url(verifier: str, challenge: str) -> str:
    """Build the Anthropic OAuth authorization URL.

    Args:
        verifier: PKCE code verifier (stored server-side for the exchange).
        challenge: PKCE S256 challenge derived from verifier.

    Returns:
        Full authorization URL the user should open in their browser.
    """
    params = {
        "code": "true",
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{_AUTHORIZE_URL}?{query}"


async def exchange_code(code: str, state: str, verifier: str) -> OAuthTokens:
    """Exchange an authorization code for access + refresh tokens.

    Args:
        code: The authorization code from the callback.
        state: The state parameter from the callback.
        verifier: The PKCE code verifier generated at the start of the flow.

    Returns:
        OAuthTokens with access_token, refresh_token, and expiry.

    Raises:
        OAuthError: If the token exchange fails.
    """
    payload = {
        "grant_type": "authorization_code",
        "client_id": _CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": verifier,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if response.status_code != 200:
        error_text = response.text
        logger.error("oauth.exchange_code: token exchange failed: %s", error_text)
        raise OAuthError(f"Token exchange failed (HTTP {response.status_code}): {error_text}")

    data = response.json()
    expires_at = time.time() + data["expires_in"] - _REFRESH_BUFFER_SECONDS

    logger.info("oauth.exchange_code: tokens acquired, expires_in=%d", data["expires_in"])

    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
    )


async def refresh_access_token(refresh_token: str) -> OAuthTokens:
    """Refresh an expired access token using a refresh token.

    Args:
        refresh_token: The stored refresh token.

    Returns:
        New OAuthTokens with fresh access_token, refresh_token, and expiry.

    Raises:
        OAuthError: If the refresh fails (user may need to re-login).
    """
    payload = {
        "grant_type": "refresh_token",
        "client_id": _CLIENT_ID,
        "refresh_token": refresh_token,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if response.status_code != 200:
        error_text = response.text
        logger.error("oauth.refresh: token refresh failed: %s", error_text)
        raise OAuthError(f"Token refresh failed (HTTP {response.status_code}): {error_text}")

    data = response.json()
    expires_at = time.time() + data["expires_in"] - _REFRESH_BUFFER_SECONDS

    logger.info("oauth.refresh: tokens refreshed, expires_in=%d", data["expires_in"])

    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=expires_at,
    )


class OAuthError(Exception):
    """Raised when an OAuth operation fails."""
    pass


class OAuthManager:
    """Manages OAuth tokens with SQLite persistence and auto-refresh.

    Stores tokens in the ``oauth_tokens`` table. Supports a single global
    token set (the bot operator's Claude account). Auto-refreshes expired
    tokens before returning them.
    """

    def __init__(self, db) -> None:
        """Initialize with a DatabaseManager instance."""
        self._db = db
        self._cached_tokens: OAuthTokens | None = None
        self._pending_flows: dict[str, str] = {}  # flow_id -> verifier

    def start_flow(self) -> tuple[str, str]:
        """Start a new OAuth flow.

        Returns:
            (flow_id, auth_url) — flow_id is used to complete the flow later.
        """
        verifier, challenge = generate_pkce()
        auth_url = build_auth_url(verifier, challenge)

        # Use the verifier as the flow ID (unique per flow)
        flow_id = verifier
        self._pending_flows[flow_id] = verifier

        logger.info("oauth_manager.start_flow: flow started")
        return flow_id, auth_url

    async def complete_flow(self, flow_id: str, auth_code: str) -> OAuthTokens:
        """Complete an OAuth flow with the authorization code.

        Args:
            flow_id: The flow_id returned by start_flow().
            auth_code: The full code string from the callback (format: code#state).

        Returns:
            OAuthTokens with the new tokens.

        Raises:
            OAuthError: If the flow_id is unknown or token exchange fails.
        """
        verifier = self._pending_flows.pop(flow_id, None)
        if verifier is None:
            raise OAuthError("Unknown or expired OAuth flow. Please start a new login.")

        # Parse "code#state" format
        parts = auth_code.split("#", 1)
        code = parts[0]
        state = parts[1] if len(parts) > 1 else ""

        tokens = await exchange_code(code, state, verifier)
        await self._save_tokens(tokens)
        self._cached_tokens = tokens

        logger.info("oauth_manager.complete_flow: tokens saved")
        return tokens

    async def get_access_token(self) -> str | None:
        """Get a valid access token, refreshing if expired.

        Returns:
            The access token string, or None if no OAuth tokens are configured.

        Raises:
            OAuthError: If refresh fails (user needs to re-login).
        """
        tokens = await self._load_tokens()
        if tokens is None:
            return None

        if tokens.is_expired:
            logger.info("oauth_manager.get_access_token: token expired, refreshing")
            try:
                tokens = await refresh_access_token(tokens.refresh_token)
                await self._save_tokens(tokens)
                self._cached_tokens = tokens
            except OAuthError:
                # Refresh failed — clear cached tokens so we fall back to API key
                self._cached_tokens = None
                logger.warning("oauth_manager.get_access_token: refresh failed, clearing tokens")
                raise

        return tokens.access_token

    async def has_tokens(self) -> bool:
        """Check if OAuth tokens are stored (may be expired but refreshable)."""
        tokens = await self._load_tokens()
        return tokens is not None

    async def clear_tokens(self) -> None:
        """Remove all stored OAuth tokens (logout)."""
        await self._db.execute("DELETE FROM oauth_tokens WHERE id = 1")
        self._cached_tokens = None
        logger.info("oauth_manager.clear_tokens: tokens cleared")

    async def _load_tokens(self) -> OAuthTokens | None:
        """Load tokens from cache or database."""
        if self._cached_tokens is not None:
            return self._cached_tokens

        row = await self._db.fetchone(
            "SELECT access_token, refresh_token, expires_at FROM oauth_tokens WHERE id = 1"
        )
        if row is None:
            return None

        self._cached_tokens = OAuthTokens(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
        )
        return self._cached_tokens

    async def _save_tokens(self, tokens: OAuthTokens) -> None:
        """Save tokens to database (upsert)."""
        await self._db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "access_token = excluded.access_token, "
            "refresh_token = excluded.refresh_token, "
            "expires_at = excluded.expires_at",
            (tokens.access_token, tokens.refresh_token, tokens.expires_at),
        )
