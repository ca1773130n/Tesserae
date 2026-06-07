# 前端重新设计 — 带注释的路由导览

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
本文档是对重新设计后的 Tesserae 静态站点中每个可见路由的导览。它补充了 [`architecture.md`](architecture.zh.md) 中的高层模型，以及 [`feature-map.md`](feature-map.zh.md) 中的状态表。

执行 `tesserae compile` 后，站点位于 `.tesserae/site/`。要在本地浏览：

```bash
tesserae serve --port 8765
# open http://127.0.0.1:8765/
```

每个路由都是一个静态 HTML 文件，并带有两个供 AI 消费者使用的同级文件（`<page>.txt`、`<page>.json`）。站点级 AI 导出（`llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、`manifest.json`）在本文末尾说明。

状态图例：✅ 已发布 · ⚠ 进行中。

## 所有页面的通用约定

每个叶子页面都遵循相同结构（设计规范 §3.3）：

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

站点级外壳：

- **顶部栏。** 左侧是 Logo + 项目名，右侧是搜索触发器 + 主题切换。
- **左侧栏**（桌面端 ≥ 1024 px）：层级树 — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About。数量来自 `manifest.json`。
- **底部导航**（移动端）：抽屉侧栏折叠；底部导航展示最常用的类别。
- **搜索面板。** `cmd+k` / `ctrl+k` / `/` — 基于 `search-index.json` 做模糊匹配，并按 wiki 类别限定范围。最近页面保存在 `localStorage` 中。
- **主题。** 默认浅色；切换后会把 `data-theme="dark"` 持久化到 `localStorage`。为避免闪烁，会在绘制前应用。

## 首页

### `/` ✅

> _截图：home.png_

首页是项目脉搏页。它由全局 `pulse` 综合（`wiki/syntheses/pulse.md`）驱动，并在每次编译时重新生成。Hero 区域是一排统计数据 — sources、concepts、papers、open questions — 后面跟着 1–3 张从最新 `pulse` 正文中抽取的“本周新内容”卡片。Hero 下方的精选入口会链接到每种类别的索引页，因此首次访问者无需阅读导航也能深入浏览。

这是最适合让 LLM agent 首先落地的页面；它提供了整个语料中信噪比最高的摘要。卡片链接到叶子页面，而不是回到索引页。

**值得注意的交互。** 点击统计行会滚动到或导航到对应类别索引。Hero 文案可编辑 — 如果手写 `wiki/overview.md`，首页会在下一次编译时采用它。

**相关路由。** 活动日志见 [Timeline](#timeline)，长篇内容见 [Syntheses](#syntheses)，空间视图见 [Graph](#graph-view)。

## Sources

这些是 L1 原始文档 — `.tesserae/config.json` 引用的 `data/`、`docs/` 和项目树中的文件。每个 source 都会成为一个 `SourceDocument`（或 `Paper` / `Repository`）节点，并由 `WikiLayerProjector` 投影为 wiki 页面。

### `/sources/` ✅

> _截图：sources-index.png_

语料已知的所有原始文档表。列包括：类型徽章（Document / Paper / Repository / Project）、标题、提及次数、最后更新时间。表格使用斑马纹；悬停时显示一行预览；类型徽章可通过搜索面板（`/ kind:papers`）筛选。

当 agent 需要枚举构建 wiki 所依据的字面证据时，这就是它的索引。

**相关路由。** 仅论文切片见 [Papers](#papers)，仅仓库见 [Repos](#repos)，抽取视角见 [Concepts](#concepts)。

### `/sources/<slug>.html` ✅

> _截图：source-detail.png_

单个 source 文档，通过 stdlib markdown 管线（`tesserae/site/markdown.py`）渲染。正文是原始 markdown，并带有安全的链接/图片渲染。正文下方包括：

- **Mentions** — 从该 source 中抽取的所有 concept / entity / paper，并带 edge 类型徽章。
- **Backlinks** — 链接到这里的所有其他 wiki 页面。
- **Related** — 四信号排序（direct link 3.0 + source overlap 4.0 + Adamic-Adar 1.5 + type affinity 1.0）。
- **Source provenance** — 原始文件路径 + 原始文件前 12 行作为证据。
- **Activity** — 最近 12 周每周提及的 sparkline。
- **AI siblings footer** — `<page>.txt` 纯文本视图，`<page>.json` 结构化记录。

**值得注意的交互。** 正文中的 URL 和 arXiv ID 会自动链接；代码块可点击复制；右侧栏 TOC 会跟随滚动。

## Concepts

Concepts 是贯穿语料反复出现的想法、术语、算法、架构和方法论。范围包括 `Concept`、`TechnicalTerm`、`Algorithm`、`MathematicalConcept`、`MethodologicalConcept`、`ArchitecturePattern`、`TrainingParadigm`、`InferenceStrategy`、`EvaluationProtocol`、`Task`、`Capability`、`ObjectiveFunction`。

### `/concepts/` ✅

> _截图：concepts-index.png_

一个可按 facet 筛选的卡片网格。每张卡片包含类型徽章、标题、一行定义、提及次数和最后更新时间。页面支持通过标签 chip（Algorithm, Architecture, Training paradigm, …）按类型筛选，并默认按提及次数排序。

当你想问“这个语料在讨论什么？”时，就到这里。

**相关路由。** [Papers](#papers) — concepts 通常在论文中引入；[Topics](#topics) — concepts 会聚类成 topics。

### `/concepts/<slug>.html` ✅

> _截图：concept-detail.png_

一个丰富的 concept 页面，包含综合定义（如果没有综合，则使用最权威 source 的第一段）、语料中的全部提及列表、排序后的相关邻居，以及 activity sparkline。

“Mentions in the corpus”部分对 agent 最关键 — 它列出所有引用该 concept 的 paper / source / repo，并附上一行摘录提供上下文。

**值得注意的交互。** 右侧栏 TOC 跟踪正文中的 `<h2>` / `<h3>`；相关卡片网格遵循四信号分数，因此邻居会显得相关，而不只是相邻。

## Entities

Entities 是语料中具名且可识别的对象：`Model`、`Dataset`、`Benchmark`、`Metric`、`Organization`、`Person`。它们从 graph nodes 投影而来，页面强调 claims 和 results，而不是普通叙述。

### `/entities/` ✅

> _截图：entities-index.png_

按类型分面的表格。列包括：类型徽章、名称、摘要（如果存在 frontmatter `description`，则取其第一句，否则取正文第一段）、提及次数。可通过类型 chip 筛选。

### `/entities/<slug>.html` ✅

> _截图：entity-detail.png_

详情页突出三个部分：

- **Claims** — 与该 entity 相连的 `ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim` edges，并内联显示证据摘录。（Claim 节点本身没有自己的 URL — 它们在这里以 bullet 形式呈现。）
- **Reported results** — 与该 entity 相关的所有 `Result` / `evaluated_on` / `reports_result`，并列出 metric + score + paper provenance。
- **Mentions in the corpus** — 形状与 concept 页面相同。

当 agent 需要回答“我们对模型 X 了解什么？”或“benchmark Y 是在哪些 datasets 上报告的？”时，这是合适的落地页。

## Papers

Papers 是作为一等证据处理的研究文献。重新设计把它们从通用 source 池中移出，并赋予专门类别，以便渲染论文特有的功能。

### `/papers/` ✅

> _截图：papers-index.png_

带有年份、topic 和 family chips 的可分面筛选卡片网格。每张卡片包含：标题、作者（前三位 + “et al.”）、一行 abstract 摘录、年份徽章、提及次数。默认按年份降序排序。

### `/papers/<slug>.html` ✅

> _截图：paper-detail.png_

论文卡片布局：标题、作者、年份、abstract 摘录，然后是：

- **Contributions** — 来自论文的 `ContributionClaim` edges。
- **Results** — 带 metric + score 的 `reports_result` edges。
- **Comparisons** — `compares_against` edges。
- **Related concepts** — `introduces` / `extends` / `uses` edges。
- **Open questions** — 通过论文链接的 `OpenQuestion`。

ArXiv 链接会通过 `tesserae/site/markdown.py` 自动链接；右侧栏 TOC 反映上面的 section 列表。

## Repos

Repos 是软件项目 — `Repository`、`Project`、`CodeProject`。重新设计明确不渲染每个 class / function 的 HTML 页面；repo 页面总结项目表面，并链接到 source tree。

### `/repos/` ✅

> _截图：repos-index.png_

带语言徽章的卡片网格。每张卡片包含：名称、一行 README 摘录、主要语言、已知时的 star 数、最后更新时间。

### `/repos/<slug>.html` ✅

> _截图：repo-detail.png_

Repo 页面显示：

- **README excerpt** — 如果语料中有该 repo 的 `README.md`，则取前约 600 个字符。
- **Dependencies** — 指向其他 repos / models / concepts 的 `depends_on` / `imports` / `uses` 类型出边。
- **Implements** — 来自 papers 的 `implemented_in` edges（即“这个 repo 实现了论文 X”）。
- **Module overview** — modules / classes / functions 的计数，但没有每个 function 的链接 — 这是设计选择。
- **Related** — 四信号排序。

当 agent 需要从一族方法中选择一个 repo 时，这是合适的页面。

## Topics

Topics 把 concepts 归入更广泛的领域：`ResearchField`、`ResearchTopic`、`ProblemArea`、`ApproachFamily`、`Trend`。Topic 页面一部分从 graph nodes 投影，一部分由 synthesis 生成 — 驱动 topic intro 的领域概览页面见 [Syntheses](#syntheses)。

### `/topics/` ✅

> _截图：topics-index.png_

以类型 chip 为键的卡片网格。每张卡片展示 topic 名称、父领域，以及“X papers · Y concepts · Z repos”统计。

### `/topics/<slug>.html` ✅

> _截图：topic-detail.png_

Topic 页面以 synthesis 段落开头（当 `wiki/syntheses/topic-<slug>.md` 中存在时），并列出：

- **Papers in this topic** — 表格。
- **Approach families** — 类型为 `ApproachFamily` 的子 topics。
- **Concepts in scope** — chip cloud。
- **Open questions** — 链接的 `OpenQuestion` nodes。
- **Related fields** — `belongs_to` / `rising_in` 邻居。

**相关路由。** 长篇叙事见 [Syntheses → topic-…](#syntheses)，组成原子见 [Concepts](#concepts)。

## Syntheses

Syntheses 是由 `SynthesisProjector` 生成的高阶页面。七种类型（pulse, daily_digest, weekly, topic, comparison, field_overview）覆盖语料的时间和结构切面。当前 synthesis 正文是确定性模板；`TESSERAE_SYNTHESIS_LLM=1` 是 LLM 升级钩子（stub）。

### `/syntheses/` ✅

> _截图：syntheses-index.png_

索引按 kind 对所有 synthesis 分组列出，并在每组内按 `generated_at` 降序排序。每行包含：kind 徽章、标题、一行 lead、generated-at timestamp。

### `/syntheses/<slug>.html` ✅

> _截图：synthesis-detail.png_

Synthesis 页面按原样渲染 markdown 正文。Frontmatter 包含 `synthesis_kind`、`slug`、`sources`、`inputs`、`generated_at`、`generator`、`content_hash` — 页面在 eyebrow 中暴露 `synthesis_kind` 和 `generated_at`。正文下方包括：

- **Sources consumed** — `summarizes` edge targets — synthesis 使用的每个 source 各一项。
- **Inputs (graph nodes)** — `synthesizes` edge targets — 所有输入节点。
- **Related syntheses** — 同一 kind / 重叠 inputs。

`pulse` synthesis 是首页；daily / weekly syntheses 锚定 [Timeline](#timeline) 侧栏。

## Questions

Open questions 作为 `OpenQuestion` nodes 从语料中抽取 — 即 paper、source 或 synthesis 明确标出未解决问题的地方。

### `/questions/` ✅

> _截图：questions-index.png_

列表视图，每个 open question 一行。列包括：question text、提出它的 sources、related concepts、age（自首次出现以来）。默认按新近程度排序。

### `/questions/<slug>.html` ✅

> _截图：question-detail.png_

聚焦单个 open question 的页面，包含：

- 原始问题文本。
- **Raised in** — 出现该问题的 sources / papers / syntheses。
- **Related concepts** — 该问题讨论的内容。
- **Adjacent questions** — 同一 source 或共享 concepts 的问题。

当 agent 被问到“这个领域还有什么没有回答？”时，这是合适的落地页。

## Sessions

Sessions 是导入的本地 AI-harness transcripts，会被规范化到 `.tesserae/harness_sessions/`，再渲染为可搜索的项目记忆。导入需要通过 `tesserae sessions discover --import` 或 `tesserae sessions import ...` 显式执行；普通站点构建只消费已经规范化的记录。

### `/sessions/` ✅

> _截图：sessions-index.png_

Sessions 索引对项目的顶层 Claude Code 和 Codex sessions 分组。卡片/表格展示 title、harness、model、project path、start/end timestamps、message count、tool count、已知时的 token counts、files touched、commands 和 preview text。该页面从全局侧栏、首页 Browse cards，以及 kind 为 `session` 的搜索面板条目链接而来。

### `/sessions/<project>/<session>.html` ✅

> _截图：session-detail.png_

Session 详情页使用共享 shell，而不是原始 transcript dump。布局包括 hero、stat strip、High-Level Summary、Timeline & size、decisions/files/commands/tools/errors、折叠的 subagent tree，以及逐轮 conversation block。

Session 专用左侧栏用 user/assistant turn anchors（`#turn-N`）替代通用 file-tree rail。User 和 assistant turns 通过站点 markdown renderer 渲染；inline code、command/tag markup、paths、filenames、hashtags 等语义表面会变成紧凑 chips。Tool calls 折叠在前一个 assistant turn 之下，并在 light 和 dark themes 中都使用深色 code/tool backgrounds。

当前详情排版让普通 conversation prose 保持 8 px，通用 conversation code fences 为 10 px，bash/shell fenced code content 为 11 px，tool details/summary 为 10 px，tool headers 为 8 px，tool payload text 为 6 px。Selector map 和发布隐私清单见 [`session-history.md`](session-history.zh.md)。

## Timeline

Timeline 页面是活动日志：语料何时增长、添加了哪些类型的 nodes、按天和周看起来如何？

### `/timeline/` ✅

> _截图：timeline.png_

页面有三条 rail：

- **Activity heatmap** — 26 周 SVG，带月份 + 星期标签，单元格按 node-add-count 着色。如果某天存在 `digest.md` source page，该单元格会链接过去。
- **Days** — 最近 60 个日期，每行显示 `<date> · X activity · Y papers`。如果该日期有 `digest.md`，日期就是链接。
- **Syntheses rail** — 所有 synthesis 按新近排序，显示 kind badge + title + timestamp。

这是适合为“最近发生了什么”问题收藏的页面。

### `/timeline/<YYYY-MM-DD>.html` ✅

> _截图：timeline-day.png_

每日详情页 — 列出与该日历日相关的所有 paper / repo / concept / synthesis — 现已上线。`render_timeline_day`（`tesserae/site/pages.py`）通过 `StaticSiteBuilder`（`tesserae/site/__init__.py`）按日期逐日 emit，每个页面都作为 `timeline_day` 路由记录在 `manifest.json` 中。heatmap cells 直接链接到对应的每日页面（仅当不存在每日路由时才回退到当天的 `digest.md` source page）。

## Graph view

### `/graph/` ✅

> _截图：graph.png_

交互式 graph view 是 3D force layout（3d-force-graph + Three.js，以 CDN snapshot 形式 vendored 到 `assets/`），并带有 2D fallback。Nodes 按 `ResearchNodeType` 着色。Edges 在 hover 时以标签显示其类型。

**值得注意的交互。**

- Hover 一个 node → tooltip 显示名称、类型、提及次数。
- Click 一个 node → 导航到其 wiki page。
- Hover 一条 edge → 标签显示关系（`uses` / `extends` / `compares_against` / …）。
- Mouse wheel → 以光标为锚点缩放（朝光标缩放，而不是朝中心）。
- Drag → orbit（3D）或 pan（2D）。
- 右上角切换 2D/3D。

页面内嵌 payload 上限为 `MAX_GRAPH_NODES = 1500`（见 [`pages.py`](../../tesserae/site/pages.py)）。完整 graph 始终可在 `/graph.json` 获取，供 tooling 使用。Code-graph nodes（`CodeClass`、`CodeFunction`、`Dependency`、…）按设计从可视化中滤除。

**相关路由。** 每个 wiki page 都会链接到一个聚焦的 subgraph view。

## About

### `/about.html` ✅

> _截图：about.png_

一个静态页面，用于说明 schema（每个 `ResearchNodeType` 和 edge whitelist，并附一行描述）、build info（commit SHA、generator version、generated-at timestamp）以及 AI exports inventory。

这是让新贡献者理解有哪些类型、每种类型用途是什么的合适页面。

## AI siblings — 每个页面如何同时也是数据

Tesserae 以三种形式发布每个页面：面向人的 HTML、纯文本同级文件、结构化 JSON 同级文件。另有面向 crawlers 和 agents 的站点级导出。

### 每页 `<page>.txt` ✅

单个页面的纯文本视图 — 没有导航、没有样式，除正文显式使用的内容外没有 markdown decoration。当 agent 想把单页作为上下文摄取、但不想写 HTML scraper 时适用。

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### 每页 `<page>.json` ✅

结构化记录：

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

当工具需要类型化访问 — link list、frontmatter、kind tag — 且不想使用 markdown parser 时适用。

### `/llms.txt` ✅

llmstxt.org 短索引。一个页面列出每个 kind 及其最相关条目。适合 LLM agent 发现站点时发出的第一个请求。

### `/llms-full.txt` ✅

llmstxt.org 长格式：所有 wiki 页面串接在一起。限制为 5 MB；如果达到上限，文件末尾会有 `[TRUNCATED — see graph.jsonld for the full set]` 标记。当 agent 有预算在一次请求和 5 MB 上下文中摄取整个语料时适用。

### `/graph.json` ✅

完整的 `ResearchGraph` payload — 包括没有 HTML 页面的 code-graph nodes。当工具想要完整 graph 做自己的分析时适用（MCP、Cognee、Graphiti consumers 都会读取它）。

### `/graph.jsonld` ✅

schema.org `Dataset` JSON-LD。仅 wiki-layer nodes（无 code nodes）。适合理解结构化数据的 crawlers — Google Knowledge Graph、搜索索引器、schema.org-aware aggregators。

### `/search-index.json` ✅

搜索面板 + 页面搜索索引。仅 wiki-layer kinds。当集成第三方搜索 UI 时适用；schema 是 `{kind, title, slug, body, score_hints}` 条目列表。

### `/sitemap.xml` ✅

每个已输出路由，以及从 frontmatter（`generated_at`、`updated_at`、`published_at`、`date`）派生的 `lastmod`。适合搜索引擎。

### `/rss.xml` ✅

最新优先排序的最近 30 个 syntheses。适合“订阅这个 wiki” — RSS readers、IFTTT、mailing lists。

### `/robots.txt` ✅

宽松策略 — crawl + index 一切。这个 wiki 本来就是给 agents 阅读的。

### `/ai-readme.md` ✅

给不擅长 HTML 的 AI agents 使用的机器可读站点地图。列出上面每个 artifact 的用途，并用一行说明每种格式何时合适。

### `/manifest.json` ✅

站点中每个输出文件的 sha256 + size 记录。用于：

- compile-twice idempotence test（`tests/test_project_e2e_redesign.py`）。
- 下游工具无需完整 diff 即可检测“自上次访问以来站点是否改变”。
- deploy command 在没有变化时短路 push。

## 选择正确格式

| 如果你是… | 读取 |
|---|---|
| 首次访问的人类用户 | `/`，然后深入 [Concepts](#concepts) 或 [Papers](#papers) |
| 想看“有什么新内容”的人类用户 | [Timeline](#timeline) 和最近的 [Syntheses](#syntheses) |
| 想看结构的人类用户 | [Graph view](#graph-view) |
| 执行一次查询的 LLM agent | 用 `<page>.json` 获得类型化访问 |
| 执行一次查询且预算受限的 LLM agent | `<page>.txt` |
| 摄取全部内容的 LLM agent | `/llms-full.txt`（≤ 5 MB）或 `/graph.jsonld`（不限大小） |
| 构建自定义 UI 的工具 | `/search-index.json` + `/graph.json` |
| 搜索引擎 | `/sitemap.xml` + `/graph.jsonld` |
| 订阅者 | `/rss.xml` |
| 变更检测器 | `/manifest.json` |

## 相关文档

- [Architecture](architecture.zh.md) — 三层模型、模块图、幂等性说明。
- [Feature map](feature-map.zh.md) — 每项功能及其状态、源文件和到本文的链接。
- [Quickstart](quickstart.zh.md) — 从 `project init` 到可浏览站点的最短路径。
- [Self-dogfood demo](self-dogfood.zh.md) — 对 Tesserae 自身 repo 运行 Tesserae，包括这个站点。
