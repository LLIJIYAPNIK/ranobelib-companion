-- PR 332: a heartbeat read offline waits in the device's queue (sync-queue.js) and is
-- sent once the network is back - possibly twice, if the first attempt reached the server
-- but its answer never made it back. The queue gives every heartbeat an id; the second
-- copy hits this index and is ignored, so active seconds aren't counted twice. NULL for
-- every event before this and for chapter_read rows (unique indexes ignore NULLs).
ALTER TABLE activity_events ADD COLUMN event_id TEXT;
CREATE UNIQUE INDEX idx_activity_events_user_event ON activity_events(user_id, event_id);
