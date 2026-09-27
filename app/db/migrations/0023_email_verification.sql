-- PR 246: email confirmation at registration. Logging in is blocked until
-- users.email_verified_at is set (see app/api/auth.py).
--
-- Every account that already exists is grandfathered in as verified, stamped with its own
-- created_at - those accounts predate the requirement, and leaving the column NULL for
-- them would lock every current user out on their next login.
ALTER TABLE users ADD COLUMN email_verified_at TEXT;
UPDATE users SET email_verified_at = created_at;

-- One row per verification email sent. A single email carries both a one-click link
-- (token_hash - a long random token, same scheme as password_reset_tokens in
-- 0021_password_reset_tokens.sql) and a 6-digit code typed on the "check your email"
-- screen (code_hash); using either one marks the whole row used.
--
-- A 6-digit code is guessable by brute force, unlike the link token, so each row also
-- counts failed code attempts and stops accepting the code after a few (see
-- app/db/email_verification.py) - the visitor then asks for a new email.
CREATE TABLE email_verification_tokens (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    code_hash TEXT NOT NULL,
    failed_code_attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_email_verification_tokens_user ON email_verification_tokens(user_id);
