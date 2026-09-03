CREATE TABLE download_history (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    slug_url TEXT NOT NULL,
    fmt TEXT NOT NULL,
    status TEXT NOT NULL,
    chapter_count INTEGER,
    error TEXT,
    finished_at TEXT NOT NULL
);
