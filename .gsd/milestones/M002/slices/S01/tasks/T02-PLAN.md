---
estimated_steps: 5
estimated_files: 3
skills_used:
  - test
  - review
---

# T02: Implement GitHubClient with JWT auth and repo validation

**Slice:** S01 — GitHub Client + Channel-Repo Linking
**Milestone:** M002

## Description

Create the GitHubClient class — the highest-risk component in this slice. It handles GitHub App JWT generation (RS256 signing with a PEM private key), installation token exchange, token caching with expiry-aware refresh, and the get_repo() API method. This is the foundation every downstream slice builds on. All GitHub API calls are tested against mocked httpx responses — no real GitHub credentials needed.

## Steps

1. **Create `bot/github_client.py` with the GitHubClient class.** The class takes `app_id: str`, `private_key: str`, and an optional `installation_id: int | None` (auto-discovered or configured). Key methods:
   - `_generate_jwt() -> str`: Generate an RS256-signed JWT using PyJWT with the App's private key. The JWT payload has `iss` (app_id), `iat` (now - 60s), `exp` (now + 10min). This is the GitHub App authentication standard.
   - `_get_installation_token() -> str`: POST to `/app/installations/{installation_id}/access_tokens` using the JWT as Bearer auth. Parse the response for `token` and `expires_at`. Cache the token and its expiry.
   - `_ensure_token() -> str`: Check if the cached installation token is still valid (not expired with a safety margin). If expired or missing, call `_get_installation_token()`. Return the valid token.
   - `get_repo(owner: str, repo: str) -> dict`: GET `/repos/{owner}/{repo}` using the installation token. Return the response JSON on success. Raise `GitHubAPIError` on 404 (repo not found) or other errors.
   - `_get_installation_id() -> int`: GET `/app/installations` using JWT auth, return the first installation's ID. Called once if installation_id is not provided.

2. **Define exception classes.** Create `GitHubAPIError(Exception)` with status_code and message attributes, and `GitHubConfigError(Exception)` for missing/invalid config.

3. **Use httpx.AsyncClient for all HTTP calls.** Create the client in `__init__` with `base_url="https://api.github.com"` and default headers (`Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`). Use `async with` or manage lifecycle cleanly.

4. **Add structured logging.** Log JWT generation (without the JWT value), token exchange success/failure, API call URLs and status codes, token refresh events. Never log private keys or tokens.

5. **Write comprehensive tests in `tests/test_github_client.py`.** Test cases:
   - JWT generation produces a valid JWT with correct claims (iss, iat, exp) — decode and verify
   - Installation token exchange with mocked httpx response (mock POST to /app/installations/*/access_tokens)
   - Token caching: second call to _ensure_token() uses cached token, doesn't make API call
   - Token refresh: expired token triggers new exchange
   - get_repo() success: mocked GET /repos/owner/repo returns 200 with repo data
   - get_repo() 404: mocked GET returns 404, GitHubAPIError raised
   - get_repo() server error: mocked GET returns 500, GitHubAPIError raised
   - _get_installation_id() with mocked response
   - Use `unittest.mock.patch` or `pytest-httpx` or manual httpx mock transport to mock HTTP calls. Prefer `httpx.MockTransport` for clean async mocking without additional dependencies.

## Must-Haves

- [ ] GitHubClient generates RS256-signed JWTs with correct claims
- [ ] Installation token exchange works via mocked API call
- [ ] Token caching prevents redundant API calls; expired tokens trigger refresh
- [ ] get_repo() returns repo data on success, raises GitHubAPIError on 404/error
- [ ] GitHubAPIError and GitHubConfigError exception classes defined
- [ ] All API calls use httpx.AsyncClient with proper auth headers
- [ ] No secrets (private keys, tokens, JWTs) appear in log output
- [ ] Tests use mocked httpx responses — no real GitHub API calls

## Verification

- `.venv/bin/python -m pytest tests/test_github_client.py -v` — all tests pass
- `.venv/bin/python -c "from bot.github_client import GitHubClient, GitHubAPIError"` — imports succeed

## Observability Impact

- Signals added: structured logs for JWT generation timing, token exchange success/failure with status code, API call URL + status code, token cache hits/misses
- How a future agent inspects this: grep logs for `github_client` logger messages
- Failure state exposed: GitHubAPIError includes status_code and message; token exchange failure logged with response status

## Inputs

- `bot/config.py` — Config with github_app_id, github_private_key (from T01)
- `pyproject.toml` — PyJWT[crypto] dependency available (from T01)

## Expected Output

- `bot/github_client.py` — GitHubClient class with JWT auth, token management, get_repo()
- `tests/test_github_client.py` — comprehensive test suite for GitHubClient
