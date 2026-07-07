#!/usr/bin/env bash
# fetch-sidebars.sh — download Element Plus sidebar nav → regenerate sidebars/*.md
#
# First-use bootstrap for the element-dev KB: a fresh checkout has no
# `sidebars/` (the directory is .gitignore-d as a dev-time build input), so
# `kb build` would fail with FileNotFoundError. This script regenerates the
# two sidebar files the parser expects:
#
#   sidebars/element-plus-design-guide-sidebar.md
#   sidebars/element-plus-component-sidebar.md
#
# Usage:
#   bash scripts/fetch-sidebars.sh             # download + write (network)
#   bash scripts/fetch-sidebars.sh --dry-run   # offline verification
#   bash scripts/fetch-sidebars.sh --lang zh-CN --source github
#
# Delegates to scripts/fetch-sidebars.py; passes all args through. See that
# file for source priority (site scrape → GitHub Contents API fallback) and
# SSRF guarantees.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${script_dir}/fetch-sidebars.py" "$@"
