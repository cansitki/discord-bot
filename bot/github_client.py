"""GitHub App client with JWT authentication and installation token management."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx
import jwt

log = logging.getLogger(__name__)


class GitHubConfigError(Exception):
    """Raised when GitHub App configuration is missing or invalid."""


class GitHubAPIError(Exception):
    """Raised when a GitHub API call fails.

    Attributes:
        status_code: HTTP status code from the response.
        message: Human-readable error description.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"GitHub API error {status_code}: {message}")


class GitHubClient:
    """Async client for the GitHub API authenticated as a GitHub App.

    Handles RS256 JWT generation, installation token exchange with
    expiry-aware caching, and API calls to the GitHub REST API.

    Parameters:
        app_id: The GitHub App's numeric ID.
        private_key: PEM-encoded RSA private key for the App.
        installation_id: Optional installation ID.  If not provided, the
            client auto-discovers it on first API call.
        http_client: Optional pre-configured ``httpx.AsyncClient`` (useful
            for testing with mock transports).
    """

    BASE_URL = "https://api.github.com"
    DEFAULT_HEADERS = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: int | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id
        self._cached_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self.DEFAULT_HEADERS,
        )
        log.info("GitHubClient initialised for app_id=%s", app_id)

    # ------------------------------------------------------------------
    # JWT generation
    # ------------------------------------------------------------------

    def _generate_jwt(self) -> str:
        """Generate an RS256-signed JWT for GitHub App authentication.

        The JWT is valid for 10 minutes and back-dated by 60 seconds to
        account for clock skew, following GitHub's recommendation.
        """
        now = int(time.time())
        payload = {
            "iss": self.app_id,
            "iat": now - 60,
            "exp": now + (10 * 60),
        }
        encoded = jwt.encode(payload, self._private_key, algorithm="RS256")
        log.debug("JWT generated for app_id=%s (exp in 10m)", self.app_id)
        return encoded

    # ------------------------------------------------------------------
    # Installation token management
    # ------------------------------------------------------------------

    async def _get_installation_id(self) -> int:
        """Discover the first installation ID for this GitHub App."""
        token = self._generate_jwt()
        resp = await self._client.get(
            "/app/installations",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            log.error(
                "Failed to list installations: status=%d", resp.status_code
            )
            raise GitHubAPIError(
                resp.status_code,
                "Failed to list app installations",
            )
        installations = resp.json()
        if not installations:
            raise GitHubAPIError(404, "No installations found for this app")
        installation_id = installations[0]["id"]
        log.info("Discovered installation_id=%d", installation_id)
        return installation_id

    async def _get_installation_token(self) -> str:
        """Exchange a JWT for an installation access token.

        The token and its expiry are cached internally.
        """
        if self._installation_id is None:
            self._installation_id = await self._get_installation_id()

        token = self._generate_jwt()
        url = f"/app/installations/{self._installation_id}/access_tokens"
        resp = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 201:
            log.error(
                "Token exchange failed: status=%d url=%s",
                resp.status_code,
                url,
            )
            raise GitHubAPIError(
                resp.status_code,
                "Failed to create installation access token",
            )

        data = resp.json()
        self._cached_token = data["token"]
        self._token_expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        )
        log.info(
            "Installation token acquired, expires_at=%s",
            self._token_expires_at.isoformat(),
        )
        return self._cached_token

    async def _ensure_token(self) -> str:
        """Return a valid installation token, refreshing if needed.

        Uses a 5-minute safety margin before the real expiry so we never
        send a token that's about to expire mid-request.
        """
        if self._cached_token and self._token_expires_at:
            now = datetime.now(timezone.utc)
            # 5-minute safety margin
            margin_seconds = 5 * 60
            remaining = (self._token_expires_at - now).total_seconds()
            if remaining > margin_seconds:
                log.debug("Using cached installation token (%.0fs remaining)", remaining)
                return self._cached_token
            log.info("Cached token expiring soon (%.0fs remaining), refreshing", remaining)

        return await self._get_installation_token()

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    async def get_repo(self, owner: str, repo: str) -> dict:
        """Fetch repository metadata from the GitHub API.

        Parameters:
            owner: Repository owner (user or organisation).
            repo: Repository name.

        Returns:
            The parsed JSON response with repository metadata.

        Raises:
            GitHubAPIError: If the repo doesn't exist (404) or the API
                returns an error status code.
        """
        token = await self._ensure_token()
        url = f"/repos/{owner}/{repo}"
        resp = await self._client.get(
            url,
            headers={"Authorization": f"token {token}"},
        )
        log.info("GET %s -> %d", url, resp.status_code)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 404:
            raise GitHubAPIError(404, f"Repository {owner}/{repo} not found")

        raise GitHubAPIError(
            resp.status_code,
            f"Failed to fetch repository {owner}/{repo}",
        )

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict:
        """Create an issue in a GitHub repository.

        Parameters:
            owner: Repository owner (user or organisation).
            repo: Repository name.
            title: Issue title.
            body: Issue body (Markdown).
            labels: Optional list of label names to apply.

        Returns:
            The parsed JSON response with the created issue data,
            including ``number`` and ``html_url``.

        Raises:
            GitHubAPIError: If the API returns a non-201 status code
                (e.g. 422 for validation errors, 404 for missing repo).
        """
        token = await self._ensure_token()
        url = f"/repos/{owner}/{repo}/issues"
        payload: dict = {"title": title, "body": body}
        if labels is not None:
            payload["labels"] = labels
        resp = await self._client.post(
            url,
            json=payload,
            headers={"Authorization": f"token {token}"},
        )
        log.info("POST %s -> %d", url, resp.status_code)

        if resp.status_code == 201:
            return resp.json()

        raise GitHubAPIError(
            resp.status_code,
            f"Failed to create issue in {owner}/{repo}",
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
