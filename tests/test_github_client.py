"""Tests for bot.github_client — JWT auth, token management, repo API."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import jwt as pyjwt
import pytest

from bot.github_client import GitHubAPIError, GitHubClient, GitHubConfigError

# ---------------------------------------------------------------------------
# Test RSA key pair (2048-bit, generated for testing only — not a real secret)
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

_test_private_key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_KEY = _test_private_key_obj.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
TEST_PUBLIC_KEY = _test_private_key_obj.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

TEST_APP_ID = "12345"
TEST_INSTALLATION_ID = 67890


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_expiry(minutes: int = 60) -> str:
    """Return an ISO-8601 timestamp *minutes* from now (GitHub format)."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_expiry(minutes: int = 5) -> str:
    """Return an ISO-8601 timestamp *minutes* ago (expired)."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_transport(handler):
    """Build an httpx.MockTransport from a sync handler function.

    The handler receives an ``httpx.Request`` and returns an ``httpx.Response``.
    """
    return httpx.MockTransport(handler)


def _make_client(
    transport,
    *,
    app_id: str = TEST_APP_ID,
    private_key: str = TEST_PRIVATE_KEY,
    installation_id: int | None = TEST_INSTALLATION_ID,
) -> GitHubClient:
    """Build a GitHubClient backed by a mock transport."""
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url=GitHubClient.BASE_URL,
        headers=GitHubClient.DEFAULT_HEADERS,
    )
    return GitHubClient(
        app_id=app_id,
        private_key=private_key,
        installation_id=installation_id,
        http_client=http_client,
    )


# ===========================================================================
# JWT generation
# ===========================================================================


class TestJWTGeneration:
    """Test _generate_jwt produces correctly structured RS256 JWTs."""

    def test_jwt_has_correct_claims(self):
        """JWT payload contains iss, iat, and exp with expected values."""
        client = _make_client(_make_transport(lambda r: httpx.Response(200)))
        now = int(time.time())
        token = client._generate_jwt()

        decoded = pyjwt.decode(
            token,
            TEST_PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_exp": False},
        )
        assert decoded["iss"] == TEST_APP_ID
        # iat is backdated by 60s
        assert abs(decoded["iat"] - (now - 60)) <= 2
        # exp is 10 minutes from now
        assert abs(decoded["exp"] - (now + 600)) <= 2

    def test_jwt_is_rs256_signed(self):
        """JWT can be verified with the matching public key."""
        client = _make_client(_make_transport(lambda r: httpx.Response(200)))
        token = client._generate_jwt()

        # Should not raise
        decoded = pyjwt.decode(
            token,
            TEST_PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_exp": False},
        )
        assert "iss" in decoded

    def test_jwt_rejected_with_wrong_key(self):
        """JWT verification fails with a different key."""
        client = _make_client(_make_transport(lambda r: httpx.Response(200)))
        token = client._generate_jwt()

        other_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        with pytest.raises(pyjwt.exceptions.InvalidSignatureError):
            pyjwt.decode(
                token,
                other_key,
                algorithms=["RS256"],
                options={"verify_exp": False},
            )


# ===========================================================================
# Installation token exchange
# ===========================================================================


class TestInstallationToken:
    """Test _get_installation_token exchanges JWT for an access token."""

    async def test_token_exchange_success(self):
        """Successful token exchange stores token and expiry."""
        expected_token = "ghs_mock_installation_token_abc123"
        expected_expiry = _future_expiry(60)

        def handler(request: httpx.Request) -> httpx.Response:
            assert "Bearer " in request.headers["authorization"]
            return httpx.Response(
                201,
                json={"token": expected_token, "expires_at": expected_expiry},
            )

        client = _make_client(_make_transport(handler))
        token = await client._get_installation_token()

        assert token == expected_token
        assert client._cached_token == expected_token
        assert client._token_expires_at is not None
        await client.close()

    async def test_token_exchange_failure_raises(self):
        """Non-201 response raises GitHubAPIError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client._get_installation_token()
        assert exc_info.value.status_code == 401
        await client.close()


# ===========================================================================
# Token caching and refresh
# ===========================================================================


class TestTokenCaching:
    """Test _ensure_token caching and expiry-aware refresh."""

    async def test_cached_token_reused(self):
        """Second call uses the cached token without an API call."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                201,
                json={
                    "token": "ghs_cached_token",
                    "expires_at": _future_expiry(60),
                },
            )

        client = _make_client(_make_transport(handler))
        token1 = await client._ensure_token()
        token2 = await client._ensure_token()

        assert token1 == token2 == "ghs_cached_token"
        # Only one HTTP call should have been made
        assert call_count == 1
        await client.close()

    async def test_expired_token_triggers_refresh(self):
        """Expired token causes a new token exchange."""
        tokens = iter(["ghs_first_token", "ghs_second_token"])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "token": next(tokens),
                    "expires_at": _future_expiry(60),
                },
            )

        client = _make_client(_make_transport(handler))

        # Get first token
        token1 = await client._ensure_token()
        assert token1 == "ghs_first_token"

        # Simulate expiry by setting expires_at to the past
        client._token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        # Should refresh
        token2 = await client._ensure_token()
        assert token2 == "ghs_second_token"
        await client.close()

    async def test_token_nearing_expiry_triggers_refresh(self):
        """Token within the 5-minute safety margin triggers refresh."""
        tokens = iter(["ghs_old", "ghs_new"])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "token": next(tokens),
                    "expires_at": _future_expiry(60),
                },
            )

        client = _make_client(_make_transport(handler))
        await client._ensure_token()

        # Set expiry to 3 minutes from now (within 5-min margin)
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        token = await client._ensure_token()
        assert token == "ghs_new"
        await client.close()


# ===========================================================================
# get_repo
# ===========================================================================


class TestGetRepo:
    """Test get_repo API method."""

    async def test_get_repo_success(self):
        """Successful repo fetch returns parsed JSON."""
        repo_data = {
            "id": 42,
            "full_name": "octocat/Hello-World",
            "private": False,
            "description": "A test repo",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_repo_test",
                        "expires_at": _future_expiry(60),
                    },
                )
            if "/repos/octocat/Hello-World" in str(request.url):
                return httpx.Response(200, json=repo_data)
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.get_repo("octocat", "Hello-World")
        assert result["full_name"] == "octocat/Hello-World"
        assert result["id"] == 42
        await client.close()

    async def test_get_repo_not_found(self):
        """404 response raises GitHubAPIError with status_code 404."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_repo_test",
                        "expires_at": _future_expiry(60),
                    },
                )
            return httpx.Response(404, json={"message": "Not Found"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.get_repo("octocat", "nonexistent")
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.message.lower()
        await client.close()

    async def test_get_repo_server_error(self):
        """500 response raises GitHubAPIError with status_code 500."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_repo_test",
                        "expires_at": _future_expiry(60),
                    },
                )
            return httpx.Response(500, json={"message": "Internal Server Error"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.get_repo("octocat", "broken")
        assert exc_info.value.status_code == 500
        await client.close()

    async def test_get_repo_uses_token_auth(self):
        """get_repo sends the installation token as Authorization header."""
        captured_auth = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_auth_check",
                        "expires_at": _future_expiry(60),
                    },
                )
            if "/repos/" in str(request.url):
                captured_auth.append(request.headers.get("authorization"))
                return httpx.Response(200, json={"id": 1, "full_name": "a/b"})
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.get_repo("a", "b")
        assert captured_auth == ["token ghs_auth_check"]
        await client.close()


# ===========================================================================
# Installation ID discovery
# ===========================================================================


class TestInstallationDiscovery:
    """Test _get_installation_id auto-discovery."""

    async def test_discovers_first_installation(self):
        """Returns the first installation's ID."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/app/installations" in str(request.url) and request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {"id": 11111, "account": {"login": "org1"}},
                        {"id": 22222, "account": {"login": "org2"}},
                    ],
                )
            return httpx.Response(404)

        client = _make_client(
            _make_transport(handler), installation_id=None
        )
        installation_id = await client._get_installation_id()
        assert installation_id == 11111
        await client.close()

    async def test_no_installations_raises(self):
        """Empty installations list raises GitHubAPIError."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/app/installations" in str(request.url):
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        client = _make_client(
            _make_transport(handler), installation_id=None
        )
        with pytest.raises(GitHubAPIError) as exc_info:
            await client._get_installation_id()
        assert exc_info.value.status_code == 404
        await client.close()

    async def test_api_error_raises(self):
        """Non-200 response when listing installations raises GitHubAPIError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Unauthorized"})

        client = _make_client(
            _make_transport(handler), installation_id=None
        )
        with pytest.raises(GitHubAPIError) as exc_info:
            await client._get_installation_id()
        assert exc_info.value.status_code == 401
        await client.close()

    async def test_auto_discover_on_token_exchange(self):
        """When installation_id is None, token exchange discovers it first."""
        call_sequence = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if request.method == "GET" and "/app/installations" in url and "/access_tokens" not in url:
                call_sequence.append("list_installations")
                return httpx.Response(
                    200,
                    json=[{"id": 99999, "account": {"login": "auto"}}],
                )
            if "/access_tokens" in url:
                call_sequence.append("token_exchange")
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_auto",
                        "expires_at": _future_expiry(60),
                    },
                )
            return httpx.Response(404)

        client = _make_client(
            _make_transport(handler), installation_id=None
        )
        token = await client._ensure_token()
        assert token == "ghs_auto"
        assert client._installation_id == 99999
        assert call_sequence == ["list_installations", "token_exchange"]
        await client.close()


# ===========================================================================
# create_issue
# ===========================================================================


class TestCreateIssue:
    """Test create_issue API method."""

    def _token_handler(self, request: httpx.Request) -> httpx.Response:
        """Handle installation token exchange requests."""
        return httpx.Response(
            201,
            json={
                "token": "ghs_create_issue_test",
                "expires_at": _future_expiry(60),
            },
        )

    async def test_create_issue_success(self):
        """Successful issue creation returns parsed JSON with number and html_url."""
        issue_data = {
            "number": 1,
            "html_url": "https://github.com/octocat/Hello-World/issues/1",
            "title": "Bug report",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/octocat/Hello-World/issues" in str(request.url):
                return httpx.Response(201, json=issue_data)
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.create_issue(
            "octocat", "Hello-World", "Bug report", "Something is broken"
        )
        assert result["number"] == 1
        assert result["html_url"] == "https://github.com/octocat/Hello-World/issues/1"
        await client.close()

    async def test_create_issue_sends_auth_header(self):
        """create_issue sends the installation token as Authorization header."""
        captured_auth = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/" in str(request.url) and request.method == "POST":
                captured_auth.append(request.headers.get("authorization"))
                return httpx.Response(
                    201, json={"number": 1, "html_url": "https://github.com/a/b/issues/1"}
                )
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.create_issue("a", "b", "Title", "Body")
        assert captured_auth == ["token ghs_create_issue_test"]
        await client.close()

    async def test_create_issue_sends_title_and_body(self):
        """create_issue sends title and body in the JSON request payload."""
        import json

        captured_body = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/" in str(request.url) and request.method == "POST":
                captured_body.append(json.loads(request.content))
                return httpx.Response(
                    201, json={"number": 1, "html_url": "https://github.com/a/b/issues/1"}
                )
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.create_issue("a", "b", "My Title", "My Body")
        assert captured_body[0]["title"] == "My Title"
        assert captured_body[0]["body"] == "My Body"
        await client.close()

    async def test_create_issue_includes_labels_when_provided(self):
        """Labels are included in the JSON payload when provided."""
        import json

        captured_body = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/" in str(request.url) and request.method == "POST":
                captured_body.append(json.loads(request.content))
                return httpx.Response(
                    201, json={"number": 1, "html_url": "https://github.com/a/b/issues/1"}
                )
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.create_issue("a", "b", "Title", "Body", labels=["bug", "urgent"])
        assert captured_body[0]["labels"] == ["bug", "urgent"]
        await client.close()

    async def test_create_issue_omits_labels_when_none(self):
        """Labels key is absent from the JSON payload when labels=None."""
        import json

        captured_body = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/" in str(request.url) and request.method == "POST":
                captured_body.append(json.loads(request.content))
                return httpx.Response(
                    201, json={"number": 1, "html_url": "https://github.com/a/b/issues/1"}
                )
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.create_issue("a", "b", "Title", "Body", labels=None)
        assert "labels" not in captured_body[0]
        await client.close()

    async def test_create_issue_422_raises_error(self):
        """422 validation error raises GitHubAPIError with status_code 422."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(
                422, json={"message": "Validation Failed", "errors": [{"field": "title"}]}
            )

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.create_issue("octocat", "Hello-World", "", "Body")
        assert exc_info.value.status_code == 422
        assert "octocat/Hello-World" in exc_info.value.message
        await client.close()

    async def test_create_issue_500_raises_error(self):
        """500 server error raises GitHubAPIError with status_code 500."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(
                500, json={"message": "Internal Server Error"}
            )

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.create_issue("octocat", "broken", "Title", "Body")
        assert exc_info.value.status_code == 500
        assert "octocat/broken" in exc_info.value.message
        await client.close()


# ===========================================================================
# Exception classes
# ===========================================================================


class TestExceptions:
    """Test exception class structure."""

    def test_github_api_error_attributes(self):
        """GitHubAPIError stores status_code and message."""
        err = GitHubAPIError(403, "Forbidden")
        assert err.status_code == 403
        assert err.message == "Forbidden"
        assert "403" in str(err)
        assert "Forbidden" in str(err)

    def test_github_config_error_is_exception(self):
        """GitHubConfigError is a plain Exception subclass."""
        err = GitHubConfigError("missing private key")
        assert isinstance(err, Exception)
        assert "missing private key" in str(err)


# ===========================================================================
# Logging safety — no secrets in logs
# ===========================================================================


# ===========================================================================
# list_pulls
# ===========================================================================


class TestListPulls:
    """Test list_pulls API method."""

    def _token_handler(self, request: httpx.Request) -> httpx.Response:
        """Handle installation token exchange requests."""
        return httpx.Response(
            201,
            json={
                "token": "ghs_list_pulls_test",
                "expires_at": _future_expiry(60),
            },
        )

    async def test_list_pulls_success(self):
        """Successful pull request fetch returns a list of PR dicts."""
        prs = [
            {"number": 10, "title": "Add feature X", "user": {"login": "alice"}},
            {"number": 9, "title": "Fix bug Y", "user": {"login": "bob"}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/octocat/Hello-World/pulls" in str(request.url):
                return httpx.Response(200, json=prs)
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.list_pulls("octocat", "Hello-World")
        assert len(result) == 2
        assert result[0]["number"] == 10
        assert result[1]["title"] == "Fix bug Y"
        await client.close()

    async def test_list_pulls_empty(self):
        """Returns empty list when no open PRs exist."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/pulls" in str(request.url):
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.list_pulls("octocat", "empty-repo")
        assert result == []
        await client.close()

    async def test_list_pulls_404(self):
        """404 response raises GitHubAPIError with status_code 404."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(404, json={"message": "Not Found"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.list_pulls("octocat", "nonexistent")
        assert exc_info.value.status_code == 404
        await client.close()

    async def test_list_pulls_500(self):
        """500 response raises GitHubAPIError with status_code 500."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(500, json={"message": "Internal Server Error"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.list_pulls("octocat", "broken")
        assert exc_info.value.status_code == 500
        await client.close()

    async def test_list_pulls_sends_auth_header(self):
        """list_pulls sends the installation token as Authorization header."""
        captured_auth = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/pulls" in str(request.url):
                captured_auth.append(request.headers.get("authorization"))
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.list_pulls("a", "b")
        assert captured_auth == ["token ghs_list_pulls_test"]
        await client.close()


# ===========================================================================
# list_commits
# ===========================================================================


class TestListCommits:
    """Test list_commits API method."""

    def _token_handler(self, request: httpx.Request) -> httpx.Response:
        """Handle installation token exchange requests."""
        return httpx.Response(
            201,
            json={
                "token": "ghs_list_commits_test",
                "expires_at": _future_expiry(60),
            },
        )

    async def test_list_commits_success(self):
        """Successful commit fetch returns a list of commit dicts."""
        commits = [
            {"sha": "abc1234", "commit": {"message": "Initial commit"}},
            {"sha": "def5678", "commit": {"message": "Add README"}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/repos/octocat/Hello-World/commits" in str(request.url):
                return httpx.Response(200, json=commits)
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.list_commits("octocat", "Hello-World")
        assert len(result) == 2
        assert result[0]["sha"] == "abc1234"
        assert result[1]["commit"]["message"] == "Add README"
        await client.close()

    async def test_list_commits_empty(self):
        """Returns empty list when no commits exist."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/commits" in str(request.url):
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        result = await client.list_commits("octocat", "empty-repo")
        assert result == []
        await client.close()

    async def test_list_commits_404(self):
        """404 response raises GitHubAPIError with status_code 404."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(404, json={"message": "Not Found"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.list_commits("octocat", "nonexistent")
        assert exc_info.value.status_code == 404
        await client.close()

    async def test_list_commits_500(self):
        """500 response raises GitHubAPIError with status_code 500."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            return httpx.Response(500, json={"message": "Internal Server Error"})

        client = _make_client(_make_transport(handler))
        with pytest.raises(GitHubAPIError) as exc_info:
            await client.list_commits("octocat", "broken")
        assert exc_info.value.status_code == 500
        await client.close()

    async def test_list_commits_sends_auth_header(self):
        """list_commits sends the installation token as Authorization header."""
        captured_auth = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return self._token_handler(request)
            if "/commits" in str(request.url):
                captured_auth.append(request.headers.get("authorization"))
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        client = _make_client(_make_transport(handler))
        await client.list_commits("a", "b")
        assert captured_auth == ["token ghs_list_commits_test"]
        await client.close()


# ===========================================================================
# Logging safety — no secrets in logs
# ===========================================================================


class TestLoggingSafety:
    """Verify no secrets leak into log output."""

    async def test_no_private_key_in_logs(self, caplog):
        """Private key material never appears in log output."""
        def handler(request: httpx.Request) -> httpx.Response:
            if "/access_tokens" in str(request.url):
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_secret_token_xyz",
                        "expires_at": _future_expiry(60),
                    },
                )
            return httpx.Response(200, json={"id": 1, "full_name": "a/b"})

        client = _make_client(_make_transport(handler))
        with caplog.at_level("DEBUG"):
            await client._ensure_token()
            await client.get_repo("a", "b")

        log_text = caplog.text
        # Private key fragments must not appear
        assert "BEGIN" not in log_text
        assert "PRIVATE" not in log_text
        # Installation tokens must not appear
        assert "ghs_secret_token_xyz" not in log_text
        await client.close()
