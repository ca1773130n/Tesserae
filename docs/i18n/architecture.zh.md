# 架构

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae 是一个**上下文引擎**。它从你的项目重建一个自我改进的知识库，并把它作为随取随用的上下文交给智能体。它运行在三大支柱之上：(1) **会话监控**——观察实时的智能体/工作会话，在发现产生的当下捕获它们；(2) **自主、主动的知识摄取**——一条流水线 + 监督循环持续拉取并重新提取知识，主动改进知识库而不是等待指令；(3) **按需文档/上下文**——从同一知识库编译出的用户请求产物。类型化图谱、markdown vault 和静态站点都是知识库的*投影*；引擎则是保持它们新鲜并喂给智能体的循环。

在底层，Tesserae 把一个源材料目录变成受控的类型化知识图谱，并把该图谱经由一个持久的 markdown wiki 层投影为静态的、对 AI 友好的网站。2026 年 4 月的重设计围绕 Karpathy 三层模型重组了投影侧：原始证据保持原始，类型化图谱治理本体，markdown wiki 层位于图谱与任何渲染输出之间。静态站点是那个 wiki 层的*渲染器*，而不是图谱的直接转储，以 [`tesserae/research_graph.py`](../../tesserae/research_graph.py) 中的受控本体为 schema。**v0.5.0** 里程碑（2026 年 6 月）加入了驱动全部三大支柱的引擎主干——见下文的*引擎主干*和*按需上下文编译器*。

## Karpathy 三层模型

Andrej Karpathy 关于 LLM 友好知识库的框架区分了三个层，每层各有自己的持久性保证：

| 层 | 关注点 | 仓库位置 | 所有者 |
|---|---|---|---|
| L1 —— 原始来源 | 用户撰写或采集的字面字节。只追加。 | `data/`、`docs/`，以及 `.tesserae/config.json` 中引用的项目目录树 | 用户 |
| L2 —— Wiki | 带 YAML frontmatter 的类型化 markdown 页面（sources、concepts、entities、papers、repos、topics、syntheses、questions）。幂等：每次编译重新生成，但只有内容哈希变化时才重写。 | `.tesserae/wiki/` | `WikiPageStore`、`WikiLayerProjector`、`SynthesisProjector` |
| L3 —— 渲染层 | 静态 HTML 站点、AI sibling 导出、搜索索引、站点地图、JSON-LD。每次编译整体擦除重写，但重复运行时字节稳定。 | `.tesserae/site/` | `StaticSiteBuilder`（`tesserae/site/`） |

schema 作为一条独立的轴横跨三层：`graph.json` 中的 `ResearchGraph` 是 L2 页面所链接的受控本体，而 [`tesserae/research_graph.py`](../../tesserae/research_graph.py) 中的 `ResearchNodeType` / 边白名单是"到底存在哪些类型"的事实来源。

这次重设计把 L2 显式地拆了出来。2026 年 4 月之前，静态站点直接从 `graph.json` 投影；wiki 层只存在于 Obsidian vault 导出之内。拆分带来了：

- 一个统一的人工可编辑表面（用 Obsidian 或任意 markdown 编辑器打开 `.tesserae/wiki/`）。
- 幂等重建：只要源内容没变，重新运行 `project compile` 产生零文件 diff。
- 一份演化日志：synthesis 页面随时间积累，让项目能够自我叙述。

## 流水线

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

每一步都是增量的。图谱提取器使用 `manifest.json` 内容哈希跳过未变的源文件。当正文哈希与磁盘上已有内容一致时，`WikiPageStore.write_page` 返回 `False`（并跳过写入）。`StaticSiteBuilder` 会擦除并重写 `.tesserae/site/`，但其输出是确定性的——见下文"幂等性故事"。

## 上下文编译器数据流

按需上下文编译器（[`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)）是支柱 3 的头号路径。给定一个查询和/或显式的种子节点 id，`compile_context` 直接从图谱构建一份定制的、带引用的 markdown bundle 并在内存中返回——它不会在 `.tesserae/` 下写任何东西。

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

默认值：`depth=2`、`budget=32000`。确定性的组装（步骤 1–4）是契约；LLM 合成纯属附加。同一条流水线支撑 `project context` CLI 命令、`compile_context` MCP 工具，以及按主题切片的导出（`slice_export_context_for_topic`，主题作用域的 `llms.txt`）。

## 模块地图

### Wiki + 合成（L2）

| 模块 | 职责 |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` dataclass，用于文件系统 I/O 的 `WikiPageStore`。仅用标准库的 YAML 子集 frontmatter 解析器。正文哈希幂等。 |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`：把 `ResearchGraph` 中每个 wiki 层类型的节点映射为正确 `kind/` 目录下的一个 markdown 页面。 |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`：pulse、daily_digest、weekly、topic、comparison、field_overview 的确定性模板。把 `Synthesis` 节点和 `synthesizes` / `summarizes` 边加回图谱。 |

### 图谱 + 本体

| 模块 | 职责 |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` 枚举（含 `SYNTHESIS`）、边类型白名单（含 `synthesizes`、`summarizes`）、校验。 |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | 别名规范化 + 近重复审查队列。 |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | 面向开发切片的确定性 Python AST 提取器。 |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth 选择性提取器。 |

### 站点渲染器（L3）

| 模块 | 职责 |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`：擦除 + 重建站点，遍历每条路由，产出导出 + AI sibling + manifest。 |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | 每条路由一个渲染器（首页、索引、详情页、时间线、图谱、关于）。`SiteContext` 携带预计算的索引，让渲染器保持纯函数。 |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML 原语：`breadcrumbs`、`card`、`badge`、`node_table`、`edge_list`、`sparkline_svg`、`heatmap_svg`、`toc`、`page_shell`、`ai_siblings_footer`。 |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | 设计 token——CSS 变量、明暗双主题、布局、排版，所有组件的样式都在这里。 |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | 客户端 JS bundle：搜索面板、主题切换、sigma + 3D-force 图谱视图。 |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | 仅用标准库的 markdown 渲染器（链接、自动链接、代码、强调、标题）。无外部依赖。 |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | 四信号相关性评分（直接链接、来源重叠、Adamic-Adar、类型亲和度），供每个 `Related` 区块使用。 |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json` 构建器。仅 wiki 层的 kind。 |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 导入的 harness 历史的会话索引/详情渲染器：项目记忆摘要区、对话 turn 侧栏、markdown 转录渲染、折叠的工具调用块。 |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、每页的 `.txt`/`.json` sibling。 |

### 流水线编排

| 模块 | 职责 |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`：驱动提取 → 图谱 → 记忆 pass → wiki 层 → 站点。拥有 `ProjectPaths`（`config`、`graph`、`manifest`、`wiki`、`site` 等）。预先决定一次由 provenance 驱动的增量编译是否可行（由 `incremental_compile` 门控，默认 OFF）。 |
| [`tesserae/cli.py`](../../tesserae/cli.py) | 扁平动词式 CLI 分派（删除遗留的 `project`/`wiki` 子命令组后约 2,732 行）。动词——`init`、`compile`、`ingest`、`context`、`ask`、`query`、`doctor`、`summary`、`decisions`、`refresh`、`serve`、`engine`、`export`、`vault`、`code`、`lab`、`setup`、`config`、`projects`、`sources`、`federation`、`integrations`——以元数据的形式声明在 [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) 中，并从这棵树接线，而不是手工注册。 |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`：经由 worktree 把 `.tesserae/site/` 推送到 `gh-pages` 分支，可选地通过 `gh` 启用 Pages。 |

### 引擎主干（v0.5.0 —— 支柱 1 & 2）

引擎主干是驱动会话监控与自主再摄取的进程内循环。同一个 `Pipeline.run()` 是 CLI、监督守护进程和（之后的）MCP 服务器共同调用的唯一刷新路径。

| 模块 | 职责 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`：顺序执行的步骤运行器。把散文式的刷新链（摄取 → 编译 → 投影/发布）固化为一个可导入的对象，返回结构化的 `List[StepResult]` 而不是打印后退出，让每个调用方自行决定如何呈现结果。`run()` 按步骤捕获 `Exception`（放行 `KeyboardInterrupt`/`SystemExit`），并在首个失败处停止。 |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`：单一所有者的 asyncio 监督者。监视源目录、Obsidian vault 和 harness 会话目录；通过取消并重排的防抖机制，把一阵 `TriggerEvent` 合并为恰好一次 `Pipeline.run()`。复用既有的 `watch.py` / `vault_watch.py` 监视器（不重写它们），写 pidfile，并在进行中的异常后存活。以 `engine`（`--interval`、`--debounce`、`--once`）的形式暴露。 |
| [`tesserae/watch.py`](../../tesserae/watch.py)、[`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | 轮询监视器，由独立的 `export site --watch` 命令和守护进程的源/vault 通道共同复用。 |

### 自我改进内存（v0.5.0 —— 支柱 2）

Phase 5 激活了持久的自我改进。每个节点的可变状态存放在 `node_memory` SQLite 边车中（位于 `.tesserae/sqlite.db` 内），与不可变的 `node_provenance.first_seen_at` 首次出现时间戳（Phase 4 的边车）分开。编译会驱动一组针对图谱的确定性 pass。

| 模块 | 职责 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + 与存储无关的访问器（`read_memory`、`write_memory`、`bump_access`），覆盖 `node_memory` 表——`decay_score`、`last_accessed_at`、`confidence`、`superseded`。没有任何调用点内嵌原始 SQL。 |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`：Ebbinghaus 风格的新鲜度分数（最新 + 访问最多者优先），用于给会话发现排名。 |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass`（**默认开启**）：确定性裁定，把较旧的近重复洞见标记为被较新者取代，并添加一条 `supersedes` 边。 |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`：通过 `discusses` 边把会话洞见链接到它们讨论的代码符号。 |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py)、[`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 基于同一边车的访问强化与矛盾检测辅助函数。 |

复现置信度在输出中是数值化的：时间投影用 `NodeMemoryRow.confidence` 为每条事实盖上 `confidence`（在 SQLite 中为文本，经 `temporal.py` 呈现），只有当不存在已存值时才回退到 `infer_confidence`。

### 检索（v0.5.0 —— 支柱 2 & 3）

| 模块 | 职责 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`：本地优先的混合检索器，通过倒数排名融合（RRF，k=60）融合三条通道——Okapi BM25（k1=1.5、b=0.75）、大小写折叠的词法/FTS 式子串匹配，以及可插拔的嵌入通道。完全确定性。 |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`：HippoRAG-2 风格（arXiv:2502.14802）的图上个性化 PageRank，用于多跳种子扩展——浮现距种子数跳之外、连接良好的节点，而不只是 1 跳邻域。 |
| 嵌入后端（Phase 6，Track B） | 混合嵌入通道的默认后端是无需额外依赖的确定性哈希桶伪嵌入；优先使用 `sentence-transformers`（`all-MiniLM-L6-v2`），在可选依赖已安装时惰性加载。`embedding_status` MCP 工具报告当前激活的后端。 |

### 按需上下文编译器（v0.5.0 —— 支柱 3 头条）

| 模块 | 职责 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`：支柱 3 的头条特性。针对一组查询/种子直接从图谱编译一份定制的、**带引用的**上下文 bundle——见上文*上下文编译器数据流*。返回内存中的 `ContextBundle`（含 `ContextCitation`）；不写磁盘。以 `project context` CLI 命令和 `compile_context` MCP 工具的形式暴露。 |

### 持久化端口 + 图存储

| 模块 | 职责 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` 协议：`upsert_node`/`upsert_edge`、`get_node`、`iterate_nodes`、`query_subgraph`、`find_canonical`，以及 Phase 4 的删除表面——`delete_node` 和 `delete_nodes_by_source`（删除在移除给定源路径后 provenance 集合变空的节点，因此跨文件的概念得以幸存）。 |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`：独立的后备存储；拥有 `node_provenance` 和 `node_memory` 边车表。 |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | 把存储 URL（`sqlite:///…`、`hypepaper-postgres://…`）解析为对应的 `GraphStore`，让 MCP 服务器可以在运行时指向任意后备存储。 |

### 外部适配器（本轮未变）

| 模块 | 职责 |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian vault 投影（图谱着色、Dataview 仪表盘、原始资产）。 |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode harness 导出。 |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | 入站的 Claude Code/Codex 会话发现、归一化、存储到 `.tesserae/harness_sessions/`，以及脱敏的 markdown 摘要。 |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | 时间事实 JSONL + 可选的实时 Graphiti 同步。 |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio 服务器。检索/图谱：`schema`、`graph_summary`、`search_nodes`、`node_context`（带 `use_ppr`）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`、`doctor_report`。上下文引擎（v0.5.0）：`compile_context`（按需上下文编译器）、`embedding_status`、`fresh_insights`（按衰减排名的会话发现）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`。外加 `ask`、多项目注册表工具（`list_projects`、`register_project`、`unregister_project`、`list_sessions`），以及 `tesserae_setup_plan` / `tesserae_setup_apply`。 |

## 项目工作区布局

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/`（L2）

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

每个文件都可手工编辑；只要正文哈希与投影器将要写入的内容不同，下一次编译就会尊重用户编辑。（只编辑正文会获胜；只编辑 frontmatter 会在下次编译时被覆盖，因为 frontmatter 是重新生成的。）Obsidian 用户可以直接打开 `.tesserae/wiki/`；既有的 `obsidian_vault/` 适配器是一个独立投影，不是替代品。

### `.tesserae/site/`（L3）

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## 刻意排除的内容

重设计划下了一条明确的线：code-class 和 code-function 节点留在 `graph.json` 中（因此 MCP 和 Graphiti 消费者仍然看得到它们），但永远不会获得 HTML 页面，永远不会出现在 `search-index.json` 中，也永远不会出现在导航里。这就是面向用户的契约——wiki 是文档优先的知识库，不是函数浏览器。

具体来说，`StaticSiteBuilder` 会跳过类型不在 L2 wiki kind 映射（`tesserae/wiki_projector.py::_KIND_FOR_TYPE`）中的任何节点：

- 从 L2 + L3 中排除：`CodeClass`、`CodeFunction`、`CodeModule`、`Dependency`、`EvidenceSpan`、`SourceFile`，以及所有 `Claim` 变体（`Claim`、`ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim`）。
- 它们仍会出现的表面：在相关 wiki 页面上以列表项、徽章、邻居计数或证据摘录的形式内联出现，以及供下游工具使用的 `graph.json`。

如果你需要代码级浏览，直接把 LSP / 调用图工具指向源码树——那是与"这个项目知道什么的 wiki"不同的问题。

## 幂等性故事

重设计的目标是**在输入不变的情况下，两次连续 `project compile` 运行产生逐字节相同的输出**。构成要素：

1. **源提取**使用 `manifest.json` 内容哈希；未变的文件被跳过，因此图谱保持稳定。
2. **Wiki 层写入**在正文级别幂等。`WikiPageStore.write_page` 读取现有文件、剥离 frontmatter、对正文做 sha256，如果新正文哈希相同就短路——即使新 frontmatter 带着不同的 `generated_at` 时间戳。这是让重建时 git diff 保持紧凑的关键技巧。
3. **Synthesis 输出**在 frontmatter 中携带 `content_hash: sha256-…`。正文哈希的计算不含 `generated_at`，因此对同一图谱的重复编译产生相同哈希，且 `Synthesis` 节点在图谱元数据中携带同样的 `content_hash`。
4. **站点渲染**在 `write_site` 开始时擦除 `site/`，然后确定性地写入：路由排序、字典以 `sort_keys=True` 输出、`manifest.json` 通过 `sorted(rglob("*"))` 遍历。两次运行产生逐字节相同的文件，包括 manifest。

这由 `tests/test_site_pages.py` 和 `tests/test_project_e2e_redesign.py` 中的端到端冒烟测试验证（编译两次、diff 两个站点、期望零文件差异）。

## 规模化备注

- **图谱视图节点上限。** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) 限定了交互式力导向布局页面内嵌的载荷。超过约 1500 个节点后，浏览器侧的模拟在中端硬件上会变得迟缓，因此当数量超限时页面会先丢弃度数最低的 wiki 层节点。导出的 `graph.json` 不受影响——它始终包含完整图谱。代码节点在应用上限之前就被过滤掉了。
- **`llms-full.txt` 上限。** [`tesserae/site/exports.py`](../../tesserae/site/exports.py) 中有一个 5 MB 的安全上限；触顶时文件以 `[TRUNCATED — see graph.jsonld for the full set]` 标记结尾。`graph.jsonld` 不设上限，因为 JSON-LD 消费者期望完整集合。
- **搜索索引。** 仅 wiki 层的 kind。代码图节点永远不会进入 `search-index.json`；重设计目标是 dogfood 语料 < 500 KB，目前远低于该值。
- **每页字节预算（经验法则）。** 每个详情页 < 60 KB gz HTML，共享 CSS < 30 KB，共享 JS < 25 KB，sigma vendor 仅在图谱页（约 60 KB）。图谱视图使用一次性加载的 3D-force-graph + Three.js；其余页面保持原生。
- **dogfood 上的编译时间。** 在较新的开发机上约 300 个 markdown 文件在 5 秒内完成提取；站点渲染再加约 2 秒。wiki 层的幂等性意味着后续编译只触碰变更的路径。

## 前端交互表面

- **搜索面板** —— `cmd+k` / `ctrl+k` / `/`。对 `search-index.json` 做模糊匹配，作用域限于 wiki kind。最近页面持久化在 `localStorage`。
- **主题切换** —— 右上角按钮；`data-theme="dark"` 存储在 `localStorage` 中并在绘制前应用，避免闪烁。
- **粘性右侧 TOC** —— 仅桌面端；移动端折叠为 `<details>` 抽屉。由页面正文中的 `<h2>` / `<h3>` 生成。
- **活动热力图** —— 带月份 + 星期标签的 26 周 SVG。当当天存在 `digest.md` 源页面时，单元格链接到它。（按天的时间线详情页——`/timeline/<YYYY-MM-DD>.html`——是一项明确的后续工作；`render_timeline` 中的内联提示标记了它。⚠ 进行中。）
- **图谱视图** —— `/graph/`。3D 力导向布局（3d-force-graph + Three.js），带悬停提示、边标签、以光标为锚点的缩放，以及 2D 回退视图。节点颜色来自 `ResearchNodeType`。
- **移动端外壳** —— 抽屉侧栏、底部导航、流式排版、触控安全的命中区域（≥ 44 px）。

## 测试策略

- **单元** —— `tests/test_wiki_store.py`、`tests/test_synthesis.py`、`tests/test_site_components.py`、`tests/test_site_pages.py`、`tests/test_site_exports.py`、`tests/test_relevance.py`。
- **引擎主干** —— `tests/test_pipeline.py`、`tests/test_refresh_pipeline.py`、`tests/test_daemon_core.py`、`tests/test_daemon_sources.py`、`tests/test_cli_engine.py`。
- **自我改进内存** —— `tests/test_memory_sidecar.py`、`tests/test_decay_supersede.py`、`tests/test_supersede_suppression.py`、`tests/test_mcp_supersede_suppression.py`、`tests/test_memory_contradiction_reinforce.py`。
- **检索 + 嵌入** —— `tests/test_hybrid_search.py`、`tests/test_ppr.py`、`tests/test_real_embeddings_phase6.py`。
- **上下文编译器** —— `tests/test_context_compiler.py`（形状、引用完整性、确定性、预算、PPR 回退）、`tests/test_cli_context.py`、`tests/test_mcp_server_context.py`。
- **增量编译（实验性）** —— `tests/test_incremental_compile.py`、`tests/test_incremental_parity.py`、`tests/test_provenance_readiness.py`、`tests/test_sqlite_provenance.py`。
- **幂等性** —— `tests/test_project_e2e_redesign.py` 编译两次并断言 `wiki/` 和 `site/` 中零 diff。
- **链接完整性** —— `tests/test_frontend.py` 解析每个产出 HTML 的 href，并断言每个内部链接都解析到一个已生成的文件。不会产出 `nodes/codeclass-*.html`。
- **AI sibling** —— 对每个 `path/foo.html`，测试套件断言 `path/foo.txt` 和 `path/foo.json` 存在；JSON 可解析且包含 `{title, kind, body, links}`。
- **无 Playwright** —— 在 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 下的原生 pytest。

## 相关文档

- [快速入门](quickstart.zh.md) —— 从 `project init` 到可浏览站点的最短路径。
- [前端重设计导览](frontend-redesign.zh.md) —— 每条路由的注解式巡览。
- [功能地图](feature-map.zh.md) —— 已交付、进行中的内容，附文件指针。
- [Self-dogfood 演示](self-dogfood.zh.md) —— 用 Tesserae 索引它自己的仓库。
