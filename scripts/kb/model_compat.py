"""Shared embed_model compatibility guard (BUG-1, BUG-2).

`assert_compatible` is the single source of truth for "this embedder may
write to / read from this DB". All vector-touching entry points (query,
update_description, fetch_update) call it BEFORE any vector operation so
that cross-model writes are refused at the boundary, not after the damage.

Behaviour:
  - legacy DB (all embed_model = "") -> allow (migrate-embed-model backfills)
  - embedder.model_name not in DB's non-empty embed_model set -> refuse
  - DB with multiple non-empty embed_models -> refuse (already polluted)

The `context` arg is purely informational — it tags the error message so
the caller knows which entry point refused the operation.
"""
from __future__ import annotations

from typing import Any

_LEGACY_FIX_HINT = (
    "Either revert config.json to the DB's model, or run "
    "`python3 scripts/kb/build_db.py` to rebuild with the new model."
)


def assert_compatible(indexer: Any, embedder: Any, context: str = "") -> None:
    """Raise ValueError if `embedder.model_name` is incompatible with the DB.

    Args:
        indexer: object exposing `get_embed_models() -> set[str]`.
        embedder: object exposing `.model_name` attribute.
        context: short label of the calling entry point (e.g. "query",
            "update_description", "fetch_update"). Surfaces in the error
            message so the caller can tell which guard fired.

    Raises:
        ValueError: if the embedder's model differs from the DB's stored
            embed_model set, OR if the DB has been polluted (multiple
            distinct non-empty embed_models).
    """
    cur = getattr(embedder, "model_name", "")
    models = indexer.get_embed_models()
    real = {m for m in models if m}  # filter legacy "" values
    if not real:
        # legacy DB — allow; migrate-embed-model script will backfill later
        return
    if cur not in real:
        raise ValueError(
            f"{context}: embed_model mismatch — embedder is {cur!r}, DB was "
            f"built with {real!r}. Cosine similarity across models is "
            f"meaningless. {_LEGACY_FIX_HINT}"
        )
    # BUG-2: a DB with multiple distinct non-empty embed_models is already
    # contaminated — even if the current embedder is one of them, the existing
    # vectors cannot be compared across models. Refuse loud so the operator
    # knows to rebuild (migrate-embed-model --rebuild) before any query/write.
    if len(real) > 1:
        raise ValueError(
            f"{context}: DB already polluted — found {len(real)} distinct "
            f"embed_models ({sorted(real)!r}). Cosine similarity across "
            f"models is meaningless. Run "
            f"`python3 scripts/kb/migrate-embed-model.py --rebuild` to fix."
        )
