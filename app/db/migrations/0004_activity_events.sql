CREATE TABLE activity_events (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    slug_url TEXT NOT NULL,
    volume TEXT,
    number TEXT,
    seconds INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_activity_events_user_kind_created ON activity_events(user_id, kind, created_at);
