"""Tests for audit fixes P1-1 / P1-2 / P1-3 / P1-5 / P2-8 in cli.py.

Covers:
  * P1-1: config.json is located relative to the skill root, not the cwd;
    relative db_path/sidebars_dir are anchored to the config file's directory.
  * P1-2: `kb config --key K --value V` writes config.json (with a .bak
    backup), validates keys and coerces value types.
  * P1-3: ImportError inside a handler prints a recovery hint and exits 2.
  * P1-5: `kb show --id` prints the full doc (including context).
  * P2-8: api keys are masked in config output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb import cli  # noqa: E402
from scripts.kb.config import SKILL_ROOT  # noqa: E402


# ---------------------------------------------------------------------------
# P1-1: cwd-independent config resolution
# ---------------------------------------------------------------------------

class TestConfigPathResolution:
    def test_default_config_resolves_against_skill_root(self, tmp_path, monkeypatch):
        """From an unrelated cwd, _load_cfg still finds the skill's config and
        anchors relative db_path/sidebars_dir to the skill root."""
        monkeypatch.chdir(tmp_path)  # simulate running inside a user's project
        cfg = cli._load_cfg(None)
        assert cfg["db_path"] == str((SKILL_ROOT / "data/element-plus.qdrant").resolve())
        assert cfg["sidebars_dir"] == str((SKILL_ROOT / "sidebars").resolve())

    def test_explicit_config_anchors_to_its_own_dir(self, tmp_path):
        sub = tmp_path / "elsewhere"
        sub.mkdir()
        cfg_file = sub / "config.json"
        cfg_file.write_text(json.dumps({
            "db_path": "mydata/db.qdrant",
            "sidebars_dir": "mysidebars",
        }), encoding="utf-8")
        cfg = cli._load_cfg(str(cfg_file))
        assert cfg["db_path"] == str((sub / "mydata/db.qdrant").resolve())
        assert cfg["sidebars_dir"] == str((sub / "mysidebars").resolve())

    def test_help_works_via_main_module_path(self):
        """Dual-mode import: loading cli.py as a plain script file must not
        raise ModuleNotFoundError (the sys.path shim runs at import)."""
        import subprocess
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts/kb/cli.py"), "--help"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        assert "query" in r.stdout


# ---------------------------------------------------------------------------
# P1-2: kb config --key/--value
# ---------------------------------------------------------------------------

class TestConfigSet:
    def _write_cfg(self, tmp_path: Path) -> Path:
        p = tmp_path / "config.json"
        p.write_text(json.dumps({
            "embed_model": "sentence-transformers/paraphrase-MiniLM-L3-v2",
            "embed_api_key": "",
            "context_ttl_days": 30,
            "query": {"default_top_k": 5},
        }), encoding="utf-8")
        return p

    def test_set_and_read_back_string_key(self, tmp_path):
        cfg_file = self._write_cfg(tmp_path)
        cli.main(["config", "--key", "embed_model",
                  "--value", "sentence-transformers/all-MiniLM-L6-v2",
                  "--config", str(cfg_file)])
        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert loaded["embed_model"] == "sentence-transformers/all-MiniLM-L6-v2"
        # backup written before overwrite
        assert (tmp_path / "config.json.bak").exists()

    def test_set_nested_key_with_type_coercion(self, tmp_path):
        cfg_file = self._write_cfg(tmp_path)
        cli.main(["config", "--key", "query.default_top_k", "--value", "8",
                  "--config", str(cfg_file)])
        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert loaded["query"]["default_top_k"] == 8  # int, not "8"
        # sibling nested keys survive the write-back
        assert loaded["query"] == {"default_top_k": 8}

    def test_unknown_key_rejected(self, tmp_path):
        cfg_file = self._write_cfg(tmp_path)
        with pytest.raises(ValueError, match="unknown key"):
            cli.main(["config", "--key", "no_such_key", "--value", "1",
                      "--config", str(cfg_file)])
        # file untouched
        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert loaded["context_ttl_days"] == 30

    def test_type_mismatch_rejected(self, tmp_path):
        cfg_file = self._write_cfg(tmp_path)
        with pytest.raises(ValueError, match="invalid value"):
            cli.main(["config", "--key", "context_ttl_days", "--value", "abc",
                      "--config", str(cfg_file)])

    def test_set_api_key_masked_in_output(self, tmp_path, capsys):
        cfg_file = self._write_cfg(tmp_path)
        cli.main(["config", "--key", "embed_api_key", "--value", "sk-secret-abcd1234",
                  "--config", str(cfg_file)])
        out = json.loads(capsys.readouterr().out)
        assert out["new"] == "***1234"
        assert "sk-secret" not in json.dumps(out)  # nothing leaked via other fields
        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert loaded["embed_api_key"] == "sk-secret-abcd1234"  # stored in full

    def test_key_without_value_rejected(self, tmp_path):
        self._write_cfg(tmp_path)
        with pytest.raises(ValueError, match="together"):
            cli.main(["config", "--key", "embed_model", "--config",
                      str(tmp_path / "config.json")])


# ---------------------------------------------------------------------------
# P2-8: secret masking in config view
# ---------------------------------------------------------------------------

class TestSecretMasking:
    def test_mask_secrets_masks_api_keys(self):
        cfg = {
            "embed_api_key": "sk-1234567890",
            "rerank_api_key": "",
            "endpoints": {"nested_api_key": "tok-abcdef"},
            "embed_model": "openai://text-embedding-3-small",
        }
        masked = cli.mask_secrets(cfg)
        assert masked["embed_api_key"] == "***7890"
        assert masked["rerank_api_key"] == ""
        assert masked["endpoints"]["nested_api_key"] == "***cdef"
        assert masked["embed_model"] == "openai://text-embedding-3-small"
        # original untouched
        assert cfg["embed_api_key"] == "sk-1234567890"

    def test_config_print_masks_key(self, tmp_path, capsys):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"embed_api_key": "sk-abcdef1234"}),
                            encoding="utf-8")
        cli.main(["config", "--config", str(cfg_file)])
        out = capsys.readouterr().out
        assert "sk-abcdef1234" not in out
        assert "***1234" in out


# ---------------------------------------------------------------------------
# P1-3: missing-dependency guidance
# ---------------------------------------------------------------------------

class TestDependencyHint:
    def test_hint_names_package_and_alternative(self):
        exc = ImportError("No module named 'sentence_transformers'")
        hint = cli.dependency_hint(exc)
        assert "sentence-transformers" in hint
        assert "pip install" in hint
        assert "openai://" in hint

    def test_main_catches_import_error_and_exits_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_load_cfg", lambda cfg_arg: {})
        monkeypatch.setattr(
            cli, "make_embedder",
            lambda cfg: (_ for _ in ()).throw(
                ImportError("No module named 'sentence_transformers'")),
        )
        with pytest.raises(SystemExit) as ei:
            cli.main(["query", "--question", "x"])
        assert ei.value.code == 2
        err = capsys.readouterr().err
        assert "sentence-transformers" in err
        assert "pip install" in err


# ---------------------------------------------------------------------------
# P1-5: kb show --id
# ---------------------------------------------------------------------------

class _FakeShowIndexer:
    def __init__(self, doc):
        self._doc = doc

    def get(self, doc_id):
        return dict(self._doc) if self._doc and self._doc.get("id") == doc_id else None

    def close(self):
        pass


class TestKbShow:
    def _run(self, monkeypatch, capsys, doc, doc_id):
        monkeypatch.setattr(cli, "_load_cfg", lambda cfg_arg: {})
        monkeypatch.setattr(cli, "make_indexer", lambda cfg: _FakeShowIndexer(doc))
        cli.main(["show", "--id", doc_id])
        return json.loads(capsys.readouterr().out)

    def test_show_returns_full_context_without_embedding(self, monkeypatch, capsys):
        doc = {"id": "abc", "title": "Button", "context": "x" * 1000,
               "embedding": [0.0] * 384}
        out = self._run(monkeypatch, capsys, doc, "abc")
        assert out["context"] == "x" * 1000
        assert "embedding" not in out

    def test_show_missing_doc_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_load_cfg", lambda cfg_arg: {})
        monkeypatch.setattr(cli, "make_indexer", lambda cfg: _FakeShowIndexer(None))
        with pytest.raises(SystemExit) as ei:
            cli.main(["show", "--id", "nope"])
        assert ei.value.code == 2
        assert "doc not found" in capsys.readouterr().err
