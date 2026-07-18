# ELEMENT-DEV — Element Plus Development Skill

[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

element-dev is a Vue 3 + Element Plus component library development skill for AI agents. It aggregates 3 subcommands covering Element Plus document retrieval and knowledge management: **kb** (local Qdrant knowledge base) + **fetch** (online scraping of element-plus.org) + **config** (configuration management).

## 3 Subcommands at a Glance

| Subcommand | One-line description | Primary scripts |
| -------- | ------------------------------------------------------------------------- | ---------------------------------- |
| `kb`     | Local Qdrant knowledge base: 9 sub-actions (query/build/reindex/merge/link-auto/…) | `scripts/kb/*.py` + prebuilt library |
| `fetch`  | Direct HTTP GET scraping of element-plus.org static doc pages, extracting `<main>` to Markdown | `scripts/fetcher/{_http,fetch}.py` |
| `config` | View/modify embed_model, rerank_model, db_path, context_ttl_days and other config items | `scripts/kb/config.py` |

Subcommand routing table, trigger words, and complete sub-action flows are in [SKILL.md](SKILL.md).

## Differences from hap-dev

| Dimension | hap-dev | element-dev |
| ------------ | ---------------------------------------------- | ------------------------------------------------ |
| Doc source | HarmonyOS Search API (POST + multi-catalog routing) | element-plus.org static site (HTTP GET) |
| Online module | `scripts/search/{search.py, detail.py}` dual endpoints | `scripts/fetcher/fetch.py` single GET |
| Sidebar format | `#### N.N [title](url)` | `### N.N[.] [title](url)` (3 hashes + optional trailing period) |
| Doc count | 964 (9 categories) | 99 (2 categories: design-guide + component) |
| KB module | Domain-agnostic | Same as hap-dev (B1-B13 fixes all inherited) |

## Installation

### Python Dependencies

```bash
pip install -r requirements.txt
```

Dependency list:

- **Required**: `qdrant-client`, `rank-bm25`, `httpx`, `sentence-transformers`, `modelscope`
- **Optional**: `flashrank` (reranking), `openai` (cloud embedding models)

### sidebars Directory (must run on first use)

The `sidebars/` directory is excluded by `.gitignore` (dev-time build artifacts), so it doesn't exist after a fresh clone. `kb build` will raise `FileNotFoundError` because of this. First-time setup:

```bash
bash scripts/fetch-sidebars.sh            # download + write sidebars/*.md
bash scripts/fetch-sidebars.sh --dry-run  # offline verification (no network)
bash scripts/fetch-sidebars.sh --lang zh-CN --source github  # specify language/source
```

The script generates the two files expected by the parser:

- `element-plus-design-guide-sidebar.md` — design/navigation/installation/i18n/theme/dark-mode/SSR documentation
- `element-plus-component-sidebar.md` — component documentation (Basic/Config/Form/Data/Navigation/Feedback/Others)

Data source priority (`--source auto` by default): 1) scrape element-plus.org rendered sidebar HTML (most accurate titles); 2) fall back to GitHub Contents API enumerating `docs/<lang>/{guide,component}/*.md`. Both paths go through SSRF guard (host whitelist + internal IP blocking). Document counts change with official site updates and are not fixed to the historical snapshot of 17/82. After generation, run `python3 -m scripts.kb.cli build` to rebuild the knowledge base.

## config.json Configuration

The repo root `config.json` is the single configuration source for the kb subcommand.

| Field | Default value | Description |
| --------------------- | ----------------------------------------------- | -------------------------------------------------- |
| `embed_model` | `sentence-transformers/paraphrase-MiniLM-L3-v2` | Embedding model (local ST / `openai://` cloud) |
| `embed_dim` | `384` | Embedding dimension (must match model) |
| `embed_source` | `modelscope` | Model download source (`modelscope` / `''` HF) |
| `embed_base_url` | `""` | Cloud OpenAI-compatible base_url |
| `embed_api_key` | `""` | Cloud API key |
| `rerank_model` | `flashrank` | Rerank model (`flashrank` / `openai://…`) |
| `rerank_source` | `local` | Rerank model source |
| `db_path` | `data/element-plus.qdrant` | Qdrant local library path |
| `collection` | `element_plus_docs` | Qdrant collection name |
| `sidebars_dir` | `sidebars` | Sidebar parsing directory |
| `site_base` | `https://element-plus.org` | Doc site base URL |
| `context_ttl_days` | `30` | C1: context cache TTL (days), fetch verifies hash after expiry |
| `query.default_top_k` | `5` | Default top-k results |
| `query.bm25_weight` | `0.3` | BM25 fusion weight |
| `query.vector_weight` | `0.7` | Vector fusion weight |

## Prebuilt Knowledge Base

The repo includes a prebuilt `data/element-plus.qdrant/` (local Qdrant persistence directory), generated with the default embedding model and ready to use:

- **99 vectors** covering 2 document types (17 design-guide + 82 component)
- Run `python3 -m scripts.kb.cli query --question "ElTable virtual scrolling"` to query directly

### One-click Rebuild / Re-index After Model Switch

```bash
# Full rebuild (re-parse from sidebars/, re-embed)
python3 scripts/kb/build_db.py

# Re-embed only (docs with changed content_hash or description backfill)
python3 -m scripts.kb.cli reindex

# Force full re-embed (required after switching embed_model)
python3 -m scripts.kb.cli reindex --force
```

Standard workflow for switching `embed_model`:

1. Edit `config.json` to change `embed_model` / `embed_dim` / `embed_source`
2. Run `python3 scripts/kb/build_db.py` (full rebuild)
3. Verify query: `python3 -m scripts.kb.cli query --question "test"`

## kb Subcommand Full Actions

```bash
# Hybrid search (vector 0.7 + BM25 0.3, optional FlashRank reranking)
python3 -m scripts.kb.cli query --question "ElTable virtual scrolling" --top-k 5

# Full build
python3 -m scripts.kb.cli build

# Incremental rebuild (only changed content_hash) / force full
python3 -m scripts.kb.cli reindex
python3 -m scripts.kb.cli reindex --force

# Cosine >0.9 automatic bidirectional linking
python3 -m scripts.kb.cli link-auto --threshold 0.9 --max-per-doc 10

# Description backfill + vector recomputation
python3 -m scripts.kb.cli update-description --id <doc_id> --description "..."

# Manual bidirectional linking
python3 -m scripts.kb.cli update-links --id <doc_id> --content "<markdown with related recommendations>"

# Model migration (backfill embed_model field)
python3 -m scripts.kb.cli migrate-embed-model --model <model_name>

# Merge two DBs (entry validates embed_model consistency)
python3 -m scripts.kb.cli merge --db-a <path_a> --db-b <path_b> --out <out_path>

# Fetch URL + smart update (C1 three-layer cache: cached/refreshed/updated)
python3 -m scripts.kb.cli fetch-update --id <doc_id> [--force] [--ttl-days 7]

# Configuration management
python3 -m scripts.kb.cli config
python3 -m scripts.kb.cli config --key embed_model --value sentence-transformers/all-MiniLM-L6-v2
```

## fetch Subcommand

```bash
# Fetch a single doc page (extract <main> content, clean Cloudflare email-protection artifacts)
python3 -m scripts.fetcher.fetch https://element-plus.org/zh-CN/component/button
```

Returns `{title, url, content}`, where content is in Markdown format.

## Document Classification

| doc_type | Source sidebar | Doc count | Content |
| -------------- | ------------------------------------ | ------ | -------------------------------------------------------------------------------------- |
| `design-guide` | element-plus-design-guide-sidebar.md | 17 | design/navigation/installation/quickstart/i18n/upgrades/theme/dark-mode/SSR/transitions etc. |
| `component` | element-plus-component-sidebar.md | 82 | Basic(12) + Config(1) + Form(25) + Data(23) + Navigation(9) + Feedback(10) + Others(2) |

## Repository Structure

```
element-dev/
├── SKILL.md                          # 3 subcommand router + general rules
├── config.json                       # kb config (model/library/endpoint)
├── requirements.txt                  # Python dependencies
├── data/
│   └── element-plus.qdrant/          # prebuilt Qdrant local library
├── sidebars/                         # sidebar files (gitignore, must prepare yourself)
│   ├── element-plus-design-guide-sidebar.md
│   └── element-plus-component-sidebar.md
└── scripts/
    ├── kb/                           # knowledge base module + tests/ + build_db.py
    ├── fetcher/                      # _http.py (with SSRF guard) + fetch.py
    ├── fetch-sidebars.py             # download sidebars/*.md (site scraping / GitHub fallback)
    └── fetch-sidebars.sh             # bash entry point for above (must run on first use)
```

## Testing

```bash
python3 -m pytest scripts/ -v
```

Tests use `FakeEmbedder` (SHA1-derived deterministic vectors) instead of real model downloads, ensuring offline operation.

## Prohibitions

1. **No cross-model vector space mixing** — vector identity = (model_name, dim, source, version), build/merge/query entry points MUST validate
2. **No unidirectional links** — links are a bidirectional contract
3. **No swallowing errors** — errors must be explicitly raised or returned
4. **No set_payload writes outside the whitelist** — prevents schema pollution
5. **No confusing content_hash with context_hash** — the former detects metadata changes, the latter detects webpage content changes
6. **No fetching without cleaning Cloudflare artifacts** — otherwise context_hash is permanently unstable

## License

MIT
