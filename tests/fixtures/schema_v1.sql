PRAGMA journal_mode=WAL;
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE history_refs(
    comment_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, video_id TEXT NOT NULL,
    title TEXT NOT NULL, posted_at TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE INDEX refs_video ON history_refs(video_id, thread_id);
CREATE TABLE threads(
    thread_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL DEFAULT '', checked_at TEXT, status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '', reply_count INTEGER, last_activity_at TEXT);
CREATE INDEX threads_video ON threads(video_id);
CREATE TABLE comments(
    comment_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(thread_id),
    parent_id TEXT NOT NULL DEFAULT '', author_id TEXT NOT NULL, author_name TEXT NOT NULL,
    text TEXT NOT NULL, is_current_user INTEGER NOT NULL, posted_at TEXT,
    published_label TEXT NOT NULL, like_count INTEGER, like_label TEXT NOT NULL,
    position INTEGER NOT NULL, last_seen TEXT NOT NULL);
CREATE INDEX comments_thread ON comments(thread_id, is_current_user, position);
CREATE VIRTUAL TABLE comments_fts USING fts5(text, author_name, content='comments', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER comments_ai AFTER INSERT ON comments BEGIN
    INSERT INTO comments_fts(rowid,text,author_name) VALUES(new.rowid,new.text,new.author_name);
END;
CREATE TRIGGER comments_ad AFTER DELETE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts,rowid,text,author_name) VALUES('delete',old.rowid,old.text,old.author_name);
END;
CREATE TRIGGER comments_au AFTER UPDATE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts,rowid,text,author_name) VALUES('delete',old.rowid,old.text,old.author_name);
    INSERT INTO comments_fts(rowid,text,author_name) VALUES(new.rowid,new.text,new.author_name);
END;
PRAGMA user_version=1;
