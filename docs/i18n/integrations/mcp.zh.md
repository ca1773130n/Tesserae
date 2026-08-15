# MCP —— 把 Tesserae 接入 Claude Code、Codex、Cursor

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.fr.md">Français</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 自带一个 [Model Context Protocol](https://modelcontextprotocol.io) stdio 服务器，把编译好的类型化图谱暴露给任何支持 MCP 的客户端：Claude Code、Codex CLI、Cursor、Claude Desktop 等。该服务器同时提供三个完整的 MCP 表面 —— **tools**、**resources** 和 **prompts** —— 因此客户端既可以按需查询图谱，也可以从规范化的 URI 廉价地预热上下文。

## 先决条件

服务器从 `.tesserae/graph.json` 读取数据，因此需要先做一次编译：

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

源文件改动后可以随时重新编译。服务器会在下一次工具调用时自动读取新图谱，不需要重启。

## 1) 生成客户端配置

```bash
tesserae projects mcp-config
```

会输出大致如下的 JSON 片段：

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

具体路径会根据当前项目自动填入。如果想让服务器条目用 `tesserae` 之外的名字，可以传 `--name <alias>`。

## 2) 粘贴到你的 MCP 客户端

| 客户端 | 配置位置 |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → paste JSON |
| Hermes | `~/.hermes/config.toml` (use the TOML-equivalent block printed by `mcp-config --format hermes`) |

修改后请重启客户端。下一会话就会建立连接并发现 Tesserae 表面。

## 3) 客户端能看到什么

### Tools —— 由模型调用

每个 tool 都接受可选的 `graph_path` 或 `project`（注册表别名），因此单个服务器可在每次调用时解析任意已注册的 vault。省略时回退到活动项目。

**图谱查询与检索**

| Tool | 用途 |
|---|---|
| `graph_map` | **从这里开始。** 图谱层级的预算式地图——Descent 的入口。不带 scope 调用返回根卡片集（计数、顶级枢纽、每个最粗粒度社区一张卡片）；`scope='<某张卡片的 scope_id>'` 下降一层树状图；`org:root` 遍历智能体组织树。无需猜搜索词即可为智能体定位 |
| `schema` | 受控的 node、edge 和 wiki-kind 词汇表 |
| `graph_summary` | 当前项目的节点 + 边计数以及类型分布 |
| `search_nodes` | 按 `query`、`type`/`types`、`kind`、`limit`、混合 `mode`/`weights` 过滤公开图节点；`include_superseded` 可显示已废弃节点；`explain` 加上检索 `profile`（见下方） |
| `node_context` | 一个节点 + 它的相邻边 + 邻居节点。`use_ppr` 用个性化 PageRank 而非 1 跳游走对邻居排序；`include_superseded`、`limit` 限定结果。一个在后来的编译中失去合并的 `node_id` **不是**遗漏：它通过合并账本解析为吸收它的节点，响应增加 `status: "merged"` 带 `merged_from` / `merged_into`，让你知道今后应拿着什么 id。账本仅在图谱遗漏后被查阅，因此活着的 id 永不被重定向 |
| `embedding_status` | 报告驱动混合检索的活动嵌入后端，以及它持久化的向量缓存——该后端/维度键下的 `vectors_cached`，以及进程级的 `cache_hits` / `cache_misses` / `cache_errors`，这样冷的或不可写的缓存就不会被误认为快路径。接受 `graph_path` / `project` 以选择报告哪个项目的 sidecar |
| `search_facts` | 从图谱投影出的时序事实（Graphiti 风格），排序只看事实内容（主语、谓词、宾语、证据），不看序列化后的整条事实，因此 id 或元数据片段不算命中；`dated`（`any`、`dated`、`undated`）按事实是否带有可用的 `valid_from` 筛选；`current_only` 仅过滤当前事实，`as_of` 按过去某一日期作答。两者不可同时使用（表达不同的时钟），且 `undated_included` 会报告返回行中有多少条没有日期 |
| `timeline` | 按解析后的 `valid_from` 排序的事实，用于纵向视图；没有日期的事实统一排在所有有日期的事实之后，并以 `undated_events` 报告条数，而不是混排其间；`dated`（`any`、`dated`、`undated`）按事实是否带有可用的 `valid_from` 筛选；`as_of` 按过去某一日期作答（这是对有效区间的时点定位，而非范围下界），`undated_included` 会报告返回行中有多少条没有日期。没有日期的事实在 `as_of` 下仍会保留，因此这个计数才是区分单薄答案与完整答案的依据。`total_events` 计数**匹配过的**每条事实，而非你被交付的页面——整份匹配集在切片页面前按日期排序，因此最早的事实才是时间线真正返回的，`total_events > len(events)` 就是你区分完整页与完整答案的方式 |
| `graph_ppr` | 从一个或多个 `seed_node_id` 出发的个性化 PageRank，返回最相关的 top-K 节点；可调 `alpha`、`directed`、`edge_type_weights` |
| `wiki_page` | 节点对应的、已编译的 wiki 页面正文，以及它引用的内部链接。一个陈旧的 `node_id` 遵循同样的合并账本重定向，无声进行——被吸收节点的名字是幸存者上的别名，因此幸存者的页面*就是*你所求的页面 |
| `raw_source` | 原始 markdown 源文（上限 16 KB）。绝不返回字节：对于 `Artifact` 节点它会把你指向 `drill_down`，后者改为报告资产的路径和站点地址 |
| `verify_claim` | 针对图谱验证**一个**三元组——精确查找，不用 LLM，不做模糊匹配，不返回排序结果。返回 `{verdict, reason, triple, citation, provenance, advisory}`；`verdict` 为 `SUPPORTED`（边存在**且**其证据是文档中的逐字片段）、`PRESENT_UNEVIDENCED`，或一个拒绝。若手头只有散文，先 `search_nodes` 再 `verify_claim` |
| `doctor_run` | 运行健康检查并以 JSON 返回报告（`findings`、`exit_code` 0/1/2）。**始终只读**——修复永远不会经由 MCP 执行；要修复请在 CLI 上用 `tesserae doctor --fix` |
| `doctor_report` | `.tesserae/doctor-report.md` 的内容（上限 64 KB）；在运行 `tesserae doctor` 之前为空 |
| `charter_route` | 一次调用把一个任务定位到已授权（chartered）的域树上 —— 当没有卡片能按名字选中时，用它代替翻阅 `graph_map` 的卡片。它给每个存活域（slug、锚点名，以及已缓存的 brief）打分，再以 beam-1 下降到子树证据最强的那个域，返回 `{routed, path, brief, parent, siblings, route_quality}`；域 slug 是能跨 ingest 存活的作用域，community id 不能。`altitude`（`auto`/`division`/`department`/`team`）为下降的深度设定上限。**尽力而为，并且它会明说**：`charter.json` 的字节是幂等的，这个排序不是 —— embedding 通道随机器的后端而变，且一旦有 brief 被写入，域的行就会携带其 brief。**目前还没有任何编译会写入 brief**，因此今日每一行都是冷的，`warm_rows` 为 `0`；`route_quality` 会报告 `{backend, semantic, corpus_rows, warm_rows, evidenced_rows}`，且每张卡片都带 `evidence` —— `lexical`（词项命中，换后端仍成立）、`semantic`（仅嵌入相似度，换后端就不成立）或 `none`（只是路过）。定位不了的任务以 `routed: false` 返回，且**不**指名任何域：里面根本没有可供读出猜测的低置信候选。需要由 `tesserae compile` 写出的 `.tesserae/charter/charter.json` |
| `lint_report` | 最近一次编译期的 lint 结果（上限 64 KB） |

**检索的性能分析。** `search_nodes` 和 `compile_context` 接受
`explain: true` 并用一个 `profile` 作答——对于 `bm25`、`lexical`
和 `embedding` 这三条通道，各自的权重、`candidates_in`、评分了多少条，
`embed_calls` / `cache_hits` / `cache_misses` 及其挂钟时间，加上总体
`candidates_in` / `admitted` / `returned` 以及哪些通道对其计数的各节点有贡献。`returned` 和那个按节点的通道归因是
**预算前的**：融合在自身的 top-`k` 切片上固定两者，而约束性的 `budget_chars` 在之后于 MCP 层裁剪那个切片，不重写
profile。因此在紧预算下 `returned` 描述检索器产生的切片而非响应中的行，而 `continuation`
行是报告差异的东西。`search_nodes` 返回一份 profile；`compile_context`
返回一个列表，每条种子搜索一份。

默认关闭，关闭不只是形式：度量有成本，因此这是诊断工具而非常开不关。
它永不可能移动排名——每个数字都从融合已产出的分数与排名表中读取——而且
打开标志时响应仍携带一直都有的那些键。`cache_hits` / `cache_misses` 计数器
是你区分温暖与冷向量缓存的方式，无需事后检查 `embedding_status`。

**按需上下文编译器**（Phase 7）

| Tool | 用途 |
|---|---|
| `compile_context` | 为 `query` 或显式 `seeds` 编译一份量身定制的**带引用**上下文文档。遍历深度受限的子图（`depth`，1–10，默认 2），用 PPR 排序，并填充字符 `budget`（默认 32000；传 `0` 表示不限）。默认确定性；设 `synthesize: true` 可生成由 LLM 撰写的叙述式 "topic" 切片。返回 `body`、`citations`、`selected_node_ids` 和 `char_budget_used`. `view` 将游走限制在命名的边分割——`semantic`、`temporal`、`causal` 或 `entity`；传递一个名称数组为每个视图各运行一次游走并融合（加权 RRF）。无论何时请求视图——一个名称或多个——每条引用都携带 `via_views`（到达该引用的各视图）。`explain` 加上 `profile`，每次种子搜索一份 |
| `get_handle` | 分片（`offset`、`limit`）翻阅先前以 `handle` 形式返回的大体积载荷（例如带 `preview` 的 `compile_context`）——按需取用，而不是把全部内容一次性倒进上下文 |
| `list_communities` | 列出后编译阶段生成的 `COMMUNITY_SUMMARY` 节点，按成员数排序（`min_size`、`limit`）；通过 `node_context` 沿 `summarizes` 边回溯到成员 |
| `fresh_insights` | 按艾宾浩斯式衰减分数（最新 + 访问最多优先）排序的会话发现；过滤掉被更新近似重复项取代的发现。可选 `kind`、`limit`、`include_superseded` |

**会话记忆**（见 [sessions.md](sessions.zh.md)）

| Tool | 用途 |
|---|---|
| `list_sessions` | 活动项目的会话信封（id、started_at、title、files_touched、发现计数）；`since`、`limit` |
| `find_session_findings` | 通过 `discussed_in` / `references` 关联到 `node_id` 的所有会话发现，可按 `kinds`（insight / decision / question / todo / hypothesis / takeaway）过滤 |
| `find_code_symbol_mentions` | 将一个会话发现扩展为它提及的 `CodeFunction`/`CodeClass`/`CodeMethod` 符号（使用可选启用的 insight↔symbol 关联阶段生成的 `discusses` 边）。代码层需显式启用：没有 `codegraph` 的 `external_tools` 条目时，此工具不返回任何内容 |
| `activity_summary` | 跨已注册项目的日/周活动摘要——会话、发现、git 提交、PR、已摄取文档，每一类都按**其自身**的时间戳取窗口，绝不使用会话的 `started_at`。输出确定性的 markdown，除非关闭，否则会在前面加上一段 LLM 叙述 |
| `query_decisions` | 某个时间范围内跨已注册项目的决定：从 Claude Code 的 `AskUserQuestion` 确定性解析出的显式**人类**选择（问题与所选项），外加从对话中挖出的智能体决定 |

**智能体记忆与写回**（见 [agent-memory.zh.md](../agent-memory.zh.md)）

| 工具 | 用途 |
|---|---|
| `agent_view_explain` | *无需加载*即可解释一个智能体作用域视图：解析模式（worker / manager / org）、成员智能体、每个 L1 产物的路径与节点数，以及 `distilled_through` 陈旧水位线 |
| `drill_down` | 把蒸馏物的 `member_ref` 解析回原始 L0 节点——管理者越过蒸馏可见性的显式、可审计升级。返回状态 `alive` / `changed` / `absorbed` / `gone`；每次调用都会记入 sidecar。钻取一个**图形** `Artifact`，其资产在项目内部解析，添加三个其他节点类型绝不携带的键：`asset_path`（字节住在磁盘的何处）、`asset_sha256`（那些字节的摘要，与 kind 共同设种节点 id）以及 `asset_site_path`（已构建站点的 `raw-assets/` 下的内容寻址地址）。表格与方程 Artifact 没有资产——其内容*就是*其描述——而一个在项目根之外解析的图形永不存储路径；两者都用普通键钻取。格式错误的已声明哈希会丢弃 `asset_site_path` 而非虚拟一个地址 |
| `read_audit` | 谁在读这张图谱：按时间倒序返回记录下来的读取事件（`tool`、`actor`、`node_ids`、`at`、`tesserae_version`），并附上按 actor 的统计，好让驱动「因闲置而遗忘」的访问计数能归属到具体读者。**默认关闭、需显式开启** —— 除非在服务端进程上设置 `TESSERAE_READ_AUDIT=1`，否则什么都不记录，因为常开的审计会把每一次读取都变成一次写入。关掉开关后已记录的行仍可读取；`enabled` 报告当前设置。可按 `actor`、`tool`、`node_id` 过滤 |
| `graph_write` | 直接把有类型的节点与边写入图谱——不经 markdown，不经抽取流程。写入会追加到只增不改的 overlay，并作为编译生产者重放，因此**能挺过重新编译**。它很严格：未知类型、没有证据的边、端点既不在本次载荷内也不是已有节点 id，都会被拒绝。**要撤回**一件根本就是错的东西而不必编造替代品：把一条 `retracts` 边**按 id** 指向那个错误节点——目标脱落于发现（`search_nodes`、`fresh_insights`）、脱落于上下文选择（`compile_context`），以及脱落于 `node_context` 返回的每个邻域列表与关联边。它*不*做的是向叫出了那一个的人隐藏节点：一个按 id 或名称的准确 `node_context` 查找仍然返回节点本身，标记为 `"retracted": true`，因为调用者要求了那一个。`include_superseded: true` 把它放回发现面，且什么都不会被删除 |

**问答与注册表**

| Tool | 用途 |
|---|---|
| `ask` | 自然语言问答。省略 `scope`，智能路由器会在已注册项目中选择目标（联邦回退），并在连续问题之间重新路由（传 `conversation_id` 隔离一条线）。显式 `scope`：`current`（一个项目）、`all-registered`（每个项目一个答案）、`federated`（一个合并的、交叉引用的答案；默认开启 `semantic`）。加上 `backend`、`top_k`、`scope_aliases`、`claude_config_dir`。对一个由图谱路由的问题，信封携带 `plan`（规划者的推理、它选择的步骤，以及 `executed`——实际运行了什么），并可能携带 `proposed_write`：规划者认为值得记录的节点与边，仅根基于*问题*所声言的东西。它是**建议，绝不写入** ——其出处总是空，因此 `graph_write` 拒绝它，直到一个持有 agent 键和外部锚点的调用方提供一个。变更永远不是一个查询的副作用 |
| `query` | 不经 LLM 的原始检索——对应 `tesserae query`。`backend='wiki'`（默认）是对已编译维基的确定性 BM25/语义检索，返回带摘录的排序结果；`backend='raganything'` 在项目启用时查询可选的多模态 RAG 索引。需要综合并带引用的回答请用 `ask` |
| `ingest` | 把原始网页/文本内容（例如浏览器剪藏）摄取进所解析项目的知识图谱 |
| `list_projects` | 列出已注册的项目 |
| `register_project` | 向注册表添加一个项目 |
| `unregister_project` | 从注册表移除一个项目（不存在特权的"活动"项目） |

**引导式设置**

| Tool | 用途 |
|---|---|
| `tesserae_setup_plan` | 检测环境并以 JSON 形式提出设置计划。只读 —— 绝不触碰 `.tesserae/` |
| `tesserae_setup_apply` | 应用（可能已编辑的）计划：写入 `.tesserae/config.json` 并执行受控的安装/运行动作。受 `confirm_install_actions` / `confirm_run_actions` 限制 |

### Resources —— 自动加载到模型的上下文

客户端可以通过资源选择器拉取的 URI，不消耗一次工具调用：

- `tesserae://graph/schema` —— 与 `schema` 工具相同的载荷，作为静态上下文随时可用
- `tesserae://graph/summary` —— 当前活动项目的摘要
- `tesserae://lint-report` —— 最新 lint 报告，以 markdown 呈现

另外，客户端可以按需构造以下 URI 模板：

- `tesserae://wiki/{kind}/{slug}` —— 任意已编译的 wiki 页面正文
- `tesserae://raw/{source_path}` —— 任意原始 markdown 源文

### Prompts —— 一键式研究模板

它们会出现在客户端的斜杠菜单里（例如 Claude Code 的 `/` 调色板）：

| Prompt | 参数 | 作用 |
|---|---|---|
| `summarize-paper` | `slug` (required) | 调用 `node_context` + `wiki_page` + 可选的 `raw_source`，再返回结构化摘要：贡献、方法概要、关键结果、局限性、相关节点 |
| `find-related-work` | `topic` (required), `limit` | 串联 `search_nodes` + `node_context`，给出 top-K 相关条目及相关性说明 |
| `compare-approaches` | `a`, `b` (both required) | 对两者分别拉取 `node_context`，并通过 `search_facts` 获取性能声明；返回带综合分析的并排对比 |
| `gap-analysis` | `topic` (optional) | 浮现未解决的开放问题、缺失的基准、证据不足的声明 |
| `triage-open-questions` | _none_ | 列出每一个 `OpenQuestion` 节点，按主题分组，并给出优先级建议 |

每个 prompt 都会渲染为一条用户消息，明确告诉模型应该把哪些 Tesserae 工具串起来，模型就不必每次重新摸索表面。

## 多项目：在同一台服务器下注册多个 vault

`~/.tesserae/registry.json` 处的持久化注册表，让同一台 MCP 服务器可以按名称解析任意已注册项目：

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

注册之后，每一个接受 `project` 或 `graph_path` 的工具都会把 `project: "research"` 解析到注册表，而不需要提供完整路径。服务器甚至会校验注册的 `graph_path` 是否仍然存在，如果需要重新编译，会返回清晰的错误信息。

### 一次扇出到所有已注册 vault

`ask` 工具接受 `scope: "all-registered"`，可以并行查询每一个已注册项目并返回聚合结果：

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

通过 `scope_aliases: ["research", "notes"]` 可以限定到子集。

## 多账号 Claude CLI

如果你的 `ask` 工具走的是 Claude CLI，并且你有多个账号（例如 `~/.claude` 和 `~/.claude-personal2`），可以在每次调用时传入 `claude_config_dir`：

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

服务器只在该次调用期间导出 `CLAUDE_CONFIG_DIR`，调用结束后恢复之前的值。调用之间不会互相泄漏。

## 验证

重启 MCP 客户端后，确认连接是否建立：

- Claude Code：`/mcp` 应当列出 `tesserae` 以及工具数量。
- Cursor：聊天栏的 MCP 图标应当显示 `tesserae: connected`，并附带 tool/resource/prompt 计数。
- Codex / Hermes：按名称调用任意工具（例如 `schema`），并检查响应。

如果什么都看不到，请反复确认 `--graph` 指向的是存在的 `.tesserae/graph.json` —— 服务器现在会在启动时以及每次工具调用时都做校验，所以你会看到清晰的错误信息，而不是静默的 500。

## 它在整个体系中的位置

MCP 服务器是类型化图谱的 **读接口**。**写路径**（摄取源文件、重新编译、刷新 RAG-Anything 等配套工具）请直接使用 CLI。两者是解耦的：CLI 更新 `.tesserae/`，MCP 服务器在下一次工具调用时读取其中的内容。
