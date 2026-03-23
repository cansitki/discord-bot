"""Tests for bot.oauth — OAuth PKCE flow, token exchange, refresh, and OAuthManager."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.oauth import (
    OAuthError,
    OAuthManager,
    OAuthTokens,
    build_auth_url,
    exchange_code,
    generate_pkce,
    refresh_access_token,
)


# ── PKCE generation ─────────────────────────────────────────────────


class TestGeneratePKCE:
    """generate_pkce() creates valid verifier + challenge pairs."""

    def test_returns_verifier_and_challenge(self) -> None:
        """Returns a tuple of two base64url strings."""
        verifier, challenge = generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 20
        assert len(challenge) > 20

    def test_verifier_and_challenge_differ(self) -> None:
        """Verifier and challenge are not identical."""
        verifier, challenge = generate_pkce()
        assert verifier != challenge

    def test_unique_per_call(self) -> None:
        """Each call generates a unique verifier."""
        v1, _ = generate_pkce()
        v2, _ = generate_pkce()
        assert v1 != v2

    def test_no_padding(self) -> None:
        """base64url encoding has no '=' padding characters."""
        verifier, challenge = generate_pkce()
        assert "=" not in verifier
        assert "=" not in challenge

    def test_base64url_charset(self) -> None:
        """Output uses only base64url characters (no + or /)."""
        verifier, challenge = generate_pkce()
        for char in ("+", "/"):
            assert char not in verifier
            assert char not in challenge


# ── build_auth_url ───────────────────────────────────────────────────


class TestBuildAuthUrl:
    """build_auth_url() constructs a valid Anthropic OAuth URL."""

    def test_contains_authorize_endpoint(self) -> None:
        url = build_auth_url("verifier123", "challenge456")
        assert "https://claude.ai/oauth/authorize" in url

    def test_contains_pkce_challenge(self) -> None:
        url = build_auth_url("verifier123", "challenge456")
        assert "code_challenge=challenge456" in url
        assert "code_challenge_method=S256" in url

    def test_contains_client_id(self) -> None:
        url = build_auth_url("v", "c")
        assert "client_id=" in url

    def test_contains_redirect_uri(self) -> None:
        url = build_auth_url("v", "c")
        assert "redirect_uri=" in url

    def test_contains_scopes(self) -> None:
        url = build_auth_url("v", "c")
        assert "scope=" in url

    def test_state_equals_verifier(self) -> None:
        url = build_auth_url("my_verifier", "my_challenge")
        assert "state=my_verifier" in url


# ── OAuthTokens ─────────────────────────────────────────────────────


class TestOAuthTokens:
    """OAuthTokens dataclass behavior."""

    def test_not_expired_when_future(self) -> None:
        tokens = OAuthTokens(
            access_token="sk-ant-oat-test",
            refresh_token="refresh",
            expires_at=time.time() + 3600,
        )
        assert not tokens.is_expired

    def test_expired_when_past(self) -> None:
        tokens = OAuthTokens(
            access_token="sk-ant-oat-test",
            refresh_token="refresh",
            expires_at=time.time() - 100,
        )
        assert tokens.is_expired

    def test_is_oauth_token_true(self) -> None:
        tokens = OAuthTokens(
            access_token="sk-ant-oat-abc123",
            refresh_token="refresh",
            expires_at=time.time() + 3600,
        )
        assert tokens.is_oauth_token

    def test_is_oauth_token_false_for_api_key(self) -> None:
        tokens = OAuthTokens(
            access_token="sk-ant-api-key",
            refresh_token="refresh",
            expires_at=time.time() + 3600,
        )
        assert not tokens.is_oauth_token


# ── exchange_code ────────────────────────────────────────────────────


class TestExchangeCode:
    """exchange_code() POSTs to the token endpoint."""

    @pytest.mark.asyncio
    async def test_successful_exchange(self) -> None:
        """Successful token exchange returns OAuthTokens."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "sk-ant-oat-new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }

        with patch("bot.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            tokens = await exchange_code("code123", "state456", "verifier789")

        assert tokens.access_token == "sk-ant-oat-new-token"
        assert tokens.refresh_token == "new-refresh"
        assert tokens.expires_at > time.time()
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_exchange_raises(self) -> None:
        """Failed token exchange raises OAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_grant"

        with patch("bot.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="Token exchange failed"):
                await exchange_code("bad-code", "state", "verifier")


# ── refresh_access_token ─────────────────────────────────────────────


class TestRefreshAccessToken:
    """refresh_access_token() refreshes expired tokens."""

    @pytest.mark.asyncio
    async def test_successful_refresh(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "sk-ant-oat-refreshed",
            "refresh_token": "new-refresh-2",
            "expires_in": 7200,
        }

        with patch("bot.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            tokens = await refresh_access_token("old-refresh")

        assert tokens.access_token == "sk-ant-oat-refreshed"
        assert tokens.refresh_token == "new-refresh-2"

    @pytest.mark.asyncio
    async def test_failed_refresh_raises(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "invalid_refresh_token"

        with patch("bot.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(OAuthError, match="Token refresh failed"):
                await refresh_access_token("bad-refresh")


# ── OAuthManager ─────────────────────────────────────────────────────


class TestOAuthManager:
    """OAuthManager orchestrates the full flow with SQLite persistence."""

    @pytest.fixture
    async def db(self, tmp_path):
        """Provide a DatabaseManager with the oauth_tokens table."""
        from bot.database import DatabaseManager

        db = DatabaseManager(str(tmp_path / "test.db"))
        await db.connect()
        # Create the oauth_tokens table directly
        await db.execute(
            "CREATE TABLE IF NOT EXISTS oauth_tokens ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "access_token TEXT NOT NULL, "
            "refresh_token TEXT NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        yield db
        await db.close()

    @pytest.fixture
    def manager(self, db) -> OAuthManager:
        return OAuthManager(db)

    def test_start_flow_returns_flow_id_and_url(self, manager: OAuthManager) -> None:
        """start_flow() returns a flow_id and an authorization URL."""
        flow_id, auth_url = manager.start_flow()
        assert flow_id  # non-empty string
        assert "https://claude.ai/oauth/authorize" in auth_url

    @pytest.mark.asyncio
    async def test_complete_flow_saves_tokens(self, manager: OAuthManager, db) -> None:
        """complete_flow() exchanges the code and saves tokens to DB."""
        flow_id, _ = manager.start_flow()

        mock_tokens = OAuthTokens(
            access_token="sk-ant-oat-test-token",
            refresh_token="test-refresh",
            expires_at=time.time() + 3600,
        )
        with patch("bot.oauth.exchange_code", new_callable=AsyncMock, return_value=mock_tokens):
            tokens = await manager.complete_flow(flow_id, "code123#state456")

        assert tokens.access_token == "sk-ant-oat-test-token"

        # Verify saved in DB
        row = await db.fetchone("SELECT * FROM oauth_tokens WHERE id = 1")
        assert row is not None
        assert row["access_token"] == "sk-ant-oat-test-token"

    @pytest.mark.asyncio
    async def test_complete_flow_unknown_id_raises(self, manager: OAuthManager) -> None:
        """complete_flow() raises OAuthError for unknown flow_id."""
        with pytest.raises(OAuthError, match="Unknown or expired"):
            await manager.complete_flow("unknown-flow", "code#state")

    @pytest.mark.asyncio
    async def test_get_access_token_returns_none_when_empty(self, manager: OAuthManager) -> None:
        """get_access_token() returns None when no tokens are stored."""
        result = await manager.get_access_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_access_token_returns_valid_token(self, manager: OAuthManager, db) -> None:
        """get_access_token() returns the stored token when not expired."""
        await db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            ("sk-ant-oat-valid", "refresh-tok", time.time() + 3600),
        )

        token = await manager.get_access_token()
        assert token == "sk-ant-oat-valid"

    @pytest.mark.asyncio
    async def test_get_access_token_refreshes_expired(self, manager: OAuthManager, db) -> None:
        """get_access_token() refreshes when the token is expired."""
        await db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            ("sk-ant-oat-expired", "refresh-tok", time.time() - 100),
        )

        new_tokens = OAuthTokens(
            access_token="sk-ant-oat-refreshed",
            refresh_token="new-refresh",
            expires_at=time.time() + 3600,
        )
        with patch("bot.oauth.refresh_access_token", new_callable=AsyncMock, return_value=new_tokens):
            token = await manager.get_access_token()

        assert token == "sk-ant-oat-refreshed"

        # Verify the refreshed token was saved
        row = await db.fetchone("SELECT * FROM oauth_tokens WHERE id = 1")
        assert row["access_token"] == "sk-ant-oat-refreshed"

    @pytest.mark.asyncio
    async def test_get_access_token_refresh_failure_raises(self, manager: OAuthManager, db) -> None:
        """get_access_token() raises OAuthError when refresh fails."""
        await db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            ("sk-ant-oat-expired", "bad-refresh", time.time() - 100),
        )

        with patch(
            "bot.oauth.refresh_access_token",
            new_callable=AsyncMock,
            side_effect=OAuthError("Refresh failed"),
        ):
            with pytest.raises(OAuthError, match="Refresh failed"):
                await manager.get_access_token()

    @pytest.mark.asyncio
    async def test_has_tokens(self, manager: OAuthManager, db) -> None:
        """has_tokens() reflects stored state."""
        assert not await manager.has_tokens()

        await db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            ("token", "refresh", time.time() + 3600),
        )
        # Clear cache
        manager._cached_tokens = None

        assert await manager.has_tokens()

    @pytest.mark.asyncio
    async def test_clear_tokens(self, manager: OAuthManager, db) -> None:
        """clear_tokens() removes tokens from DB and cache."""
        await db.execute(
            "INSERT INTO oauth_tokens (id, access_token, refresh_token, expires_at) "
            "VALUES (1, ?, ?, ?)",
            ("token", "refresh", time.time() + 3600),
        )
        manager._cached_tokens = OAuthTokens("token", "refresh", time.time() + 3600)

        await manager.clear_tokens()

        assert manager._cached_tokens is None
        row = await db.fetchone("SELECT * FROM oauth_tokens WHERE id = 1")
        assert row is None

    @pytest.mark.asyncio
    async def test_flow_id_single_use(self, manager: OAuthManager) -> None:
        """Each flow_id can only be used once."""
        flow_id, _ = manager.start_flow()

        mock_tokens = OAuthTokens("token", "refresh", time.time() + 3600)
        with patch("bot.oauth.exchange_code", new_callable=AsyncMock, return_value=mock_tokens):
            await manager.complete_flow(flow_id, "code#state")

        # Second attempt with same flow_id should fail
        with pytest.raises(OAuthError, match="Unknown or expired"):
            await manager.complete_flow(flow_id, "code#state")
