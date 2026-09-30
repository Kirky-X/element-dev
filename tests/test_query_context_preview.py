"""Tests for audit P1-5: query results expose a truncated context preview.

The C1 schema stores fetched page content in the `context` payload field, but
query results used to omit it entirely. Now every result carries
`has_context` + `context_preview` (first 300 chars); the full text is
available via `kb show --id` (covered in test_cli_show_config.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb.query import query  # noqa: E402


class _FakeEmbedder:
    model_name = "mock-384"

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class _FakeIndexer:
    """Returns a fixed candidate pool; just enough for query()'s fusion."""

    def __init__(self, candidates: list[dict]):
        self._candidates = candidates

    def get_embed_models(self) -> set[str]:
        return {"mock-384"}

    def search(self, vector, top_k: int, doc_type=None):
        return self._candidates[:top_k]


def _cand(doc_id: str, context: str) -> dict:
    return {
        "id": doc_id,
        "title": f"Doc {doc_id}",
        "doc_type": "component",
        "url": f"https://example.com/{doc_id}",
        "description": "about",
        "context": context,
        "score": 0.9,
    }


class TestContextPreview:
    def test_preview_truncated_to_300_chars(self):
        long_ctx = "A" * 5000
        cands = [_cand("a", long_ctx), _cand("b", "short")]
        results = query("doc", _FakeIndexer(cands), _FakeEmbedder(), top_k=2)
        by_id = {r["id"]: r for r in results}
        assert by_id["a"]["context_preview"] == "A" * 300
        assert len(by_id["a"]["context_preview"]) == 300
        assert by_id["a"]["has_context"] is True
        assert by_id["b"]["context_preview"] == "short"

    def test_empty_context_flagged(self):
        cands = [_cand("c", "")]
        results = query("doc", _FakeIndexer(cands), _FakeEmbedder(), top_k=1)
        assert results[0]["has_context"] is False
        assert results[0]["context_preview"] == ""

    def test_missing_context_key_tolerated(self):
        cands = [_cand("d", "ctx")]
        del cands[0]["context"]
        results = query("doc", _FakeIndexer(cands), _FakeEmbedder(), top_k=1)
        assert results[0]["has_context"] is False
        assert results[0]["context_preview"] == ""
