# 功能地图

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
本文档总结 Tesserae 当前已实现的功能，附带状态、源文件以及文档位置。

Tesserae 是一个运行在三大支柱上的**上下文引擎**：(1) 会话监控，(2) 自主主动的知识摄取，(3) 按需文档/上下文。类型化图谱、vault 和静态站点是知识库的投影。下面的功能按其服务的支柱分组；**v0.5.0** 里程碑（2026 年 6 月）交付了引擎主干和支柱 3 的头条功能——按需上下文编译器。

状态图例：✅ 已交付 · ⚠ 进行中 / 部分。

## 跨项目与 UX — v0.11.0（2026 年 6 月）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| 跨项目联邦 | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` 从多个已注册项目组装出一张图——身份合并（相同 arxiv/repo/hash/symbol）+ 可退出的嵌入支持的 `shares_concept_with` 链接——并在并集上返回单个交叉引用、带引用的答案（PPR + `compile_context`）。各项目的 `graph.json` 只读；仅身份合并时结果确定。 |
| 智能 `ask` 路由器（无活动项目） | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | 移除了"活动项目"概念——所有已注册项目一律平等。裸 `ask` 自行路由（点名某项目 → 该项目；比较性问题 → 联邦；追问 → 保持原路由；否则回退到联邦），带可选的 LLM 决胜器和按对话的连续性。按项目操作从 cwd 解析项目。 |
| 联邦检查 | ✅ | `tesserae/federation.py`、`cli.py` | `tesserae federation status`（各项目节点数、身份合并、语义链接）和 `federation explain <node>`（为什么某节点桥接多个项目）。 |
| 多项目 serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py)、`cli.py` | 裸 `tesserae serve` 在一个服务器下服务每个已注册项目（`/` 是落地页，各项目位于 `/<alias>/`，页头有 Projects 切换器，路径受限）；`--project X` 服务单个项目并带实时 ask 小组件。 |
| `compile` 中的 LLM 概念层 | ✅ | `cli.py`、[`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py)、[`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` **默认**构建概念/断言层（`--extractor llm`），通过配置的提供方（codex/claude/api，取决于 `llm_provider`）；`--extractor deterministic` 是结构化、字节稳定的退出选项；`selective-llm --llm-include … --llm-limit N` 是成本感知模式。 |
| `tesserae setup`（交互式） | ✅ | `cli.py`、[`tesserae/deps.py`](../../tesserae/deps.py) | 顶层 `tesserae setup` —— 默认交互式（LLM 提供方/effort + 要安装哪些可选依赖）；标志可跳过提示。安装在无 pip 的 uv-tool 环境中也可用（uv-pip 回退）。 |

## 互操作、搜索与设置 — v0.10.0（2026 年 6 月）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| Google **OKF v0.1** 导入/导出 | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`。Markdown + YAML frontmatter 的 bundle；通过 `x_tesserae` 命名空间无损往返 Tesserae 自己的 bundle，外来 bundle 尽力而为。 |
| 快速转录搜索（memex） | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | `nicosuave/memex` 对 Claude/Codex 转录的 BM25 索引，通过 `GET /api/transcript-search` 接入 `tesserae serve` 的 sessions 仪表盘。可选，缺失时优雅降级。 |
| 读取纪律 handle | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` 的 `preview=N` 返回一个有界预览 + 一个按内容哈希的 handle；`get_handle` 分页读取其余部分。把巨大载荷挡在 agent 的上下文之外。 |
| 提取质量信号 | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | 每条发现的 `confidence` + `confidence_rationale` + `revisit_signals`（字节稳定；在 `fresh_insights` 中呈现）。 |
| 机器级设置 + 依赖 | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py)、`cli.py` | `tesserae setup` 写入全局 LLM 默认值 + 安装可选依赖（memex、raganything）；`tesserae config deps` 列出/安装；`tesserae init` 提供 memex。按项目配置仍可覆盖。 |

## 上下文引擎 — v0.5.0（2026 年 6 月）

驱动三大支柱的引擎主干。引擎主干模块地图、自我改进内存边车以及上下文编译器数据流见 [`docs/architecture.md`](architecture.zh.md)。

### 引擎主干（支柱 1 与 2）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `Pipeline` —— 返回 `List[StepResult]` 的可复用刷新链 | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | CLI、daemon 和 MCP 都调用的单一步骤运行器。逐步骤捕获 `Exception`；在首次失败处停止。 |
| `Daemon` —— 单一所有者的 asyncio 监督器 | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | 监视来源 + vault + harness 会话目录；去抖的取消并重排把一次突发合并为一次 `Pipeline.run()`。Pidfile；在途异常不致命。 |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`、`--debounce`、`--once`。`daemon` 是 `engine` 的别名。 |
| `project refresh` —— 文字链（ingest → compile → project） | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only`（可选增量）、`--no-sessions`。 |
| 实时会话监控 → 发现 | ✅ | `harness_sessions.py` + 会话图模块 | 导入的会话喂入图谱；`fresh_insights` / `find_session_findings` 呈现它们。 |

### 自我改进记忆（支柱 2）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `node_memory` SQLite 边车（衰减 / 置信度 / 取代） | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + 与存储无关的访问器；仅可变状态。首次出现记录在独立的 `node_provenance` 边车中。 |
| 艾宾浩斯衰减分数 | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | 把会话发现按最新 + 访问最多优先排序（驱动 `fresh_insights`）。 |
| 取代过程（**默认开启**） | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | 确定性裁决把较旧的近重复洞见标记为被较新者取代；添加一条 `supersedes` 边。 |
| 洞见 → 代码符号链接 | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | 从会话洞见到其引用的符号的 `discusses` 边。 |
| 强化 + 矛盾过程 | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py)、[`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 在同一边车上的访问强化 + 矛盾检测。 |
| 输出中的数值复现置信度 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | 时间事实从 `NodeMemoryRow.confidence` 打上 `confidence`，回退到 `infer_confidence`。 |

### 检索 + 嵌入（支柱 2 与 3）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| 混合检索器（BM25 + 词法 + 嵌入，RRF k=60） | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | 本地优先，完全确定性。 |
| Personalized PageRank（HippoRAG-2） | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | 多跳种子扩展；深度受限的子图。 |
| 真实默认嵌入（Track B，Phase 6） | ✅ | `retrieval/hybrid.py` | 默认 = 确定性哈希桶伪嵌入（无依赖）；`sentence-transformers`（`all-MiniLM-L6-v2`）优先，安装后惰性加载。`embedding_status` MCP 工具报告活动后端。 |

### 按需上下文编译器（支柱 3 —— 头条）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `compile_context` —— 带引用的内存 `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 种子解析 → PPR 扩展 → 预算受限选择 → 带引用的 markdown → 可选 LLM 合成。除非 `synthesize=true`，否则确定性。不向磁盘写任何内容。 |
| `project context` CLI | ✅ | `cli.py` | `[query]`、`--seeds`、`--depth`（2）、`--budget`（32000；≤0 = 不设上限）、`--llm`、`--output`。 |
| `compile_context` MCP 工具 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | 同一流水线走 MCP；`budget=0` 表示不设上限。 |
| 按主题的导出切片 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | 主题限定的 `llms.txt` + 通过 `compile_context` 的 `render_harness_context`。 |

### 增量编译（Phase 4 —— 实验性）

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| 溯源边车（`node_provenance`，首次出现） | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | changed-only 删除的基础；始终记录。 |
| `GraphStore` 删除接口 | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`、`delete_nodes_by_source`（删除溯源集合变空的节点；跨文件概念得以保留）。 |
| `url_resolver` 运行时存储分发 | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`。 |
| `incremental_compile` 标志 | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **默认 OFF / 实验性。** 若干编辑形态已证明字节一致，但多所有者/生产者生命周期仍有缺口；完整编译仍是默认。 |

## 前端重新设计 — 2026 年 4 月

以文档为先的层级式 wiki 取代旧的图谱转储。逐路由导览见 [`docs/frontend-redesign.md`](frontend-redesign.zh.md)，三层模型见 [`docs/architecture.md`](architecture.zh.md)。

### Wiki 层（L2 markdown）

| 功能 | 状态 | 源码 | 文档锚点 |
|---|---|---|---|
| `WikiPageStore`（幂等 body-hash 写入，frontmatter 解析器） | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § 模块地图](architecture.zh.md#wiki--synthesis-l2) |
| `WikiLayerProjector` —— 每个 wiki 层节点一个 md 页面 | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § 流水线](architecture.zh.md#pipeline) |
| `sources/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.zh.md#sources) |
| `concepts/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.zh.md#concepts) |
| `entities/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.zh.md#entities) |
| `papers/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.zh.md#papers) |
| `repos/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.zh.md#repos) |
| `topics/` 页面 | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.zh.md#topics) |
| `questions/` 页面（开放问题） | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.zh.md#questions) |
| `syntheses/` 页面 | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.zh.md#syntheses) |

### 合成种类（L2 → 派生）

`SynthesisProjector` 产出七个确定性模板，并把 `Synthesis` 节点 + `synthesizes` / `summarizes` 边加回图谱。

| 种类 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `pulse`（全局一个，驱动 `/`） | ✅ | `synthesis.py` | 每次编译重建。 |
| `daily_digest` | ✅ | `synthesis.py` | 每个 `data/research/daily/<date>/` 一个。 |
| `weekly` | ✅ | `synthesis.py` | 每个 `data/research/weekly/<iso-week>/` 一个。 |
| `topic` | ✅ | `synthesis.py` | 每个 ≥ 3 篇论文的 `ResearchTopic` / `ApproachFamily` 聚簇一个。 |
| `comparison` | ✅ | `synthesis.py` | 每对在同一任务上竞争的 `ApproachFamily` 一个。 |
| `field_overview` | ✅ | `synthesis.py` | 每个 `ResearchField` 一个。 |
| LLM 升级的摘要（环境变量控制） | ⚠ | 仅钩子 | 启发式基线已交付；`TESSERAE_SYNTHESIS_LLM=1` 钩子仍是存根。 |

### 静态站点路由

| 路由 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `/`（主页，hero pulse） | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | 统计行 + 精选入口 + 近期活动。 |
| `/sources/`、`/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`、`render_source_detail` | |
| `/concepts/`、`/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`、`render_concept_detail` | |
| `/entities/`、`/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`、`render_entity_detail` | |
| `/papers/`、`/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`、`render_paper_detail` | |
| `/repos/`、`/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`、`render_repo_detail` | |
| `/topics/`、`/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`、`render_topic_detail` | |
| `/syntheses/`、`/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`、`render_synthesis_detail` | |
| `/questions/`、`/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`、`render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | 热力图 + 日期列表 + 合成导航栏。 |
| `/timeline/<YYYY-MM-DD>.html`（按日详情） | ⚠ | 暂无 | 热力图单元格暂时链接到该日的 `digest.md` 来源页。Subagent P 正在通过 `StaticSiteBuilder` 接入按日详情页。 |
| `/graph/`（交互式 2D + 3D） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js，悬停提示、边标签、光标锚定缩放。 |
| `/about.html` | ✅ | `pages.py::render_about` | 模式、构建信息。 |

### AI 友好导出

| 产物 | 状态 | 源码 | 目的 |
|---|---|---|---|
| 每页 `<page>.txt` 兄弟文件 | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | 单页的纯文本视图（无导航、无样式）。 |
| 每页 `<page>.json` 兄弟文件 | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`。 |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org 的简短索引。 |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | 所有页面正文，上限 5 MB。 |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`，仅 wiki 层节点。 |
| `graph.json` | ✅ | `__init__.py::write_site` | 完整图谱载荷（含代码节点，供工具使用）。 |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | 调色板 + 页面搜索；仅 wiki 层种类。 |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | 每条发出的路由，`lastmod` 来自 frontmatter。 |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | 最近 30 条合成。 |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | 宽松——允许抓取 + 索引。 |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | 机器可读的站点地图。 |
| `manifest.json` | ✅ | `__init__.py::_manifest` | 每个发出文件的 sha256 + 大小（幂等性 harness）。 |

### 视觉设计 + UX

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| 设计 token（亮 + 暗主题，陶土色强调） | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | `assets/style.css` 中的单一 CSS bundle。 |
| 主题切换（持久化，无闪烁） | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` 存于 `localStorage`，绘制前应用。 |
| 搜索面板（`cmd+k` / `ctrl+k` / `/`） | ✅ | `js.py` | 对 `search-index.json` 的模糊匹配；最近页面列表。 |
| 吸附式右侧 TOC | ✅ | `pages.py` + `tokens.py` | 仅桌面端；移动端为 `<details>` 抽屉。 |
| 带月份 + 星期标签的活动热力图 | ✅ | `components.py::heatmap_svg` | 26 周 SVG，单元格链接到该日的 `digest.md`。 |
| 迷你趋势图（按概念/实体） | ✅ | `components.py::sparkline_svg` | 每周提及数，最近 12 周。 |
| 移动端外壳（抽屉导航栏、底部导航、流式字体） | ✅ | `tokens.py` + `pages.py` | 触摸命中目标 ≥ 44 px。 |
| 页面过渡（120 ms 透明度，prefers-reduced-motion） | ✅ | `tokens.py` | |
| 3D + 2D 图谱视图（悬停、边标签、光标锚定缩放） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js，以 CDN 快照形式内置。 |
| 每页 AI 兄弟文件页脚 | ✅ | `components.py::ai_siblings_footer` | 指向当前页 `.txt` 和 `.json` 的行内链接。 |
| Harness 会话历史页面 | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 显式的 Claude Code/Codex 导入；`/sessions/` 索引和详情页，带 markdown 轮次、左侧轮次导航栏、折叠的工具使用和搜索条目。 |

### 流水线 + CLI

| 功能 | 状态 | 源码 | 备注 |
|---|---|---|---|
| `project compile` 依次调用合成 + wiki + 站点 | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | 重设计计划的 Phase 3。 |
| `project build-site` 独立运行 | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | 读取 `wiki/` + `graph.json`，写出 `site/`。 |
| `project serve` 本地 HTTP | ✅ | `cli.py` | 纯 stdlib 服务器。 |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Worktree 推送到 `gh-pages`；可选通过 `gh` CLI 的 `--enable-pages`。`--build`、`--dry-run`、`--branch`、`--remote`、`--force`。 |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Claude Code/Codex 的入站会话历史；发现是显式的，且限定于项目工作目录。 |
| `project watch` 变更即重建 | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | 独立的轮询监视器：`--interval`、`--debounce`、`--once`、`--paths`、`--quiet`。多来源监督器位于 `project engine`/`daemon` 下（见上下文引擎）。 |
| `project context` —— 编译带引用的上下文文档 | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 支柱 3 头条；见上下文引擎小节。 |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | 文字刷新链 + 监督器循环；见上下文引擎小节。 |

## 既有功能（原样保留）

### CLI 与安装

- ✅ 通过 `pyproject.toml` 的可安装 Python 包。
- ✅ 控制台命令：`tesserae`、`tesserae`、`tesserae_mcp`。
- ✅ 用于 `curl | bash` 安装的 `scripts/install.sh`。
- ✅ 默认可编辑安装，便于快速本地开发。

### 提取

- ✅ 带受控节点/边词汇表的确定性研究笔记提取器。
- ✅ 无需 API key 即可获得更高质量结构化提取的 Claude CLI/OAuth 提取器。
- ✅ 按 glob 和预算限制的选择性 Claude 路由。
- ✅ 面向 Python 项目的确定性开发代码提取器。
- ✅ 带内容哈希和 `--changed-only` 支持的批量摄取。
- ✅ 容忍畸形 UTF-8 的来源读取。

### 图谱治理

- ✅ 受控的 `ResearchNodeType` 列表——现包含 `SYNTHESIS`。
- ✅ 受控的边类型白名单——现包含 `synthesizes`、`summarizes`。
- ✅ 拒绝模式漂移的校验。
- ✅ 别名规范化。
- ✅ 歧义近重复节点的审查队列。
- ✅ 审查决策模板和合并/保持分离的工作流。
- ✅ 由按文件图谱生成的语料趋势摘要。

### 持久化与报告

- ✅ 图 JSON 导出。
- ✅ SQLite 图存储。
- ✅ 可选的 Kuzu 图存储。
- ✅ 带计数、证据覆盖率、孤儿节点、日期分桶、别名密集节点的图报告。
- ✅ 描述从 MegaMem、Graphiti/Zep、MCP 图服务器、agentic RAG 吸收的想法的竞争报告。

### 项目本地工作流

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy`（GitHub Pages）
- ✅ `tesserae sessions discover/import/list`（显式的本地 agent 历史导入）
- ✅ `tesserae export site --watch`（独立的轮询监视器）
- ✅ `tesserae engine`（监督器循环 — v0.5.0）
- ✅ `tesserae refresh`（文字化的 ingest → compile → project 链 — v0.5.0）
- ✅ `tesserae context`（按需上下文编译器 — v0.5.0）
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ 开箱即用的 vault 导出。
- ✅ `.obsidian/app.json` 和图谱设置。
- ✅ Markdown 投影。
- ✅ `raw/assets/` 结构。
- ✅ 带 Dataview 查询的 `_meta/dashboard.md`。

### Agent harness

为以下目标生成文件：

- ✅ Claude Code：`CLAUDE.md`、`.claude/settings.json`
- ✅ Codex：`AGENTS.md`、`mcp.toml`
- ✅ Gemini：`GEMINI.md`、`.gemini/settings.json`
- ✅ Kiro：steering 与 MCP 设置
- ✅ Cursor：项目规则与 MCP 配置
- ✅ OpenCode：`AGENTS.md`、`opencode.json`

### Graphiti / 时间事实

- ✅ 带溯源、时效性、置信度和失效字段的时间事实投影。
- ✅ 无依赖的 Graphiti episode JSONL 导出。
- ✅ 未安装 Graphiti 时的 `sync-graphiti --dry-run` 冒烟。
- ✅ 使用 `graphiti_core` 和 Neo4j 的可选实时同步。

### MCP 服务器

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server`，基于 stdio JSON-RPC。
- ✅ 检索/图工具：`schema`、`graph_summary`、`search_nodes`、`node_context`（带 `use_ppr`）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`、`doctor_report`。
- ✅ 上下文引擎工具（v0.5.0）：`compile_context`、`embedding_status`、`fresh_insights`（按衰减排序）、`list_communities`、`find_session_findings`、`ask`。
- ✅ 设置工具：`tesserae_setup_plan`、`tesserae_setup_apply`。
- ✅ 多项目注册表：`list_projects`、`register_project`、`unregister_project`、`list_sessions`。通过 `url_resolver` 的存储 URL 分发。

## 测试

当前套件覆盖：

- ✅ 本体护栏（含新的 `Synthesis` 节点 + `synthesizes` / `summarizes` 边）；
- ✅ 确定性提取；
- ✅ Claude CLI 包装器的解析/校验；
- ✅ 选择性 Claude 路由；
- ✅ 规范化/审查工作流；
- ✅ 批量摄取；
- ✅ 报告；
- ✅ SQLite/Kuzu 持久化；
- ✅ Graphiti 导出/同步干跑；
- ✅ 项目 CLI 工作流；
- ✅ agent harness 导出；
- ✅ Obsidian 导出；
- ✅ 前端生成 + 链接完整性（无 `nodes/codeclass-*.html`）；
- ✅ wiki 存储幂等性；
- ✅ 合成投影器 golden + 幂等性；
- ✅ 站点组件、页面、导出、相关性；
- ✅ AI 兄弟文件形状（每页 `.txt` + `.json`）；
- ✅ 端到端两次编译幂等性；
- ✅ 引擎主干：pipeline、刷新链、daemon 核心 + 来源、`project engine` CLI；
- ✅ 自我改进记忆：边车、衰减/取代、取代抑制（含 MCP）、强化/矛盾；
- ✅ 检索 + 嵌入：混合搜索、PPR、真实默认嵌入（Phase 6）；
- ✅ 上下文编译器：形状/引用完整性/确定性/预算/PPR 回退、`project context` CLI、MCP `compile_context`；
- ✅ 增量编译（实验性）：差分器、一致性门槛、溯源就绪性、SQLite 溯源；
- ✅ 包安装与安装器契约。
