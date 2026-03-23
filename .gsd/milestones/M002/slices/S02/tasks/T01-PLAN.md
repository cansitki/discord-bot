---
estimated_steps: 3
estimated_files: 2
skills_used:
  - test
---

# T01: Add GitHubClient.create_issue() API method with tests

**Slice:** S02 — Natural Language Issue Creation
**Milestone:** M002

## Description

Add a `create_issue()` method to `GitHubClient` that calls `POST /repos/{owner}/{repo}/issues` on the GitHub REST API. This follows the exact same pattern as the existing `get_repo()` method — get an installation token via `_ensure_token()`, make the HTTP request, parse the response or raise `GitHubAPIError`.

The method signature is:
```python
async def create_issue(self, owner: str, repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
```

GitHub API returns `201 Created` with `{"number": N, "html_url": "https://github.com/owner/repo/issues/N", ...}` on success. The method returns the parsed JSON dict. On any non-201 status, it raises `GitHubAPIError`.

## Steps

1. **Add `create_issue()` to `bot/github_client.py`** — Insert after the `get_repo()` method. Use `_ensure_token()` for auth, `POST /repos/{owner}/{repo}/issues` with JSON body `{"title": title, "body": body}`, conditionally add `"labels"` if provided. Return `resp.json()` on 201. Raise `GitHubAPIError(resp.status_code, msg)` on non-201. Log with `log.info("POST %s -> %d", url, resp.status_code)`.

2. **Add `TestCreateIssue` class to `tests/test_github_client.py`** — Follow the existing `TestGetRepo` pattern exactly. Use `_make_transport()` and `_make_client()` helpers. Test cases:
   - `test_create_issue_success` — transport returns 201 with `{"number": 1, "html_url": "..."}`, verify returned dict has those fields
   - `test_create_issue_sends_auth_header` — verify the POST request includes `Authorization: token ...`
   - `test_create_issue_sends_title_and_body` — verify request JSON body contains title and body
   - `test_create_issue_includes_labels_when_provided` — labels in request JSON when passed
   - `test_create_issue_omits_labels_when_none` — no labels key in request JSON when labels=None
   - `test_create_issue_422_raises_error` — 422 response raises GitHubAPIError
   - `test_create_issue_500_raises_error` — 500 response raises GitHubAPIError

3. **Run tests** — Verify all new tests pass and all 261 existing tests still pass.

## Must-Haves

- [ ] `create_issue()` method on `GitHubClient` that POSTs to `/repos/{owner}/{repo}/issues`
- [ ] Returns parsed JSON dict on 201 Created
- [ ] Raises `GitHubAPIError` on non-201 status codes
- [ ] Labels are optional — included in payload only when provided
- [ ] Auth token obtained via `_ensure_token()` (consistent with `get_repo()`)
- [ ] 7+ tests covering success, error, auth, and label handling

## Verification

- `.venv/bin/python -m pytest tests/test_github_client.py -v -k create_issue` — all new tests pass
- `.venv/bin/python -m pytest tests/test_github_client.py -v` — all existing GitHubClient tests still pass
- `.venv/bin/python -m pytest -x -q` — full suite passes (261+ tests, zero regressions)

## Inputs

- `bot/github_client.py` — existing GitHubClient with `get_repo()` method to follow as pattern
- `tests/test_github_client.py` — existing test file with `_make_client()`, `_make_transport()` helpers and `TestGetRepo` class as pattern

## Expected Output

- `bot/github_client.py` — modified with new `create_issue()` method (~25 lines)
- `tests/test_github_client.py` — modified with new `TestCreateIssue` class (~70 lines, 7+ tests)
