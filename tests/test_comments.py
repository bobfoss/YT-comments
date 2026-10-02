from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from yt_comments.acquisition import CaptureInterrupted, capture_thread, count_value, history_bootstrap, history_next
from yt_comments.database import connection, initialize, mark_error, search, store_capture, store_refs, thread_payload
from yt_comments.plugin import YTCommentsPlugin


def entity(identity, *, own=False, text="hello", replies="0"):
    return {"commentEntityPayload": {"properties": {"commentId": identity, "content": {"content": text}, "publishedTime": "1 day ago"},
                                    "author": {"channelId": "UCowner" if own else "UCother", "displayName": "@alias" if own else "@other", "isCurrentUser": own},
                                    "toolbar": {"likeCountNotliked": "5", "likeCountA11y": "5 likes", "replyCount": replies}}}


def view(identity):
    return {"commentViewModel": {"commentId": identity}}


def continuation(token):
    return {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}}


def ref(identity="root.own", video_id="abcdefghijk"):
    return {"comment_id": identity, "thread_id": identity.split(".")[0], "video_id": video_id,
            "title": "Known title", "posted_at": "2026-09-01T01:02:03.000000Z"}


def history_html(records, token=""):
    payload = [records, token]
    return ('<script>var WIZ_global_data={"FdrFJe":"session","cfb2h":"build","SNlM0e":"csrf"};'
            "var AF_dataServiceRequests={'ds:0' : {id:'other',request:['youtube_comments']},"
            "'ds:7' : {id:'dynamic',request:[[null,null,[\"youtube_comments\"]],null,100]}};"
            "AF_initDataCallback({key: 'ds:7', hash:'a', data:" + json.dumps(payload) + ", sideChannel:{}});</script>")


def history_record(identity="root.own", video_id="abcdefghijk"):
    record = [None] * 33
    record[4], record[5], record[9] = 1789169826000000, identity, ["example"]
    record[32] = [["Replied to a comment on ", "Known title", None, f"https://www.youtube.com/watch?v={video_id}&lc={identity}"]]
    return record


class AcquisitionTests(unittest.TestCase):
    def test_history_uses_dynamic_callback_and_preserves_reply_id(self):
        refs, token, session = history_bootstrap(history_html([history_record()], "next"))
        self.assertEqual(refs[0]["comment_id"], "root.own")
        self.assertEqual(refs[0]["thread_id"], "root")
        self.assertEqual(refs[0]["title"], "Known title")
        self.assertTrue(refs[0]["posted_at"].endswith("Z"))
        self.assertEqual(token, "next")
        self.assertEqual(session["rpc"], "dynamic")

    def test_empty_history_and_signed_out_are_distinct(self):
        self.assertEqual(history_bootstrap(history_html([]))[0], [])
        with self.assertRaisesRegex(ValueError, "unavailable"):
            history_bootstrap('<a href="accounts.google.com/ServiceLogin">Sign in</a>')

    def test_continuation_uses_session_and_frame(self):
        _, _, session = history_bootstrap(history_html([], "next"))
        transport = Mock()
        transport.request_text.return_value = ")]}'\n100\n" + json.dumps([["wrb.fr", "dynamic", json.dumps([[history_record()], ""]), None]])
        refs, token = history_next(transport, session, "next")
        self.assertEqual(len(refs), 1)
        self.assertFalse(token)
        body = transport.request_text.call_args.args[1]
        self.assertEqual(json.loads(json.loads(body["f.req"])[0][0][1])[1], "next")

    def fixture(self):
        thread = {"commentThreadRenderer": {"commentViewModel": view("root"), "replies": {"commentRepliesRenderer": {"contents": [continuation("r1")]}}}}
        initial = {"items": [thread, {"commentThreadRenderer": {"commentViewModel": view("unrelated")}}],
                   "entities": [entity("root", replies="3"), entity("unrelated", own=True)]}
        first = {"appendContinuationItemsAction": {"continuationItems": [view("root.own"),
                 {"commentThreadRenderer": {"commentViewModel": view("root.other"), "replies": {"commentRepliesRenderer": {"subThreads": [continuation("r2")]}}}}]},
                 "entities": [entity("root.own", own=True), entity("root.other")]}
        second = {"appendContinuationItemsAction": {"continuationItems": [view("root.nested")]}, "entities": [entity("root.nested")]}
        session = SimpleNamespace(initial_data={"itemSectionRenderer": {"sectionIdentifier": "comment-item-section", "contents": [continuation("start")]}},
                                  request_json=Mock(side_effect=[initial, first, second]))
        return SimpleNamespace(youtube_video_session=Mock(return_value=session)), session

    def test_only_target_thread_and_nested_replies_are_retained(self):
        context, session = self.fixture()
        result = capture_thread(context, "abcdefghijk", "root", lambda: False)
        rows = {row["comment_id"]: row for row in result["comments"]}
        self.assertEqual(set(rows), {"root", "root.own", "root.other", "root.nested"})
        self.assertEqual(rows["root.nested"]["parent_id"], "root.other")
        self.assertTrue(result["complete"])
        context.youtube_video_session.assert_called_once_with("abcdefghijk", comment_id="root")
        self.assertEqual(session.request_json.call_count, 3)

    def test_capture_stops_without_request_and_partial_is_explicit(self):
        context, session = self.fixture()
        with self.assertRaises(CaptureInterrupted):
            capture_thread(context, "abcdefghijk", "root", lambda: True)
        session.request_json.assert_not_called()
        result = capture_thread(context, "abcdefghijk", "root", lambda: False, max_pages=2)
        self.assertFalse(result["complete"])

    def test_wrong_account_and_missing_highlight_cannot_capture_other_threads(self):
        context, session = self.fixture()
        with self.assertRaisesRegex(ValueError, "Highlighted"):
            capture_thread(context, "abcdefghijk", "missing", lambda: False)
        context, session = self.fixture()
        with self.assertRaisesRegex(ValueError, "participation"):
            capture_thread(context, "abcdefghijk", "root", lambda: False, max_pages=1)

    def test_rounded_or_absent_like_counts_do_not_become_exact(self):
        self.assertIsNone(count_value("1.2K"))
        self.assertIsNone(count_value(""))
        self.assertEqual(count_value("12,345 likes"), 12345)
        self.assertEqual(count_value("0"), 0)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "comments.sqlite3"
        initialize(self.path)

    def capture(self):
        context, _ = AcquisitionTests().fixture()
        return capture_thread(context, "abcdefghijk", "root", lambda: False)

    def test_bootstrap_idempotent_and_search_groups_all_own_comments(self):
        result = self.capture()
        second = dict(result["comments"][1], comment_id="root.own2", text="another contribution")
        result["comments"].append(second)
        with connection(self.path) as conn:
            store_refs(conn, [ref()])
            store_capture(conn, result)
            result["comments"][0]["text"] = "unique other person's search term"
            store_capture(conn, result)
            hits = search(conn, "unique", 10, 0, "likes")
            self.assertEqual(hits["total"], 1)
            self.assertEqual(len(hits["results"][0]["own_comments"]), 2)
            self.assertFalse(hits["results"][0]["match"]["is_current_user"])
            self.assertEqual(search(conn, "missing", 10, 0, "newest")["total"], 0)
        initialize(self.path)
        with connection(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0], 5)

    def test_failed_or_partial_capture_preserves_content_and_unknown_counts(self):
        result = self.capture()
        with connection(self.path) as conn:
            store_refs(conn, [ref()])
            store_capture(conn, result)
            result["comments"] = result["comments"][:2]
            result["comments"][1]["like_count"] = None
            result["complete"] = False
            store_capture(conn, result)
            mark_error(conn, "root", "temporary failure")
            saved = thread_payload(conn, "root", full=True)
            self.assertEqual(len(saved["comments"]), 4)
            self.assertEqual(saved["own_comments"][0]["like_count"], 5)
            self.assertEqual(saved["status"], "unavailable")
            self.assertEqual(saved["own_comments"][0]["posted_at"], ref()["posted_at"])
            self.assertIsNone(saved["root_comment"]["posted_at"])

    def test_changed_account_rejected(self):
        result = self.capture()
        with connection(self.path) as conn:
            store_capture(conn, result)
        result["comments"][1]["author_id"] = "UCdifferent"
        with self.assertRaisesRegex(ValueError, "changed"):
            with connection(self.path) as conn:
                store_capture(conn, result)

    def test_complete_refresh_removes_missing_comments_from_search(self):
        result = self.capture()
        result["comments"][-1]["text"] = "removedreply"
        with connection(self.path) as conn:
            store_capture(conn, result)
            self.assertEqual(search(conn, "removedreply", 10, 0, "newest")["total"], 1)
            result["comments"].pop()
            result["reply_count"] = 2
            store_capture(conn, result)
            self.assertEqual(len(thread_payload(conn, "root", full=True)["comments"]), 3)
            self.assertEqual(search(conn, "removedreply", 10, 0, "newest")["total"], 0)

    def test_unknown_newer_schema_refused(self):
        with connection(self.path) as conn:
            conn.execute("PRAGMA user_version=2")
        with self.assertRaises(RuntimeError):
            initialize(self.path)

    def test_planning_is_targeted_and_discovery_never_lists_library(self):
        plugin = YTCommentsPlugin()
        path = Path(self.temp.name) / "config.json"
        plugin.start(SimpleNamespace(plugin_config={"config": str(path)}, resolve_path=Path))
        with connection(plugin.db_path) as conn:
            store_refs(conn, [ref(), ref("other", "ABCDEFGHIJK")])
        context = Mock()
        self.assertEqual(len(list(plugin.plan_worker("discover", context, {}))), 1)
        tasks = list(plugin.plan_worker("fetch", context, {"video_id": ["abcdefghijk"]}))
        self.assertEqual([task["subject_id"] for task in tasks], ["root"])
        context.library_videos.assert_not_called()

    def test_video_filter_counts_only_captured_participation_and_searches_context(self):
        plugin = YTCommentsPlugin()
        plugin.db_path = self.path
        result = self.capture()
        result["comments"][0]["text"] = "distinct conversation context"
        with connection(self.path) as conn:
            store_refs(conn, [ref(), ref("pending", "ABCDEFGHIJK")])
            store_capture(conn, result)
            mark_error(conn, "root", "temporary error")
        presence = plugin.filter_videos("")
        self.assertEqual(presence["video_ids"], frozenset({"abcdefghijk"}))
        self.assertFalse(presence["search_match_ids"])
        self.assertEqual(plugin.filter_videos("distinct context")["search_match_ids"], presence["video_ids"])
        self.assertFalse(plugin.filter_videos("notpresent")["search_match_ids"])


if __name__ == "__main__":
    unittest.main()
