-- PR 225: bumped on every successful password reset via the "Забыли пароль?" flow
-- (app/api/auth.py's confirm_password_reset(), app/db/users.py's
-- update_user_password_from_reset()) to invalidate every session issued before the reset -
-- otherwise whoever had the old session hijacked stays logged in straight through it.
-- Checked against the value stashed in the session cookie at login/register time (see
-- app/auth/dependencies.py's get_current_user()). Defaults to 1, not 0, so a brand-new
-- account's very first session - which stashes this same default at login - compares
-- equal without needing to special-case "never reset yet" separately from "reset once".
ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1;
