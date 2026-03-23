---
estimated_steps: 5
estimated_files: 5
skills_used:
  - test
  - best-practices
---

# T03: Wire webhook server into bot lifecycle and update infrastructure

**Slice:** S04 — Webhook Receiver + Event Notifications
**Milestone:** M002

## Description

Integrate the aiohttp webhook server into the bot's async lifecycle (`setup_hook` / `close`) and update all deployment infrastructure (verify-deploy script, .env.example). This closes the slice by ensuring the webhook server starts conditionally (only when `github_webhook_secret` is configured), binds to `0.0.0.0:PORT` for Railway routing, shuts down cleanly, and all deployment checks pass.

The architecture follows D017: AppRunner/TCPSite embedded in the bot process, sharing the same asyncio event loop as discord.py. Railway provides the `PORT` env var; the bot defaults to 8080 for local dev.

## Steps

1. **Edit `bot/bot.py` — add webhook server startup in `setup_hook`**:
   - After the command tree sync block (at the end of `setup_hook`), add:
     ```python
     if self.config.github_webhook_secret:
         from bot.webhook import create_webhook_app
         from aiohttp import web
         self._webhook_app = create_webhook_app(self)
         self._webhook_runner = web.AppRunner(self._webhook_app)
         await self._webhook_runner.setup()
         port = int(os.getenv("PORT", "8080"))
         site = web.TCPSite(self._webhook_runner, "0.0.0.0", port)
         await site.start()
         log.info("Webhook server started on port %d", port)
     else:
         self._webhook_runner = None
         log.info("Webhook server not started — GITHUB_WEBHOOK_SECRET not configured")
     ```
   - Initialize `self._webhook_runner = None` in `__init__`.

2. **Edit `bot/bot.py` — add webhook server shutdown in `close()`**:
   - Before `await super().close()`, add:
     ```python
     if self._webhook_runner is not None:
         await self._webhook_runner.cleanup()
         log.info("Webhook server stopped")
     ```

3. **Edit `scripts/verify-deploy.sh`**:
   - Add `"bot.webhook"` to the MODULES array (after `"bot.cogs.github"`).
   - Add `check "import aiohttp" $PYTHON -c "import aiohttp"` to the Key dependencies section (after `import httpx`).

4. **Edit `.env.example`**:
   - Add PORT documentation in the Railway-specific section:
     ```
     # Port for webhook receiver (Railway sets this automatically)
     # For local development, defaults to 8080 if not set
     # PORT=8080
     ```

5. **Add bot integration tests to `tests/test_webhook.py`** (~3-5 tests) and run full regression:
   - `test_bot_starts_webhook_with_secret` — Mock bot with `github_webhook_secret` set, verify `setup_hook` creates runner (can test by checking `bot._webhook_runner is not None` after setup). Use a minimal integration approach: create a DiscordBot-like mock that exercises the webhook startup path.
   - `test_bot_skips_webhook_without_secret` — Mock bot with `github_webhook_secret=None`, verify runner is None.
   - `test_bot_shutdown_cleans_up_runner` — Verify `close()` calls `runner.cleanup()`.
   - Run `.venv/bin/python -m pytest tests/ -q` to confirm all tests pass (≥345 total, 0 failures).
   - Run `bash scripts/verify-deploy.sh` to confirm all deployment checks pass.

## Must-Haves

- [ ] Webhook server starts only when `github_webhook_secret` is configured
- [ ] Server binds to `0.0.0.0` (not localhost) on PORT env var (default 8080)
- [ ] Server shuts down cleanly in `close()` via `runner.cleanup()`
- [ ] `verify-deploy.sh` includes `bot.webhook` import check and `aiohttp` dependency check
- [ ] `.env.example` documents PORT
- [ ] Zero test regressions — all 315+ existing tests still pass
- [ ] `self._webhook_runner` initialized to `None` in `__init__` (safe default)

## Verification

- `bash scripts/verify-deploy.sh` — all checks pass (including new `bot.webhook` and `aiohttp` checks)
- `.venv/bin/python -m pytest tests/ -q` — ≥345 tests pass, 0 failures
- `grep -q "bot.webhook" scripts/verify-deploy.sh` — module listed in deploy checks
- `grep -q "PORT" .env.example` — PORT documented

## Observability Impact

- Signals added/changed: `bot.bot` logger gains two new messages: "Webhook server started on port %d" (on startup with secret) and "Webhook server stopped" (on shutdown). "Webhook server not started" message when secret is absent.
- How a future agent inspects this: Check bot startup logs for webhook port message. `GET /health` on the bot's PORT returns 200 when running.
- Failure state exposed: If webhook server fails to bind (port in use), `site.start()` raises and the bot's `setup_hook` fails — bot won't start, Railway restarts it.

## Inputs

- `bot/webhook.py` — T02 output: complete webhook module with app factory, handlers, formatters, routing
- `tests/test_webhook.py` — T02 output: ~30 passing webhook tests
- `bot/bot.py` — Current bot with `setup_hook` and `close()` methods
- `scripts/verify-deploy.sh` — Current deploy verification script
- `.env.example` — Current env example file

## Expected Output

- `bot/bot.py` — Updated with webhook server start in `setup_hook` and cleanup in `close()`
- `scripts/verify-deploy.sh` — Updated with `bot.webhook` module import and `aiohttp` dependency check
- `.env.example` — Updated with PORT documentation
- `tests/test_webhook.py` — Updated with ~3 bot integration tests (~33 total webhook tests)
