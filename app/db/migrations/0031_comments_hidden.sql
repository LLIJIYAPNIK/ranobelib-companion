-- PR 350: «Скрыть» from the admin panel's «Комментарии». A hidden comment stays in the
-- table (the admin can «Показать» it again) but the public API stops returning it to
-- anyone but its author (app/db/comments.py). UTC ISO-8601 TEXT like every other
-- timestamp here; NULL = shown.
ALTER TABLE comments ADD COLUMN hidden_at TEXT;

-- The admin list opens newest first.
CREATE INDEX idx_comments_created ON comments(created_at);
