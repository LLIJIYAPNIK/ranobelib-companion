-- PR 356: readers' reports on a comment or on another user's profile, the queue behind
-- the admin panel's «Модерация» (PR 358). ROADMAP.md names this file 0030; that number
-- went to 0030_user_bans.sql (PR 349) first.
--
-- target_id is a comments.id or a users.id depending on target_type - no foreign key,
-- the same polymorphic shape the admin audit log uses. reason is a key of
-- app/reports.py's REPORT_REASONS (the list lives in code, not in a CHECK here).
-- status: open -> dismissed («Одобрить») or actioned («Скрыть»/«Заблокировать»), with
-- resolution saying what was done. Timestamps are UTC ISO-8601 TEXT like every other
-- table here.
CREATE TABLE reports (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reporter_user_id INTEGER NOT NULL REFERENCES users(id),
    target_type TEXT NOT NULL CHECK (target_type IN ('comment', 'profile')),
    target_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed', 'actioned')),
    resolution TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

-- One open report per reporter and object: a repeat updates it (app/db/reports.py).
CREATE UNIQUE INDEX idx_reports_open_once
    ON reports(reporter_user_id, target_type, target_id) WHERE status = 'open';

-- The daily limit counts a reporter's reports of the last 24 hours.
CREATE INDEX idx_reports_reporter_created ON reports(reporter_user_id, created_at);

-- The moderation queue groups the open reports by object.
CREATE INDEX idx_reports_target ON reports(target_type, target_id, status);
