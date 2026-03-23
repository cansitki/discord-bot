CREATE TABLE IF NOT EXISTS channel_repos (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    repo_owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    linked_by INTEGER NOT NULL,
    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, channel_id)
);
