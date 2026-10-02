"""Regression tests for the --rerank path (query._rerank).

2026-10-02 fix: _rerank used to import a nonexistent `flashrank.RankModel`
(real API: `Ranker` + `RerankRequest`) and swallowed the ImportError, so
--rerank silently degraded to the fused order forever. These tests pin:

1. the call contract — Ranker().rerank(RerankRequest(query, passages))
2. result mapping — reranked ids are mapped back to the original docs,
   dropped docs re-appended in input order
3. no swallowing — a missing flashrank install raises ImportError (which
   cli.main turns into the pip-install hint + exit 2), per SKILL.md
   Prohibitions 3 (no swallowing errors).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.kb import query as query_mod  # noqa: E402

_DOCS = [
    {"id": "a", "title": "ElTable virtual scroll", "description": "table lazy load"},
    {"id": "b", "title": "ElButton type", "description": "button primary plain"},
    {"id": "c", "title": "ElForm validation", "description": "form rules validator"},
]


class _FakeRerankRequest:
    def __init__(self, query=None, passages=None):
        self.query = query
        self.passages = passages


class _FakeRanker:
    last_request = None

    def rerank(self, request):
        _FakeRanker.last_request = request
        # reverse to prove reordering is reflected in the output
        return list(reversed(request.passages))


@pytest.fixture()
def fake_flashrank(monkeypatch):
    _FakeRanker.last_request = None
    monkeypatch.setitem(
        sys.modules, "flashrank",
        types.SimpleNamespace(Ranker=_FakeRanker, RerankRequest=_FakeRerankRequest),
    )
    return _FakeRanker


def test_rerank_call_contract(fake_flashrank):
    """_rerank must call Ranker().rerank(RerankRequest(query, passages))."""
    out = query_mod._rerank("virtual scrolling", [dict(d) for d in _DOCS])
    req = fake_flashrank.last_request
    assert req is not None, "rerank() was never called"
    assert req.query == "virtual scrolling"
    assert [p["id"] for p in req.passages] == ["a", "b", "c"]
    assert [r["id"] for r in out] == ["c", "b", "a"]


def test_rerank_maps_reranked_ids_back_to_docs(fake_flashrank):
    """Returned items must be the original doc dicts, ordered by the reranker."""
    docs = [dict(d) for d in _DOCS]
    out = query_mod._rerank("q", docs)
    by_id = {d["id"]: d for d in docs}
    assert all(out[i] is by_id[doc_id] for i, doc_id in enumerate(["c", "b", "a"]))


def test_rerank_reappends_docs_dropped_by_reranker(fake_flashrank, monkeypatch):
    """If the reranker drops a passage, the doc is re-appended, not lost."""

    class _DroppingRanker(_FakeRanker):
        def rerank(self, request):
            return request.passages[:1]  # keep only the first passage

    monkeypatch.setitem(
        sys.modules, "flashrank",
        types.SimpleNamespace(Ranker=_DroppingRanker, RerankRequest=_FakeRerankRequest),
    )
    out = query_mod._rerank("q", [dict(d) for d in _DOCS])
    assert [r["id"] for r in out] == ["a", "b", "c"]


def test_rerank_empty_results_is_noop(fake_flashrank):
    assert query_mod._rerank("q", []) == []


def test_rerank_missing_flashrank_raises_not_swallows(monkeypatch):
    """A missing flashrank install must raise ImportError (--rerank was an
    explicit request) instead of silently returning the fused order."""
    monkeypatch.setitem(sys.modules, "flashrank", None)  # import → ImportError
    with pytest.raises(ImportError):
        query_mod._rerank("q", [dict(d) for d in _DOCS])
