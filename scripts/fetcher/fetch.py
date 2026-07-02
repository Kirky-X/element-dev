"""Element Plus doc fetcher — HTTP GET a doc URL and return Markdown content.

Usage:
    python3 -m scripts.fetcher.fetch <url>

Unlike hap-dev (which uses HarmonyOS search APIs with POST payloads and
object_id lookup), Element Plus docs are served as static HTML pages. The
sidebar already gives us the full URL — we just GET it, extract the <main>
content, convert HTML→Markdown, and return {title, url, content}.

This module replaces hap-dev's scripts/search/{search.py, detail.py} pair
with a single, simpler fetch() function.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow both ``python3 -m scripts.fetcher.fetch`` and direct
# ``python3 scripts/fetcher/fetch.py`` invocation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from scripts.fetcher._http import (
    COMMON_HEADERS,
    TIMEOUT,
    extract_main_html,
    extract_page_title,
    html_to_markdown,
)


def fetch(url: str, client: httpx.Client | None = None) -> dict:
    """GET an Element Plus doc URL and return its Markdown content.

    Returns ``{title, url, content}`` on success, or ``{"error": "..."}`` on
    HTTP failure (errors surfaced explicitly, never swallowed — Rule 12).
    """
    if not url or not url.strip():
        raise ValueError("url must not be empty")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must be absolute (http/https): {url!r}")

    should_close = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers=COMMON_HEADERS, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text
    except httpx.HTTPError as exc:
        return {"error": f"HTTP request failed: {exc}", "url": url}
    finally:
        if should_close:
            client.close()

    main_html = extract_main_html(html)
    markdown = html_to_markdown(main_html)
    title = extract_page_title(html)

    return {
        "title": title,
        "url": url,
        "content": markdown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an Element Plus doc URL and convert to Markdown",
    )
    parser.add_argument("url", help="Absolute doc URL (https://element-plus.org/...)")
    args = parser.parse_args(argv)

    try:
        result = fetch(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
