-- GitHub Personal Access Token storage per guild.
-- Alternative to GitHub App authentication (GITHUB_APP_ID env var).

CREATE TABLE IF NOT EXISTS github_tokens (
    guild_id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    github_username TEXT NOT NULL,
    set_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
