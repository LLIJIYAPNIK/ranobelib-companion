-- Issue 432: private user-uploaded titles (EPUB) independent of external ranobelib.me
-- catalog slugs (library_entries.slug_url).
--
-- Storage layout:
-- - Binary EPUB files, cover images and extracted chapter images live in file/object
--   storage (Settings.user_titles_dir), referenced only by opaque internal storage_key
--   values that are never exposed in public URLs, HTML or JSON.
-- - Normalized, strictly sanitized chapter HTML text lives in user_title_chapters.content
--   so the existing web reader can render a chapter in one query without re-opening ZIPs.
-- - Timestamps are UTC ISO-8601 TEXT, matching the rest of this database.

CREATE TABLE user_titles (
    id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    cover_storage_key TEXT,
    cover_media_type TEXT,
    last_read_position INTEGER CHECK (last_read_position IS NULL OR last_read_position >= 1),
    last_read_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_user_titles_user_updated ON user_titles(user_id, updated_at DESC);

CREATE TABLE user_title_files (
    id UUID PRIMARY KEY,
    title_id UUID NOT NULL REFERENCES user_titles(id) ON DELETE CASCADE,
    storage_key TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL,
    import_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (import_status IN ('pending', 'ready', 'failed')),
    import_error TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    uploaded_at TEXT NOT NULL
);

-- MVP invariant: at most one active EPUB file per user title.
CREATE UNIQUE INDEX idx_user_title_files_one_active
    ON user_title_files(title_id) WHERE is_active;

CREATE TABLE user_title_chapters (
    id UUID PRIMARY KEY,
    title_id UUID NOT NULL REFERENCES user_titles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 1),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_href TEXT NOT NULL,
    UNIQUE (title_id, position)
);

CREATE TABLE user_title_assets (
    id UUID PRIMARY KEY,
    title_id UUID NOT NULL REFERENCES user_titles(id) ON DELETE CASCADE,
    storage_key TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    source_href TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_user_title_assets_title ON user_title_assets(title_id);
