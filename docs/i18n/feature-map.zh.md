# 功能地图

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
本文档汇总了 Tesserae 当前已实现的功能，包括状态、源文件以及文档位置。

Tesserae 是一个运行在三大支柱之上的**上下文引擎**：(1) 会话监控，(2) 自主、主动的知识摄取，(3) 按需文档/上下文。类型化图、库（vault）和静态站点都是知识库的投影。下面的功能按其服务的支柱分组；**v0.5.0** 里程碑（2026 年 6 月）发布了引擎主干以及支柱 3 的标志性功能 —— 按需上下文编译器。

状态图例：✅ 已发布 · ⚠ 进行中 / 部分完成。

## 上下文引擎 —— v0.5.0 (2026 年 6 月)

驱动三大支柱的引擎主干。引擎主干模块映射、自我改进内存边车以及上下文编译器数据流，参见 [`docs/architecture.md`](architecture.zh.md)。

### 引擎主干（支柱 1 & 2）

| 功能 | 状态 | 源 | 备注 |
|---|---|---|---|
| `Pipeline` —— 返回 `List[StepResult]` 的可复用刷新链 | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | CLI、守护进程、MCP 都调用的同一个步骤运行器。对每步捕获 `Exception`；首次失败处停止。 |
| `Daemon` —— 单一所有者 asyncio 监督器 | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | 监视源 + 库 + harness 会话目录；去抖的取消并重新调度将一批合并为一次 `Pipeline.run()`。pidfile；执行中异常下存活。 |
| `engine` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`、`--debounce`、`--once`。`daemon` 是 `engine` 的别名。 |
| `project refresh` —— 散文式链（摄取 → 编译 → 投影） | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only`（选用增量）、`--skip-sessions`。 |
| 实时会话监控 → 发现 | ✅ | `harness_sessions.py` + 会话图模块 | 导入的会话喂入图；`fresh_insights` / `find_session_findings` 呈现它们。 |

### 自我改进内存（支柱 2）

| 功能 | 状态 | 源 | 备注 |
|---|---|---|---|
| `node_memory` SQLite 边车（衰减 / 置信度 / 被取代） | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + 与存储无关的访问器；仅可变状态。首见存放于独立的 `node_provenance` 边车。 |
| 艾宾浩斯衰减评分 | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | 对会话发现按最新 + 最常访问排名（驱动 `fresh_insights`）。 |
| 取代遍历（**默认开启**） | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | 确定性裁决将较旧的近重复洞见标记为被较新者取代；添加 `supersedes` 边。 |
| 洞见 → 代码符号链接 | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | 从会话洞见到所引用符号的 `discusses` 边。 |
| 强化 + 矛盾遍历 | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py)、[`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 针对同一边车的访问强化 + 矛盾检测。 |
| 输出中的数值化复现置信度 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | 时间事实从 `NodeMemoryRow.confidence` 为 `confidence` 打标，否则回退到 `infer_confidence`。 |

### 检索 + 嵌入（支柱 2 & 3）

| 功能 | 状态 | 源 | 备注 |
|---|---|---|---|
| 混合检索器（BM25 + 词法 + 嵌入，RRF k=60） | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | 本地优先，完全确定性。 |
| 个性化 PageRank（HippoRAG-2） | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | 多跳种子扩展；深度受限子图。 |
| 真实默认嵌入（Track B，阶段 6） | ✅ | `retrieval/hybrid.py` | 默认 = 确定性哈希桶伪嵌入（无依赖）；安装后优先使用 `sentence-transformers`（`all-MiniLM-L6-v2`）并惰性加载。`embedding_status` MCP 工具报告活动后端。 |

### 按需上下文编译器（支柱 3 —— 标志）

| 功能 | 状态 | 源 | 备注 |
|---|---|---|---|
| `compile_context` —— 带引用的内存 `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 种子解析 → PPR 扩展 → 预算受限选择 → 带引用 Markdown → 可选 LLM 综合。除非 `synthesize=true` 否则确定性。不向磁盘写入。 |
| `project context` CLI | ✅ | `cli.py` | `[query]`、`--seeds`、`--depth`（2）、`--budget`（32000；≤0 = 不限）、`--synthesize`、`--output`。 |
| `compile_context` MCP 工具 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | 经 MCP 的同一流水线；`budget=0` 为不限。 |
| 主题范围导出切片 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | 主题范围 `llms.txt` + 经 `compile_context` 的 `render_harness_context`。 |

### 增量编译（阶段 4 —— 实验性）

| 功能 | 状态 | 源 | 备注 |
|---|---|---|---|
| 来源边车（`node_provenance`，首见） | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | 变更删除的基础；始终记录。 |
| `GraphStore` 删除面 | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`、`delete_nodes_by_source`（丢弃来源集变空的节点；跨文件概念存活）。 |
| `url_resolver` 运行时存储分派 | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`。 |
| `incremental_compile` 标志 | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **默认 OFF / 实验性。** 多种编辑形态已证明字节一致，但仍存在多所有者/生产者生命周期缺口；全量编译仍为默认。 |

## 前端重设计 — 2026 年 4 月

以文档为中心的层级式 wiki 取代旧的图谱转储。逐路由导览见 [`docs/frontend-redesign.md`](frontend-redesign.zh.md)，三层模型见 [`docs/architecture.md`](architecture.zh.md)。

### Wiki 层（L2 markdown）

| 功能 | 状态 | 来源 | 文档锚点 |
|---|---|---|---|
| `WikiPageStore`（幂等 body-hash 写入、frontmatter 解析器） | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § 模块地图](architecture.zh.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — 每个 wiki 层节点生成一个 md 页面 | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § 流水线](architecture.zh.md#pipeline) |
| `sources/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.zh.md#sources) |
| `concepts/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.zh.md#concepts) |
| `entities/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.zh.md#entities) |
| `papers/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.zh.md#papers) |
| `repos/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.zh.md#repos) |
| `topics/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.zh.md#topics) |
| `questions/` 页面（开放问题） | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.zh.md#questions) |
| `syntheses/` 页面 | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.zh.md#syntheses) |

### 综合类型（L2 → 派生）

`SynthesisProjector` 生成七个确定性模板，并把 `Synthesis` 节点以及 `synthesizes` / `summarizes` 边写回图谱。

| 类型 | 状态 | 来源 | 备注 |
|---|---|---|---|
| `pulse`（全局一个，驱动 `/`） | ✅ | `synthesis.py` | 每次 compile 都会重建。 |
| `daily_digest` | ✅ | `synthesis.py` | 每个 `data/research/daily/<date>/` 一个。 |
| `weekly` | ✅ | `synthesis.py` | 每个 `data/research/weekly/<iso-week>/` 一个。 |
| `topic` | ✅ | `synthesis.py` | 每个含 ≥ 3 篇 papers 的 `ResearchTopic` / `ApproachFamily` 集群一个。 |
| `comparison` | ✅ | `synthesis.py` | 每对在同一任务上竞争的 `ApproachFamily` 一个。 |
| `field_overview` | ✅ | `synthesis.py` | 每个 `ResearchField` 一个。 |
| LLM 升级摘要（由环境标志启用） | ⚠ | 仅 hook | 已发布启发式基线；`TESSERAE_SYNTHESIS_LLM=1` hook 保留为 stub。 |

### 静态站点路由

| 路由 | 状态 | 来源 | 备注 |
|---|---|---|---|
| `/`（首页，hero pulse） | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | 统计行 + 精选入口 + 最近活动。 |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | 热力图 + 日期列表 + synthesis 侧栏。 |
| `/timeline/<YYYY-MM-DD>.html`（每日详情） | ⚠ | 暂无 | 热力图单元格暂时链接到当天的 `digest.md` 源页面。Subagent P 正在通过 `StaticSiteBuilder` 接入每日详情页。 |
| `/graph/`（交互式 2D + 3D） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js，悬停提示、边标签、以光标为锚点缩放。 |
| `/about.html` | ✅ | `pages.py::render_about` | Schema、构建信息。 |

### AI 友好导出

| 工件 | 状态 | 来源 | 目的 |
|---|---|---|---|
| 每页 `<page>.txt` 兄弟文件 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | 单页纯文本视图（无导航、无样式）。 |
| 每页 `<page>.json` 兄弟文件 | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`。 |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org 短索引。 |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | 所有页面正文，上限 5 MB。 |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`，仅 wiki 层节点。 |
| `graph.json` | ✅ | `__init__.py::write_site` | 完整图谱载荷（含工具用代码节点）。 |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | 命令面板 + 页面搜索；仅 wiki 层类型。 |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | 所有生成路由，`lastmod` 来自 frontmatter。 |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | 最近 30 个 syntheses。 |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | 宽松 — 允许抓取 + 索引。 |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | 机器可读站点地图。 |
| `manifest.json` | ✅ | `__init__.py::_manifest` | 每个生成文件的 sha256 + 大小（幂等性 harness）。 |

### 视觉设计 + UX

| 功能 | 状态 | 来源 | 备注 |
|---|---|---|---|
| 设计 token（浅色 + 深色主题、赤陶强调色） | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | `assets/style.css` 中的单个 CSS 包。 |
| 主题切换（持久化、无闪烁） | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `localStorage` 中的 `data-theme="dark"`，绘制前应用。 |
| 搜索面板（`cmd+k` / `ctrl+k` / `/`） | ✅ | `js.py` | 基于 `search-index.json` 的模糊匹配；最近页面列表。 |
| 右侧固定 TOC | ✅ | `pages.py` + `tokens.py` | 仅桌面端；移动端通过 `<details>` 抽屉。 |
| 带月份 + 星期标签的活动热力图 | ✅ | `components.py::heatmap_svg` | 26 周 SVG，单元格链接到当天 `digest.md`。 |
| Sparkline（每个概念/实体） | ✅ | `components.py::sparkline_svg` | 每周提及次数，最近 12 周。 |
| 移动端外壳（抽屉栏、底部导航、流式字号） | ✅ | `tokens.py` + `pages.py` | 触控目标 ≥ 44 px。 |
| 页面过渡（120 ms 透明度，prefers-reduced-motion） | ✅ | `tokens.py` | |
| 3D + 2D 图谱视图（悬停、边标签、以光标为锚点缩放） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js，以 CDN 快照形式内置。 |
| 每页 AI 兄弟文件页脚 | ✅ | `components.py::ai_siblings_footer` | 指向当前页 `.txt` 和 `.json` 的内联链接。 |
| Harness 会话历史页面 | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 显式 Claude Code/Codex 导入；`/sessions/` 索引和详情页包含 markdown 轮次、左侧轮次栏、折叠的工具使用和搜索条目。 |

### 流水线 + CLI

| 功能 | 状态 | 来源 | 备注 |
|---|---|---|---|
| `project compile` 按顺序调用 synthesis + wiki + site | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | 重设计计划第 3 阶段。 |
| 独立 `project build-site` | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | 读取 `wiki/` + `graph.json`，写入 `site/`。 |
| `project serve` 本地 HTTP | ✅ | `cli.py` | 普通 stdlib 服务器。 |
| `export site --deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | worktree push 到 `gh-pages`；可通过 `gh` CLI 选用 `--enable-pages`。`--build`, `--dry-run`, `--branch`, `--remote`, `--force`。 |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Claude Code/Codex 的入站会话历史；发现过程显式且限定在项目工作目录。 |
| `export site --watch` 变更时重建 | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | 独立轮询 watcher：`--interval`、`--debounce`、`--once`、`--paths`、`--quiet`。多源监督器位于 `project engine`/`daemon`（见上下文引擎）。 |
| `project context` —— 编译带引用的上下文文档 | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 支柱 3 标志；见上下文引擎章节。 |
| `project refresh` / `engine` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | 散文式刷新链 + 监督器循环；见上下文引擎章节。 |

## 既有功能（原样保留）

### CLI 与安装

- ✅ 通过 `pyproject.toml` 安装的 Python 包。
- ✅ 控制台命令：`tesserae`, `tesserae`, `tesserae_mcp`。
- ✅ 用于 `curl | bash` 安装的 `scripts/install.sh`。
- ✅ 默认 editable 安装，便于快速本地开发。

### 抽取

- ✅ 使用受控节点/边词汇表的确定性研究笔记抽取器。
- ✅ Claude CLI/OAuth 抽取器，无需 API key 即可获得更高质量结构化抽取。
- ✅ 按 glob 和预算限制选择性路由到 Claude。
- ✅ 面向 Python 项目的确定性开发代码抽取器。
- ✅ 支持内容哈希和 `--changed-only` 的批量摄取。
- ✅ 容忍畸形 UTF-8 的源读取。

### 图谱治理

- ✅ 受控 `ResearchNodeType` 列表 — 现在包含 `SYNTHESIS`。
- ✅ 受控边类型白名单 — 现在包含 `synthesizes`, `summarizes`。
- ✅ 拒绝 schema 漂移的验证。
- ✅ 别名规范化。
- ✅ 用于模糊近重复节点的审核队列。
- ✅ 审核决策模板以及合并/保持分离工作流。
- ✅ 基于每文件图谱的语料趋势总结。

### 持久化与报告

- ✅ Graph JSON 导出。
- ✅ SQLite 图存储。
- ✅ 可选 Kuzu 图存储。
- ✅ 图谱报告，包含计数、证据覆盖率、孤立节点、日期桶、别名密集节点。
- ✅ 竞争性报告，描述从 MegaMem、Graphiti/Zep、MCP graph servers、agentic RAG 吸收的想法。

### 项目本地工作流

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy`（GitHub Pages）
- ✅ `tesserae sessions discover/import/list`（显式本地 agent-history 导入）
- ✅ `tesserae export site --watch`（独立轮询 watcher）
- ✅ `tesserae engine`（监督器循环 —— v0.5.0）
- ✅ `tesserae refresh`（散文式 摄取 → 编译 → 投影 链 —— v0.5.0）
- ✅ `tesserae context`（按需上下文编译器 —— v0.5.0）
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ 可直接打开的 vault 导出。
- ✅ `.obsidian/app.json` 和图谱设置。
- ✅ Markdown 投影。
- ✅ `raw/assets/` 结构。
- ✅ 带 Dataview 查询的 `_meta/dashboard.md`。

### Agent harnesses

生成的目标文件：

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering 和 MCP 设置
- ✅ Cursor: 项目规则和 MCP 配置
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / 时间事实

- ✅ 带 provenance、currentness、confidence 和 invalidation 字段的时间事实投影。
- ✅ 无依赖 Graphiti episode JSONL 导出。
- ✅ 未安装 Graphiti 时的 `sync-graphiti --dry-run` 冒烟测试。
- ✅ 使用 `graphiti_core` 和 Neo4j 的可选实时同步。

### Cognee

- ✅ Cognee JSONL 包（`nodes.jsonl`, `edges.jsonl`, `manifest.json`）。
- ✅ 可选 add-only 直接导入。
- ✅ 可选 Codex CLI/OAuth 支持的 Cognee cognify 适配器。
- ✅ 用于无 API key 冒烟/质量工作流的确定性和 Ollama embedding 适配器路径。

### MCP server

- ✅ 基于 stdio JSON-RPC 的 `tesserae_mcp` / `python3 -m tesserae.mcp_server`。
- ✅ 检索/图工具：`schema`、`graph_summary`、`search_nodes`、`node_context`（含 `use_ppr`）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`。
- ✅ 上下文引擎工具（v0.5.0）：`compile_context`、`embedding_status`、`fresh_insights`（按衰减排名）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`、`ask`。
- ✅ 设置工具：`tesserae_setup_plan`、`tesserae_setup_apply`。
- ✅ 多项目注册表：`list_projects`、`register_project`、`activate_project`、`unregister_project`、`list_sessions`。经 `url_resolver` 的存储 URL 分派。

## 测试

当前测试套件覆盖：

- ✅ ontology guardrails（含新的 `Synthesis` 节点 + `synthesizes` / `summarizes` 边）；
- ✅ 确定性抽取；
- ✅ Claude CLI wrapper 解析/验证；
- ✅ 选择性 Claude 路由；
- ✅ 规范化/审核工作流；
- ✅ 批量摄取；
- ✅ 报告；
- ✅ SQLite/Kuzu 持久化；
- ✅ Cognee bundles/import patches；
- ✅ Graphiti export/sync dry-run；
- ✅ 项目 CLI 工作流；
- ✅ agent harness export；
- ✅ Obsidian export；
- ✅ 前端生成 + 链接完整性（无 `nodes/codeclass-*.html`）；
- ✅ wiki store 幂等性；
- ✅ synthesis projector golden + 幂等性；
- ✅ 站点组件、页面、导出、相关性；
- ✅ AI 兄弟文件形状（每页 `.txt` + `.json`）；
- ✅ end-to-end 编译两次幂等性；
- ✅ 引擎主干：流水线、刷新链、守护进程核心 + 源、`project engine` CLI；
- ✅ 自我改进内存：边车、衰减/取代、取代抑制（含 MCP）、强化/矛盾；
- ✅ 检索 + 嵌入：混合检索、PPR、真实默认嵌入（阶段 6）；
- ✅ 上下文编译器：形态/引用完整性/确定性/预算/PPR 回退、`project context` CLI、MCP `compile_context`；
- ✅ 增量编译（实验性）：差异器、一致性门、来源就绪性、SQLite 来源；
- ✅ 包安装和安装器契约。
