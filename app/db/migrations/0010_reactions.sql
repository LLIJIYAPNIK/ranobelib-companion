CREATE TABLE reactions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    slug_url TEXT NOT NULL,
    volume TEXT NOT NULL,
    number TEXT NOT NULL,
    branch_id TEXT NOT NULL DEFAULT '',
    paragraph_index INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, slug_url, volume, number, branch_id, paragraph_index)
);

CREATE INDEX idx_reactions_paragraph
    ON reactions(slug_url, volume, number, branch_id, paragraph_index);
