# Quick Task: make sure that every feature works and the bot has the github login and help command to see every command plus a way to speak directly to the ai

**Date:** 2026-03-23
**Branch:** gsd/quick/2-make-sure-that-every-feature-works-and-t

## What Changed
- Added `/help` command (HelpCog) — displays all bot commands grouped by category (AI & Chat, Authentication, GitHub, Verification, Server Design, Utility) with permission badges, sent as ephemeral embed
- Added `/ask` slash command — lets users speak directly to the AI from any channel via a slash command, with tool support, response chunking, and deferred responses
- Added `/github-login`, `/github-logout`, `/github-status` commands (GitHubAuthCog) — PAT-based GitHub authentication as an alternative to GitHub App auth; validates tokens against GitHub API, stores in SQLite
- Added migration `005_github_tokens.sql` for per-guild GitHub PAT storage
- Disabled built-in help command in favor of custom HelpCog
- Wired new cogs (github_auth, help) into bot startup
- Updated `.env.example` with GitHub PAT documentation

## Files Modified
- `bot/bot.py` — load new cogs, disable built-in help
- `bot/cogs/ai.py` — added `/ask` command
- `bot/cogs/help.py` — **new** — comprehensive help command
- `bot/cogs/github_auth.py` — **new** — GitHub PAT login/logout/status
- `migrations/005_github_tokens.sql` — **new** — github_tokens table
- `.env.example` — added PAT documentation
- `tests/test_ai_cog.py` — 8 new tests for /ask command
- `tests/test_help_cog.py` — **new** — 12 tests for help command
- `tests/test_github_auth_cog.py` — **new** — 15 tests for GitHub auth
- `tests/test_database.py` — updated migration count assertion

## Verification
- All 443 tests pass (up from 408 — 35 new tests added)
- All existing features verified working: ping, verification, AI routing, server design, assistant tools, Claude OAuth, GitHub App integration, webhooks
- New features tested: /help embed rendering, /ask AI interaction, /github-login token validation, /github-logout cleanup, /github-status display
- Error paths tested: invalid tokens, network errors, DM rejection, missing DB
