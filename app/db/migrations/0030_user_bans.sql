-- PR 349: blocking an account from the admin panel. A table rather than a column in
-- users: the history is kept (wave 41's moderation screen shows how many bans an author
-- had), with a reason and a term each. ROADMAP.md names this file 0029; that number went
-- to 0029_activity_events_slug_idx.sql (PR 346) first.
--
-- A ban is active while lifted_at is NULL and expires_at is NULL (no end) or still in
-- the future - it runs out by itself, checked on every request (app/db/bans.py), with no
-- background job. Timestamps are UTC ISO-8601 TEXT like every other table here.
CREATE TABLE user_bans (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    reason TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    lifted_at TEXT,
    lifted_reason TEXT
);

CREATE INDEX idx_user_bans_user ON user_bans(user_id, id);
