-- PR 202: two more privacy toggles alongside 0009's show_currently_reading/show_favorite/
-- show_library - same "INTEGER NOT NULL DEFAULT 1" shape (visible by default, so nobody
-- who's never opened this settings section sees any change in behavior).
--
-- show_friends_activity_home is a personal preference, not about what others see on this
-- user's own profile: it gates the friends-activity column (PR 200) on *this user's own*
-- home page. show_friends is the same kind of flag as the other three - whether *other*
-- visitors see this user's friend list on their public profile page (PR 201); the owner's
-- own view of their own profile ignores it, same as the other three.
ALTER TABLE users ADD COLUMN show_friends_activity_home INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN show_friends INTEGER NOT NULL DEFAULT 1;
