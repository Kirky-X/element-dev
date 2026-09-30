"""Tests for update-links CLI argument parsing (BUG-5).

Validates that `--content` is always treated as a literal string (never
read as a file path) and `--file` explicitly reads from disk, replacing
the old `os.path.exists` heuristic with an argparse mutually exclusive
group.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make the project root importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.kb import cli  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> "argparse.Namespace":  # type: ignore[name-defined]
    """Parse CLI args using the real build_parser.

    tasks.md referenced a `cli._parse_args` helper that doesn't exist in
    the current codebase — `build_parser().parse_args(...)` is the actual
    entry point, so we use it directly.
    """
    import argparse  # local to avoid top-level import noise
    parser = cli.build_parser()
    return parser.parse_args(argv)


def _patch_cli_deps(monkeypatch, capture: list) -> None:
    """Patch cli.update_links / make_indexer / _load_cfg so tests don't
    touch real config or Qdrant. `capture` collects the content arg."""
    monkeypatch.setattr(cli, "update_links",
                        lambda doc_id, content, idx: capture.append(content) or content)
    monkeypatch.setattr(cli, "make_indexer", lambda cfg: MagicMock())
    monkeypatch.setattr(cli, "_load_cfg", lambda cfg: {})


# ---------------------------------------------------------------------------
# BUG-5: --content / --file mutually exclusive group
# ---------------------------------------------------------------------------

class TestUpdateLinksArgParsing:
    """update-links must use --content (literal) / --file (path) mutex
    group, eliminating the os.path.exists heuristic."""

    def test_content_treated_as_literal_even_when_path_exists(
        self, monkeypatch,
    ) -> None:
        """--content /etc/hosts must pass the literal string '/etc/hosts'
        to update_links, NOT read the file's contents."""
        capture: list = []
        _patch_cli_deps(monkeypatch, capture)

        args = _parse_args(
            ["update-links", "--id", "doc1", "--content", "/etc/hosts"],
        )
        cli._run_update_links(args)

        assert capture, "update_links was never called"
        assert capture[0] == "/etc/hosts", (
            f"expected literal '/etc/hosts', got file contents: {capture[0]!r}"
        )

    def test_file_argument_reads_from_disk(self, monkeypatch, tmp_path) -> None:
        """--file <path> reads the file's contents and passes them to
        update_links."""
        capture: list = []
        _patch_cli_deps(monkeypatch, capture)

        links_file = tmp_path / "links.json"
        links_file.write_text('["doc2"]', encoding="utf-8")

        args = _parse_args(
            ["update-links", "--id", "doc1", "--file", str(links_file)],
        )
        cli._run_update_links(args)

        assert capture, "update_links was never called"
        assert capture[0] == '["doc2"]', (
            f"expected file contents '[\"doc2\"]', got: {capture[0]!r}"
        )

    def test_content_and_file_mutually_exclusive(self) -> None:
        """Providing both --content and --file must exit (argparse mutex
        group error)."""
        with pytest.raises(SystemExit):
            _parse_args(
                ["update-links", "--id", "doc1",
                 "--content", "x", "--file", "y"],
            )

    def test_one_of_content_file_required(self) -> None:
        """Providing neither --content nor --file must exit (required
        mutex group)."""
        with pytest.raises(SystemExit):
            _parse_args(["update-links", "--id", "doc1"])

    def test_nonexistent_file_raises_filenotfound(self, monkeypatch) -> None:
        """--file <nonexistent> must raise FileNotFoundError (surfaced by
        open(), not swallowed)."""
        # _load_cfg / make_indexer patched so we reach the open() call.
        monkeypatch.setattr(cli, "make_indexer", lambda cfg: MagicMock())
        monkeypatch.setattr(cli, "_load_cfg", lambda cfg: {})

        args = _parse_args(
            ["update-links", "--id", "doc1",
             "--file", "/nonexistent/path.json"],
        )
        with pytest.raises(FileNotFoundError):
            cli._run_update_links(args)
