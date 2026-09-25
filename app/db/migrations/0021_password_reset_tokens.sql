-- PR 225: "Забыли пароль?" - one row per requested reset link. Only the token's hash is
-- stored (token_hash), never the raw value - handed out once in the emailed link and
-- never persisted or logged anywhere, the same "never store the secret itself" treatment
-- already applied to users.password_hash.
CREATE TABLE password_reset_tokens (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_password_reset_tokens_user ON password_reset_tokens(user_id);
