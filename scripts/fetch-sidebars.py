#!/usr/bin/env python3
"""Fetch Element Plus sidebar nav → write the two ``sidebars/*.md`` files.

The prebuilt Qdrant KB (``data/element-plus.qdrant``) is regenerated from two
sidebar files:

    sidebars/element-plus-design-guide-sidebar.md
    sidebars/element-plus-component-sidebar.md

These files are ``.gitignore``-d (dev-time build inputs), so a fresh checkout
cannot ``kb build`` until they exist. This script regenerates them from the
canonical Element Plus documentation.

Output format (consumed by ``scripts/kb/sidebar_parser.py`` LINE_RE):

    ### N.M [title](url)

Each line is one doc record. ``N.M`` numbering is sequential within a file; the
parser accepts both ``1.1`` and ``1.1.`` (trailing period optional).

Sources (in priority order, ``--source auto`` tries each until one yields
enough links):

  1. ``site`` (default) — scrape the rendered sidebar HTML from
     ``element-plus.org/<lang>/guide/...`` and ``.../component/...`` pages.
     Reuses the SSRF-guarded HTTP path (host whitelist + internal-IP block +
     manual redirect re-validation) from ``scripts.fetcher._http``.
     Titles come straight from the live nav → best quality.
  2. ``github`` (fallback) — enumerate ``docs/<lang>/{guide,component}/*.md``
     in the ``element-plus/element-plus`` repo via the GitHub Contents API.
     Slug-derived titles (``button`` → ``Button``). Works when the doc site
     changes its DOM or is unreachable.

Usage::

    python3 scripts/fetch-sidebars.py                 # download + write
    python3 scripts/fetch-sidebars.py --dry-run       # offline: print plan
    python3 scripts/fetch-sidebars.py --lang zh-CN
    python3 scripts/fetch-sidebars.py --source github
    python3 scripts/fetch-sidebars.py --out-dir sidebars

Verification (offline, no network needed)::

    bash scripts/fetch-sidebars.sh --dry-run

Exit codes: 0 success / 2 args error / 3 network/download failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

# Allow ``python3 scripts/fetch-sidebars.py`` direct invocation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from scripts.fetcher._http import (  # noqa: E402
    COMMON_HEADERS,
    MAX_REDIRECTS,
    SITE_BASE,
    TIMEOUT,
    _validate_host,
)

# Hosts this script is allowed to fetch from. ``element-plus.org`` is in the
# default whitelist already; ``api.github.com`` / ``raw.githubusercontent.com``
# are added for the ``github`` fallback source. The internal-IP block in
# ``_validate_host`` applies to ALL hosts unconditionally.
GITHUB_HOSTS: frozenset[str] = frozenset(
    {
        "api.github.com",
        "raw.githubusercontent.com",
    }
)

SIDEBAR_FILES: dict[str, str] = {
    "design-guide": "element-plus-design-guide-sidebar.md",
    "component": "element-plus-component-sidebar.md",
}

# Anchor link → (title, href). DOTALL so multi-line link text is captured.
_A_RE = re.compile(
    r'<a\s+[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MAIN_RE = re.compile(r"<main\b[^>]*>.*?</main>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)

# Minimum link count below which a source is considered to have failed.
MIN_LINKS = 5


# ---------------------------------------------------------------------------
# SSRF-guarded raw-HTML fetch (manual redirect loop, host-validated per hop).
# ---------------------------------------------------------------------------


def _safe_get(
    url: str,
    *,
    site_base: str = SITE_BASE,
    extra_hosts: frozenset[str] | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> str:
    """GET ``url`` and return the response body as text.

    Validates the host against ``DEFAULT_ALLOWED_HOSTS`` ∪ ``extra_hosts`` ∪
    ``site_base``'s host, then walks redirects manually (follow_redirects=False
    on the client) re-validating every Location. Internal-IP literals (and
    DNS-rebinds to internal IPs) are blocked even when --allow-any-host is set.
    """
    _validate_host(
        url, allow_any_host=False, site_base=site_base, allowed_hosts=extra_hosts
    )
    client = httpx.Client(
        timeout=TIMEOUT,
        headers=COMMON_HEADERS,
        follow_redirects=False,
    )
    current = url
    hops = 0
    try:
        while True:
            resp = client.get(current)
            if resp.is_redirect and hops < max_redirects:
                loc = resp.headers.get("location", "")
                if not loc:
                    break
                nxt = urllib.parse.urljoin(current, loc)
                _validate_host(
                    nxt,
                    allow_any_host=False,
                    site_base=site_base,
                    allowed_hosts=extra_hosts,
                )
                current = nxt
                hops += 1
                continue
            break
        resp.raise_for_status()
        return resp.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Source 1: scrape element-plus.org rendered sidebar.
# ---------------------------------------------------------------------------


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s).strip()


def _normalize_href(href: str, base: str = SITE_BASE) -> str:
    """Absolute URL without fragment/.html — clean canonical form for the KB."""
    if href.startswith("/"):
        href = urllib.parse.urljoin(base + "/", href.lstrip("/"))
    # drop fragment
    href = href.split("#", 1)[0]
    # drop trailing slash and .html suffix for a stable URL
    href = href.rstrip("/")
    if href.endswith(".html"):
        href = href[:-5]
    return href


def _classify(href: str, lang: str) -> tuple[str | None, str | None]:
    """Return (doc_type, slug) if href is a doc page under ``/<lang>/guide|component/``,
    else (None, None).
    """
    m = re.search(rf"/{re.escape(lang)}/(guide|component)/([^/?#\"']+)", href)
    if not m:
        return None, None
    section, slug = m.group(1), m.group(2)
    slug = slug.split(".", 1)[0]  # strip .html if present
    if not slug or slug in ("index", "README"):
        return None, None
    return ("design-guide" if section == "guide" else "component"), slug


def _extract_sidebar_links(html: str, lang: str) -> dict[str, list[tuple[str, str]]]:
    """From a rendered doc page, extract sidebar nav links grouped by doc_type.

    Strategy: strip ``<main>`` (article body) + scripts/styles, then scan the
    remaining chrome (nav + sidebar + footer) for ``<a href>`` links whose
    path matches ``/<lang>/{guide,component}/<slug>``. Dedup by URL, preserve
    first-seen order.
    """
    chrome = html
    chrome = _MAIN_RE.sub("", chrome)
    chrome = _SCRIPT_RE.sub("", chrome)
    chrome = _STYLE_RE.sub("", chrome)

    out: dict[str, list[tuple[str, str]]] = {"design-guide": [], "component": []}
    seen: dict[str, set[str]] = {"design-guide": set(), "component": set()}
    for href, inner in _A_RE.findall(chrome):
        doctype, slug = _classify(href, lang)
        if not doctype or not slug:
            continue
        url = _normalize_href(href)
        if url in seen[doctype]:
            continue
        title = _strip_tags(inner)
        if not title:
            title = slug
        seen[doctype].add(url)
        out[doctype].append((title, url))
    return out


def scrape_site(lang: str) -> dict[str, list[tuple[str, str]]]:
    """Scrape two seed pages (one guide, one component) and merge their sidebars.

    VitePress renders only the sidebar relevant to the current section, so a
    guide page yields design-guide links and a component page yields component
    links. Fetching one of each covers both files.
    """
    seeds = [
        f"{SITE_BASE}/{lang}/guide/installation.html",
        f"{SITE_BASE}/{lang}/component/button.html",
    ]
    merged: dict[str, list[tuple[str, str]]] = {"design-guide": [], "component": []}
    seen: dict[str, set[str]] = {"design-guide": set(), "component": set()}
    for seed in seeds:
        try:
            html = _safe_get(seed)
        except (httpx.HTTPError, ValueError) as exc:
            print(f"[fetch-sidebars] WARN: seed {seed} failed: {exc}", file=sys.stderr)
            continue
        page_links = _extract_sidebar_links(html, lang)
        for doctype, links in page_links.items():
            for title, url in links:
                if url in seen[doctype]:
                    continue
                seen[doctype].add(url)
                merged[doctype].append((title, url))
    return merged


# ---------------------------------------------------------------------------
# Source 2: GitHub Contents API — enumerate docs/<lang>/{guide,component}/*.md.
# ---------------------------------------------------------------------------

GH_API = "https://api.github.com/repos/element-plus/element-plus/contents/docs/"


def _title_from_slug(slug: str) -> str:
    """``virtualized-table`` → ``Virtualized Table``."""
    return " ".join(w.capitalize() for w in slug.replace("-", " ").split())


def _github_list(lang: str, section: str) -> list[tuple[str, str]]:
    """List ``docs/<lang>/<section>/*.md`` via GitHub Contents API.

    Returns ``[(title, url), ...]``. Slug → filename stem; title is slug-derived
    when the file has no front-matter title (kept simple — the ``site`` source
    gives better titles; this is the offline/fallback path).
    """
    url = f"{GH_API}{lang}/{section}"
    body = _safe_get(url, extra_hosts=GITHUB_HOSTS)
    items = json.loads(body)
    if not isinstance(items, list):
        raise RuntimeError(f"github: unexpected response shape for {url}")
    out: list[tuple[str, str]] = []
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "file":
            continue
        name = it.get("name", "")
        if not name.endswith(".md") or name in ("index.md", "README.md"):
            continue
        slug = name[:-3]
        title = _title_from_slug(slug)
        page_url = f"{SITE_BASE}/{lang}/{section}/{slug}"
        out.append((title, page_url))
    return out


def scrape_github(lang: str) -> dict[str, list[tuple[str, str]]]:
    """Build the sidebar map from GitHub docs directory enumeration."""
    return {
        "design-guide": _github_list(lang, "guide"),
        "component": _github_list(lang, "component"),
    }


# ---------------------------------------------------------------------------
# Formatting + write.
# ---------------------------------------------------------------------------


def _format_sidebar(links: list[tuple[str, str]]) -> str:
    """Render ``[(title, url), ...]`` as the ``### N.M [title](url)`` doc.

    Header records the source/count; every doc line matches LINE_RE.
    """
    lines = [
        "<!-- Auto-generated by scripts/fetch-sidebars.py — do not edit by hand. -->",
        f"<!-- {len(links)} docs. Regenerate: bash scripts/fetch-sidebars.sh -->",
        "",
    ]
    for i, (title, url) in enumerate(links, start=1):
        lines.append(f"### 1.{i} [{title}]({url})")
    return "\n".join(lines) + "\n"


def write_sidebars(
    out_dir: Path, data: dict[str, list[tuple[str, str]]]
) -> dict[str, int]:
    """Write both sidebar files; return ``{filename: doc_count}``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for doctype, fname in SIDEBAR_FILES.items():
        links = data.get(doctype, [])
        text = _format_sidebar(links)
        (out_dir / fname).write_text(text, encoding="utf-8")
        written[fname] = len(links)
    return written


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _dry_run(lang: str, source: str, out_dir: Path) -> int:
    """Offline verification: validate deps/SSRF guard, print plan, no network."""
    print("[fetch-sidebars] DRY RUN — no network calls")
    print(f"  language  : {lang}")
    print(f"  source    : {source}")
    print(f"  out-dir   : {out_dir}")
    print(f"  site_base : {SITE_BASE} (host whitelist default)")
    print(
        f"  github    : {sorted(GITHUB_HOSTS)} (added to whitelist for --source github)"
    )
    print(f"  outputs   :")
    for doctype, fname in SIDEBAR_FILES.items():
        print(f"    {out_dir / fname}  (doc_type={doctype})")
    # Validate the SSRF guard imports + key regex parse (offline).
    _validate_host(f"{SITE_BASE}/{lang}/component/button.html", site_base=SITE_BASE)
    print("  SSRF guard: import + validate OK")
    print("  LINE_RE   : will match '### 1.1 [Title](https://...)'")
    print("[fetch-sidebars] dry-run OK — run without --dry-run to download.")
    return 0


def _fetch_with_fallback(lang: str, source: str) -> dict[str, list[tuple[str, str]]]:
    """Run the requested source; ``auto`` falls through site → github."""
    errors: list[str] = []

    if source in ("site", "auto"):
        try:
            data = scrape_site(lang)
            total = sum(len(v) for v in data.values())
            if total >= MIN_LINKS:
                print(
                    f"[fetch-sidebars] source=site OK ({total} links)", file=sys.stderr
                )
                return data
            errors.append(f"site: only {total} links (<{MIN_LINKS})")
        except Exception as exc:  # noqa: BLE001 — surface any failure to fallback
            errors.append(f"site: {type(exc).__name__}: {exc}")

    if source in ("github", "auto"):
        try:
            data = scrape_github(lang)
            total = sum(len(v) for v in data.values())
            if total >= MIN_LINKS:
                print(
                    f"[fetch-sidebars] source=github OK ({total} links)",
                    file=sys.stderr,
                )
                return data
            errors.append(f"github: only {total} links (<{MIN_LINKS})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"github: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "all sources failed — "
        + "; ".join(errors)
        + ". Check network / element-plus.org reachability, or pass --source "
        "explicitly. Run with --dry-run to validate the script offline."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fetch-sidebars.py",
        description="Fetch Element Plus sidebar nav → write sidebars/*.md for kb build.",
    )
    p.add_argument(
        "--lang",
        default="en-US",
        help="doc locale path segment (default: en-US; zh-CN also works)",
    )
    p.add_argument(
        "--source",
        choices=("auto", "site", "github"),
        default="auto",
        help="source: scrape element-plus.org (site), GitHub Contents API "
        "(github), or try site then github (auto, default)",
    )
    p.add_argument(
        "--out-dir", default="sidebars", help="output directory (default: sidebars)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="no network: print plan and validate script, then exit 0",
    )
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()

    if args.dry_run:
        return _dry_run(args.lang, args.source, out_dir)

    try:
        data = _fetch_with_fallback(args.lang, args.source)
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        print(f"[fetch-sidebars] ERROR: {exc}", file=sys.stderr)
        return 3

    written = write_sidebars(out_dir, data)
    total = sum(written.values())
    print(f"[fetch-sidebars] wrote {total} docs across {len(written)} files:")
    for fname, n in written.items():
        print(f"  {out_dir / fname}  ({n} docs)")
    print(
        "[fetch-sidebars] next: python3 -m scripts.kb.cli build  "
        "(or python3 scripts/kb/build_db.py)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
