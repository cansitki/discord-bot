-- Twitter/X feed watcher: track watched profiles per channel.

CREATE TABLE IF NOT EXISTS twitter_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    twitter_handle TEXT NOT NULL,
    last_tweet_id TEXT,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(guild_id, channel_id, twitter_handle)
);
