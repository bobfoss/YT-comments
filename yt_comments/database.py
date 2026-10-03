"""Plugin-owned current thread state and full-text search."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .acquisition import utc_now
from .dates import comment_date_fields


@contextmanager
def connection(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError("YT Comments database requires a newer plugin")
        if version == 2:
            return
        if version == 1:
            _migrate_v2(conn)
            return
        conn.executescript("""
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
        """)
        _migrate_v2(conn)


def _migrate_v2(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        BEGIN IMMEDIATE;
        DROP TRIGGER comments_ai;
        DROP TRIGGER comments_ad;
        DROP TRIGGER comments_au;
        DROP TABLE comments_fts;
        CREATE VIRTUAL TABLE comments_fts USING fts5(
            text, author_name, comment_id, thread_id,
            content='comments', content_rowid='rowid', tokenize='unicode61');
        CREATE TRIGGER comments_ai AFTER INSERT ON comments BEGIN
            INSERT INTO comments_fts(rowid,text,author_name,comment_id,thread_id)
            VALUES(new.rowid,new.text,new.author_name,new.comment_id,new.thread_id);
        END;
        CREATE TRIGGER comments_ad AFTER DELETE ON comments BEGIN
            INSERT INTO comments_fts(comments_fts,rowid,text,author_name,comment_id,thread_id)
            VALUES('delete',old.rowid,old.text,old.author_name,old.comment_id,old.thread_id);
        END;
        CREATE TRIGGER comments_au AFTER UPDATE ON comments BEGIN
            INSERT INTO comments_fts(comments_fts,rowid,text,author_name,comment_id,thread_id)
            VALUES('delete',old.rowid,old.text,old.author_name,old.comment_id,old.thread_id);
            INSERT INTO comments_fts(rowid,text,author_name,comment_id,thread_id)
            VALUES(new.rowid,new.text,new.author_name,new.comment_id,new.thread_id);
        END;
        INSERT INTO comments_fts(comments_fts) VALUES('rebuild');
        PRAGMA user_version=2;
        COMMIT;
    """)


def setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else ""


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def store_refs(conn: sqlite3.Connection, refs: list[dict[str, Any]]) -> int:
    new = 0
    for ref in refs:
        inserted = conn.execute("INSERT OR IGNORE INTO history_refs VALUES(?,?,?,?,?,?)", (
            ref["comment_id"], ref["thread_id"], ref["video_id"], ref["title"], ref["posted_at"], utc_now(),
        )).rowcount
        new += inserted
        conn.execute("INSERT OR IGNORE INTO threads(thread_id,video_id,title,last_activity_at) VALUES(?,?,?,?)",
                     (ref["thread_id"], ref["video_id"], ref["title"], ref["posted_at"]))
        conn.execute("UPDATE threads SET last_activity_at=MAX(COALESCE(last_activity_at,''),?) WHERE thread_id=?",
                     (ref["posted_at"], ref["thread_id"]))
        if inserted:
            conn.execute("UPDATE threads SET checked_at=NULL WHERE thread_id=?", (ref["thread_id"],))
    return new


def store_capture(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    own = [row for row in result["comments"] if row["is_current_user"] and row["author_id"]]
    identities = {row["author_id"] for row in own}
    if len(identities) != 1:
        raise ValueError("Capture must identify exactly one authenticated participant")
    account_id = next(iter(identities))
    prior = setting(conn, "account_id")
    if prior and prior != account_id:
        raise ValueError("Authenticated YouTube channel changed; existing account comments were preserved")
    set_setting(conn, "account_id", account_id)
    set_setting(conn, "account_name", next((row["author_name"] for row in own if row["author_name"]), account_id))
    now = utc_now()
    thread_id = result["thread_id"]
    conn.execute("INSERT OR IGNORE INTO threads(thread_id,video_id) VALUES(?,?)", (thread_id, result["video_id"]))
    conn.execute("UPDATE threads SET account_id=?,checked_at=?,status=?,error='',reply_count=? WHERE thread_id=?",
                 (account_id, now, "complete" if result["complete"] else "partial", result["reply_count"], thread_id))
    if result["complete"]:
        captured_ids = {row["comment_id"] for row in result["comments"]}
        missing = [(row[0],) for row in conn.execute(
            "SELECT comment_id FROM comments WHERE thread_id=?", (thread_id,)) if row[0] not in captured_ids]
        conn.executemany("DELETE FROM comments WHERE comment_id=?", missing)
    for row in result["comments"]:
        ref = conn.execute("SELECT posted_at FROM history_refs WHERE comment_id=?", (row["comment_id"],)).fetchone()
        conn.execute("""INSERT INTO comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(comment_id) DO UPDATE SET
              parent_id=excluded.parent_id,author_id=excluded.author_id,author_name=excluded.author_name,
              text=excluded.text,is_current_user=excluded.is_current_user,
              posted_at=COALESCE(excluded.posted_at,comments.posted_at),published_label=excluded.published_label,
              like_count=COALESCE(excluded.like_count,comments.like_count),like_label=excluded.like_label,
              position=excluded.position,last_seen=excluded.last_seen""", (
            row["comment_id"], thread_id, row["parent_id"], row["author_id"], row["author_name"], row["text"],
            int(row["is_current_user"]), ref[0] if ref else None, row["published_label"], row["like_count"],
            row["like_label"], row["position"], now,
        ))


def mark_error(conn: sqlite3.Connection, thread_id: str, message: str) -> None:
    conn.execute("UPDATE threads SET checked_at=?,status='unavailable',error=? WHERE thread_id=?",
                 (utc_now(), message[:1000], thread_id))


def thread_payload(conn: sqlite3.Connection, thread_id: str, *, full: bool = False) -> dict[str, Any] | None:
    thread = conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
    if not thread:
        return None
    result = dict(thread)
    comments = [dict(row) for row in conn.execute(
        "SELECT * FROM comments WHERE thread_id=? ORDER BY position,comment_id", (thread_id,))]
    for row in comments:
        row.update(comment_date_fields(row))
    result["own_comments"] = [row for row in comments if row["is_current_user"]]
    if not result["own_comments"]:
        return None
    result["root_comment"] = next((row for row in comments if row["comment_id"] == thread_id), None)
    result["captured_replies"] = sum(row["comment_id"] != thread_id for row in comments)
    if full:
        result["comments"] = comments
    return result


def search(conn: sqlite3.Connection, query: str, limit: int, offset: int, sort: str, video_id: str = "") -> dict[str, Any]:
    conditions = ["EXISTS(SELECT 1 FROM comments c WHERE c.thread_id=t.thread_id AND c.is_current_user=1)"]
    params: list[Any] = []
    if video_id:
        conditions.append("t.video_id=?")
        params.append(video_id)
    words = query.strip().split()
    fts = " AND ".join('"' + word.replace('"', '""') + '"' for word in words)
    if fts:
        conditions.append("t.thread_id IN (SELECT c.thread_id FROM comments_fts f JOIN comments c ON c.rowid=f.rowid WHERE comments_fts MATCH ?)")
        params.append(fts)
    where = " AND ".join(conditions)
    order = {"oldest": "t.last_activity_at ASC", "likes": "(SELECT MAX(like_count) FROM comments c WHERE c.thread_id=t.thread_id AND c.is_current_user=1) DESC"}.get(sort, "t.last_activity_at DESC")
    total = conn.execute(f"SELECT COUNT(*) FROM threads t WHERE {where}", params).fetchone()[0]
    results = []
    for row in conn.execute(f"SELECT t.thread_id FROM threads t WHERE {where} ORDER BY {order},t.thread_id LIMIT ? OFFSET ?", (*params, limit, offset)):
        item = thread_payload(conn, row[0])
        if item:
            if fts:
                match = conn.execute("""SELECT c.author_name,c.text,c.is_current_user FROM comments_fts f
                    JOIN comments c ON c.rowid=f.rowid WHERE comments_fts MATCH ? AND c.thread_id=?
                    ORDER BY c.is_current_user DESC,c.position LIMIT 1""", (fts, row[0])).fetchone()
                item["match"] = dict(match) if match else None
            item["query"] = query
            results.append(item)
    return {"total": total, "totalIsExact": True, "results": results}
