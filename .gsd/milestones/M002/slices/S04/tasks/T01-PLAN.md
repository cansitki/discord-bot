---
estimated_steps: 5
estimated_files: 2
skills_used:
  - test
  - best-practices
---

# T01: Implement webhook module with signature verification, health endpoint, and security tests

**Slice:** S04 — Webhook Receiver + Event Notifications
**Milestone:** M002

## Description

Create the `bot/webhook.py` module with an aiohttp web application factory, HMAC-SHA256 signature verification, a health endpoint, and the webhook POST endpoint skeleton. This task focuses on the security layer (signature verification) and the aiohttp test harness — the highest-risk, most novel parts of the slice. Event routing and embed formatting are stubbed (return 200) and completed in T02.

The architecture follows D017: the aiohttp app is created by a factory function `create_webhook_app(bot)` that takes the bot instance and returns an `aiohttp.web.Application`. The bot reference is stored on `app["bot"]` so handlers can access `bot.db` and `bot.get_channel()`.

## Steps

1. **Create `bot/webhook.py`** with:
   - `verify_signature(payload_body: bytes, secret: str, signature_header: str) -> bool` — Uses `hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()` with `hmac.compare_digest()` for timing-safe comparison. Returns False if signature_header is empty/None.
   - `create_webhook_app(bot) -> aiohttp.web.Application` — Factory function. Stores `bot` on `app["bot"]`. Registers routes: `POST /webhook/github` → `handle_webhook`, `GET /health` → `handle_health`.
   - `handle_health(request)` — Returns `web.Response(text="ok", status=200)`.
   - `handle_webhook(request)` — Reads raw body, gets `X-Hub-Signature-256` header (401 if missing), gets webhook secret from `app["bot"].config.github_webhook_secret` (503 if None), calls `verify_signature` (403 if invalid). Reads `X-GitHub-Event` header (400 if missing). For now, logs the event type and returns 200 with `{"status": "accepted"}`. The actual event dispatch is added in T02.

2. **Create `tests/test_webhook.py`** with test infrastructure:
   - Import `aiohttp.test_utils.TestClient`, `TestServer` and `create_webhook_app` from `bot.webhook`.
   - Create a `_make_mock_bot()` helper that returns a `MagicMock` with `config.github_webhook_secret` set and `db = AsyncMock()` and `get_channel = MagicMock()`.
   - Create a `_sign_payload(payload_bytes: bytes, secret: str) -> str` helper that computes `sha256=<hex>` signature.
   - Use `async with TestClient(TestServer(create_webhook_app(bot))) as client:` pattern for each test.

3. **Write signature verification tests** (~6 tests):
   - `test_verify_signature_valid` — known payload + secret → True
   - `test_verify_signature_invalid` — tampered payload → False
   - `test_verify_signature_wrong_secret` — wrong secret → False
   - `test_verify_signature_missing_header` — None/empty signature → False
   - `test_verify_signature_empty_payload` — empty bytes + valid sig → True
   - `test_verify_signature_uses_compare_digest` — inspect source or verify timing-safe (structural test)

4. **Write webhook security endpoint tests** (~5 tests):
   - `test_webhook_valid_signature_accepted` — signed POST → 200
   - `test_webhook_invalid_signature_rejected` — bad sig → 403
   - `test_webhook_missing_signature_rejected` — no header → 401
   - `test_webhook_missing_secret_config` — bot has `github_webhook_secret=None` → 503
   - `test_webhook_missing_event_header` — valid sig but no `X-GitHub-Event` → 400

5. **Write health and misc tests** (~4 tests):
   - `test_health_returns_200` — GET /health → 200 "ok"
   - `test_health_post_not_allowed` — POST /health → 405
   - `test_unrecognised_event_returns_200` — valid sig, `X-GitHub-Event: unknown_event` → 200
   - `test_webhook_get_not_allowed` — GET /webhook/github → 405

## Must-Haves

- [ ] `verify_signature()` uses `hmac.compare_digest()` for timing-safe comparison
- [ ] Missing `X-Hub-Signature-256` header → 401 response
- [ ] Invalid signature → 403 response
- [ ] Missing `github_webhook_secret` in config → 503 response
- [ ] Missing `X-GitHub-Event` header → 400 response
- [ ] `GET /health` → 200 "ok"
- [ ] Unrecognised event types → 200 (not an error — prevents GitHub retries)
- [ ] aiohttp test harness working with `TestClient`/`TestServer`

## Verification

- `.venv/bin/python -m pytest tests/test_webhook.py -v` — all ~15 tests pass
- `grep -q "compare_digest" bot/webhook.py` — timing-safe comparison used
- `.venv/bin/python -c "from bot.webhook import create_webhook_app, verify_signature"` — imports succeed

## Inputs

- `bot/config.py` — Config dataclass with `github_webhook_secret` field (already exists from S01)
- `bot/bot.py` — DiscordBot class structure (to understand what bot attributes are available on `app["bot"]`)

## Expected Output

- `bot/webhook.py` — New module with `create_webhook_app()`, `verify_signature()`, `handle_webhook()`, `handle_health()`
- `tests/test_webhook.py` — ~15 passing tests covering signature verification, security, health, and edge cases

## Observability Impact

- **New logger**: `bot.webhook` — logs signature verification failures (WARNING), missing webhook secret (ERROR), and received event types (INFO).
- **Inspection surface**: `GET /health` endpoint returns 200 "ok" when the webhook server is running — usable as a liveness probe.
- **Failure visibility**: Signature failures produce WARNING logs with no payload details (redaction-safe). Missing webhook secret produces ERROR log once per rejected request, plus 503 response. Missing headers produce WARNING logs identifying which header is absent.
- **What a future agent inspects**: `grep "bot.webhook" <logs>` for signature rejections, event reception, and configuration errors. The test suite (`tests/test_webhook.py`) covers all security error paths.
