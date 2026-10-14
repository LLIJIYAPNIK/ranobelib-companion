-- PR 346: the admin analytics' title page reads one title's events across every user -
-- its numbers for a period and who of the first chapter's readers reached each chapter
-- (app/db/admin_analytics.py). The existing indexes start with user_id (a user's own
-- activity) or kind (all titles over a time range); neither narrows a query to one title.
CREATE INDEX idx_activity_events_slug_kind ON activity_events(slug_url, kind);
