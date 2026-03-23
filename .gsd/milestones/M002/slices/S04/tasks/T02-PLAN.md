---
estimated_steps: 4
estimated_files: 2
skills_used:
  - test
  - best-practices
---

# T02: Add event embed formatters and routing logic with tests

**Slice:** S04 — Webhook Receiver + Event Notifications
**Milestone:** M002

## Description

Complete the webhook handler with embed formatting for all four GitHub event types and routing logic that looks up `channel_repos` to send embeds to the correct Discord channels. This is the user-facing value of the slice — when a push/PR/issue/CI event fires on GitHub, users see a nicely formatted embed in their linked Discord channel.

The reverse lookup query is: `SELECT guild_id, channel_id FROM channel_repos WHERE repo_owner = ? AND repo_name = ?`. This can return multiple rows (same repo linked in multiple channels). The handler iterates all matches and sends the embed to each channel via `bot.get_channel(channel_id).send(embed=embed)`.

## Steps

1. **Add embed formatter functions to `bot/webhook.py`**:
   - `format_push_embed(payload) -> discord.Embed` — Blue (0x0366d6). Title: "[repo] N new commit(s) to branch". Show up to 3 commit messages as description lines (`sha[:7] message`). Footer with pusher name. URL = `payload["compare"]`. If >3 commits, add "and N more..." line.
   - `format_issues_embed(payload) -> discord.Embed | None` — Only handle `opened` and `closed` actions (return None for others). Green (0x238636) for opened, purple (0x8957e5) for closed. Title: `[repo] Issue #N: title`. Author field = issue opener/closer. URL = issue html_url.
   - `format_pull_request_embed(payload) -> discord.Embed | None` — Handle `opened`, `closed` actions (return None for others). Green for opened, purple for closed/merged. If `payload["pull_request"]["merged"]` is True, note "merged" in title. Title: `[repo] PR #N: title`. Author = PR author. URL = PR html_url.
   - `format_check_suite_embed(payload) -> discord.Embed | None` — Only handle `completed` action. Green (0x238636) for `success`/`neutral` conclusion, red (0xcb2431) for `failure`/`timed_out`, yellow (0xdbab09) for others. Title: `[repo] CI: conclusion on branch`. URL = check suite html_url or None.

2. **Complete the webhook handler routing** in `handle_webhook()`:
   - After signature verification and event type parsing, extract `payload["repository"]["full_name"]` — if missing, return 400.
   - Split `full_name` on `/` to get `repo_owner` and `repo_name`.
   - Map event type to formatter: `{"push": format_push_embed, "issues": format_issues_embed, "pull_request": format_pull_request_embed, "check_suite": format_check_suite_embed}`.
   - If event type not in map, log info and return 200 `{"status": "ignored"}`.
   - Call formatter — if it returns None (e.g. issues action is "labeled"), return 200 `{"status": "ignored"}`.
   - Query `bot.db.fetchall()` with reverse lookup SQL.
   - For each row, call `bot.get_channel(row["channel_id"])` — if None, log warning and skip. Otherwise `await channel.send(embed=embed)`.
   - Return 200 `{"status": "delivered", "channels": N}` where N is count of successful sends.

3. **Write embed formatter tests** (~8 tests):
   - `test_format_push_embed` — 2 commits, correct colour (0x0366d6), commit messages in description, compare URL, pusher in footer
   - `test_format_push_embed_truncation` — 5+ commits → shows 3 + "and N more" line
   - `test_format_issues_opened_embed` — green colour, issue number + title in embed title, user, URL
   - `test_format_issues_closed_embed` — purple colour
   - `test_format_issues_ignored_action` — action "labeled" → returns None
   - `test_format_pr_opened_embed` — green, PR number + title, user, URL
   - `test_format_pr_merged_embed` — purple, "merged" indicator in title
   - `test_format_check_suite_success` — green, conclusion "success", branch
   - `test_format_check_suite_failure` — red, conclusion "failure"

4. **Write routing tests** (~8 tests):
   - `test_event_routes_to_matching_channel` — push event with matching channel_repos row → embed sent to that channel
   - `test_event_routes_to_multiple_channels` — same repo linked in 2 channels → embed sent to both
   - `test_event_no_matching_channels` — no channel_repos match → 200 accepted, no sends
   - `test_event_channel_not_found` — `bot.get_channel()` returns None → warning logged, skip, no crash
   - `test_event_missing_repository_key` — payload without `repository` → 400
   - `test_check_suite_only_completed` — action "requested" → None/ignored
   - `test_pr_closed_not_merged` — closed but `merged=False` → purple, no "merged" text
   - `test_push_single_commit` — 1 commit → no truncation, correct format

## Must-Haves

- [ ] Push embeds show up to 3 commits with truncation for larger pushes
- [ ] Issues embeds handle opened (green) and closed (purple) — other actions ignored
- [ ] PR embeds handle opened (green), closed (purple), merged (purple + indicator) — other actions ignored
- [ ] Check suite embeds only fire on `completed` with colour-coded conclusion
- [ ] Reverse channel_repos lookup sends embed to ALL matching channels
- [ ] Missing `repository` key in payload → 400
- [ ] Channel not found by `bot.get_channel()` → log warning, skip (don't crash)
- [ ] All embed formatters return `discord.Embed` objects with correct colour, title, URL

## Verification

- `.venv/bin/python -m pytest tests/test_webhook.py -v` — all ~30 tests pass (T01 tests + T02 tests)
- `.venv/bin/python -m pytest tests/test_webhook.py -v -k "format or route or channel"` — embed and routing tests pass

## Inputs

- `bot/webhook.py` — T01 output: webhook app skeleton with signature verification and health endpoint
- `tests/test_webhook.py` — T01 output: test infrastructure with `_make_mock_bot()` and `_sign_payload()` helpers
- `bot/models.py` — `ChannelRepo` dataclass (for understanding row structure)
- `bot/database.py` — `DatabaseManager.fetchall()` method signature

## Expected Output

- `bot/webhook.py` — Updated with embed formatters (`format_push_embed`, `format_issues_embed`, `format_pull_request_embed`, `format_check_suite_embed`) and complete routing logic in `handle_webhook()`
- `tests/test_webhook.py` — Updated with ~15 additional tests for embed formatting and event routing (~30 total)
