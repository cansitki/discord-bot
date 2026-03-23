-- OAuth token storage for Anthropic Claude authentication.
-- Single-row table (id=1) holding the bot's OAuth tokens.

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at REAL NOT NULL
);
