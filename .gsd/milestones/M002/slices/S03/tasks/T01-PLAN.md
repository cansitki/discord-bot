---
estimated_steps: 4
estimated_files: 2
skills_used:
  - test
---

# T01: Add list_pulls and list_commits to GitHubClient with tests

**Slice:** S03 — Repo Status Commands
**Milestone:** M002

## Description

Add two new API methods to `GitHubClient` — `list_pulls()` and `list_commits()` — that fetch open PRs and recent commits from the GitHub REST API. These follow the identical pattern as the existing `get_repo()` method: call `_ensure_token()`, make a GET request with installation token auth, return parsed JSON on success, raise `GitHubAPIError` on failure. Both return `list[dict]` (empty list for repos with no PRs/commits).

## Steps

1. Add `list_pulls(self, owner: str, repo: str, *, limit: int = 5) -> list[dict]` to `GitHubClient` in `bot/github_client.py`. It calls `_ensure_token()`, GETs `/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc&per_page={limit}`, sends `Authorization: token {token}`, returns `resp.json()` on 200, raises `GitHubAPIError` on any other status. Log the request URL and status code like `get_repo` does.

2. Add `list_commits(self, owner: str, repo: str, *, limit: int = 5) -> list[dict]` to `GitHubClient` in `bot/github_client.py`. Same pattern: `_ensure_token()`, GETs `/repos/{owner}/{repo}/commits?per_page={limit}`, returns `resp.json()` on 200, raises `GitHubAPIError` on error.

3. Add `TestListPulls` test class to `tests/test_github_client.py` with these tests:
   - `test_list_pulls_success` — returns list of PR dicts from a mocked 200 response
   - `test_list_pulls_empty` — returns empty list `[]` when the response is `[]`
   - `test_list_pulls_404` — raises `GitHubAPIError` with status 404
   - `test_list_pulls_500` — raises `GitHubAPIError` with status 500
   - `test_list_pulls_sends_auth_header` — verifies the `Authorization: token {token}` header is sent

4. Add `TestListCommits` test class to `tests/test_github_client.py` with these tests:
   - `test_list_commits_success` — returns list of commit dicts from a mocked 200 response
   - `test_list_commits_empty` — returns empty list `[]`
   - `test_list_commits_404` — raises `GitHubAPIError` with status 404
   - `test_list_commits_500` — raises `GitHubAPIError` with status 500
   - `test_list_commits_sends_auth_header` — verifies auth header

Use the established `_make_client()` and `_make_transport()` helpers. Follow the `TestGetRepo` / `TestCreateIssue` test patterns exactly — each test sets up a handler function, creates a client via `_make_client(_make_transport(handler))`, and calls the method under test. Token exchange is handled by routing `/access_tokens` requests in the handler.

## Must-Haves

- [ ] `list_pulls()` returns `list[dict]` on 200, raises `GitHubAPIError` on non-200
- [ ] `list_commits()` returns `list[dict]` on 200, raises `GitHubAPIError` on non-200
- [ ] Both methods use `_ensure_token()` and send `Authorization: token {token}`
- [ ] Both accept `limit` keyword arg controlling `per_page` query parameter
- [ ] ~10 new tests all passing, zero regressions in existing tests

## Verification

- `.venv/bin/python -m pytest tests/test_github_client.py -v` — all tests pass including new `TestListPulls` and `TestListCommits`
- `.venv/bin/python -m pytest -q` — zero failures, no regressions

## Inputs

- `bot/github_client.py` — existing GitHubClient with `get_repo()` and `create_issue()` patterns to follow
- `tests/test_github_client.py` — existing test file with `_make_client`, `_make_transport`, `_future_expiry` helpers and `TestGetRepo`/`TestCreateIssue` patterns

## Expected Output

- `bot/github_client.py` — modified with `list_pulls()` and `list_commits()` methods added
- `tests/test_github_client.py` — modified with `TestListPulls` and `TestListCommits` test classes added
