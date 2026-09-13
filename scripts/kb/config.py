"""Configuration helpers for the kb submodule (task 4.1).

Loads/saves the repo config.json and provides a DEFAULT_CONFIG constant.
No hard-coded paths: db_path/collection come from config.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

CONFIG_FILENAME = "config.json"

# Fix (audit P1-1): config.json used to be resolved against os.getcwd(), which
# broke every invocation from outside the skill directory (e.g. a user's Vue
# project). The skill root is now derived from config.py's own location:
#   scripts/kb/config.py -> parents[2] == element-dev/ (skill root)
SKILL_ROOT = Path(__file__).resolve().parents[2]

# Config keys whose values may be relative paths. They are resolved against
# the directory containing the config file (skill root by default), so
# relative db_path/sidebars_dir in config.json keep working from any cwd.
_PATH_KEYS = ("db_path", "sidebars_dir")


def default_config_path() -> str:
    """Absolute path of the skill's bundled config.json (config.py's skill root)."""
    return str(SKILL_ROOT / CONFIG_FILENAME)

DEFAULT_CONFIG: dict[str, Any] = {
    "embed_model": "sentence-transformers/paraphrase-MiniLM-L3-v2",
    "embed_dim": 384,
    "embed_source": "modelscope",
    "embed_base_url": "",
    "embed_api_key": "",
    "rerank_model": "flashrank",
    "rerank_source": "local",
    "rerank_base_url": "",
    "rerank_api_key": "",
    "db_path": "data/element-plus.qdrant",
    "collection": "element_plus_docs",
    "sidebars_dir": "sidebars",
    "site_base": "https://element-plus.org",
    "context_ttl_days": 30,
    "endpoints": {},
    "query": {
        "default_top_k": 5,
        "bm25_weight": 0.3,
        "vector_weight": 0.7,
    },
}


def load_config(path: str) -> Optional[dict[str, Any]]:
    """Load a JSON config file.

    Returns None if the file does not exist. Raises ValueError on malformed JSON
    so callers cannot silently swallow corruption (Rule 12: fail loud).
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config {path}: {e}") from e


def save_config(cfg: dict[str, Any], path: str) -> None:
    """Write cfg to path as UTF-8 JSON (with a trailing newline, matching the
    committed config.json format)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def resolve_config_paths(cfg: dict[str, Any], base_dir: str | Path) -> dict[str, Any]:
    """Anchor relative path keys (db_path, sidebars_dir) to ``base_dir``.

    Config.json stores relative paths (e.g. ``data/element-plus.qdrant``) so
    the skill stays relocatable. They are meaningless against an arbitrary
    cwd, so after loading we resolve them against the directory that contains
    the config file. Absolute values are left untouched. Mutates and returns
    ``cfg``.
    """
    base = Path(base_dir)
    for key in _PATH_KEYS:
        val = cfg.get(key)
        if isinstance(val, str) and val and not os.path.isabs(val):
            cfg[key] = str((base / val).resolve())
    return cfg


def ensure_config(config_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Load config.json from the skill root (or explicit override).

    Fix (audit P1-1): the default lookup is now ``<skill_root>/config.json``
    (derived from config.py's own path) instead of ``os.getcwd()``. ``--config``
    still overrides the file location.

    Returns None when the file is missing — caller decides whether to fall back
    to DEFAULT_CONFIG or report an error. Relative db_path/sidebars_dir values
    are resolved against the config file's directory.
    """
    if config_path is None:
        config_path = default_config_path()
    cfg = load_config(config_path)
    if cfg is not None:
        resolve_config_paths(cfg, Path(config_path).parent)
    return cfg
