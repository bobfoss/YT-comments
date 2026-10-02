"""YT Library integration; all library writes go through host services."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .acquisition import CaptureInterrupted, capture_thread, history_bootstrap, history_next, utc_now
from .database import connection, initialize, mark_error, search, set_setting, setting, store_capture, store_refs, thread_payload


class YTCommentsPlugin:
    plugin_id = "comments"
    plugin_name = "YT Comments"
    plugin_version = __version__
    plugin_api_version = 2
    required_host_features = {"youtube_next_session_v1", "my_activity_session_v1", "worker_followup_v1", "video_discovery_v1", "video_facet_result_cards_v1"}
    capabilities = {"comment_search", "comment_threads", "comment_presence", "worker_processes"}
    browser_assets = ({"path": "browser.js", "type": "script"}, {"path": "browser.css", "type": "style"})

    def start(self, context: Any) -> None:
        self.context = context
        self.config_path = context.resolve_path(context.plugin_config.get("config") or "../YT Comments/yt_comments.config.json")
        self.lock = threading.RLock()
        self.config = {"database": "yt_comments.sqlite3", "refresh_hours": 24, "sort": "newest", "max_thread_pages": 500}
        if self.config_path.exists():
            self.config.update(json.loads(self.config_path.read_text(encoding="utf-8-sig")))
        else:
            self._save_config()
        db = Path(self.config["database"])
        self.db_path = db if db.is_absolute() else self.config_path.parent / db
        initialize(self.db_path)

    def _save_config(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.config_path)

    def status(self) -> dict[str, Any]:
        with connection(self.db_path) as conn:
            counts = {"threads": conn.execute("SELECT COUNT(DISTINCT thread_id) FROM comments WHERE is_current_user=1").fetchone()[0],
                      "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
                      "references": conn.execute("SELECT COUNT(*) FROM history_refs").fetchone()[0],
                      "unavailable": conn.execute("SELECT COUNT(*) FROM threads WHERE status='unavailable'").fetchone()[0]}
            return {"state": "ready", **counts, "accountName": setting(conn, "account_name"),
                    "historyCheckedAt": setting(conn, "history_checked_at"),
                    "historyComplete": setting(conn, "history_complete") == "1",
                    "adminMetrics": [{"id": k, "label": label, "value": counts[k], "format": "integer"}
                                     for k, label in (("threads", "Participating threads"), ("comments", "Captured comments"),
                                                      ("references", "History references"), ("unavailable", "Unavailable checks"))]}

    def worker_processes(self) -> tuple[dict[str, Any], ...]:
        return (
            {"id": "discover", "name": "Discover comment participation", "description": "Read My Activity comment history and queue participating threads, including historical entries.",
             "service": "youtube", "maxInFlight": 1, "hooks": ["library_update"], "adminSurface": "advanced", "buttonLabel": "Discover comments"},
            {"id": "fetch", "name": "Refresh comment threads", "description": "Refresh threads discovered through My Activity. Existing comments survive unavailable or incomplete responses.",
             "service": "youtube", "maxInFlight": 2, "hooks": ["video_scan"], "adminSurface": "advanced", "buttonLabel": "Refresh comments"},
        )

    def plan_worker(self, worker_id: str, context: Any, params: dict[str, Any]):
        if worker_id == "discover":
            yield {"task_id": "history", "title": "Discover participating comment threads", "priority": 0}
            return
        if worker_id != "fetch":
            raise KeyError(worker_id)
        video_ids = params.get("video_id", [])
        if isinstance(video_ids, str):
            video_ids = [video_ids]
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(v)) for v in video_ids):
            raise ValueError("Expected YouTube video IDs")
        force = params.get("manual") is True
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, float(self.config["refresh_hours"])))).isoformat().replace("+00:00", "Z")
        with connection(self.db_path) as conn:
            rows = conn.execute("SELECT thread_id,video_id,title,checked_at FROM threads ORDER BY last_activity_at DESC").fetchall()
        requested = set(video_ids)
        for row in rows:
            if requested and row["video_id"] not in requested:
                continue
            if not force and row["checked_at"] and row["checked_at"] >= cutoff:
                continue
            yield {"task_id": row["thread_id"], "subject_id": row["thread_id"], "video_id": row["video_id"],
                   "title": row["title"], "payload": {"thread_id": row["thread_id"]}}

    def run_worker(self, worker_id: str, task: dict[str, Any], runtime: Any) -> dict[str, Any]:
        try:
            if worker_id == "discover":
                return self._discover(runtime)
            thread_id = task["payload"]["thread_id"]
            try:
                result = capture_thread(self.context, task["video_id"], thread_id, runtime.stop_requested,
                                        max_pages=max(1, int(self.config["max_thread_pages"])))
                with connection(self.db_path) as conn:
                    store_capture(conn, result)
                added = runtime.discover_videos([{"video_id": task["video_id"], "title": task.get("title", "")}])
                runtime.log("info", f"Captured {len(result['comments'])} comments in {result['pages']} pages; complete={result['complete']}; added videos={added}")
                return {"outcome": "complete" if result["complete"] else "partial", "found": len(result["comments"])}
            except CaptureInterrupted:
                raise
            except Exception as exc:
                with connection(self.db_path) as conn:
                    mark_error(conn, thread_id, str(exc))
                runtime.log("warn", str(exc))
                return {"outcome": "unavailable", "failed": 1, "message": str(exc)}
        except CaptureInterrupted:
            # The host cancellation signal keeps this task in its persistent queue.
            from yt_library.plugins import PluginWorkerStopped
            raise PluginWorkerStopped("Comment capture stopped") from None

    def _discover(self, runtime: Any) -> dict[str, Any]:
        with connection(self.db_path) as conn:
            previously_complete = setting(conn, "history_complete") == "1"
        total_new = 0
        pages = 0
        seen = set()
        with self.context.my_activity_session() as transport:
            refs, token, session = history_bootstrap(transport.request_text("/product/youtube_comments?hl=en"))
            while True:
                if runtime.stop_requested():
                    raise CaptureInterrupted("Discovery stopped")
                with connection(self.db_path) as conn:
                    new = store_refs(conn, refs)
                runtime.discover_videos([{"video_id": ref["video_id"], "title": ref["title"]} for ref in refs])
                total_new += new
                pages += 1
                runtime.log("info", f"History page {pages}: {len(refs)} references; {new} new")
                if not token or (previously_complete and not new):
                    break
                if token in seen:
                    raise RuntimeError("My Activity returned a repeated continuation; discovery remains incomplete")
                seen.add(token)
                refs, token = history_next(transport, session, token)
        # Persist the cursor only after all relevant pages succeeded. Partial runs retry safely.
        with connection(self.db_path) as conn:
            set_setting(conn, "history_complete", "1")
            set_setting(conn, "history_checked_at", utc_now())
        planned = runtime.enqueue_process("fetch", {})
        return {"outcome": "complete", "found": total_new, "processed": pages, "message": f"Indexed history; follow-up plan: {planned.get('queued', planned.get('inserted', 0))}"}

    def filter_videos(self, query: str) -> dict[str, frozenset[str]]:
        with connection(self.db_path) as conn:
            participating = "EXISTS(SELECT 1 FROM comments own WHERE own.thread_id=t.thread_id AND own.is_current_user=1)"
            video_ids = frozenset(row[0] for row in conn.execute(
                f"SELECT DISTINCT video_id FROM threads t WHERE {participating}"))
            fts = " AND ".join('"' + word.replace('"', '""') + '"' for word in query.strip().split())
            matches = frozenset(row[0] for row in conn.execute(f"""
                SELECT DISTINCT t.video_id FROM comments_fts f
                JOIN comments c ON c.rowid=f.rowid JOIN threads t ON t.thread_id=c.thread_id
                WHERE comments_fts MATCH ? AND {participating}
            """, (fts,))) if fts else frozenset()
        return {"video_ids": video_ids, "search_match_ids": matches}

    def handle_api(self, method: str, path: str, query: dict[str, list[str]]):
        if method != "GET":
            return None
        value = lambda key, default="": str((query.get(key) or [default])[0])
        with connection(self.db_path) as conn:
            if path == "search":
                result = search(conn, value("q"), min(5000, max(1, int(value("limit", "30")))), max(0, int(value("offset", "0"))),
                                str(self.config["sort"]), value("video_id"))
                result["sort"] = self.config["sort"]
                return 200, result
            if path.startswith("threads/"):
                result = thread_payload(conn, path.split("/", 1)[1], full=True)
                return (200, result) if result else (404, {"error": "Thread not captured"})
            if path == "videos":
                ids = list(dict.fromkeys(query.get("id", [])))[:500]
                result = {}
                for video_id in ids:
                    count = conn.execute("""SELECT COUNT(*) FROM threads t WHERE video_id=? AND EXISTS
                        (SELECT 1 FROM comments c WHERE c.thread_id=t.thread_id AND c.is_current_user=1)""", (video_id,)).fetchone()[0]
                    if count:
                        result[video_id] = count
                return 200, {"videos": result}
            if path == "preferences":
                return 200, {"sort": self.config["sort"]}
        return None

    def handle_api_request(self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]):
        if method == "POST" and path == "preferences":
            if body.get("sort") not in {"newest", "oldest", "likes"}:
                return 400, {"error": "Unknown comment order"}
            with self.lock:
                self.config["sort"] = body["sort"]
                self._save_config()
            return 200, {"sort": self.config["sort"]}
        return None

    def handle_browser_asset(self, path: str) -> tuple[str, bytes]:
        if path not in {"browser.js", "browser.css"}:
            raise FileNotFoundError(path)
        return ("text/javascript" if path.endswith(".js") else "text/css"), Path(__file__).with_name(path).read_bytes()
