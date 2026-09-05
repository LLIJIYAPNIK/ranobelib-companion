-- PR 199: friend requests/relationships. `status` is a plain text tag ('pending' |
-- 'accepted'), same "no schema ceremony ahead of actual need" reasoning already applied
-- to notifications.kind (0014_notifications.sql) - a request row's status flips to
-- 'accepted' in place rather than moving to a separate table.
CREATE TABLE friendships (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    addressee_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    responded_at TEXT,
    CHECK (requester_id <> addressee_id)
);

CREATE INDEX idx_friendships_requester ON friendships(requester_id, status);
CREATE INDEX idx_friendships_addressee ON friendships(addressee_id, status);

-- At most one relationship between any two users regardless of direction or status -
-- backstop behind the application-level both-directions check in send_request()
-- (app/db/friendships.py), same "pre-check + DB constraint as race-safe backstop"
-- pattern already used for users.email/nickname uniqueness (0017_users_nickname_unique.sql).
CREATE UNIQUE INDEX idx_friendships_pair
    ON friendships (LEAST(requester_id, addressee_id), GREATEST(requester_id, addressee_id));
