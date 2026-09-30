"""Tests for embed_model compatibility checks (BUG-1, BUG-2).

Validates that all write-vector paths (query / update_description /
fetch_update) call `assert_compatible` to refuse cross-model writes,
and that a legacy DB (all embed_model = "") is allowed through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb.fetch_update import fetch_and_update  # noqa: E402
from scripts.kb.update_description import update_description  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes — minimal, deterministic, no Qdrant dependency.
# ---------------------------------------------------------------------------

class _FakeIndexer:
    """Minimal fake indexer returning a configured embed_model set.

    Only the methods exercised by update_description / fetch_update are
    implemented; everything else raises to fail loud on test setup drift.
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


class _FakeEmbedder:
    """Fake embedder whose model_name is configurable."""

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
    """A doc dict with all fields update_description / fetch_update touch."""
    return {
        "id": "abc123",
        "title": "Doc Title",
        "doc_type": "component",
        "url": "https://example.com/doc",
        "description": "No description",
        "context": "",
        "links": [],
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "content_hash": "deadbeef",
        "context_hash": "",
        "embed_model": "text-embedding-3-small",
    }


# ---------------------------------------------------------------------------
# BUG-1: write-vector paths must reject mismatched embedder
# ---------------------------------------------------------------------------

class TestUpdateDescriptionModelCheck:
    """update_description must call assert_compatible before indexer.upsert."""

    def test_raises_when_embedder_model_differs_from_db(self) -> None:
        doc = _base_doc()
        indexer = _FakeIndexer({"text-embedding-3-small"}, doc=doc)
        embedder = _FakeEmbedder(model_name="bge-large-zh")

        with pytest.raises(ValueError) as ctx:
            update_description(doc["id"], "real description", indexer, embedder)

        msg = str(ctx.value)
        assert "update_description" in msg, (
            f"expected context='update_description' in message, got: {msg!r}"
        )
        assert "embed_model mismatch" in msg, (
            f"expected 'embed_model mismatch' in message, got: {msg!r}"
        )
        # guard must fire BEFORE the write — no upsert should happen
        assert indexer.upsert_calls == [], (
            "assert_compatible must run before indexer.upsert; "
            f"got {len(indexer.upsert_calls)} upsert call(s)"
        )


class TestFetchUpdateModelCheck:
    """fetch_and_update must call assert_compatible before indexer.upsert."""

    def test_raises_when_embedder_model_differs_from_db(self) -> None:
        doc = _base_doc()
        doc["context"] = "old context"
        indexer = _FakeIndexer({"text-embedding-3-small"}, doc=doc)
        embedder = _FakeEmbedder(model_name="bge-large-zh")
        fetcher = _FakeFetcher("fresh content")

        with pytest.raises(ValueError) as ctx:
            fetch_and_update(doc["id"], indexer, embedder, fetcher, force=True)

        msg = str(ctx.value)
        assert "fetch_update" in msg, (
            f"expected context='fetch_update' in message, got: {msg!r}"
        )
        # guard must fire BEFORE the write
        assert indexer.upsert_calls == [], (
            "assert_compatible must run before indexer.upsert; "
            f"got {len(indexer.upsert_calls)} upsert call(s)"
        )

    def test_legacy_db_allows_through(self) -> None:
        """Legacy DB (all embed_model = "") is allowed — migrate script will
        backfill later. Neither update_description nor fetch_update should
        raise purely on model grounds."""
        # update_description path
        doc1 = _base_doc()
        idx1 = _FakeIndexer({""}, doc=doc1)
        emb1 = _FakeEmbedder(model_name="any-model")
        update_description(doc1["id"], "real description", idx1, emb1)
        assert idx1.upsert_calls, "upsert should fire for legacy DB"

        # fetch_update path
        doc2 = _base_doc()
        doc2["context"] = "old context"
        idx2 = _FakeIndexer({""}, doc=doc2)
        emb2 = _FakeEmbedder(model_name="any-model")
        fetcher = _FakeFetcher("fresh content")
        result = fetch_and_update(doc2["id"], idx2, emb2, fetcher, force=True)
        assert result["action"] == "updated"


# ---------------------------------------------------------------------------
# BUG-2: detect DB already polluted by mixed embed_models
# ---------------------------------------------------------------------------

class TestMixedDBDetection:
    """`assert_compatible` must refuse a DB with >1 distinct non-empty
    embed_model — even when the current embedder's model is one of them."""

    def test_raises_when_db_has_multiple_non_empty_models(self) -> None:
        from scripts.kb.model_compat import assert_compatible

        indexer = _FakeIndexer({"text-embedding-3-small", "bge-large-zh"})
        embedder = _FakeEmbedder(model_name="text-embedding-3-small")

        with pytest.raises(ValueError) as ctx:
            assert_compatible(indexer, embedder, context="query")

        msg = str(ctx.value)
        assert "DB already polluted" in msg, (
            f"expected 'DB already polluted' in message, got: {msg!r}"
        )
        assert "2 distinct embed_models" in msg, (
            f"expected count in message, got: {msg!r}"
        )

    def test_legacy_db_with_empty_and_one_real_still_passes(self) -> None:
        """A DB that mixes legacy "" with one real model is NOT polluted —
        the "" entries are pre-migration stragglers, not cross-model writes."""
        from scripts.kb.model_compat import assert_compatible

        indexer = _FakeIndexer({"", "text-embedding-3-small"})
        embedder = _FakeEmbedder(model_name="text-embedding-3-small")
        # Should NOT raise
        assert_compatible(indexer, embedder, context="query")


# ---------------------------------------------------------------------------
# BUG-2 (Verify): three entry points must all refuse a polluted DB
# ---------------------------------------------------------------------------

class TestMixedDBIntegration:
    """All three write/read entry points must surface the pollution error."""

    def _polluted_indexer(self) -> _FakeIndexer:
        doc = _base_doc()
        doc["context"] = "old context"
        return _FakeIndexer(
            {"text-embedding-3-small", "bge-large-zh"}, doc=doc,
        )

    def test_query_rejects_polluted_db(self) -> None:
        from scripts.kb.query import _check_model_compatibility

        idx = self._polluted_indexer()
        emb = _FakeEmbedder(model_name="text-embedding-3-small")
        with pytest.raises(ValueError) as ctx:
            _check_model_compatibility(idx, emb)
        assert "DB already polluted" in str(ctx.value)

    def test_update_description_rejects_polluted_db(self) -> None:
        idx = self._polluted_indexer()
        emb = _FakeEmbedder(model_name="text-embedding-3-small")
        with pytest.raises(ValueError) as ctx:
            update_description(idx.get("abc123")["id"], "real description", idx, emb)
        assert "DB already polluted" in str(ctx.value)

    def test_fetch_update_rejects_polluted_db(self) -> None:
        idx = self._polluted_indexer()
        emb = _FakeEmbedder(model_name="text-embedding-3-small")
        fetcher = _FakeFetcher("fresh content")
        with pytest.raises(ValueError) as ctx:
            fetch_and_update("abc123", idx, emb, fetcher, force=True)
        assert "DB already polluted" in str(ctx.value)
