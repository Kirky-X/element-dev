"""Tests for audit P2-7: strip element-plus.run demo-link noise from fetches.

Element Plus component pages link live demos as
https://element-plus.run/#<base64>, where the base64 blob encodes the entire
demo project (several KB per link). These anchors must be removed in
extract_main_html() — before HTML→Markdown — or they dominate the fetched
context and destabilize context_hash-length metrics.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetcher._http import extract_main_html, html_to_markdown  # noqa: E402


class TestDemoLinkNoise:
    def test_demo_anchor_stripped_from_main_html(self):
        html = (
            "<main><p>Button docs prose.</p>"
            '<a href="https://element-plus.run/#eyJzY3JpcHQiOiJ4In0=">'
            "Edit on Element Plus Playground</a>"
            "<p>More prose.</p></main>"
        )
        out = extract_main_html(html)
        assert "element-plus.run" not in out
        assert "eyJzY3JpcHQiOiJ4In0" not in out
        assert "Button docs prose." in out
        assert "More prose." in out

    def test_noise_ratio_drops_after_cleanup(self):
        """The audited ~43% noise case: a huge base64 demo blob vs real prose."""
        blob = "eyAibW9yZSI6IiJ9" * 300  # ~4.8KB encoded demo
        html = (
            "<main><p>Real documentation paragraph.</p>"
            f'<a href="https://element-plus.run/#{blob}">demo</a>'
            "</main>"
        )
        md = html_to_markdown(extract_main_html(html))
        assert "element-plus.run" not in md
        assert blob not in md
        assert "Real documentation paragraph." in md
        # noise gone: remaining content is the prose only
        assert len(md) < 100

    def test_prose_links_are_preserved(self):
        html = (
            "<main>"
            '<a href="https://element-plus.org/zh-CN/component/icon">Icon</a>'
            "<p>body text</p>"
            "</main>"
        )
        md = html_to_markdown(extract_main_html(html))
        assert "[Icon](https://element-plus.org/zh-CN/component/icon)" in md
        assert "body text" in md

    def test_cloudflare_artifacts_still_stripped(self):
        """C1 behavior must not regress alongside the P2-7 filter."""
        html = (
            "<main><a href=\"/cdn-cgi/l/email-protection#8fa2fdbcec\">[email&#160;protected]</a>"
            "<p>ok</p></main>"
        )
        out = extract_main_html(html)
        assert "email-protection" not in out
        assert "8fa2fdbcec" not in out
