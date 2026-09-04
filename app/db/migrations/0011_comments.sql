CREATE TABLE comments (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    slug_url TEXT NOT NULL,
    volume TEXT NOT NULL,
    number TEXT NOT NULL,
    branch_id TEXT NOT NULL DEFAULT '',
    paragraph_index INTEGER NOT NULL,
    parent_comment_id INTEGER REFERENCES comments(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_comments_paragraph
    ON comments(slug_url, volume, number, branch_id, paragraph_index);

CREATE INDEX idx_comments_parent ON comments(parent_comment_id);
