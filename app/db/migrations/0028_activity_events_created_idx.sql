-- PR 344: the admin panel's metrics (app/db/admin_stats.py) aggregate activity_events
-- over every user for a time range - active readers, chapters read, reading time. The
-- existing index starts with user_id (it serves one user's own activity page), so it
-- can't narrow such a query by time; this one can, per event kind.
CREATE INDEX idx_activity_events_kind_created ON activity_events(kind, created_at);
