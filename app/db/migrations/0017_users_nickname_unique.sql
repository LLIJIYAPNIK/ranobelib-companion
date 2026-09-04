-- PR 194: nickname wasn't unique at all (see 0006_users_nickname_bio.sql) - fine while
-- nothing looked a user up by it, but PR 122's public profile and PR 132/133's
-- reactions/comments now show it as a public-facing identity, so two accounts sharing one
-- nickname are indistinguishable there. Case-insensitive (WHERE nickname IS NOT NULL so
-- the many accounts that never set one don't collide with each other on NULL).
CREATE UNIQUE INDEX users_nickname_lower_unique ON users (LOWER(nickname))
    WHERE nickname IS NOT NULL;
