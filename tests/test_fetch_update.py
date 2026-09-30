"""Tests for fetch_and_update empty-content guard (BUG-3).

Validates that fetch_and_update refuses to overwrite existing
context/description/vector when the fetcher returns empty or
whitespace-only content — protecting existing assets from silent
destruction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb.fetch_update import fetch_and_update  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes — minimal, deterministic, no Qdrant dependency.
# ---------------------------------------------------------------------------

class _FakeIndexer:
    """Minimal fake indexer returning a single configured doc.

    Tracks upsert / set_payload calls so tests can assert the empty-content
    guard fires BEFORE any write.
    """

    def __init__(self, embed_models: set[str], doc: dict | None = None) -> None:
        self._models = set(embed_models)
        self._doc = dict(doc) if doc else None
        self.upsert_calls: list[tuple[dict, object]] = []
        self.set_payload_calls: list[tuple[str, dict]] = []

    def get_embed_models(self) -> set[str]:
        return set(self._models)

    def get(self, doc_id: str) -> dict | None:
        if self._doc is None:
            return None
        return dict(self._doc)

    def upsert(self, doc: dict, embedder: object) -> None:
        self.upsert_calls.append((doc, embedder))

    def set_payload(self, doc_id: str, fields: dict) -> None:
        self.set_payload_calls.append((doc_id, fields))


class _StrictFakeEmbedder:
    """Embedder that FAILS if embed() is called.

    Used by empty-content tests: the guard must return BEFORE any vector
    operation, so embed() being invoked proves the guard didn't fire.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        pytest.fail(
            "embed must not be called for empty-content fetch — "
            "the guard should return before any vector operation"
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        pytest.fail("embed_batch must not be called for empty-content fetch")


class _FakeEmbedder:
    """Plain embedder for the non-empty content path."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        return [0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class _FakeFetcher:
    def __init__(self, content: str) -> None:
        self._content = content

    def fetch(self, url: str) -> dict:
        return {"content": self._content, "url": url}


def _base_doc() -> dict:
    """A doc with precious existing context/description that must survive
    an empty-content fetch."""
    return {
        "id": "abc123",
        "title": "Doc Title",
        "doc_type": "component",
        "url": "https://example.com/doc",
        "description": "old desc",
        "context": "precious old context",
        "links": [],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "content_hash": "deadbeef",
        "context_hash": "abc123",
        "embed_model": "m1",
    }


# ---------------------------------------------------------------------------
# BUG-3: empty-content guard
# ---------------------------------------------------------------------------

class TestEmptyContentGuard:
    """fetch_and_update must refuse empty/whitespace-only content and
    preserve existing context/description/vector."""

    def test_empty_content_returns_error_and_preserves_existing_data(self) -> None:
        doc = _base_doc()
        indexer = _FakeIndexer({"m1"}, doc=doc)
        embedder = _StrictFakeEmbedder(model_name="m1")
        fetcher = _FakeFetcher(content="")

        result = fetch_and_update(doc["id"], indexer, embedder, fetcher, force=True)

        assert result["action"] == "error", (
            f"expected action='error' for empty content, got {result['action']!r}"
        )
        assert "empty content" in result["reason"], (
            f"expected 'empty content' in reason, got: {result['reason']!r}"
        )
        # Existing assets must be untouched.
        assert result["doc"]["context"] == "precious old context", (
            "existing context must be preserved on empty-content fetch"
        )
        assert result["doc"]["description"] == "old desc", (
            "existing description must be preserved on empty-content fetch"
        )
        # No write operations may fire.
        assert indexer.upsert_calls == [], (
            "indexer.upsert must not be called for empty-content fetch"
        )
        assert indexer.set_payload_calls == [], (
            "indexer.set_payload must not be called for empty-content fetch"
        )

    def test_whitespace_only_content_returns_error(self) -> None:
        doc = _base_doc()
        indexer = _FakeIndexer({"m1"}, doc=doc)
        embedder = _StrictFakeEmbedder(model_name="m1")
        fetcher = _FakeFetcher(content="   \n\t  ")

        result = fetch_and_update(doc["id"], indexer, embedder, fetcher, force=True)

        assert result["action"] == "error"
        assert "empty content" in result["reason"]
        assert result["doc"]["context"] == "precious old context"
        assert result["doc"]["description"] == "old desc"
        assert indexer.upsert_calls == []
        assert indexer.set_payload_calls == []

    def test_non_empty_content_proceeds_normally(self) -> None:
        """Guard must not fire on real content — existing update path
        preserved."""
        doc = _base_doc()
        indexer = _FakeIndexer({"m1"}, doc=doc)
        embedder = _FakeEmbedder(model_name="m1")
        fetcher = _FakeFetcher(content="real new content")

        result = fetch_and_update(doc["id"], indexer, embedder, fetcher, force=True)

        assert result["action"] == "updated", (
            f"expected action='updated' for non-empty content, "
            f"got {result['action']!r}"
        )
