-- Issue 434: publishing user-uploaded EPUB titles for other readers, per-reader
-- reading progress, reporting user titles, and moderator hiding with owner notifications.

-- 1. Visibility, publication metadata, moderator hiding, and download policy on user_titles.
--    All existing and new titles default to 'private'.
--    'hidden' means hidden by a moderator: the owner sees the status and hidden_reason
--    but cannot re-publish the book themselves.
ALTER TABLE user_titles
    ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private', 'published', 'hidden')),
    ADD COLUMN published_at TEXT,
    ADD COLUMN hidden_reason TEXT,
    ADD COLUMN hidden_at TEXT,
    ADD COLUMN allow_download BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_user_titles_published
    ON user_titles(published_at DESC) WHERE visibility = 'published';

CREATE INDEX idx_user_titles_user_published
    ON user_titles(user_id, published_at DESC) WHERE visibility = 'published';

-- 2. Per-reader reading progress so each reader of a published book keeps their own
--    reading position without overwriting the owner's or other readers' progress.
CREATE TABLE user_title_reading_progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title_id UUID NOT NULL REFERENCES user_titles(id) ON DELETE CASCADE,
    last_read_position INTEGER NOT NULL CHECK (last_read_position >= 1),
    last_read_at TEXT NOT NULL,
    PRIMARY KEY (user_id, title_id)
);

CREATE INDEX idx_user_title_reading_progress_title
    ON user_title_reading_progress(title_id);

-- Migrate existing owners' reading positions into the per-reader table without loss.
INSERT INTO user_title_reading_progress (user_id, title_id, last_read_position, last_read_at)
SELECT user_id, id, last_read_position, COALESCE(last_read_at, updated_at)
FROM user_titles
WHERE last_read_position IS NOT NULL
ON CONFLICT (user_id, title_id) DO NOTHING;

-- 3. Extend reports to support UUID-keyed 'user_title' targets while keeping integer
--    target_id semantics unchanged for 'comment' and 'profile'.
ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_target_type_check;
ALTER TABLE reports
    ADD CONSTRAINT reports_target_type_check
    CHECK (target_type IN ('comment', 'profile', 'user_title'));

ALTER TABLE reports ALTER COLUMN target_id DROP NOT NULL;
ALTER TABLE reports ADD COLUMN target_uuid UUID;

ALTER TABLE reports
    ADD CONSTRAINT reports_target_ref_check CHECK (
        (target_type IN ('comment', 'profile') AND target_id IS NOT NULL AND target_uuid IS NULL)
        OR (target_type = 'user_title' AND target_uuid IS NOT NULL AND target_id IS NULL)
    );

CREATE UNIQUE INDEX idx_reports_open_once_uuid
    ON reports(reporter_user_id, target_type, target_uuid)
    WHERE status = 'open' AND target_uuid IS NOT NULL;

CREATE INDEX idx_reports_target_uuid
    ON reports(target_type, target_uuid, status)
    WHERE target_uuid IS NOT NULL;

-- 4. Allow system/moderation notifications (no user actor) linked to a user_title.
ALTER TABLE notifications ALTER COLUMN actor_user_id DROP NOT NULL;
ALTER TABLE notifications
    ADD COLUMN user_title_id UUID REFERENCES user_titles(id) ON DELETE SET NULL,
    ADD COLUMN message TEXT;
