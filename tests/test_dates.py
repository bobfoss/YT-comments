from __future__ import annotations

import unittest

from yt_comments.dates import approximate_posted_at, comment_date_fields


class CommentDateTests(unittest.TestCase):
    def test_relative_units_are_anchored_to_capture(self):
        expected = {
            "30 seconds ago": "2026-10-02T07:51:24Z",
            "2 minutes ago": "2026-10-02T07:49:54Z",
            "an hour ago": "2026-10-02T06:51:54Z",
            "a day ago": "2026-10-01T07:51:54Z",
            "2 weeks ago": "2026-09-18T07:51:54Z",
            "5 months ago (edited)": "2026-05-02T07:51:54Z",
            "3 years ago": "2023-10-02T07:51:54Z",
            "just now": "2026-10-02T07:51:54Z",
            "yesterday": "2026-10-01T07:51:54Z",
            "today": "2026-10-02T07:51:54Z",
        }
        for label, timestamp in expected.items():
            with self.subTest(label=label):
                self.assertEqual(approximate_posted_at(label, "2026-10-02T07:51:54Z"), timestamp)

    def test_month_ends_leap_years_and_capture_timezone(self):
        self.assertEqual(approximate_posted_at("1 month ago", "2024-03-31T01:00:00Z"), "2024-02-29T01:00:00Z")
        self.assertEqual(approximate_posted_at("1 year ago", "2024-02-29T01:00:00Z"), "2023-02-28T01:00:00Z")
        self.assertEqual(approximate_posted_at("1 day ago", "2026-10-02T00:51:54-07:00"), "2026-10-01T07:51:54Z")

    def test_unsupported_or_invalid_evidence_does_not_invent_a_date(self):
        for label in ("", "recently", "vor 5 Monaten", "-2 days ago", "1.5 years ago", "99999999999 years ago"):
            with self.subTest(label=label):
                self.assertIsNone(approximate_posted_at(label, "2026-10-02T07:51:54Z"))
        for capture in ("", "invalid", "2026-10-02T07:51:54"):
            self.assertIsNone(approximate_posted_at("5 months ago", capture))

    def test_exact_dates_remain_exact_and_still_expose_edits(self):
        row = {"posted_at": "2026-04-08T18:58:06Z", "published_label": "5 months ago (edited)",
               "last_seen": "2026-10-02T07:51:54Z"}
        self.assertEqual(comment_date_fields(row), {"is_edited": True, "estimated_posted_at": None})
        self.assertEqual(row["published_label"], "5 months ago (edited)")


if __name__ == "__main__":
    unittest.main()
