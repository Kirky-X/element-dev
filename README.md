# ELEMENT-DEV —— Element Plus 开发技能

[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

element-dev 是一个面向 AI agent 的 Vue 3 + Element Plus 组件库开发 skill。它聚合 3 个子命令，覆盖 Element Plus 文档检索与知识管理：**kb**（本地 Qdrant 知识库）+ **fetch**（在线抓取 element-plus.org）+ **config**（配置管理）。

## 3 个子命令速览

| 子命令   | 一句话功能                                                              | 主要脚本                                       |
| -------- | ----------------------------------------------------------------------- | ---------------------------------------------- |
| `kb`     | 本地 Qdrant 知识库：9 子动作（query/build/reindex/merge/link-auto/…）   | `scripts/kb/*.py` + 预构建库                   |
| `fetch`  | 直接 HTTP GET 抓取 element-plus.org 静态文档页，提取 `<main>` 转 Markdown | `scripts/fetcher/{_http,fetch}.py`             |
| `config` | 显示/修改 embed_model、rerank_model、db_path、context_ttl_days 等配置项 | `scripts/kb/config.py`                         |

子命令路由表、触发词、各子动作完整流程见 [SKILL.md](SKILL.md)。

## 与 hap-dev 的差异

| 维度 | hap-dev | element-dev |
| ---- | ------- | ----------- |
| 文档源 | HarmonyOS 搜索 API（POST + 多 catalog 路由） | element-plus.org 静态站点（HTTP GET） |
| 在线模块 | `scripts/search/{search.py, detail.py}` 双端点 | `scripts/fetcher/fetch.py` 单一 GET |
| Sidebar 格式 | `#### N.N [title](url)` | `### N.N[.] [title](url)`（3 hashes + 可选尾点） |
| 文档数 | 964（9 类） | 99（2 类：design-guide + component） |
| KB 模块 | 领域无关 | 同 hap-dev（B1-B13 修复全部继承） |

## 安装

### Python 依赖

```bash
pip install -r requirements.txt
```

依赖清单：

- **必需**：`qdrant-client`、`rank-bm25`、`httpx`、`sentence-transformers`、`modelscope`
- **可选**：`flashrank`（重排）、`openai`（云端嵌入模型）

### sidebars 目录

`sidebars/` 目录被 `.gitignore` 排除（开发期产物），首次使用需自行准备：

- `element-plus-design-guide-sidebar.md` —— 17 篇设计/导航/安装/国际化/主题/暗黑模式/SSR 等文档
- `element-plus-component-sidebar.md` —— 82 篇组件文档（Basic/Config/Form/Data/Navigation/Feedback/Others）

放置于仓库根 `sidebars/` 目录后，运行 `kb build` 构建知识库。

## config.json 配置

仓库根 `config.json` 是 kb 子命令的唯一配置源。

| 字段 | 默认值 | 说明 |
| ---- | ------ | ---- |
| `embed_model` | `sentence-transformers/paraphrase-MiniLM-L3-v2` | 嵌入模型（本地 ST / `openai://` 云端） |
| `embed_dim` | `384` | 嵌入维度（必须匹配模型） |
| `embed_source` | `modelscope` | 模型下载源（`modelscope` / `''` HF） |
| `embed_base_url` | `""` | 云端 OpenAI 兼容 base_url |
| `embed_api_key` | `""` | 云端 API key |
| `rerank_model` | `flashrank` | 重排模型（`flashrank` / `openai://…`） |
| `rerank_source` | `local` | 重排模型源 |
| `db_path` | `data/element-plus.qdrant` | Qdrant 本地库路径 |
| `collection` | `element_plus_docs` | Qdrant 集合名 |
| `sidebars_dir` | `sidebars` | sidebar 解析目录 |
| `site_base` | `https://element-plus.org` | 文档站点基址 |
| `context_ttl_days` | `30` | C1: context 缓存 TTL（天），过期后 fetch 校验 hash |
| `query.default_top_k` | `5` | 默认返回 top-k |
| `query.bm25_weight` | `0.3` | BM25 融合权重 |
| `query.vector_weight` | `0.7` | 向量融合权重 |

## 预构建知识库

仓库附带预构建的 `data/element-plus.qdrant/`（本地 Qdrant 持久化目录），由默认嵌入模型生成，开箱即用：

- **99 条向量**覆盖 2 个文档类型（17 design-guide + 82 component）
- 直接运行 `python3 -m scripts.kb.cli query --question "ElTable 虚拟滚动"` 即可查询

### 一键重建 / 切换模型后重索引

```bash
# 完全重建（从 sidebars/ 重新解析、重新嵌入）
python3 scripts/kb/build_db.py

# 仅重嵌入（content_hash 变化或 description 回填的文档）
python3 -m scripts.kb.cli reindex

# 强制全量重嵌入（切换 embed_model 后必跑）
python3 -m scripts.kb.cli reindex --force
```

切换 `embed_model` 的标准流程：

1. 编辑 `config.json` 修改 `embed_model` / `embed_dim` / `embed_source`
2. 运行 `python3 scripts/kb/build_db.py`（全量重建）
3. 验证查询：`python3 -m scripts.kb.cli query --question "测试"`

## kb 子命令完整动作

```bash
# 混合检索（向量 0.7 + BM25 0.3，可选 FlashRank 重排）
python3 -m scripts.kb.cli query --question "ElTable 虚拟滚动" --top-k 5

# 全量构建
python3 -m scripts.kb.cli build

# 增量重建（仅 content_hash 变化）/ 强制全量
python3 -m scripts.kb.cli reindex
python3 -m scripts.kb.cli reindex --force

# 余弦 >0.9 自动双向链接
python3 -m scripts.kb.cli link-auto --threshold 0.9 --max-per-doc 10

# 描述回填 + 向量重算
python3 -m scripts.kb.cli update-description --id <doc_id> --description "..."

# 手动双向链接
python3 -m scripts.kb.cli update-links --id <doc_id> --content "<markdown with 相关推荐>"

# 模型迁移（回填 embed_model 字段）
python3 -m scripts.kb.cli migrate-embed-model --model <model_name>

# 合并两个 DB（入口校验 embed_model 一致）
python3 -m scripts.kb.cli merge --db-a <path_a> --db-b <path_b> --out <out_path>

# 抓取 URL + 智能更新（C1 三层缓存：cached/refreshed/updated）
python3 -m scripts.kb.cli fetch-update --id <doc_id> [--force] [--ttl-days 7]

# 配置管理
python3 -m scripts.kb.cli config
python3 -m scripts.kb.cli config --key embed_model --value sentence-transformers/all-MiniLM-L6-v2
```

## fetch 子命令

```bash
# 抓取单个文档页（提取 <main> 内容，清洗 Cloudflare email-protection artifacts）
python3 -m scripts.fetcher.fetch https://element-plus.org/zh-CN/component/button
```

返回 `{title, url, content}`，content 为 Markdown 格式。

## 文档分类

| doc_type | 来源 sidebar | 文档数 | 内容 |
| -------- | ------------ | ------ | ---- |
| `design-guide` | element-plus-design-guide-sidebar.md | 17 | 设计/导航/安装/快速开始/国际化/升级/主题/暗黑模式/SSR/过渡动画等 |
| `component` | element-plus-component-sidebar.md | 82 | Basic(12) + Config(1) + Form(25) + Data(23) + Navigation(9) + Feedback(10) + Others(2) |

## 仓库结构

```
element-dev/
├── SKILL.md                          # 3 子命令路由器 + 通用规则
├── config.json                       # kb 配置（模型/库/端点）
├── requirements.txt                  # Python 依赖
├── data/
│   └── element-plus.qdrant/          # 预构建 Qdrant 本地库
├── sidebars/                         # sidebar 文件（gitignore，需自行准备）
│   ├── element-plus-design-guide-sidebar.md
│   └── element-plus-component-sidebar.md
└── scripts/
    ├── kb/                           # 知识库模块 + tests/ + build_db.py
    └── fetcher/                      # _http.py + fetch.py
```

## 测试

```bash
python3 -m pytest scripts/ -v
```

测试使用 `FakeEmbedder`（SHA1 派生的确定性向量）替代真实模型下载，确保离线可运行。

## 禁止事项

1. **禁止跨模型向量空间混用** —— 向量身份 = (model_name, dim, source, version)，build/merge/query 入口 MUST 校验
2. **禁止单方向链接** —— links 是双向契约
3. **禁止吞掉错误** —— 错误必须显式抛出或返回
4. **禁止 set_payload 写白名单外字段** —— 防止 schema 污染
5. **禁止混淆 content_hash 与 context_hash** —— 前者检测元数据变更，后者检测网页内容变更
6. **禁止 fetch 时不清洗 Cloudflare artifacts** —— 否则 context_hash 永远不稳定

## License

MIT
