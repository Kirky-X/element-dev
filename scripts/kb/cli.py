"""Unified CLI entrypoint for the kb submodule (task 4.18 + B7 link-auto/migrate).

Usage:
    python3 -m scripts.kb.cli <action> [options]        # from the skill root
    python3 /path/to/element-dev/scripts/kb/cli.py ...  # from anywhere (dual-mode)

Actions:
    query                --question --top-k --doc-type --rerank
    build                --sidebars-dir
    show                 --id                    (print one doc incl. full context)
    merge                --db-a --db-b --out
    reindex              --force
    update-description   --id --description
    update-links         --id --content
    link-auto            --threshold --max-per-doc     (B2)
    migrate-embed-model  [--model <name>]              (B1 migration)
    fetch-update         --id [--force] [--ttl-days N] (C1)
    config               [--key K --value V]           (view / modify config)

db_path / collection / sidebars_dir / embed_model all come from config.json —
no hard-coded paths (per spec). `--config` overrides the config file location;
otherwise `<skill_root>/config.json` (next to this package) is used — NOT the
current working directory, so the CLI works from any directory. Relative
db_path/sidebars_dir values are anchored to the config file's directory.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# Allow both ``python3 -m scripts.kb.cli`` and direct
# ``python3 <skill_root>/scripts/kb/cli.py`` invocation by ensuring the
# skill root (element-dev) is on sys.path when run as a plain script.
# Fix (audit P1-1): without this, running the file from another directory
# raised ModuleNotFoundError because relative imports have no parent package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.kb.config import (
    DEFAULT_CONFIG,
    default_config_path,
    ensure_config,
    load_config,
    resolve_config_paths,
    save_config,
)
from scripts.kb.embed import Embedder
from scripts.kb.fetch_update import fetch_and_update
from scripts.kb.indexer import QdrantIndexer
from scripts.kb.links import update_links
from scripts.kb.links_auto import auto_link
from scripts.kb.merge import merge as do_merge
from scripts.kb.query import query as do_query
from scripts.kb.reindex import reindex as do_reindex
from scripts.kb.sidebar_parser import parse_all_sidebars
from scripts.kb.update_description import update_description

ACTIONS = ("query", "build", "show", "merge", "reindex", "update-description",
           "update-links", "link-auto", "migrate-embed-model",
           "fetch-update", "config")


# ---- factories (kept module-level so tests can monkeypatch them) -----------

def make_embedder(cfg: dict[str, Any]) -> Embedder:
    return Embedder(
        cfg["embed_model"],
        base_url=cfg.get("embed_base_url", ""),
        api_key=cfg.get("embed_api_key", ""),
        source=cfg.get("embed_source", ""),
    )


def make_indexer(cfg: dict[str, Any]) -> QdrantIndexer:
    return QdrantIndexer(
        db_path=cfg["db_path"],
        collection=cfg["collection"],
        dim=cfg.get("embed_dim", 384),
    )


def _resolve_config_path(config_arg: Optional[str]) -> str:
    """Config file location: --config wins, else the skill root's config.json.

    Fix (audit P1-1): the default no longer depends on os.getcwd() —
    config.json lives next to the scripts/ package, so the CLI works from any
    directory.
    """
    if config_arg:
        return config_arg
    return default_config_path()


def _load_cfg(config_arg: Optional[str]) -> dict[str, Any]:
    path = _resolve_config_path(config_arg)
    if config_arg:
        cfg = load_config(config_arg)
        if cfg is None:
            raise FileNotFoundError(f"config file not found: {config_arg}")
    else:
        cfg = ensure_config()
        if cfg is None:
            cfg = dict(DEFAULT_CONFIG)
    # Relative db_path/sidebars_dir are anchored to the config file's dir so
    # they resolve identically from any cwd (audit P1-1).
    return resolve_config_paths(cfg, Path(path).parent)


# ---- argument parser -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts.kb.cli",
        description="Element Plus local Qdrant knowledge-base tool",
    )
    sub = p.add_subparsers(dest="action", required=True)

    q = sub.add_parser("query", help="hybrid vector+BM25 search")
    q.add_argument("--question", required=True)
    q.add_argument("--top-k", type=int, default=None)
    q.add_argument("--doc-type", default=None)
    q.add_argument("--rerank", action="store_true")
    q.add_argument("--config", default=None)

    b = sub.add_parser("build", help="parse sidebars and build the index")
    b.add_argument("--sidebars-dir", default=None,
                   help="override sidebars_dir from config")
    b.add_argument("--config", default=None)

    sh = sub.add_parser("show",
                        help="print one doc's full payload by id "
                             "(including the complete context field)")
    sh.add_argument("--id", required=True, help="doc sha1 id (from query results)")
    sh.add_argument("--config", default=None)

    m = sub.add_parser("merge", help="merge two DBs into a new one")
    m.add_argument("--db-a", required=True)
    m.add_argument("--db-b", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--config", default=None)

    r = sub.add_parser("reindex", help="recompute embeddings")
    r.add_argument("--force", action="store_true")
    r.add_argument("--config", default=None)

    ud = sub.add_parser("update-description", help="backfill a doc description")
    ud.add_argument("--id", required=True)
    ud.add_argument("--description", required=True)
    ud.add_argument("--config", default=None)

    ul = sub.add_parser("update-links", help="extract & write bidirectional links")
    ul.add_argument("--id", required=True)
    # BUG-5: --content (literal) / --file (path) mutually exclusive —
    # eliminates the old os.path.exists heuristic that mis-read literal
    # content as a file path when it happened to exist on disk.
    g = ul.add_mutually_exclusive_group(required=True)
    g.add_argument("--content", help="literal links content (JSON or markdown)")
    g.add_argument("--file", dest="file_path",
                   help="path to file containing links content")
    ul.add_argument("--config", default=None)

    la = sub.add_parser("link-auto",
                        help="auto-link docs by vector cosine similarity (B2)")
    la.add_argument("--threshold", type=float, default=0.9,
                    help="cosine similarity above which two docs are linked")
    la.add_argument("--max-per-doc", type=int, default=10,
                    help="cap on each doc's link list")
    la.add_argument("--config", default=None)

    me = sub.add_parser("migrate-embed-model",
                        help="backfill embed_model field on legacy docs (B1)")
    me.add_argument("--model", default=None,
                    help="override the model name to stamp "
                         "(defaults to config.json's embed_model)")
    me.add_argument("--config", default=None)

    c = sub.add_parser("config", help="print the effective config, or set one key")
    c.add_argument("--config", default=None)
    c.add_argument("--key", default=None,
                   help="config key to modify (dotted for nested, e.g. "
                        "query.default_top_k); must be used with --value")
    c.add_argument("--value", default=None,
                   help="new value; coerced to the existing value's type "
                        "(JSON for dict/list keys). Use with --key.")

    # C1: fetch + smart update with TTL caching
    fu = sub.add_parser("fetch-update",
                        help="fetch URL, update context/description/vector (C1)")
    fu.add_argument("--id", required=True, help="doc id to update")
    fu.add_argument("--force", action="store_true",
                    help="skip TTL check, always fetch")
    fu.add_argument("--ttl-days", type=int, default=None,
                    help="override context_ttl_days from config")
    fu.add_argument("--config", default=None)
    return p


# ---- action handlers -------------------------------------------------------

def _run_query(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    emb = make_embedder(cfg)
    idx = make_indexer(cfg)
    try:
        top_k = args.top_k if args.top_k is not None else cfg.get("query", {}).get("default_top_k", 5)
        results = do_query(args.question, idx, emb, top_k=top_k,
                           doc_type=args.doc_type, rerank=args.rerank)
    finally:
        idx.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def _run_build(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    sidebars_dir = args.sidebars_dir or cfg.get("sidebars_dir", "sidebars")
    docs = parse_all_sidebars(sidebars_dir)
    emb = make_embedder(cfg)
    idx = make_indexer(cfg)
    try:
        idx.build(docs, emb)
        counts = idx.count_by_type()
    finally:
        idx.close()
    out = {"built": len(docs), "counts": counts}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _run_merge(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    res = do_merge(args.db_a, args.db_b, args.out, cfg["collection"],
                   dim=cfg.get("embed_dim", 384))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["needs_reindex_count"] > 0:
        print(f"NOTE: {res['needs_reindex_count']} docs had description changes — "
              f"run `reindex --force` on {args.out} to refresh their vectors.",
              file=sys.stderr)
    return res


def _run_reindex(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    emb = make_embedder(cfg)
    idx = make_indexer(cfg)
    try:
        n = do_reindex(idx, emb, force=args.force)
    finally:
        idx.close()
    out = {"reindexed": n}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _run_update_description(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    emb = make_embedder(cfg)
    idx = make_indexer(cfg)
    try:
        doc = update_description(args.id, args.description, idx, emb)
    finally:
        idx.close()
    out = {"updated": args.id}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return doc


def _run_update_links(args: argparse.Namespace) -> Any:
    cfg = _load_cfg(args.config)
    idx = make_indexer(cfg)
    try:
        # BUG-5: --content is literal, --file reads from disk. No heuristic.
        if args.file_path:
            with open(args.file_path, encoding="utf-8") as f:
                content = f.read()
        else:
            content = args.content
        linked = update_links(args.id, content, idx)
    finally:
        idx.close()
    out = {"linked": linked}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return linked


# ---- config view/modify + secret masking (audit P1-2, P2-8) ---------------

_SECRET_KEY_RE = re.compile(r"api_key|token", re.IGNORECASE)


def mask_secrets(value: Any) -> Any:
    """Return a copy of `value` with secret-looking fields masked.

    Any dict key matching api_key/token has its string value replaced by
    ``***<last4>`` (audit P2-8: `kb config` used to print embed_api_key in
    plaintext). Empty values stay empty.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, dict):
                out[k] = mask_secrets(v)
            elif isinstance(v, str) and _SECRET_KEY_RE.search(str(k)):
                out[k] = _mask_secret_str(v)
            else:
                out[k] = v
        return out
    return value


def _mask_secret_str(value: str) -> str:
    if not value:
        return ""
    return f"***{value[-4:]}" if len(value) > 4 else "***"


def _coerce_config_value(key: str, current: Any, raw: str) -> Any:
    """Coerce the --value string to the current value's type (Rule: type-safe
    config writes). dict/list keys must be given as JSON."""
    try:
        if isinstance(current, bool):
            low = raw.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
            raise ValueError(f"{key} expects a boolean (true/false), got {raw!r}")
        if isinstance(current, int):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
        if isinstance(current, (dict, list)):
            return json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"config: invalid value for {key!r} "
            f"(expected {type(current).__name__}): {raw!r} ({exc})"
        ) from exc
    return raw


def _set_config_key(cfg: dict[str, Any], key: str, raw_value: str) -> tuple[Any, Any]:
    """Set a (possibly dotted) key in cfg, validating against DEFAULT_CONFIG.

    Returns (old_value, new_value). Raises ValueError for unknown keys or
    type-mismatched values — unknown keys are rejected so typos can't silently
    add dead config entries (Rule 12: fail loud).
    """
    parts = key.split(".")
    template: Any = DEFAULT_CONFIG
    for p in parts:
        if isinstance(template, dict) and p in template:
            template = template[p]
        else:
            raise ValueError(
                f"config: unknown key {key!r} — allowed top-level keys: "
                f"{sorted(DEFAULT_CONFIG)}; nested keys use dots "
                f"(e.g. query.default_top_k)"
            )
    node: dict[str, Any] = cfg
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            node[p] = nxt
        node = nxt
    old = node.get(parts[-1], template)
    new = _coerce_config_value(key, old if old is not None else template, raw_value)
    node[parts[-1]] = new
    return old, new


def _run_config(args: argparse.Namespace) -> Any:
    if (args.key is None) != (args.value is None):
        raise ValueError("config: --key and --value must be used together")

    path = _resolve_config_path(args.config)
    if args.key is None:
        cfg = _load_cfg(args.config)
        masked = mask_secrets(cfg)
        print(json.dumps(masked, ensure_ascii=False, indent=2))
        return masked

    # --key/--value: load the RAW file config (not cwd/path-resolved) so the
    # write-back preserves the user's relative paths (audit P1-2).
    cfg = load_config(path)
    if cfg is None:
        if args.config:
            raise FileNotFoundError(f"config file not found: {args.config}")
        cfg = dict(DEFAULT_CONFIG)
    old, new = _set_config_key(cfg, args.key, args.value)
    # Back up the existing file before writing (config.json.bak, gitignored).
    backup_path: Optional[str] = None
    if Path(path).exists():
        backup_path = path + ".bak"
        shutil.copy2(path, backup_path)
    save_config(cfg, path)
    secret = bool(_SECRET_KEY_RE.search(args.key))
    out = {
        "updated": args.key,
        "old": _mask_secret_str(old) if secret and isinstance(old, str) else old,
        "new": _mask_secret_str(new) if secret and isinstance(new, str) else new,
        "config": path,
        "backup": backup_path,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _run_show(args: argparse.Namespace) -> Any:
    """Audit P1-5: print a single doc's full payload — `query` only returns a
    300-char context preview; the complete context is read back here."""
    cfg = _load_cfg(args.config)
    idx = make_indexer(cfg)
    try:
        doc = idx.get(args.id)
    finally:
        idx.close()
    if doc is None:
        print(json.dumps({"error": f"doc not found: {args.id}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
    # The 384-dim vector is storage detail, not documentation content.
    doc.pop("embedding", None)
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def _run_fetch_update(args: argparse.Namespace) -> Any:
    """C1: fetch URL → update context/description/vector with TTL caching."""
    cfg = _load_cfg(args.config)
    ttl_days = args.ttl_days if args.ttl_days is not None else cfg.get("context_ttl_days", 30)
    emb = make_embedder(cfg)
    idx = make_indexer(cfg)
    # Lazy-import fetcher to avoid httpx dependency at module load time
    from scripts.fetcher.fetch import fetch as do_fetch
    class _FetcherAdapter:
        """Adapts fetch() function to the fetcher.fetch(url) interface."""
        def fetch(self, url: str) -> dict:
            return do_fetch(url)
    try:
        result = fetch_and_update(
            args.id, idx, emb, _FetcherAdapter(),
            ttl_days=ttl_days, force=args.force,
        )
    finally:
        idx.close()
    out = {
        "action": result["action"],
        "reason": result["reason"],
        "doc_id": args.id,
        "title": result["doc"].get("title", ""),
        "url": result["doc"].get("url", ""),
        "description": result["doc"].get("description", ""),
        "has_context": bool(result["doc"].get("context")),
        "context_length": len(result["doc"].get("context", "")),
        "context_hash": result["doc"].get("context_hash", "")[:16],
        "updated_at": result["doc"].get("updated_at", ""),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _run_link_auto(args: argparse.Namespace) -> Any:
    """B2: auto-link docs by vector cosine similarity > threshold."""
    cfg = _load_cfg(args.config)
    idx = make_indexer(cfg)
    try:
        stats = auto_link(
            idx,
            threshold=args.threshold,
            max_per_doc=args.max_per_doc,
        )
    finally:
        idx.close()
    out = {
        "pairs_linked": stats["pairs_linked"],
        "docs_scanned": stats["docs_scanned"],
        "threshold": args.threshold,
        "max_per_doc": args.max_per_doc,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _run_migrate_embed_model(args: argparse.Namespace) -> Any:
    """B1 migration: stamp embed_model onto legacy docs that lack it.

    Reads the DB's current embed_model set:
      - if all docs have the same non-empty value → no-op (already migrated)
      - if all docs have empty embed_model (legacy) → stamp config.embed_model
      - if mixed → refuse (Run handler's stderr message explains recovery)
    """
    cfg = _load_cfg(args.config)
    target_model = args.model or cfg.get("embed_model", "")
    if not target_model:
        raise ValueError(
            "migrate-embed-model: no model to stamp — pass --model or set "
            "embed_model in config.json"
        )
    idx = make_indexer(cfg)
    try:
        docs = idx.list_all()
        if not docs:
            out = {"migrated": 0, "skipped": 0, "model": target_model, "note": "empty DB"}
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return out
        models = {d.get("embed_model", "") for d in docs}
        real_models = {m for m in models if m}
        if len(real_models) > 1:
            raise RuntimeError(
                f"migrate-embed-model: DB already contains mixed embed_models "
                f"{real_models!r} — contaminated, refusing to migrate. "
                f"Rebuild from sidebars: `python3 scripts/kb/build_db.py`."
            )
        if len(real_models) == 1 and next(iter(real_models)) != target_model:
            existing = next(iter(real_models))
            raise RuntimeError(
                f"migrate-embed-model: DB already stamped with {existing!r} "
                f"but config says {target_model!r}. Either revert config.json "
                f"to {existing!r}, or run `python3 scripts/kb/build_db.py` to "
                f"rebuild with {target_model!r}."
            )
        if len(real_models) == 1:
            # All docs already stamped with target_model — nothing to do
            out = {
                "migrated": 0, "skipped": len(docs),
                "model": target_model,
                "note": "all docs already have embed_model",
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return out
        # All docs have empty embed_model (legacy) — stamp target_model
        migrated = 0
        for d in docs:
            idx.set_payload(d["id"], {"embed_model": target_model})
            migrated += 1
        out = {"migrated": migrated, "skipped": 0, "model": target_model}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out
    finally:
        idx.close()


_DISPATCH = {
    "query": _run_query,
    "build": _run_build,
    "show": _run_show,
    "merge": _run_merge,
    "reindex": _run_reindex,
    "update-description": _run_update_description,
    "update-links": _run_update_links,
    "link-auto": _run_link_auto,
    "migrate-embed-model": _run_migrate_embed_model,
    "fetch-update": _run_fetch_update,
    "config": _run_config,
}


# ---- dependency failure guidance (audit P1-3) ------------------------------

# module (as named in the ImportError) -> pip distribution name
_PIP_NAMES = {
    "sentence_transformers": "sentence-transformers",
    "rank_bm25": "rank-bm25",
    "qdrant_client": "qdrant-client",
    "flashrank": "flashrank",
    "modelscope": "modelscope",
    "httpx": "httpx",
    "openai": "openai",
}
_MODULE_IMPORT_RE = re.compile(r"No module named '(?P<mod>[A-Za-z0-9_.]+)'")


def dependency_hint(exc: ImportError) -> str:
    """Human-readable recovery message for a missing optional/heavy dependency.

    Audit P1-3: a missing sentence-transformers/torch (or rank-bm25 etc.) used
    to surface as a raw ModuleNotFoundError traceback with no guidance.
    """
    mod = ""
    m = _MODULE_IMPORT_RE.search(str(exc))
    if m:
        mod = m.group("mod").split(".")[0]
    pkg = _PIP_NAMES.get(mod, mod or "<package>")
    lines = [
        f"[deps] missing Python dependency: {mod or exc.name or '<unknown>'}",
        f"  fix:   pip install {pkg}",
        f"  (all deps at once: pip install -r requirements.txt)",
        "  alternative: switch to a cloud embedding model — set config.json",
        "  embed_model to \"openai://<model>\" plus embed_base_url/embed_api_key,",
        "  then no local sentence-transformers/torch is needed.",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> Any:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH[args.action]
    try:
        return handler(args)
    except ImportError as exc:
        # Audit P1-3: heavy deps (sentence-transformers+torch, flashrank, …)
        # are imported lazily inside the handlers; a missing one prints a
        # recovery hint on stderr and exits non-zero instead of a bare traceback.
        print(dependency_hint(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
