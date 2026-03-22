# Railway Deployment Guide

Deploy the Discord bot to [Railway](https://railway.com) as a persistent worker process with SQLite persistence via an attached volume.

## Prerequisites

- **GitHub repository** — bot code pushed to a GitHub repo
- **Railway account** — [railway.com](https://railway.com) (free tier or paid)
- **Discord bot token** — from the [Discord Developer Portal](https://discord.com/developers/applications)
- **Anthropic API key** — from the [Anthropic Console](https://console.anthropic.com/)

## Railway Setup

### 1. Create the Project

1. Log in to [railway.com](https://railway.com) and click **New Project**
2. Select **Deploy from GitHub repo**
3. Choose the repository containing this bot
4. Railway auto-detects Python from `pyproject.toml` and installs dependencies via `pip install .`

### 2. Configure as Worker

This bot connects to Discord's Gateway via WebSocket — it is **not** an HTTP server. Configure it as a worker process:

1. Go to your service **Settings → Networking**
2. Remove any auto-generated port/domain — the bot does not serve HTTP traffic
3. The start command is already defined in `railway.json`: `python -m bot`

### 3. Attach a Volume

SQLite requires persistent disk storage that survives redeploys:

1. In the Railway dashboard, click **+ New** → **Volume**
2. Attach the volume to your bot service
3. Set the **mount path** to `/data`
4. Set the environment variable `DATABASE_PATH=/data/bot.db` (see below)

Without a volume, the SQLite database resets on every redeploy.

## Environment Variables

Configure these in Railway's **Variables** tab:

### Required

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal |
| `ANTHROPIC_API_KEY` | API key from the Anthropic Console |

### Recommended

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/bot.db` | Path to the SQLite database. **Set to `/data/bot.db`** on Railway to use the volume mount for persistence |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `COMMAND_PREFIX` | `!` | Prefix for text-based commands |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Anthropic model used for AI responses |
| `DEV_GUILD_ID` | *(empty)* | Guild ID for instant slash-command sync. Leave empty in production for global sync |
| `RAILWAY_RUN_UID` | *(unset)* | Set to `0` if the container user lacks write permission on the volume mount |

> **Security note:** `Config.from_env()` never logs secret values — only variable names appear in startup output and error messages.

## Deploy

Railway auto-deploys on every push to the default branch:

1. Commit and push your changes to `main`
2. Railway detects the push, builds the Python environment, and runs `python -m bot`
3. Watch the deploy logs in the Railway dashboard for startup messages

The bot logs at startup:
- `Config loaded: ...` — lists which environment variables were found
- `Bot online: ... (guilds: N, db: status)` — confirms successful connection to Discord

### Pre-Deploy Verification

Before pushing, run the local verification script to catch import errors and missing config:

```bash
bash scripts/verify-deploy.sh
```

This checks that all bot modules import, `railway.json` is valid, dependencies resolve, and migrations exist — without requiring real tokens.

## Verify Deployment

After the bot is running on Railway, verify each feature against a live Discord server:

### Checklist

1. **Bot online** — bot appears with a green "Online" status in Discord
2. **Ping** — run `/ping` → bot responds with latency in milliseconds
3. **AI chat** — mention the bot in any channel → Claude responds conversationally
4. **AI channel** — run `/ai-channel set` in a channel → bot automatically responds to all messages in that channel
5. **Verification gate** — new member joins → they're restricted to `#verify` → admin clicks **Approve** → member gains full access
6. **Server design** — describe a server layout → Claude proposes channels/roles → click **Approve** → channels and roles are created
7. **Summarize & fetch** — run `/summarize` → bot summarizes content; test URL fetching to confirm `httpx` works
8. **Persistence** — trigger a Railway redeploy → bot comes back online → previous data (AI channels, verification settings) is intact in SQLite

If step 8 fails, verify the volume is attached at `/data` and `DATABASE_PATH=/data/bot.db` is set.

## Troubleshooting

### Bot doesn't start

- **Check the deploy logs** in Railway for error messages
- **Missing environment variable** — `Config.from_env()` raises a `ValueError` naming the exact missing variable (e.g., `"Missing required environment variable: DISCORD_BOT_TOKEN"`)
- **Import error** — run `bash scripts/verify-deploy.sh` locally to identify broken imports

### Slash commands don't appear

- **Global sync delay** — slash commands take up to 1 hour to propagate globally. Wait, or set `DEV_GUILD_ID` for instant sync during testing
- **Bot permissions** — ensure the bot was invited with the `applications.commands` scope

### Database resets on redeploy

- **Volume not attached** — verify a volume is attached at `/data` in the Railway dashboard
- **Wrong DATABASE_PATH** — must be set to `/data/bot.db` (not `./data/bot.db`) on Railway
- **Permission error** — try setting `RAILWAY_RUN_UID=0` to run as root for volume write access

### Bot goes offline frequently

- The bot has an `ON_FAILURE` restart policy with up to 10 retries
- If it exhausts retries, check logs for the root cause (usually a missing env var or API key issue)
- Redeploy from the Railway dashboard to reset the retry counter

### Claude doesn't respond

- **Invalid API key** — verify `ANTHROPIC_API_KEY` is set correctly
- **Rate limits** — check Anthropic dashboard for usage/rate limit status
- **Model unavailable** — ensure `CLAUDE_MODEL` (if set) is a valid model identifier
