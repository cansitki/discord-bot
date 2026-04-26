# Deployment Guide

The Discord bot runs on a Coder workspace VM as a persistent process managed via tmux.

## Prerequisites

- **Coder workspace** — `main` workspace on `https://dev.bmu.one`
- **Python 3.12+** with a `.venv/` virtual environment
- **Discord bot token** — from the [Discord Developer Portal](https://discord.com/developers/applications)
- **Anthropic API key** — from the [Anthropic Console](https://console.anthropic.com/)

## Running the Bot

The bot runs in a tmux session called `discord-bot`:

```bash
# Start (if not already running)
cd ~/projects/discord-bot
tmux new-session -d -s discord-bot '.venv/bin/python -m bot'

# Attach to see logs
tmux attach-session -t discord-bot

# Detach: Ctrl+B, then D
```

### Restarting

```bash
# Kill existing session and start fresh
tmux kill-session -t discord-bot
tmux new-session -d -s discord-bot '.venv/bin/python -m bot'
```

## Environment Variables

Configured in `.env` at the project root.

### Required

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal |
| `ANTHROPIC_API_KEY` | API key from the Anthropic Console |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `./data/bot.db` | Path to the SQLite database |
| `COMMAND_PREFIX` | `!` | Prefix for text-based commands |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Anthropic model for AI responses |
| `DEV_GUILD_ID` | *(empty)* | Guild ID for instant slash-command sync |

## Pre-Deploy Verification

Before restarting, run the verification script to catch import errors and missing config:

```bash
bash scripts/verify-deploy.sh
```

## Verify Deployment

After restart, check:

1. **Bot online** — green "Online" status in Discord
2. **Ping** — `/ping` responds with latency
3. **AI chat** — mention the bot, Claude responds
4. **Verification gate** — new member flow works
5. **Twitter feeds** — `/watchlist` shows monitored accounts

## Troubleshooting

### Bot doesn't start

- Check tmux session logs: `tmux attach -t discord-bot`
- Missing env var: `Config.from_env()` raises `ValueError` naming the missing variable
- Import error: run `bash scripts/verify-deploy.sh`

### Slash commands don't appear

- Global sync takes up to 1 hour. Set `DEV_GUILD_ID` for instant sync during testing
- Ensure the bot was invited with `applications.commands` scope

### Claude doesn't respond

- Verify `ANTHROPIC_API_KEY` in `.env`
- Check Anthropic dashboard for rate limits
