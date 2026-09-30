"""Tests for _add_link_bidirectional half-pair completion (BUG-4).

Validates that _add_link_bidirectional can complete a missing direction
on an existing half-pair, provided the receiving side has capacity.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb.links_auto import _add_link_bidirectional  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc(doc_id: str, links: list[str]) -> dict:
    """Minimal doc dict — _add_link_bidirectional only touches `links`."""
    return {
        "id": doc_id,
        "title": f"Doc {doc_id}",
        "doc_type": "component",
        "url": f"https://example.com/{doc_id}",
        "description": "desc",
        "context": "",
        "links": list(links),
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "content_hash": "hash",
        "context_hash": "",
        "embed_model": "m1",
    }


def _other_ids(n: int, exclude: str = "") -> list[str]:
    """Return n distinct ids not equal to `exclude`."""
    return [f"other-{i}" for i in range(n) if f"other-{i}" != exclude][:n]


# ---------------------------------------------------------------------------
# BUG-4: half-pair completion + capacity rules
# ---------------------------------------------------------------------------

class TestAddLinkBidirectionalHalfPair:
    """_add_link_bidirectional must complete a missing direction on an
    existing half-pair when the receiving side has capacity."""

    def test_completes_missing_b_to_a_when_b_has_capacity(self) -> None:
        """A→B already exists, B→A missing, B has capacity → append A to
        B's links, leave A's links untouched, return True."""
        a_doc = _doc("a", ["b"])
        b_doc = _doc("b", [])

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is True, (
            f"expected True for half-pair completion, got {result!r}"
        )
        assert b_doc["links"] == ["a"], (
            f"expected b.links == ['a'], got {b_doc['links']!r}"
        )
        assert a_doc["links"] == ["b"], (
            f"expected a.links unchanged == ['b'], got {a_doc['links']!r}"
        )

    def test_completes_missing_a_to_b_when_a_has_capacity(self) -> None:
        """Symmetric: B→A already exists, A→B missing, A has capacity."""
        a_doc = _doc("a", [])
        b_doc = _doc("b", ["a"])

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is True
        assert a_doc["links"] == ["b"], (
            f"expected a.links == ['b'], got {a_doc['links']!r}"
        )
        assert b_doc["links"] == ["a"], (
            f"expected b.links unchanged == ['a'], got {b_doc['links']!r}"
        )

    def test_rejects_completion_when_target_side_full(self) -> None:
        """A→B already exists, B→A missing, but B's links already at
        max_per_doc (and doesn't contain A) → refuse, return False, B's
        links unchanged."""
        a_doc = _doc("a", ["b"])
        b_doc = _doc("b", _other_ids(10, exclude="a"))

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is False, (
            f"expected False when target side full, got {result!r}"
        )
        assert len(b_doc["links"]) == 10, (
            f"expected b.links length unchanged at 10, got {len(b_doc['links'])}"
        )
        assert a_doc["links"] == ["b"]

    def test_full_new_pair_rejected_when_either_side_full(self) -> None:
        """Brand-new pair where either side is already full → refuse."""
        a_doc = _doc("a", _other_ids(10, exclude="b"))
        b_doc = _doc("b", [])

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is False, (
            f"expected False when source side full on new pair, got {result!r}"
        )
        # Both sides unchanged.
        assert len(a_doc["links"]) == 10
        assert b_doc["links"] == []

    def test_already_complete_pair_returns_false(self) -> None:
        """Both directions already present → idempotent False."""
        a_doc = _doc("a", ["b"])
        b_doc = _doc("b", ["a"])

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is False, (
            f"expected False for already-complete pair, got {result!r}"
        )
        assert a_doc["links"] == ["b"]
        assert b_doc["links"] == ["a"]

    def test_completes_when_source_side_full_but_not_adding_to_source(self) -> None:
        """BUG-4 core: A→B already exists, A's links are FULL (10), B→A
        missing, B has capacity. The old code refused because it checked
        A's capacity unconditionally — but A isn't receiving a new link.
        The guard must only check the side that will actually grow.

        This is the scenario design.md BUG-4 targets: half-pair where the
        already-linked side is full but the missing-direction side is not.
        """
        a_doc = _doc("a", ["b"] + _other_ids(9, exclude="b"))  # 10 links, full
        b_doc = _doc("b", [])  # empty, has capacity

        result = _add_link_bidirectional(
            indexer=None, a_id="a", b_id="b",
            a_doc=a_doc, b_doc=b_doc, max_per_doc=10, now="t",
        )

        assert result is True, (
            f"expected True for half-pair completion when source side full "
            f"but not adding to source, got {result!r}"
        )
        assert b_doc["links"] == ["a"], (
            f"expected b.links == ['a'], got {b_doc['links']!r}"
        )
        assert len(a_doc["links"]) == 10, (
            f"expected a.links length unchanged at 10, got {len(a_doc['links'])}"
        )
        assert "b" in a_doc["links"], "a→b link must still be present"
