"""Regression test for audit P1-4: merge must not silently drop context.

_merge_two() used to merge only title/doc_type/url/description scalar fields
plus links — C1's context/context_hash were absent from the field list, so
merging two DBs that share a doc id replaced a fetched context with "" when
the other side had none. After the fix, context/context_hash follow the same
non-empty-priority / newer-wins rule as the other scalars.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from typing import Any
from pathlib import Path

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb.indexer import QdrantIndexer  # noqa: E402
from scripts.kb.merge import _merge_two, merge  # noqa: E402
from scripts.kb.sidebar_parser import NO_DESCRIPTION, _make_content_hash  # noqa: E402


class MockEmbedder:
    """Deterministic sha1-derived embedder (same pattern as the kb suite)."""

    def __init__(self, dim: int = 384, model_name: str = "mock-384") -> None:
        self.dim = dim
        self.model_name = model_name

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha1(text.encode("utf-8")).digest()
        repeats = (self.dim + len(h) - 1) // len(h)
        full = (h * repeats)[: self.dim]
        return [float(b) / 255.0 for b in full]

    def embed(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


URL = "https://example.com/doc-a"
DOC_ID = hashlib.sha1(URL.encode("utf-8")).hexdigest()
NOW_OLD = "2026-01-01T00:00:00+00:00"
NOW_NEW = "2026-06-01T00:00:00+00:00"


def _doc(context: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": DOC_ID,
        "title": "Doc A — Button",
        "doc_type": "component",
        "url": URL,
        "description": NO_DESCRIPTION,
        "context": context,
        "context_hash": hashlib.sha1(context.encode("utf-8")).hexdigest() if context else "",
        "links": [],
        "created_at": NOW_OLD,
        "updated_at": updated_at,
        "content_hash": _make_content_hash(
            "Doc A — Button", URL, "component", NO_DESCRIPTION, []
        ),
        "embed_model": "mock-384",
    }


class TestMergeKeepsContext(unittest.TestCase):
    """P1-4: context/context_hash survive a same-id merge."""

    def test_nonempty_side_wins_when_other_empty(self) -> None:
        """One side fetched (context set), other legacy (empty) -> keep context.
        Argument order must not matter (B10 symmetry)."""
        a = _doc("fetched button docs…", NOW_NEW)
        b = _doc("", NOW_OLD)

        merged_ab, _ = _merge_two(dict(a), dict(b))
        self.assertEqual(merged_ab["context"], "fetched button docs…")
        self.assertEqual(merged_ab["context_hash"], a["context_hash"])

        merged_ba, _ = _merge_two(dict(b), dict(a))
        self.assertEqual(merged_ba["context"], "fetched button docs…")
        self.assertEqual(merged_ba["context_hash"], a["context_hash"])

    def test_newer_context_wins_when_both_nonempty(self) -> None:
        old = _doc("old content", NOW_OLD)
        new = _doc("new content", NOW_NEW)
        merged, _ = _merge_two(dict(old), dict(new))
        self.assertEqual(merged["context"], "new content")
        self.assertEqual(merged["context_hash"], new["context_hash"])
        # symmetric
        merged2, _ = _merge_two(dict(new), dict(old))
        self.assertEqual(merged2["context"], "new content")

    def test_both_empty_stay_empty(self) -> None:
        a = _doc("", NOW_OLD)
        b = _doc("", NOW_NEW)
        merged, _ = _merge_two(dict(a), dict(b))
        self.assertEqual(merged["context"], "")
        self.assertEqual(merged["context_hash"], "")

    def test_end_to_end_merge_preserves_context_in_db(self) -> None:
        """Full merge() pipeline: side A has context, side B same id without."""
        tmpdir = tempfile.mkdtemp(prefix="element_dev_merge_ctx_")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        db_a = os.path.join(tmpdir, "a.qdrant")
        db_b = os.path.join(tmpdir, "b.qdrant")
        db_out = os.path.join(tmpdir, "out.qdrant")
        emb = MockEmbedder()

        idx_a = QdrantIndexer(db_path=db_a, collection="t", dim=384)
        idx_b = QdrantIndexer(db_path=db_b, collection="t", dim=384)
        try:
            idx_a.build([_doc("context from side A", NOW_NEW)], emb)
            idx_b.build([_doc("", NOW_OLD)], emb)
        finally:
            idx_a.close()
            idx_b.close()

        # merge() renames inputs to .bak.<ts>; merge the copies.
        copy_a, copy_b = db_a + ".copy", db_b + ".copy"
        shutil.copytree(db_a, copy_a)
        shutil.copytree(db_b, copy_b)
        merge(copy_a, copy_b, db_out, "t", dim=384)

        idx_out = QdrantIndexer(db_path=db_out, collection="t", dim=384)
        try:
            docs = idx_out.list_all()
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["context"], "context from side A")
            self.assertEqual(docs[0]["context_hash"],
                             hashlib.sha1(b"context from side A").hexdigest())
        finally:
            idx_out.close()


if __name__ == "__main__":
    unittest.main()
