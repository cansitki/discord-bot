# S01: GitHub Client + Channel-Repo Linking

**Goal:** GitHubClient authenticates via GitHub App JWT, `/link-repo` and `/unlink-repo` commands work at contract level, channel_repos table created, ChannelRepo model tested, action_log entries written for link/unlink.

**Demo:** User runs `/link-repo owner/repo` in a Discord channel, bot validates the repo exists via GitHub API (mocked in tests), stores the binding in channel_repos, and confirms "✅ Linked #dev to owner/repo". User runs `/unlink-repo` to remove it. All at contract level with mocked GitHub API.

## Must-Haves

- Config accepts optional GITHUB_APP_ID, GITHUB_PRIVATE_KEY, GITHUB_WEBHOOK_SECRET
- PyJWT[crypto] added to pyproject.toml dependencies
- Migration 003 creates channel_repos table (guild_id, channel_id, repo_owner, repo_name, linked_by, linked_at)
- ChannelRepo dataclass with from_row() classmethod in bot/models.py
- GitHubClient with JWT generation (RS256), installation token exchange, get_repo() method
- GitHubClient uses httpx (already a dependency) for all API calls
- Installation token caching with expiry-aware refresh
- /link-repo slash command validates repo exists, stores binding, writes action_log, confirms
- /unlink-repo slash command removes binding, writes action_log, confirms
- Error handling: repo not found, already linked, not linked, missing GitHub config
- GitHub cog implements tool-provider protocol (get_tools/handle_tool_call) — empty for S01, wired in S02
- verify-deploy.sh updated with new module imports
- .env.example updated with GitHub App variables

## Proof Level

- This slice proves: contract
- Real runtime required: no
- Human/UAT required: no

## Verification

- `cd /home/coder/projects/discord-bot/.gsd/worktrees/M002 && .venv/bin/python -m pytest tests/test_github_client.py tests/test_github_cog.py tests/test_models.py tests/test_config.py -v` — all pass
- `bash scripts/verify-deploy.sh` — all checks pass including new github module import
- Existing M001 tests still pass: `.venv/bin/python -m pytest tests/ -v` — 206+ tests pass
- Failure-path diagnostic: `.venv/bin/python -c "from bot.config import Config; import os; os.environ.pop('GITHUB_APP_ID', None); c = Config.from_env(); print('github_app_id:', c.github_app_id)"` — prints `github_app_id: None` confirming graceful degradation when GitHub config is absent

## Observability / Diagnostics

- Runtime signals: structured logging in GitHubClient (jwt generation, token exchange, API calls with status codes), GitHub cog (link/unlink actions with guild_id, channel_id, repo)
- Inspection surfaces: channel_repos table queryable via SQLite, action_log entries for link/unlink actions
- Failure visibility: GitHubClient logs API response status codes and error messages; missing config logged at startup; action_log records who linked/unlinked and when
- Redaction constraints: GITHUB_PRIVATE_KEY and installation tokens never logged; only key names logged at startup

## Integration Closure

- Upstream surfaces consumed: `bot/config.py` (Config dataclass), `bot/database.py` (DatabaseManager), `bot/models.py` (ActionLog), `bot/bot.py` (setup_hook cog loading, DynamicItem registration)
- New wiring introduced in this slice: `bot/cogs/github.py` loaded in setup_hook, `migrations/003_channel_repos.sql` applied at startup, `bot/github_client.py` instantiated by GitHub cog
- What remains before the milestone is truly usable end-to-end: S02 (issue creation), S03 (repo status), S04 (webhooks), plus real GitHub App credentials for live testing

## Tasks

- [x] **T01: Add GitHub config, channel_repos migration, and ChannelRepo model** `est:45m`
  - Why: Every other task in this slice depends on the Config accepting GitHub credentials, the channel_repos table existing, and the ChannelRepo model being available for queries. This is the data foundation.
  - Files: `bot/config.py`, `bot/models.py`, `migrations/003_channel_repos.sql`, `pyproject.toml`, `.env.example`
  - Do: Add optional github_app_id, github_private_key, github_webhook_secret to Config.from_env(). Add ChannelRepo dataclass with from_row(). Create migration 003 with channel_repos schema (guild_id INTEGER, channel_id INTEGER, repo_owner TEXT, repo_name TEXT, linked_by INTEGER, linked_at TEXT, PRIMARY KEY (guild_id, channel_id)). Add PyJWT[crypto] to pyproject.toml dependencies. Update .env.example with GitHub vars. Set up .venv and install dependencies.
  - Verify: `.venv/bin/python -m pytest tests/test_config.py tests/test_models.py -v` passes; `.venv/bin/python -c "from bot.config import Config; from bot.models import ChannelRepo"` succeeds
  - Done when: Config loads GitHub vars as optional fields, ChannelRepo model round-trips through database, migration 003 creates channel_repos table, PyJWT importable

- [x] **T02: Implement GitHubClient with JWT auth and repo validation** `est:1h`
  - Why: The GitHubClient is the highest-risk piece — JWT generation with RS256, installation token exchange, and token caching. Isolating it ensures the auth flow is correct before wiring it into commands.
  - Files: `bot/github_client.py`, `tests/test_github_client.py`
  - Do: Create GitHubClient class that generates RS256 JWTs using PyJWT, exchanges them for installation tokens via POST /app/installations/{id}/access_tokens, caches tokens with expiry-aware refresh. Implement get_repo(owner, repo) that returns repo metadata or raises. Use httpx.AsyncClient for all API calls. Write comprehensive tests with mocked httpx responses for: JWT generation, token exchange, token caching/refresh, get_repo success, get_repo 404, get_repo error.
  - Verify: `.venv/bin/python -m pytest tests/test_github_client.py -v` — all tests pass
  - Done when: GitHubClient generates valid JWTs, exchanges for installation tokens, caches tokens, validates repos via API — all proven with mocked httpx responses

- [x] **T03: Implement GitHub cog with /link-repo and /unlink-repo commands** `est:1h`
  - Why: This is the user-facing surface — the slash commands that let users link and unlink repos. Consumes GitHubClient (T02) and ChannelRepo model (T01).
  - Files: `bot/cogs/github.py`, `bot/bot.py`, `scripts/verify-deploy.sh`
  - Do: Create GitHubCog with /link-repo (validates repo via GitHubClient, stores ChannelRepo, writes action_log, sends confirmation embed) and /unlink-repo (removes binding, writes action_log, sends confirmation). Implement get_tools()/handle_tool_call() stubs (empty tools list for now — S02 adds the create_issue tool). Handle error cases: repo not found, already linked, not linked, missing GitHub config. Register cog in bot.py setup_hook. Update verify-deploy.sh with new module imports.
  - Verify: `.venv/bin/python -c "import bot.cogs.github"` succeeds; `bash scripts/verify-deploy.sh` passes
  - Done when: GitHub cog loads, /link-repo and /unlink-repo commands are registered, bot.py wires everything up, verify-deploy.sh passes

- [x] **T04: Comprehensive test suite for GitHub cog and integration verification** `est:1h`
  - Why: The slice isn't done until every must-have is proven by tests. T02 already tests GitHubClient; this task tests the cog's command logic, database interactions, error handling, and action_log entries.
  - Files: `tests/test_github_cog.py`, `tests/conftest.py`
  - Do: Write tests for /link-repo (success, repo not found, already linked, missing config), /unlink-repo (success, not linked), action_log entries for link/unlink, ChannelRepo database round-trip through migration, error embeds. Mock GitHubClient and discord.py interactions following existing test patterns (test_server_design.py, test_verification.py). Run full test suite to ensure no M001 regressions.
  - Verify: `.venv/bin/python -m pytest tests/test_github_cog.py tests/test_github_client.py -v` — all pass; `.venv/bin/python -m pytest tests/ -v` — 206+ tests pass (no regressions)
  - Done when: All new tests pass, all M001 tests still pass, verify-deploy.sh passes

## Files Likely Touched

- `bot/config.py`
- `bot/models.py`
- `bot/github_client.py`
- `bot/cogs/github.py`
- `bot/bot.py`
- `migrations/003_channel_repos.sql`
- `pyproject.toml`
- `.env.example`
- `scripts/verify-deploy.sh`
- `tests/test_github_client.py`
- `tests/test_github_cog.py`
- `tests/test_config.py`
- `tests/test_models.py`
- `tests/conftest.py`
