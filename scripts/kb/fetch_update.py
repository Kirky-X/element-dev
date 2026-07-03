"""C1: fetch URL → summarize → update context/description/vector with TTL caching.

Implements the smart update flow:
1. If doc has context AND update_at is within TTL → return cached (no fetch)
2. If context expired or missing → fetch URL
   a. Compute context_hash = sha1(fetched_content)
   b. If hash matches stored → only update update_at (content unchanged)
   c. If hash differs → update context, description, vector, context_hash,
      content_hash, update_at (and optionally re-run link-auto for new neighbors)

The `description` is generated from `context` via an extractive summary by
default (first meaningful paragraph, max 120 chars). An optional `summarize`
callback can be passed for LLM-based summarization (Rule 5: summarization is
a legitimate model task).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

from .sidebar_parser import (
    NO_DESCRIPTION,
    NO_CONTEXT,
    _make_content_hash,
    make_context_hash,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    """Parse ISO8601 timestamp; tolerant of trailing Z."""
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_expired(updated_at: str, ttl_days: int) -> bool:
    """True if `updated_at` is older than `ttl_days` from now."""
    if not updated_at:
        return True
    ts = _parse_iso(updated_at)
    now = datetime.now(timezone.utc)
    age = now - ts
    return age > timedelta(days=ttl_days)


def _extractive_summary(content: str, max_len: int = 120) -> str:
    """Default description generator: first meaningful paragraph.

    Skips markdown headings and empty lines, takes the first content paragraph
    and truncates to `max_len` chars. This is a deterministic fallback for when
    no LLM summarize callback is provided (Rule 5: deterministic logic stays
    in code; LLM is optional enhancement).
    """
    if not content:
        return NO_DESCRIPTION
    lines = [l.strip() for l in content.split("\n")
             if l.strip() and not l.startswith("#") and not l.startswith("```")]
    if not lines:
        # fallback: first 120 chars of raw content
        return content[:max_len].replace("\n", " ").strip()
    desc = lines[0]
    if len(desc) > max_len:
        desc = desc[:max_len - 3] + "..."
    return desc


def fetch_and_update(
    doc_id: str,
    indexer: Any,
    embedder: Any,
    fetcher: Any,
    ttl_days: int = 30,
    force: bool = False,
    summarize: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """Smart fetch + update for a doc.

    Args:
        doc_id: the doc's sha1 id
        indexer: QdrantIndexer instance
        embedder: embedder with .model_name and .embed()
        fetcher: object with .fetch(url) -> {title, url, content} or {error}
        ttl_days: context freshness TTL in days (default 30)
        force: if True, skip TTL check and always fetch
        summarize: optional callback (content: str) -> description: str.
                   If None, uses _extractive_summary.

    Returns:
        dict with keys:
          - action: "cached" | "refreshed" | "updated" | "error"
          - doc: the (possibly updated) doc dict
          - reason: human-readable explanation

    Actions:
        cached    — context fresh within TTL, no fetch performed
        refreshed — fetched but content unchanged (hash match), only
                    update_at bumped
        updated   — fetched and content changed, all fields updated
        error     — fetch failed, doc unchanged
    """
    doc = indexer.get(doc_id)
    if doc is None:
        raise KeyError(f"fetch_and_update: doc_id not in index: {doc_id}")

    url = doc["url"]
    has_context = bool(doc.get("context"))
    is_expired = _is_expired(doc.get("updated_at", ""), ttl_days)

    # 1. TTL check: if context exists and not expired (and not forced), cache hit
    if has_context and not is_expired and not force:
        return {
            "action": "cached",
            "doc": doc,
            "reason": f"context fresh within TTL ({ttl_days} days)",
        }

    # 2. Fetch the URL
    result = fetcher.fetch(url)
    if "error" in result:
        return {
            "action": "error",
            "doc": doc,
            "reason": f"fetch failed: {result['error']}",
        }

    content = result.get("content", "")
    # BUG-3: refuse empty/whitespace-only fetch — the hash compare below
    # would otherwise see "content changed" (empty != old) and overwrite
    # the doc's existing context/description/vector with emptiness, silently
    # destroying assets. Guard before any write.
    if not content.strip():
        return {
            "action": "error",
            "doc": doc,
            "reason": "fetch returned empty content — existing context/description/vector preserved",
        }
    new_context_hash = make_context_hash(content)
    old_context_hash = doc.get("context_hash", "")

    # 3. Hash comparison: if content unchanged, just bump update_at
    if new_context_hash == old_context_hash and has_context:
        # Content unchanged — only refresh update_at (and context_hash stays)
        doc["updated_at"] = _now_iso()
        indexer.set_payload(doc_id, {"updated_at": doc["updated_at"]})
        return {
            "action": "refreshed",
            "doc": doc,
            "reason": "content unchanged (hash match), only update_at bumped",
        }

    # 4. Content changed (or first fetch) — update everything
    # 4a. Generate description from content
    if summarize:
        description = summarize(content)
    else:
        description = _extractive_summary(content)

    # 4b. Update doc fields
    doc["context"] = content
    doc["description"] = description
    doc["context_hash"] = new_context_hash
    doc["updated_at"] = _now_iso()
    # content_hash (B4 formula) must reflect new description
    doc["content_hash"] = _make_content_hash(
        doc["title"], doc["url"], doc["doc_type"],
        doc["description"], doc.get("links", []),
    )

    # BUG-1: refuse cross-model writes — same guard as `query()`. A mismatched
    # embedder here would silently stamp a foreign embed_model onto the doc and
    # pollute the DB's vector space (cosine similarity across models is noise).
    from .model_compat import assert_compatible
    assert_compatible(indexer, embedder, context="fetch_update")

    # 4c. upsert recomputes vector from new description + stamps embed_model
    indexer.upsert(doc, embedder)

    return {
        "action": "updated",
        "doc": doc,
        "reason": "content changed, updated context/description/vector/hash",
    }
