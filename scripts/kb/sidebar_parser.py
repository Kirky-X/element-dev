"""Sidebar markdown parser for Element Plus docs.

Parses Element Plus sidebar `.md` files into doc records. Each line of the form

    ### N.N [title](url)        # design-guide sidebar
    ### N.N. [title](url)       # component sidebar (trailing period after number)

( exactly 3 hashes, a numbered heading with optional trailing period, then a
markdown link ) is one doc record.

Lines whose `###` heading has plain text only (no `[title](url)` link) are NOT
doc records — they are category headers (e.g. `## 2. Basic 基础组件`).

Differs from hap-dev's parser because Element Plus sidebars use 3 hashes (not 4)
and the component sidebar uses a trailing period after the section number
(`2.1.` instead of `2.1`).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 3 hashes + numbered heading (with optional trailing period) + markdown link.
# Compared to hap-dev: 3 hashes (not 4) and `[\d.]+\.?` allows trailing period.
LINE_RE = re.compile(r"^###\s+[\d.]+\.?\s+\[([^\]]+)\]\(([^)]+)\)")

# Map sidebar filename -> doc_type.
SIDEBAR_FILE_MAP: dict[str, str] = {
    "element-plus-design-guide-sidebar.md": "design-guide",
    "element-plus-component-sidebar.md": "component",
}

NO_DESCRIPTION = "无描述"
NO_CONTEXT = ""  # empty string = context not yet fetched


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _make_content_hash(
    title: str,
    url: str,
    doc_type: str,
    description: str = NO_DESCRIPTION,
    links: list[str] | None = None,
) -> str:
    """Content fingerprint = sha1 over the doc's content-bearing fields.

    Same formula as hap-dev (B4): title + url + doc_type + description +
    sorted links. Order-invariant on links. Used by reindex to detect if
    a doc needs re-embedding (description/links changed).
    """
    sorted_links = sorted(links) if links else []
    raw = (
        title + url + doc_type + description
        + "|links:" + ",".join(sorted_links)
    ).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def make_context_hash(context: str) -> str:
    """Hash of the fetched webpage content (context field).

    Used by fetch_update to detect if the webpage content changed since the
    last fetch. Empty string → empty hash (so unfetched docs all share "").
    """
    if not context:
        return ""
    return hashlib.sha1(context.encode("utf-8")).hexdigest()


def parse_sidebar(path: str, doc_type: str) -> list[dict[str, Any]]:
    """Parse a sidebar markdown file into a list of doc records.

    Each record has the 12-field schema (after C1 upgrade — context support):
      id, title, doc_type, url, description, context, links,
      created_at, updated_at, content_hash, context_hash, embed_model.

    `context` and `context_hash` are initially empty — populated by
    fetch_update when the URL is fetched. `embed_model` is "" until the
    indexer stamps it on build/upsert.

    Raises FileNotFoundError if `path` does not exist (Rule 12: fail loud).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"sidebar file not found: {path}")

    now = _now_iso()
    docs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            title, url = m.group(1), m.group(2)
            doc_id = _make_id(url)
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            docs.append({
                "id": doc_id,
                "title": title,
                "doc_type": doc_type,
                "url": url,
                "description": NO_DESCRIPTION,
                "context": NO_CONTEXT,  # C1: empty until fetch_update populates it
                "links": [],
                "created_at": now,
                "updated_at": now,
                "content_hash": _make_content_hash(
                    title, url, doc_type, NO_DESCRIPTION, [],
                ),
                "context_hash": "",  # C1: empty until fetch_update populates it
                "embed_model": "",
            })
    return docs


def parse_all_sidebars(sidebars_dir: str) -> list[dict[str, Any]]:
    """Parse all known Element Plus sidebar files in `sidebars_dir`."""
    base = Path(sidebars_dir)
    all_docs: list[dict[str, Any]] = []
    for fname, doc_type in SIDEBAR_FILE_MAP.items():
        fpath = base / fname
        if not fpath.exists():
            raise FileNotFoundError(f"missing sidebar: {fpath}")
        all_docs.extend(parse_sidebar(str(fpath), doc_type))
    return all_docs
