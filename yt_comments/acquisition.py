"""Parse account history and retrieve only identified participating threads."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Iterator


class CaptureInterrupted(RuntimeError):
    pass


def walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def leaves(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from leaves(child)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_history_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("My Activity history response changed shape")
    refs = []
    for record in payload[0]:
        if not isinstance(record, list) or len(record) < 10:
            raise ValueError("My Activity history record changed shape")
        timestamp = record[4]
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise ValueError("My Activity comment timestamp is missing")
        links = [value for value in leaves(record) if value.startswith("https://www.youtube.com/watch?") and "lc=" in value]
        if not links:
            continue  # Removed content can lack a recoverable video/comment identity.
        parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(links[0]).query)
        video_id, comment_id = parsed.get("v", [""])[0], parsed.get("lc", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,250}", comment_id):
            continue
        title = ""
        for part in record:
            if isinstance(part, list):
                for item in part:
                    if isinstance(item, list) and links[0] in item and len(item) > 1:
                        title = item[1] if isinstance(item[1], str) else ""
        seconds, micros = divmod(timestamp, 1_000_000)
        posted = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=micros)
        refs.append({"comment_id": comment_id, "thread_id": comment_id.split(".")[0], "video_id": video_id,
                     "posted_at": posted.isoformat(timespec="microseconds").replace("+00:00", "Z"), "title": title})
    token = payload[1] if len(payload) > 1 and isinstance(payload[1], str) else ""
    return refs, token


def history_bootstrap(page: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"['\"](ds:\d+)['\"]\s*:\s*\{id:'([^']+)',request:", page):
        try:
            request, _ = decoder.raw_decode(page[match.end():].lstrip())
        except json.JSONDecodeError:
            continue
        if (not isinstance(request, list) or len(request) < 3 or request[1] is not None
                or not isinstance(request[2], int) or "youtube_comments" not in set(leaves(request))):
            continue
        key, rpc = match.group(1), match.group(2)
        callback = re.search(r"AF_initDataCallback\(\{key:\s*['\"]" + re.escape(key) + r"['\"].*?\bdata:", page)
        if not callback:
            continue
        payload, _ = decoder.raw_decode(page[callback.end():].lstrip())
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            continue
        wiz = re.search(r"WIZ_global_data\s*=\s*", page)
        if not wiz:
            raise ValueError("My Activity session metadata is missing")
        globals_, _ = decoder.raw_decode(page[wiz.end():].lstrip())
        session = {name: globals_.get(name) for name in ("FdrFJe", "cfb2h", "SNlM0e")}
        if not all(session.values()):
            raise ValueError("My Activity session is incomplete; refresh Google cookies")
        session.update(rpc=rpc, request=request)
        refs, token = parse_history_payload(payload)
        return refs, token, session
    raise ValueError("My Activity comment history is unavailable; check Google cookies")


def history_next(transport: Any, session: dict[str, Any], token: str) -> tuple[list[dict[str, Any]], str]:
    request = list(session["request"])
    request[1] = token
    query = urllib.parse.urlencode({"rpcids": session["rpc"], "source-path": "/page",
                                   "f.sid": session["FdrFJe"], "bl": session["cfb2h"], "hl": "en",
                                   "_reqid": int(time.time() * 1000) % 1000000, "rt": "c"})
    text = transport.request_text("/_/FootprintsMyactivityUi/data/batchexecute?" + query, {
        "f.req": json.dumps([[[session["rpc"], json.dumps(request), None, "generic"]]]),
        "at": session["SNlM0e"],
    })
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\["wrb.fr",', text):
        envelope, _ = decoder.raw_decode(text[match.start():])
        if envelope[1] == session["rpc"] and isinstance(envelope[2], str):
            return parse_history_payload(json.loads(envelope[2]))
    raise ValueError("My Activity continuation did not contain comment history")


def plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("content") or value.get("simpleText") or "".join(str(r.get("text") or "") for r in value.get("runs", []))
    return ""


def count_value(label: Any) -> int | None:
    text = str(label or "").strip()
    if re.fullmatch(r"[\d,\s]+", text):
        return int(re.sub(r"\D", "", text)) if re.search(r"\d", text) else None
    exact = re.fullmatch(r"([\d,\s]+) (?:likes?|replies|reply)", text, re.I)
    if exact:
        return count_value(exact[1])
    return None


def parse_comments(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    comments = {}
    for node in walk(response):
        entity = node.get("commentEntityPayload")
        if isinstance(entity, dict):
            prop, author, toolbar = entity.get("properties", {}), entity.get("author", {}), entity.get("toolbar", {})
            comment_id = str(prop.get("commentId") or "")
            if not comment_id:
                continue
            label = toolbar.get("likeCountNotliked")
            comments[comment_id] = {
                "comment_id": comment_id, "author_id": str(author.get("channelId") or ""),
                "author_name": str(author.get("displayName") or ""), "text": plain_text(prop.get("content")),
                "is_current_user": author.get("isCurrentUser") is True,
                "published_label": str(prop.get("publishedTime") or ""),
                "like_count": count_value(toolbar.get("likeCountA11y")) if count_value(toolbar.get("likeCountA11y")) is not None else count_value(label),
                "like_label": str(label or ""), "reply_count": count_value(toolbar.get("replyCount")),
                "parent_id": "", "position": len(comments),
            }
        old = node.get("commentRenderer")
        if isinstance(old, dict) and old.get("commentId"):
            author_id = old.get("authorEndpoint", {}).get("browseEndpoint", {}).get("browseId", "")
            comments.setdefault(old["commentId"], {
                "comment_id": old["commentId"], "author_id": author_id,
                "author_name": plain_text(old.get("authorText")), "text": plain_text(old.get("contentText")),
                "is_current_user": old.get("isCurrentUser") is True,
                "published_label": plain_text(old.get("publishedTimeText")),
                "like_count": count_value(plain_text(old.get("voteCount"))),
                "like_label": plain_text(old.get("voteCount")), "reply_count": old.get("replyCount"),
                "parent_id": "", "position": len(comments),
            })
    return comments


def continuations(value: Any) -> list[tuple[str, str]]:
    result = []
    for node in walk(value):
        for key in ("continuationEndpoint", "command", "serviceEndpoint"):
            endpoint = node.get(key)
            if isinstance(endpoint, dict):
                token = endpoint.get("continuationCommand", {}).get("token")
                if token and (token, endpoint.get("clickTrackingParams", "")) not in result:
                    result.append((token, endpoint.get("clickTrackingParams", "")))
        old = node.get("nextContinuationData")
        if isinstance(old, dict) and old.get("continuation"):
            result.append((old["continuation"], old.get("clickTrackingParams", "")))
    return result


def renderer_id(value: dict[str, Any]) -> str:
    for node in walk(value.get("commentViewModel") or value.get("comment") or value):
        if node.get("commentId"):
            return str(node["commentId"])
    return ""


def capture_thread(context: Any, video_id: str, thread_id: str, stop: Callable[[], bool], max_pages: int = 500) -> dict[str, Any]:
    def check() -> None:
        if stop():
            raise CaptureInterrupted("Capture stopped")

    check()
    session = context.youtube_video_session(video_id, comment_id=thread_id)
    sections = [node["itemSectionRenderer"] for node in walk(session.initial_data)
                if isinstance(node.get("itemSectionRenderer"), dict) and node["itemSectionRenderer"].get("sectionIdentifier") == "comment-item-section"]
    tokens = continuations(sections)
    if not tokens:
        raise ValueError("Comments are disabled, unavailable, or the comment surface changed")
    token, tracking = tokens[0]
    check()
    response = session.request_json("next", {"continuation": token}, click_tracking_params=tracking)
    target = next((node["commentThreadRenderer"] for node in walk(response)
                   if isinstance(node.get("commentThreadRenderer"), dict) and renderer_id(node["commentThreadRenderer"]) == thread_id), None)
    if target is None:
        raise ValueError("Highlighted thread was not returned; saved comments were preserved")
    # Restrict the first page to the highlighted thread. Other root comments are transient.
    all_comments = parse_comments(response)
    comments = {}
    pending: list[tuple[str, str, str]] = []

    def ingest(tree: Any, entities: dict[str, dict[str, Any]], parent: str) -> None:
        if isinstance(tree, list):
            for child in tree:
                ingest(child, entities, parent)
            return
        if not isinstance(tree, dict):
            return
        if "commentThreadRenderer" in tree:
            branch = tree["commentThreadRenderer"]
            identity = renderer_id(branch)
            if identity in entities:
                row = dict(entities[identity], parent_id=parent, position=len(comments))
                comments[identity] = row
            ingest(branch.get("replies", {}), entities, identity or parent)
            return
        if "commentViewModel" in tree or "commentRenderer" in tree:
            identity = renderer_id(tree)
            if identity in entities:
                comments[identity] = dict(entities[identity], parent_id=parent, position=len(comments))
            return
        if "continuationItemRenderer" in tree:
            pending.extend((token, tracking, parent) for token, tracking in continuations(tree))
            return
        for child in tree.values():
            ingest(child, entities, parent)

    ingest({"commentThreadRenderer": target}, all_comments, "")
    seen = set()
    pages = 1
    complete = True
    while pending:
        check()
        token, tracking, parent = pending.pop(0)
        if token in seen:
            complete = False
            continue
        if pages >= max_pages:
            complete = False
            break
        seen.add(token)
        response = session.request_json("next", {"continuation": token}, click_tracking_params=tracking)
        pages += 1
        entities = parse_comments(response)
        commands = [node["appendContinuationItemsAction"] for node in walk(response) if "appendContinuationItemsAction" in node]
        if not commands:
            complete = False
        for command in commands:
            ingest(command.get("continuationItems", []), entities, parent or thread_id)
    own = [row for row in comments.values() if row["is_current_user"]]
    if not own:
        raise ValueError("Authenticated account participation was not found; account mismatch or unavailable comment")
    reported = comments.get(thread_id, {}).get("reply_count")
    if reported is not None and len(comments) - 1 < reported:
        complete = False
    return {"thread_id": thread_id, "video_id": video_id, "comments": list(comments.values()),
            "complete": complete, "reply_count": reported, "pages": pages}
