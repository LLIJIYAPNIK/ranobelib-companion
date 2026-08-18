CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    comment_id INTEGER REFERENCES comments(id),
    actor_user_id INTEGER NOT NULL REFERENCES users(id),
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_notifications_user ON notifications(user_id, is_read);

CREATE INDEX idx_notifications_dedupe
    ON notifications(user_id, kind, comment_id, actor_user_id, is_read);
