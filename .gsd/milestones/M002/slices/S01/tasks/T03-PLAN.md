---
estimated_steps: 5
estimated_files: 4
skills_used:
  - review
---

# T03: Implement GitHub cog with /link-repo and /unlink-repo commands

**Slice:** S01 — GitHub Client + Channel-Repo Linking
**Milestone:** M002

## Description

Create the GitHubCog — the Discord-facing surface that lets users link channels to GitHub repos. Implements `/link-repo owner/repo` and `/unlink-repo` slash commands, consumes GitHubClient for repo validation and ChannelRepo for database persistence. Follows established cog patterns (server_design.py, verification.py). Implements the tool-provider protocol stubs for S02.

## Steps

1. **Create `bot/cogs/github.py` with GitHubCog.** Follow the same structure as `bot/cogs/server_design.py`:
   - `__init__(self, bot)`: Store bot reference. Instantiate GitHubClient from `bot.config.github_app_id` and `bot.config.github_private_key` if both are set; otherwise set `self.github_client = None` (GitHub features disabled).
   - Implement `get_tools() -> list[dict]`: Return empty list for now (S02 adds create_issue tool).
   - Implement `handle_tool_call(tool_name, tool_input, message) -> str`: Return "Unknown tool" for now.
   - Add `setup()` function at module level: `async def setup(bot): await bot.add_cog(GitHubCog(bot))`

2. **Implement `/link-repo` slash command.** A hybrid command that:
   - Takes `repo: str` parameter (format: "owner/repo")
   - Validates format (must contain exactly one `/`)
   - If `self.github_client is None`, respond with error: "GitHub integration is not configured. Set GITHUB_APP_ID and GITHUB_PRIVATE_KEY."
   - Call `self.github_client.get_repo(owner, repo_name)` to validate repo exists
   - On GitHubAPIError (404): respond "Repository `owner/repo` not found or not accessible."
   - Check if channel is already linked: query channel_repos for (guild_id, channel_id)
   - If already linked: respond "This channel is already linked to `existing_owner/existing_repo`. Run `/unlink-repo` first."
   - Insert into channel_repos: (guild_id, channel_id, repo_owner, repo_name, linked_by=user_id)
   - Write action_log entry: action_type="repo_linked", target=f"{owner}/{repo_name}", details includes channel_id
   - Send confirmation embed: green color, "✅ Linked #{channel_name} to owner/repo"
   - Requires `manage_channels` permission (same as ai-channel command)
   - Guild-only (no DMs)

3. **Implement `/unlink-repo` slash command.** A hybrid command that:
   - No parameters needed — operates on the current channel
   - Query channel_repos for (guild_id, channel_id)
   - If not linked: respond "This channel is not linked to any repository."
   - Delete from channel_repos where guild_id and channel_id match
   - Write action_log entry: action_type="repo_unlinked", target=repo_full_name
   - Send confirmation embed: "✅ Unlinked #{channel_name} from owner/repo"
   - Requires `manage_channels` permission
   - Guild-only

4. **Wire into bot.py.** Add `await self.load_extension("bot.cogs.github")` in setup_hook, after the existing cog loads. No DynamicItems to register for this cog yet.

5. **Update scripts/verify-deploy.sh.** Add `"bot.cogs.github"` to the MODULES array so the pre-deploy check verifies the new cog can be imported.

## Must-Haves

- [ ] GitHubCog loads as a discord.py extension via setup() function
- [ ] /link-repo validates repo format, checks repo exists via GitHubClient, stores binding, writes action_log, sends embed
- [ ] /unlink-repo removes binding, writes action_log, sends confirmation
- [ ] Error cases handled: bad format, repo not found, already linked, not linked, missing config
- [ ] Tool-provider protocol stubs (get_tools/handle_tool_call) present for AICog discovery
- [ ] Cog loaded in bot.py setup_hook
- [ ] verify-deploy.sh updated with bot.cogs.github import check

## Verification

- `.venv/bin/python -c "import bot.cogs.github"` — import succeeds
- `bash scripts/verify-deploy.sh` — all checks pass including new module
- `.venv/bin/python -m pytest tests/ -v` — no regressions

## Observability Impact

- Signals added: structured logs for link/unlink actions (guild_id, channel_id, repo, user_id), GitHub config status at cog init
- How a future agent inspects this: query channel_repos table, query action_log for repo_linked/repo_unlinked entries
- Failure state exposed: missing config logged at init; GitHubAPIError details logged on repo validation failure

## Inputs

- `bot/github_client.py` — GitHubClient class (from T02)
- `bot/config.py` — Config with GitHub fields (from T01)
- `bot/models.py` — ChannelRepo and ActionLog models (from T01)
- `bot/database.py` — DatabaseManager for queries
- `bot/bot.py` — setup_hook to add cog loading
- `bot/cogs/server_design.py` — reference for cog structure, tool-provider protocol, embed patterns
- `scripts/verify-deploy.sh` — MODULES array to update

## Expected Output

- `bot/cogs/github.py` — GitHubCog with /link-repo, /unlink-repo, tool-provider stubs
- `bot/bot.py` — updated setup_hook with github cog loading
- `scripts/verify-deploy.sh` — updated MODULES array
