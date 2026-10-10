-- PR 343: the admin panel's action log - who (always "admin": one password, no accounts),
-- what, to which entity, from which IP, with the before/after of a change in details.
-- Written by app/db/admin_audit.py; the screen to read it is PR 353.
--
-- Entries are never edited, and deleted only by the retention cleanup
-- (ADMIN_AUDIT_RETENTION_DAYS): there are no routes for either, and the trigger below
-- refuses an UPDATE outright and a DELETE unless the cleanup has said so for its own
-- transaction (app.admin_audit_purge = 'on', set with SET LOCAL).
CREATE TABLE admin_audit_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'admin',
    action TEXT NOT NULL,
    entity TEXT,
    entity_id TEXT,
    entity_label TEXT,
    ip TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_admin_audit_log_at ON admin_audit_log(at);

CREATE FUNCTION admin_audit_log_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('app.admin_audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'admin_audit_log entries are append-only (% refused)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER admin_audit_log_append_only
    BEFORE UPDATE OR DELETE ON admin_audit_log
    FOR EACH ROW EXECUTE FUNCTION admin_audit_log_guard();
