# S04: Webhook Receiver + Event Notifications

**Goal:** An aiohttp web server embedded in the bot process receives GitHub webhook POST requests, verifies HMAC-SHA256 signatures, routes events to the correct Discord channels via channel_repos lookup, and formats them as Discord embeds.
**Demo:** A push/PR/issue event on GitHub triggers a formatted notification embed in the correct linked Discord channel within seconds. Unsigned payloads are rejected with 401/403.

## Must-Haves

- aiohttp web app with `POST /webhook/github` endpoint and `GET /health` returning 200
- HMAC-SHA256 signature verification using `X-Hub-Signature-256` header — timing-safe via `hmac.compare_digest`
- Invalid/missing signatures rejected (401/403), missing webhook secret returns 503
- Reverse channel_repos lookup: `(repo_owner, repo_name) → [(guild_id, channel_id), ...]`
- Embed formatters for push, issues (opened/closed), pull_request (opened/closed/merged), check_suite (completed)
- Each embed type has appropriate colour, fields, and links
- Unrecognised event types logged and return 200 (don't cause GitHub retries)
- aiohttp server starts in `setup_hook` via `AppRunner`/`TCPSite` on `PORT` env var, stops in `close()`
- `verify-deploy.sh` updated with `bot.webhook` import and `aiohttp` dependency check
- `.env.example` updated with PORT documentation
- All existing tests (315) continue to pass — zero regressions

## Proof Level

- This slice proves: contract
- Real runtime required: no
- Human/UAT required: no (live webhook delivery requires registered GitHub App — documented as manual UAT)

## Verification

- `cd /home/coder/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest tests/test_webhook.py -v` — all ~30 webhook tests pass
- `cd /home/coder/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest tests/ -q` — total test count ≥ 345, zero failures
- `cd /home/coder/discord-bot/.gsd/worktrees/M002 && bash scripts/verify-deploy.sh` — all checks pass including `bot.webhook` import and `aiohttp` dependency

## Observability / Diagnostics

- Runtime signals: `bot.webhook` logger — signature verification results, event type dispatch, channel send success/failure, unrecognised events
- Inspection surfaces: `GET /health` returns 200 when webhook server is running; `channel_repos` table for binding state
- Failure visibility: signature failures logged with event type; channel-not-found warnings logged with channel_id; missing webhook secret logged at startup with 503 response on all webhook requests
- Redaction constraints: webhook secret never logged; payload bodies not logged (may contain sensitive repo data)

## Integration Closure

- Upstream surfaces consumed: `bot/config.py` (`github_webhook_secret`), `bot/database.py` (`fetchall` for channel_repos reverse lookup), `bot/models.py` (`ChannelRepo`), `bot/bot.py` (`setup_hook`, `close`)
- New wiring introduced in this slice: aiohttp `AppRunner`/`TCPSite` started in `setup_hook`, stopped in `close()`; `bot.webhook` module added to verify-deploy checks
- What remains before the milestone is truly usable end-to-end: S02 (issue creation) and S03 (repo status) — independent of S04. Live webhook delivery requires GitHub App webhook URL pointed at Railway deployment (manual UAT).

## Tasks

- [ ] **T01: Implement webhook module with signature verification, health endpoint, and security tests** `est:45m`
  - Why: The aiohttp app factory, HMAC signature verification, and security-layer tests are the highest-risk, most novel components. Getting the test harness (aiohttp test_utils) working first de-risks everything else.
  - Files: `bot/webhook.py`, `tests/test_webhook.py`
  - Do: Create `bot/webhook.py` with `create_webhook_app(bot)` factory, `verify_signature()` function (stdlib hmac+hashlib), `POST /webhook/github` handler (signature check + event type header parsing — stub event routing as 200 for now), `GET /health` handler returning 200 "ok". Write tests using `aiohttp.test_utils.TestClient`/`TestServer`: signature verification (valid, invalid, missing, wrong secret, empty payload), webhook security (401/403/503), health endpoint (GET→200, POST→405), missing X-GitHub-Event header → 400, unrecognised event type → 200.
  - Verify: `.venv/bin/python -m pytest tests/test_webhook.py -v -k "signature or health or security or unrecogn"` — all pass
  - Done when: `bot/webhook.py` exists with working aiohttp app, signature verification rejects bad signatures and accepts valid ones, health endpoint responds 200, ~15 tests pass

- [ ] **T02: Add event embed formatters and routing logic with tests** `est:45m`
  - Why: The domain logic — formatting GitHub events as Discord embeds and routing them to the correct channels — is the user-facing value of this slice. Depends on T01's webhook app skeleton.
  - Files: `bot/webhook.py`, `tests/test_webhook.py`
  - Do: Add embed formatter functions for push (blue, up to 3 commits, compare URL), issues opened/closed (green/purple, title, user, link), pull_request opened/closed/merged (green/purple, merged indicator, link), check_suite completed (green/red/yellow by conclusion). Add reverse channel_repos SQL lookup in the webhook handler: `SELECT guild_id, channel_id FROM channel_repos WHERE repo_owner = ? AND repo_name = ?`. Complete the webhook handler to parse event type → format embed → send to each matched channel via `bot.get_channel().send()`. Handle: missing `repository` key → 400, no matching channels → 200 (accepted, no-op), channel not found by bot → log warning and skip. Write tests for each embed formatter, routing with mock bot.db.fetchall and bot.get_channel, multi-channel routing, missing repo key.
  - Verify: `.venv/bin/python -m pytest tests/test_webhook.py -v` — all ~30 tests pass
  - Done when: All 4 event types produce correctly-coloured embeds with appropriate fields, events route to all linked channels, edge cases handled, ~30 total webhook tests pass

- [ ] **T03: Wire webhook server into bot lifecycle and update infrastructure** `est:30m`
  - Why: The webhook module must integrate into the bot's startup/shutdown lifecycle, and deployment checks must cover the new module. This closes the slice.
  - Files: `bot/bot.py`, `scripts/verify-deploy.sh`, `.env.example`, `tests/test_webhook.py`
  - Do: In `bot/bot.py` `setup_hook`: after cog loading, if `self.config.github_webhook_secret` is set, import `create_webhook_app`, create app, start `AppRunner`/`TCPSite` on `0.0.0.0:PORT` (default 8080). Store runner as `self._webhook_runner`. In `close()`: if runner exists, call `cleanup()`. Add `bot.webhook` to MODULES array in `verify-deploy.sh`. Add `import aiohttp` to key dependencies check. Add PORT documentation to `.env.example`. Add bot integration tests to `test_webhook.py`: startup with secret creates server, startup without secret skips server, shutdown cleans up runner. Run full test suite to confirm zero regressions.
  - Verify: `bash scripts/verify-deploy.sh` passes all checks AND `.venv/bin/python -m pytest tests/ -q` shows ≥ 345 tests, 0 failures
  - Done when: Bot starts webhook server conditionally, shuts down cleanly, verify-deploy passes with new checks, .env.example documents PORT, full test suite green with zero regressions

## Files Likely Touched

- `bot/webhook.py` (new — aiohttp app factory, handlers, signature verification, embed formatters)
- `bot/bot.py` (edit — start/stop webhook server in setup_hook/close)
- `tests/test_webhook.py` (new — comprehensive test suite)
- `scripts/verify-deploy.sh` (edit — add bot.webhook import + aiohttp dependency check)
- `.env.example` (edit — add PORT documentation)
