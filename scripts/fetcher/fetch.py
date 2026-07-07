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
from urllib.parse import urljoin

# Allow both ``python3 -m scripts.fetcher.fetch`` and direct
# ``python3 scripts/fetcher/fetch.py`` invocation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from scripts.fetcher._http import (
    COMMON_HEADERS,
    MAX_REDIRECTS,
    SITE_BASE,
    TIMEOUT,
    _validate_host,
    extract_main_html,
    extract_page_title,
    html_to_markdown,
)


def fetch(
    url: str,
    client: httpx.Client | None = None,
    *,
    allow_any_host: bool = False,
    max_redirects: int = MAX_REDIRECTS,
    site_base: str | None = None,
) -> dict:
    """GET an Element Plus doc URL and return its Markdown content.

    Returns ``{title, url, content}`` on success, or ``{"error": "..."}`` on
    HTTP failure (errors surfaced explicitly, never swallowed — Rule 12).

    SSRF guards (P0):
      * Host whitelist — default-allow only Element Plus doc domains +
        ``site_base``'s host. ``allow_any_host=True`` (``--allow-any-host``)
        opens the whitelist but the internal-IP block ALWAYS applies.
      * ``follow_redirects=False`` on the client; redirects are followed
        manually up to ``max_redirects`` times, and every Location target is
        re-validated against the same host whitelist. Prevents open-redirect →
        internal-network pivoting.
    """
    if not url or not url.strip():
        raise ValueError("url must not be empty")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must be absolute (http/https): {url!r}")

    # Validate the request URL BEFORE any network call.
    _validate_host(url, allow_any_host=allow_any_host, site_base=site_base or SITE_BASE)

    should_close = client is None
    # follow_redirects=False: we walk Location ourselves so each hop is
    # host-validated. If a caller passes their own client, they own its
    # redirect setting; we still validate the entry URL above.
    client = client or httpx.Client(
        timeout=TIMEOUT,
        headers=COMMON_HEADERS,
        follow_redirects=False,
    )
    try:
        current_url = url
        hops = 0
        while True:
            resp = client.get(current_url)
            if resp.is_redirect and hops < max_redirects:
                location = resp.headers.get("location", "")
                if not location:
                    break
                next_url = urljoin(current_url, location)
                # Re-validate EVERY redirect target — no exceptions.
                _validate_host(
                    next_url,
                    allow_any_host=allow_any_host,
                    site_base=site_base or SITE_BASE,
                )
                current_url = next_url
                hops += 1
                continue
            break
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
        "url": current_url,
        "content": markdown,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an Element Plus doc URL and convert to Markdown",
    )
    parser.add_argument("url", help="Absolute doc URL (https://element-plus.org/...)")
    parser.add_argument(
        "--allow-any-host",
        dest="allow_any_host",
        action="store_true",
        help="disable the host whitelist (internal-IP block still applies); "
        "use only when fetching non-element-plus.org URLs deliberately",
    )
    args = parser.parse_args(argv)

    try:
        result = fetch(args.url, allow_any_host=args.allow_any_host)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
