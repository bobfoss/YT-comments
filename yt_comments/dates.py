"""Approximate dates from saved YouTube labels, without changing source evidence."""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone


EDITED_SUFFIX = re.compile(r"\s*\(edited\)\s*$", re.IGNORECASE)
RELATIVE_LABEL = re.compile(
    r"(\d[\d,]*|a|an) (second|minute|hour|day|week|month|year)s? ago", re.IGNORECASE
)


def approximate_posted_at(label: str, captured_at: str) -> str | None:
    """Anchor a rounded source age to its capture, never to the current clock."""
    label = EDITED_SUFFIX.sub("", label).strip().lower()
    match = RELATIVE_LABEL.fullmatch(label)
    if not match and label not in {"just now", "today", "yesterday"}:
        return None
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            return None
        captured = captured.astimezone(timezone.utc)
        if match:
            count, unit = match.groups()
            count = 1 if count in {"a", "an"} else int(count.replace(",", ""))
        else:
            count, unit = (1 if label == "yesterday" else 0), "day"
        if unit in {"month", "year"}:
            months = count * (12 if unit == "year" else 1)
            year, month = divmod(captured.year * 12 + captured.month - 1 - months, 12)
            day = min(captured.day, calendar.monthrange(year, month + 1)[1])
            estimate = captured.replace(year=year, month=month + 1, day=day)
        else:
            estimate = captured - timedelta(**{unit + "s": count})
        return estimate.isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def comment_date_fields(row: dict) -> dict:
    label = row.get("published_label") or ""
    return {
        "is_edited": bool(EDITED_SUFFIX.search(label)),
        "estimated_posted_at": None if row.get("posted_at") else approximate_posted_at(
            label, row.get("last_seen") or ""
        ),
    }
