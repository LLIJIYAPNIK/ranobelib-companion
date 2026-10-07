-- PR 322: the user's IANA time zone (e.g. "Europe/Moscow"), so activity "days" - today's
-- stats, the streak, the profile calendar - follow the user's own midnight instead of
-- UTC's. NULL (every account before this, and a new one until the browser reports its
-- zone via POST /settings/timezone) means UTC, exactly the old behavior. created_at
-- columns stay UTC; only the day bucketing reads this.
ALTER TABLE users ADD COLUMN timezone TEXT;
