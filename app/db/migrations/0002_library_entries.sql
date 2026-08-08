CREATE TABLE library_entries (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    slug_url TEXT NOT NULL,
    added_at TEXT NOT NULL,
    last_read_volume TEXT,
    last_read_number TEXT,
    last_read_at TEXT,
    UNIQUE(user_id, slug_url)
);
