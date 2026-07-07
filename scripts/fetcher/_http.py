"""Shared HTTP utilities for Element Plus fetcher.

Element Plus docs are served as static HTML at element-plus.org. Unlike hap-dev
(which uses HarmonyOS search APIs with POST payloads), here we just GET a URL.

This module keeps the html_to_markdown converter from hap-dev verbatim (it is
generic HTML→Markdown, not HarmonyOS-specific) plus a few Element Plus
specifics: site base URL, main-content container selector, common headers.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

# Element Plus doc site base. Sidebars already store full URLs, so this is
# only used for documentation / sanity checks.
SITE_BASE = "https://element-plus.org"

# Default SSRF host whitelist — the doc site the skill is built to fetch from.
# Hosts are compared case-insensitively. `site_base`'s host is always added at
# validate-time, so changing config.json's site_base extends the whitelist
# without editing code.
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "element-plus.org",
        "www.element-plus.org",
    }
)

# Cap on manual redirect following. We disable httpx auto-redirect and walk
# Location headers ourselves so every hop is host-validated (SSRF guard against
# open-redirect → internal-network pivoting).
MAX_REDIRECTS = 3


def _is_blocked_ip(ip_str: str) -> bool:
    """True if `ip_str` points at a private/loopback/link-local/reserved/
    multicast/multicast/unspecified address — the SSRF "internal network" space.

    Applies unconditionally, even when `allow_any_host=True`, because allowing
    arbitrary hosts must NOT permit pivoting onto 127.0.0.1 / 169.254.169.254
    (cloud metadata) / RFC1918 ranges.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False  # not an IP literal — host validation handles DNS names
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_hosts(hostname: str) -> list[str]:
    """Resolve `hostname` to its IPv4/IPv6 address strings. Empty on DNS failure.

    Used to block hostnames that resolve to internal IPs (DNS-rebinding / split
    horizon SSRF). Resolution failures are NOT fatal — the host-whitelist check
    still applies; we just can't assert about IPs we couldn't resolve.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, socket.herror, OSError):
        return []
    seen: list[str] = []
    for info in infos:
        try:
            addr = info[4][0]
        except (IndexError, TypeError):
            continue
        if addr not in seen:
            seen.append(addr)
    return seen


def _validate_host(
    url: str,
    *,
    allow_any_host: bool = False,
    site_base: str | None = None,
    allowed_hosts: frozenset[str] | None = None,
    resolve_dns: bool = True,
) -> None:
    """SSRF guard — raise ValueError if `url`'s host is not permitted.

    Two-layer defense:
      1. DNS resolution → block any resolved IP in the internal network space
         (private/loopback/link-local/reserved/multicast/unspecified). This
         fires even when `allow_any_host=True` — it is the hard floor.
      2. Hostname whitelist — default-allow only the Element Plus doc domains
         plus `site_base`'s host. `allow_any_host=True` skips this layer
         (but NOT layer 1).

    Call this on the request URL and on every redirect Location.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"SSRF guard: refusing non-http(s) URL: {url!r}")
    host = parts.hostname
    if not host:
        raise ValueError(f"SSRF guard: URL has no host: {url!r}")

    # Layer 1: IP-space block (unconditional).
    # First if the host is itself an IP literal, then via DNS resolution.
    if _is_blocked_ip(host):
        raise ValueError(f"SSRF guard: host {host!r} is a blocked IP literal ({url!r})")
    if resolve_dns:
        for addr in _resolve_hosts(host):
            if _is_blocked_ip(addr):
                raise ValueError(
                    f"SSRF guard: host {host!r} resolves to internal IP "
                    f"{addr} ({url!r}) — refusing (even with --allow-any-host)"
                )

    # Layer 2: hostname whitelist.
    if allow_any_host:
        return
    allow = set(DEFAULT_ALLOWED_HOSTS)
    if allowed_hosts:
        allow.update(allowed_hosts)
    if site_base:
        sb_host = urlsplit(site_base).hostname
        if sb_host:
            allow.add(sb_host)
    if host.lower() not in {h.lower() for h in allow}:
        raise ValueError(
            f"SSRF guard: host {host!r} not in whitelist {sorted(allow)} "
            f"(url={url!r}). Pass allow_any_host=True / --allow-any-host to "
            f"override (internal-IP block still applies)."
        )


# Element Plus doc pages put the main content inside <main> ... </main>.
# We extract that subset before HTML→Markdown conversion to avoid converting
# the nav/sidebar/footer chrome.
_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)

# <title> tag for the page title fallback.
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)

# C1: Cloudflare email protection generates RANDOM hash tokens in
# /cdn-cgi/l/email-protection#<hex> URLs on every page load. These make the
# content_hash unstable (changes every fetch even though content is identical).
# Strip these artifacts before HTML→Markdown conversion so context_hash is
# deterministic across fetches of the same page.
_CF_EMAIL_LINK_RE = re.compile(
    r'<a\s+[^>]*href="/cdn-cgi/l/email-protection[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_CF_EMAIL_ATTR_RE = re.compile(
    r"/cdn-cgi/l/email-protection#[0-9a-fA-F]+",
    re.IGNORECASE,
)

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
_STRONG_RE = re.compile(
    r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", re.DOTALL | re.IGNORECASE
)
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

    C1: also strips Cloudflare email-protection artifacts (random hash tokens
    that change every page load) so context_hash is deterministic across
    fetches of the same page.
    """
    if not full_html:
        return ""
    m = _MAIN_RE.search(full_html)
    html = m.group(1) if m else full_html
    # C1: strip Cloudflare email protection artifacts for stable content_hash
    html = _CF_EMAIL_LINK_RE.sub(r"\1", html)  # unwrap email-protection links
    html = _CF_EMAIL_ATTR_RE.sub("", html)  # remove leftover hash attrs
    return html


def extract_page_title(full_html: str) -> str:
    """Return the <title> tag content, stripped. Empty string if absent."""
    if not full_html:
        return ""
    m = _TITLE_RE.search(full_html)
    if not m:
        return ""
    return _decode_entities(_strip_tags(m.group(1))).strip()
