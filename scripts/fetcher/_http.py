"""Shared HTTP utilities for Element Plus fetcher.

Element Plus docs are served as static HTML at element-plus.org. Unlike hap-dev
(which uses HarmonyOS search APIs with POST payloads), here we just GET a URL.

This module keeps the html_to_markdown converter from hap-dev verbatim (it is
generic HTML→Markdown, not HarmonyOS-specific) plus a few Element Plus
specifics: site base URL, main-content container selector, common headers.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

# Element Plus doc site base. Sidebars already store full URLs, so this is
# only used for documentation / sanity checks.
SITE_BASE = "https://element-plus.org"

# Element Plus doc pages put the main content inside <main> ... </main>.
# We extract that subset before HTML→Markdown conversion to avoid converting
# the nav/sidebar/footer chrome.
_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)

# <title> tag for the page title fallback.
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)

COMMON_HEADERS: dict[str, str] = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "user-agent": "Element-Plus-Dev-Skill/1.0",
}

TIMEOUT = 30


# ---------------------------------------------------------------------------
# HTML -> Markdown converter (copied verbatim from hap-dev scripts/search/_http.py).
# Generic HTML→Markdown with re module. Not HarmonyOS-specific.
# ---------------------------------------------------------------------------

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_CODE_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_A_RE = re.compile(
    r'<a\s+[^>]*?href\s*=\s*"([^"]*)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_A_SQUOTE_RE = re.compile(
    r"<a\s+[^>]*?href\s*=\s*'([^']*)'[^>]*>(.*?)</a>",
    re.DOTALL | re.IGNORECASE,
)
_STRONG_RE = re.compile(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", re.DOTALL | re.IGNORECASE)
_EM_RE = re.compile(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", re.DOTALL | re.IGNORECASE)
_TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_OL_RE = re.compile(r"<ol[^>]*>(.*?)</ol>", re.DOTALL | re.IGNORECASE)
_UL_RE = re.compile(r"<ul[^>]*>(.*?)</ul>", re.DOTALL | re.IGNORECASE)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_H_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_P_OPEN_RE = re.compile(r"<p\b[^>]*>", re.IGNORECASE)
_P_CLOSE_RE = re.compile(r"</p>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HR_RE = re.compile(r"<hr\s*/?>", re.IGNORECASE)
_BQ_RE = re.compile(r"<blockquote\b[^>]*>(.*?)</blockquote>", re.DOTALL | re.IGNORECASE)

_ENTITY_MAP = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
    "&nbsp;": " ",
}
_ENTITY_DEC_RE = re.compile(r"&#(\d+);")
_ENTITY_HEX_RE = re.compile(r"&#x([0-9a-fA-F]+);", re.IGNORECASE)
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_SPACES_RE = re.compile(r" +\n")


def _decode_entities(text: str) -> str:
    for ent, ch in _ENTITY_MAP.items():
        text = text.replace(ent, ch)
    text = _ENTITY_DEC_RE.sub(lambda m: _safe_chr(m.group(1), 10), text)
    text = _ENTITY_HEX_RE.sub(lambda m: _safe_chr(m.group(1), 16), text)
    return text


def _safe_chr(value: str, base: int) -> str:
    try:
        return chr(int(value, base))
    except (ValueError, OverflowError):
        return f"&#{('x' + value) if base == 16 else value};"


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html)


def _convert_table(match: re.Match[str]) -> str:
    table_html = match.group(0)
    rows = _TR_RE.findall(table_html)
    parsed: list[list[str]] = []
    for row in rows:
        cells = _CELL_RE.findall(row)
        parsed.append([_strip_tags(c).strip() for c in cells])
    parsed = [r for r in parsed if any(cell for cell in r)]
    if not parsed:
        return ""
    n_cols = max(len(r) for r in parsed)
    lines: list[str] = []
    for idx, row in enumerate(parsed):
        padded = [row[c].strip() if c < len(row) else "" for c in range(n_cols)]
        lines.append("| " + " | ".join(padded) + " |")
        if idx == 0:
            lines.append("| " + " | ".join("-" for _ in range(n_cols)) + " |")
    return "\n" + "\n".join(lines) + "\n"


def _convert_list(match: re.Match[str], ordered: bool) -> str:
    body = match.group(1)
    items = _LI_RE.findall(body)
    out: list[str] = []
    for i, raw in enumerate(items, start=1):
        text = _strip_tags(raw).strip()
        text = re.sub(r"\s+", " ", text)
        prefix = f"{i}. " if ordered else "- "
        out.append(prefix + text)
    return "\n" + "\n".join(out) + "\n" if out else ""


def _convert_heading(match: re.Match[str]) -> str:
    level = int(match.group(1))
    text = _strip_tags(match.group(2)).strip()
    text = re.sub(r"\s+", " ", text)
    return f"\n\n{'#' * level} {text}\n\n"


def _convert_pre(match: re.Match[str]) -> str:
    inner = match.group(1)
    inner = _TAG_RE.sub("", inner)
    inner = _decode_entities(inner)
    inner = inner.replace("\r\n", "\n").replace("\r", "\n")
    if inner.startswith("\n"):
        inner = inner[1:]
    if inner.endswith("\n"):
        inner = inner[:-1]
    return f"\n```\n{inner}\n```\n"


def html_to_markdown(html: str) -> str:
    """Convert an HTML fragment to Markdown.

    Copied verbatim from hap-dev scripts/search/_http.py — generic converter,
    not specific to any doc site. Preserves code blocks, headings, lists,
    tables, and links.
    """
    if not html or not html.strip():
        return ""

    text = html
    text = _SCRIPT_RE.sub("", text)
    text = _STYLE_RE.sub("", text)

    pre_blocks: list[str] = []

    def _stash_pre(m: re.Match[str]) -> str:
        pre_blocks.append(_convert_pre(m))
        return f"\x00PRE{len(pre_blocks) - 1}\x00"

    text = _PRE_RE.sub(_stash_pre, text)
    text = _TABLE_RE.sub(_convert_table, text)
    for _ in range(3):
        new_text = _UL_RE.sub(lambda m: _convert_list(m, ordered=False), text)
        if new_text == text:
            break
        text = new_text
    for _ in range(3):
        new_text = _OL_RE.sub(lambda m: _convert_list(m, ordered=True), text)
        if new_text == text:
            break
        text = new_text
    text = _H_RE.sub(_convert_heading, text)
    text = _A_RE.sub(r"[\2](\1)", text)
    text = _A_SQUOTE_RE.sub(r"[\2](\1)", text)
    text = _STRONG_RE.sub(r"**\1**", text)
    text = _EM_RE.sub(r"*\1*", text)
    text = _BQ_RE.sub(lambda m: "\n> " + _strip_tags(m.group(1)).strip() + "\n", text)
    text = _CODE_RE.sub(r"`\1`", text)
    text = _P_OPEN_RE.sub("\n\n", text)
    text = _P_CLOSE_RE.sub("\n", text)
    text = _BR_RE.sub("\n", text)
    text = _HR_RE.sub("\n---\n", text)
    text = _TAG_RE.sub("", text)
    text = _decode_entities(text)

    def _restore_pre(m: re.Match[str]) -> str:
        return pre_blocks[int(m.group(1))]

    text = re.sub(r"\x00PRE(\d+)\x00", _restore_pre, text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    text = _TRAILING_SPACES_RE.sub("\n", text)
    return text.strip()


def extract_main_html(full_html: str) -> str:
    """Return the <main>...</main> subset of `full_html`, or the whole HTML
    if no <main> tag is present (graceful degradation).

    Element Plus doc pages wrap the article content in <main>, so extracting
    it first avoids converting nav/sidebar/footer chrome into Markdown.
    """
    if not full_html:
        return ""
    m = _MAIN_RE.search(full_html)
    if m:
        return m.group(1)
    return full_html


def extract_page_title(full_html: str) -> str:
    """Return the <title> tag content, stripped. Empty string if absent."""
    if not full_html:
        return ""
    m = _TITLE_RE.search(full_html)
    if not m:
        return ""
    return _decode_entities(_strip_tags(m.group(1))).strip()
