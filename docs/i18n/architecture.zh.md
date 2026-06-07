<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
＃ 建筑学

Tesserae 是一个**上下文引擎**。它从你的项目重建一个自我改进的知识库，并将其作为可直接使用的上下文交给智能体。它运行在三大支柱之上：(1) **会话监控** —— 观察实时智能体/工作会话，在事件发生时捕获发现；(2) **自主、主动的知识摄取** —— 流水线 + 监督循环持续拉取并重新提取知识，主动改进知识库而非等待指令；(3) **按需文档/上下文** —— 从同一知识库编译出的用户请求产物。类型化图、Markdown 库（vault）和静态站点都是知识库的*投影*；引擎是保持它们新鲜并喂给智能体的循环。

在底层，Tesserae 将源材料目录转换为受控的、类型化的知识图，并通过持久的 Markdown wiki 层将图转换为静态的、人工智能友好的网站。 2026 年 4 月的重新设计围绕 Karpathy 三层模型重新组织了投影侧：原始证据保持原始状态，类型图控制本体，Markdown wiki 层位于图和任何渲染输出之间。静态站点是该 wiki 层的“渲染器”，而不是图的直接转储，以 [`tesserae/research_graph.py`](../../tesserae/research_graph.py) 中的受控本体作为模式。**v0.5.0** 里程碑（2026 年 6 月）增加了驱动全部三大支柱的引擎主干 —— 参见下文的*引擎主干*和*按需上下文编译器*。

## Karpathy 三层模型

Andrej Karpathy 的 LLM 友好知识库框架区分了三个层，每个层都有自己的耐久性保证：

|层|关注|回购位置 |业主|
|---|---|---|---|
| L1 — 原始来源 |用户创作或收获的文字字节。仅追加。 | `data/`、`docs/`、`.tesserae/config.json` | 中引用的项目树用户|
| L2 — 维基 |使用 YAML frontmatter 键入 Markdown 页面（来源、概念、实体、论文、存储库、主题、综合、问题）。幂等：每次编译都会重新生成，但仅在内容哈希更改时重写。 | `.tesserae/wiki/`| `WikiPageStore`、`WikiLayerProjector`、`SynthesisProjector` |
| L3 — 渲染 |静态 HTML 站点、AI 兄弟导出、搜索索引、站点地图、JSON-LD。擦除并重写每个编译，但在重新运行时字节稳定。 | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

该模式作为一个单独的轴横跨所有三层：`graph.json` 中的 `ResearchGraph` 是 L2 页面链接的受控本体，[`tesserae/research_graph.py`](../../tesserae/research_graph.py) 中的 `ResearchNodeType` / 边缘白名单是根本存在的类型的真相来源。

重新设计明确添加了 L2。 2026 年 4 月之前，静态站点直接从 `graph.json` 投影； wiki 层仅存在于 Obsidian 库导出内部。将其拆分出来给我们：

- 单个人工可编辑界面（在 Obsidian 或任何 Markdown 编辑器中打开 `.tesserae/wiki/`）。
- 幂等重建：重新运行 `project compile` 会产生零文件差异，除非源内容发生更改。
- 演变日志：综合页面随着时间的推移而积累，让项目自行叙述。

## 管道

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

每一步都是渐进的。图形提取器使用 `manifest.json` 内容哈希来跳过未更改的源文件。当主体哈希与磁盘上已有的哈希匹配时，`WikiPageStore.write_page` 返回 `False`（并跳过写入）。 `StaticSiteBuilder` 擦除并重写了 `.tesserae/site/`，但其输出是确定性的 - 请参阅下面的“幂等性故事”。

## 上下文编译器数据流

按需上下文编译器（[`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)）是支柱 3 的标志性路径。给定一个查询和/或显式种子节点 id，`compile_context` 直接从图构建一份量身定制的、**带引用的** Markdown 包并在内存中返回 —— 它不会向 `.tesserae/` 写入任何内容。

```
query / seeds
     │
     ▼  1. 种子解析
        显式种子（仅当存在于图中时保留）+ hybrid_search() 命中，去重，稳定顺序
     │
     ▼  2. PPR 扩展
        retrieval.ppr.personalized_pagerank 对深度受限的 k 跳邻域排名；
        若结果为空（种子不连通）→ 回退到种子顺序（包永不为空）
     │
     ▼  3. 预算受限的选择
        沿 PPR 顺序遍历，包含每个节点的引用正文，直到下一段正文将超出
        `budget` 字符（budget <= 0 = 不限；在词边界处插入超预算标记）
     │
     ▼  4. 带引用的 Markdown 组装
        每个选中节点一节 + 末尾的 `## Citations` 块。
        正文优先采用投影的 wiki 页面（当存在 store 与公共 wiki 类型时），
        否则用节点描述，再否则用最小桩。无 LLM 的正文不嵌入任何挂钟
        时间戳 → 对相同的 (graph, query, seeds, depth, budget) 字节相同。
     │
     ▼  5. 可选的 LLM 综合  （仅当 synthesize=true 且设置了 ANTHROPIC_API_KEY 时）
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

默认值：`depth=2`、`budget=32000`。确定性组装（步骤 1–4）是契约；LLM 综合纯属附加。同一流水线支撑 `project context` CLI 命令、`compile_context` MCP 工具，以及主题范围的导出切片（`slice_export_context_for_topic`、主题范围的 `llms.txt`）。

## 模块图

### Wiki + 综合（L2）

|模块|责任|
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` 数据类，`WikiPageStore` 用于文件系统 I/O。仅标准库 YAML 子集 frontmatter 解析器。主体哈希幂等性。 |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`：将 wiki 层类型的每个 `ResearchGraph` 节点映射到右侧 `kind/` 文件夹中的 Markdown 页面。 |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`：脉冲、每日摘要、每周、主题、比较、字段概述的确定性模板。将 `Synthesis` 节点和 `synthesizes` / `summarizes` 边添加回图中。 |

### 图+本体

|模块|责任|
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` 枚举（包括 `SYNTHESIS`）、边缘类型白名单（包括 `synthesizes`、`summarizes`）、验证。 |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) |别名规范化+近乎重复的审核队列。 |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) |用于开发切片的确定性 Python AST 提取器。 |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth 选择性提取器。 |

### 站点渲染器（L3）

|模块|责任|
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`：擦除+重建站点，走每条路线，发出导出+AI兄弟+清单。 |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) |每条路线一个渲染器（主页、索引、详细信息页面、时间线、图表、关于）。 `SiteContext` 带有预先计算的索引，因此渲染器保持纯净。 |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML 基元：`breadcrumbs`、`card`、`badge`、`node_table`、`edge_list`、`sparkline_svg`、`heatmap_svg`、`toc`、`page_shell`、`ai_siblings_footer`。 |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) |设计标记 — CSS 变量、浅色 + 深色主题、布局、版式、所有组件均在此处设置样式。 |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) |客户端 JS 捆绑包：搜索调色板、主题切换、sigma + 3D-force 图形视图。 |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) |仅标准库 Markdown 渲染器（链接、自动链接、代码、强调、标题）。没有外部依赖。 |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) |每个 `Related` 部分使用的四信号相关性评分（直接链接、源重叠、Adamic-Adar、类型亲和力）。 |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json` 建造者。仅限 Wiki 层类型。 |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) |用于导入线束历史记录的会话索引/详细渲染器：项目内存摘要部分、对话转轨、Markdown 转录渲染和折叠的工具使用块。 |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、每页 `.txt`/`.json` 兄弟姐妹。 |

### 管道编排

|模块|责任|
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`：驱动提取→图表→内存遍历→wiki层→站点。拥有`ProjectPaths`（`config`、`graph`、`manifest`、`wiki`、`site`等）。预先决定是否符合基于来源（provenance）的增量编译条件（由 `incremental_compile` 控制，默认 OFF）。 |
| [`tesserae/cli.py`](../../tesserae/cli.py) | 扁平动词式 CLI 调度（删除遗留的 `project`/`wiki` 子命令组后约 2,732 行）。动词 —— `init`、`compile`、`context`、`ask`、`refresh`、`serve`、`engine`、`export`、`vault`、`code`、`lab`、`config`、`projects`、`integrations` —— 在 [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) 中以元数据声明，并从该树自动接线，而非手动注册。 |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`：通过工作树将 `.tesserae/site/` 推送到 `gh-pages` 分支，可以选择通过 `gh` 启用页面。 |

### 引擎主干 (v0.5.0 —— 支柱 1 & 2)

引擎主干是驱动会话监控与自主重新摄取的进程内循环。同一个 `Pipeline.run()` 是 CLI、监督守护进程以及（之后的）MCP 服务器都调用的单一刷新路径。

| 模块 | 职责 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`：顺序步骤运行器。把散文式刷新链（摄取 → 编译 → 投影/发布）固化为一个可导入对象，返回结构化的 `List[StepResult]` 而非打印后退出，使每个调用方自行决定如何呈现结果。`run()` 对每步捕获 `Exception`（放过 `KeyboardInterrupt`/`SystemExit`），并在首次失败处停止。 |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`：单一所有者的 asyncio 监督器。监视源目录、Obsidian 库以及 harness 会话目录；通过取消并重新调度的去抖，将一批 `TriggerEvent` 合并为恰好一次 `Pipeline.run()`。复用现有的 `watch.py` / `vault_watch.py` 监视器（不重写它们），写入 pidfile，并在执行中异常下存活。通过 `engine`（`--interval`、`--debounce`、`--once`）暴露。 |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | 独立的 `export site --watch` 命令与守护进程的源/库通道共同复用的轮询监视器。 |

### 自我改进内存 (v0.5.0 —— 支柱 2)

阶段 5 激活了持久的自我改进。每节点的可变状态存放在 `node_memory` SQLite 边车（位于 `.tesserae/sqlite.db` 内部），与不可变的 `node_provenance.first_seen_at` 首见时间戳（阶段 4 边车）分离。编译会对图驱动一组确定性遍历。

| 模块 | 职责 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + 对 `node_memory` 表的与存储无关的访问器（`read_memory`、`write_memory`、`bump_access`）—— `decay_score`、`last_accessed_at`、`confidence`、`superseded`。没有调用点内嵌原始 SQL。 |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`：艾宾浩斯式新鲜度评分（最新 + 最常访问优先），用于对会话发现排名。 |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass`（**默认开启**）：确定性裁决，将较旧的近重复洞见标记为被较新者取代，并添加 `supersedes` 边。 |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`：通过 `discusses` 边将会话洞见连接到它们论及的代码符号。 |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 针对同一边车的访问强化与矛盾检测助手。 |

复现置信度在输出中是数值化的：时间投影从 `NodeMemoryRow.confidence`（SQLite 中为文本，经 `temporal.py` 呈现）为每个事实的 `confidence` 打标，仅当无存储值时才回退到 `infer_confidence`。

### 检索 (v0.5.0 —— 支柱 2 & 3)

| 模块 | 职责 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`：本地优先的混合检索器，通过倒数排名融合（RRF，k=60）融合三条通道 —— Okapi BM25（k1=1.5、b=0.75）、大小写无关的词法/FTS 式子串匹配，以及一条可插拔的嵌入通道。完全确定性。 |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`：对图执行 HippoRAG-2 式（arXiv:2502.14802）个性化 PageRank，用于多跳种子扩展 —— 表面化离种子数跳但连接良好的节点，而不仅是 1 跳邻域。 |
| 嵌入后端 (阶段 6, Track B) | 混合嵌入通道的默认后端是一个无需额外依赖的确定性哈希桶伪嵌入；当安装了可选依赖时，优先使用 `sentence-transformers`（`all-MiniLM-L6-v2`）并惰性加载。`embedding_status` MCP 工具报告当前活动的后端。 |

### 按需上下文编译器 (v0.5.0 —— 支柱 3 标志)

| 模块 | 职责 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`：支柱 3 的标志性功能。直接从图为查询/种子集编译一份量身定制的、**带引用的**上下文包 —— 参见下文*上下文编译器数据流*。返回内存中的 `ContextBundle`（含 `ContextCitation`）；不向磁盘写入任何内容。通过 `project context` CLI 命令和 `compile_context` MCP 工具暴露。 |

### 持久化端口 + 图存储

| 模块 | 职责 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` 协议：`upsert_node`/`upsert_edge`、`get_node`、`iterate_nodes`、`query_subgraph`、`find_canonical`，以及阶段 4 的删除面 —— `delete_node` 和 `delete_nodes_by_source`（删除在移除给定源路径后来源集变空的节点，因此跨文件概念得以存活）。 |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`：独立的后端存储；拥有 `node_provenance` 和 `node_memory` 边车表。 |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | 将存储 URL（`sqlite:///…`、`hypepaper-postgres://…`）解析到正确的 `GraphStore`，让 MCP 服务器在运行时指向任意后端存储。 |

### 外部适配器（本轮不变）

|模块|责任|
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian Vault 投影（图形着色、Dataview 仪表板、原始资产）。 |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude 代号/Codex/Gemini/Kiro/Cursor/OpenCode 线束出口。 |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) |入站 Claude 代码/Codex 会话发现、标准化、`.tesserae/harness_sessions/` 下的存储以及经过编辑的降价摘要。 |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) |时间事实 JSONL + 可选的实时 Graphiti 同步。 |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | Cognee 节点/边缘 JSONL 捆绑和直接添加/认知路径。 |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio 服务器。检索/图：`schema`、`graph_summary`、`search_nodes`、`node_context`（含 `use_ppr`）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`。上下文引擎（v0.5.0）：`compile_context`（按需上下文编译器）、`embedding_status`、`fresh_insights`（按衰减排名的会话发现）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`。此外还有 `ask`、多项目注册表工具（`list_projects`、`register_project`、`activate_project`、`unregister_project`、`list_sessions`）以及 `tesserae_setup_plan` / `tesserae_setup_apply`。 |

## 项目工作区布局

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store；同时拥有 node_provenance（首见，阶段 4）
                              与 node_memory（衰减 / 置信度 / 被取代，阶段 5）边车表
  temporal_facts.jsonl        Graphiti-style temporal projection（数值化复现置信度）
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

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

每个文件都可以手动编辑；只要主体哈希与投影仪写入的内容不同，下一次编译就会尊重用户编辑。 （仅编辑正文获胜；编辑 frontmatter 会在下次编译时失败，因为 frontmatter 会重新生成。） Obsidian 用户可以直接打开 `.tesserae/wiki/`；现有的`obsidian_vault/`适配器是一个单独的投影，而不是替代品。

### `.tesserae/site/` (L3)

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

## 故意排除的内容

重新设计划出了一条明确的界限：代码类和代码功能节点保留在 `graph.json` 中（因此 MCP、Cognee 和 Graphiti 消费者仍然可以看到它们），但永远不会获得 HTML 页面，永远不会出现在 `search-index.json` 中，也永远不会出现在导航中。这就是面向用户的契约——wiki 是一个文档优先的知识库，而不是一个函数浏览器。

具体来说，`StaticSiteBuilder` 会跳过类型不在 L2 wiki 类型映射 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) 中的任何节点：

- 从 L2 + L3 中排除：`CodeClass`、`CodeFunction`、`CodeModule`、`Dependency`、`EvidenceSpan`、`SourceFile`、所有 `Claim` 变体（`Claim`、`ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim`）。
- 在它们仍然出现的地方进行表面处理：作为项目符号、徽章、邻居计数或相关 wiki 页面上内联的证据摘录，以及下游工具的 `graph.json` 中的内容。

如果您需要代码级浏览，请直接将 LSP / 调用图工具指向源树 - 这与“该项目所知道的内容的 wiki”是不同的问题。

## 幂等性故事

重新设计的目标是**在未更改的输入上连续两次运行 `project compile` 时获得字节相同的输出**。作品：

1. **源提取**使用`manifest.json`内容哈希；未更改的文件将被跳过，因此图表保持稳定。
2. **Wiki 层写入** 在主体级别是幂等的。 `WikiPageStore.write_page` 读取现有文件，剥离 frontmatter，对正文进行 sha256，如果新正文散列相同，则短路 - 即使新 frontmatter 具有不同的 `generated_at` 时间戳。这是在重建时保持 git diff 紧密的关键技巧。
3. **合成输出** 在其前面带有 `content_hash: sha256-…`。主体哈希值是在没有 `generated_at` 的情况下计算的，因此在同一图上重复编译会产生相同的哈希值，并且 `Synthesis` 节点在图元数据中携带相同的 `content_hash`。
4. **站点渲染**在`write_site`的开头擦除`site/`，然后确定性地写入：路线已排序，字典用`sort_keys=True`转储，`manifest.json`通过`sorted(rglob("*"))`行走。两次运行会生成字节相同的文件，包括清单。

这是由 `tests/test_site_pages.py` 和 `tests/test_project_e2e_redesign.py` 中的端到端烟雾验证的（编译两次，差异站点，预计文件增量为零）。

## 缩放注释

- **图形视图节点上限。** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) 限制交互式力布局的页面嵌入有效负载。超过约 1500 个节点，浏览器端模拟在中档硬件上会变得缓慢，因此当计数超过上限时，页面首先删除最低度的 wiki 层节点。导出的 `graph.json` 不受影响 - 它始终包含完整的图表。在应用上限之前，代码节点会被过滤掉。
- **`llms-full.txt` 上限。** 5 MB 安全上限适用于 [`tesserae/site/exports.py`](../../tesserae/site/exports.py)；如果击中上限，文件将以 `[TRUNCATED — see graph.jsonld for the full set]` 标记结束。 `graph.jsonld` 没有上限，因为 JSON-LD 消费者期望全套。
- **搜索索引。** 仅限 Wiki 层类型。代码图节点永远不会进入 `search-index.json`； Dogfood 语料库的重新设计目标是 < 500 KB，而我们今天远远低于这个目标。
- **每页字节预算（经验法则）。** 每个详细信息页面 < 60 KB gz HTML，共享 CSS < 30 KB，共享 JS < 25 KB，仅图表页面上的 sigma 供应商 (~60 KB)。图形视图使用3D-force-graph + Three.js加载一次；所有其他页面保持原样。
- **dogfood 上的编译时间。** 在最近的开发机器上，不到 5 秒即可提取约 300 个 Markdown 文件；站点渲染又增加了约 2 秒。 wiki 层的幂等性意味着后续编译仅涉及更改的路径。

## 前端交互界面

- **搜索调色板** — `cmd+k` / `ctrl+k` / `/`。 `search-index.json` 上的模糊匹配，范围为 wiki 类型。最近的页面保留在 `localStorage` 中。
- **主题切换** — 右上角按钮； `data-theme="dark"`储存在`localStorage`中并在油漆前涂抹以避免飞边。
- **粘性右侧目录** — 仅限桌面；折叠到移动设备上的 `<details>` 抽屉中。从页面正文中的 `<h2>` / `<h3>` 生成。
- **活动热图** — 26 周 SVG，带有月份 + 工作日标签。如果存在，单元格会链接到当天的 `digest.md` 源页面。 （每日时间线详细信息页面 - `/timeline/<YYYY-MM-DD>.html` - 是明确的后续行动；`render_timeline` 中的内联通知对其进行了标记。⚠ 正在进行中。）
- **图表视图** — `/graph/`。 3D 力布局（3d-force-graph + Three.js），带有悬停工具提示、边缘标签、光标锚定缩放和 2D 后备视图。节点颜色来自`ResearchNodeType`。
- **移动外壳** — 抽屉导轨、底部导航、流体类型、触摸安全命中目标（≥ 44 像素）。

## 测试策略

- **单位** — `tests/test_wiki_store.py`、`tests/test_synthesis.py`、`tests/test_site_components.py`、`tests/test_site_pages.py`、`tests/test_site_exports.py`、`tests/test_relevance.py`。
- **引擎主干** — `tests/test_pipeline.py`、`tests/test_refresh_pipeline.py`、`tests/test_daemon_core.py`、`tests/test_daemon_sources.py`、`tests/test_cli_engine.py`。
- **自我改进内存** — `tests/test_memory_sidecar.py`、`tests/test_decay_supersede.py`、`tests/test_supersede_suppression.py`、`tests/test_mcp_supersede_suppression.py`、`tests/test_memory_contradiction_reinforce.py`。
- **检索 + 嵌入** — `tests/test_hybrid_search.py`、`tests/test_ppr.py`、`tests/test_real_embeddings_phase6.py`。
- **上下文编译器** — `tests/test_context_compiler.py`（形态、引用完整性、确定性、预算、PPR 回退）、`tests/test_cli_context.py`、`tests/test_mcp_server_context.py`。
- **增量编译（实验性）** — `tests/test_incremental_compile.py`、`tests/test_incremental_parity.py`、`tests/test_provenance_readiness.py`、`tests/test_sqlite_provenance.py`。
- **幂等性** — `tests/test_project_e2e_redesign.py` 编译两次并断言 `wiki/` 和 `site/` 中的差异为零。
- **链接完整性** — `tests/test_frontend.py` 解析每个发出的 HTML 的 href，并断言每个内部链接解析为生成的文件。 `nodes/codeclass-*.html`没有生产。
- **AI 兄弟** — 对于每个 `path/foo.html`，测试套件断言 `path/foo.txt` 和 `path/foo.json` 存在； JSON 解析并包含 `{title, kind, body, links}`。
- **没有剧作家** - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 下的普通 pytest。

## 相关文档

- [快速入门](quickstart.zh.md) — 从 `project init` 到可浏览站点的最小路径。
- [前端重新设计演练](frontend-redesign.zh.md) — 每条路线的注释导览。
- [功能地图](feature-map.zh.md) — 已发布的内容、正在进行的内容以及文件指针。
- [Self-dogfood 演示](self-dogfood.zh.md) — 针对自己的存储库运行 Tesserae。
