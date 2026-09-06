# 快速入门

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
本页展示从一个已有的项目目录到可浏览的 Tesserae 的最短路径。

## 命令总览

CLI 是分组的：顶层是少数几个日常动词，其余归入分组（`sessions`、`vault`、`export`、`code`、`config`、`projects`、`agents`、`domains`、`integrations`、`lab`）。运行 `tesserae --help` 查看整棵命令树：

```text
tesserae 0.31.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  verify-attribution  Is each figure in an answer attributed to the right system and benchmark? No graph, JSON out
  schema-drift  Propose ResearchNodeType sub-types from clustered nodes (proposals only; promotion is a human edit)
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site | okf | kuzu — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

运行 `tesserae <command> --help`（例如 `tesserae compile --help`）查看任意单个命令的标志。

## 1. 运行设置向导

在你想要索引的项目里：

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` 是唯一的入门步骤。向导会检测常见来源，如 `README.md`、`docs`、`src`、`lib`、`app`、`packages` 和 `data`，探测哪些 LLM CLI 已安装**且已登录**，让你选择 LLM 提供方，并写入 `.tesserae/config.json`。可选的 RAG-Anything 内存后端**默认关闭**；之后可在配置的 `memory_backends` 中启用，并通过 `tesserae query --backend raganything` 显式查询。

对于非交互式设置（CI、脚本），传入 `--yes` 接受检测出的默认值而不发出提示（所有可选集成均为 OFF）：

```bash
tesserae init --yes
```

### LLM 提供方配置

向导的提供方选择（或等价的标志）会持久化以下配置键：

| 配置键 | 标志 | 含义 |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,openai,custom}` | LLM 客户端的后端：`claude`/`codex` 通过 OAuth 使用已登录的 CLI；`anthropic` 和 `openai` 直接使用那些 API；`custom` 指向你命名的一个端点。 |
| `llm_model` | `--llm-model` | 合成/洞见 LLM 客户端使用的模型。 |
| `llm_base_url` | `--llm-base-url` | `anthropic`/`openai`/`custom` 的端点基础 URL。 |
| `llm_api_key` | `--llm-api-key` | `anthropic`/`openai`/`custom` 的 API key。 |

> **明文警告。** `llm_api_key` 以**明文**存储在 `.tesserae/config.json` 中。请优先使用环境变量：
> `TESSERAE_LLM_API_KEY`（密钥）、`TESSERAE_LLM_BASE_URL`（端点）和 `TESSERAE_LLM_MODEL`（模型）。解析顺序为 env → 项目配置 →
> 机器级配置（`~/.tesserae/config.json`，由 `tesserae setup` 写入）
> → 内置默认值。较旧的 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 仍然
> 有效，在 Tesserae 自有名称下面一级。

在已有项目上重新运行 `init` 会**合并**——你已配置的 `sources` 和 `memory_backends` 会被保留，而不是被覆盖。

非交互式提供方设置示例：

```bash
tesserae init --yes --llm-provider codex

# 一个 OpenAI 兼容端点（vLLM、LiteLLM、OpenRouter、Ollama、LM Studio）。
# 线路是一个与提供商分开的设置，init 不持久化它——在环境中或用 `tesserae config llm` 设置它。
tesserae init --yes --llm-provider custom \
  --llm-base-url http://localhost:8000/v1 \
  --llm-model qwen2.5-coder-32b-instruct
export TESSERAE_LLM_API_STYLE=openai      # POST {base_url}/chat/completions
export TESSERAE_LLM_AUTH_TOKEN=sk-...     # 无密钥本地服务器则完全省略

# 一个 Anthropic 兼容网关。无 /v1：SDK 自身追加 /v1/messages，
# 而以 /v1 结尾的基础 URL 被削减回来以防止它加倍。
tesserae init --yes --llm-provider custom \
  --llm-base-url https://gateway.internal.example \
  --llm-model claude-sonnet-4-6           # key via TESSERAE_LLM_API_KEY
```

然后在花一次编译之前确认实际解析的内容——线路、模型、URL、凭证种类及其来源层：

```bash
tesserae config status --project .
```

[调优 → LLM 后端](tuning.md#llm-后端) 是完整参考：每个
`llm_*` 键、两个自定义端点方案及配置的端点在无法构建时做什么。

> **跳过向导。** `tesserae init --bare` 写入一个最小化的 `.tesserae/config.json`，不做来源检测或后端探测——当你想在首次编译前手工编辑配置时很方便。

## 2. 编译图谱与投影

```bash
tesserae compile
```

`compile` 写出持久产物：

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
```

首次运行之后使用 `--changed-only` 可跳过未变化的 markdown 文件，在没有文件变化时保留之前的图谱。

若要在不触及已配置来源的情况下临时摄取额外路径，把它们作为位置参数传入：`tesserae compile path/to/extra.md docs/`。

### 集成开关现在位于配置中

`tesserae compile` 被刻意限制在日常标志上（位置路径参数加上 `--project`、`--changed-only`、`--limit`、`--refresh-integrations`、`--sessions`/`--no-sessions`，以及三个 LLM 标志）。其余所有旧编译标志都移入了 `.tesserae/config.json` 的 `compile_options` 块；旧的 argparse 默认值仍是回退值。在那里设置某个键即可改变行为：

| `compile_options` 键 | 旧标志 | 默认值 | 作用 |
|---|---|---|---|
| `source_kind` | `--source-kind` | （无） | 覆盖已配置的来源类型。 |
| `trends` | `--trends` | `false` | 添加语料级 Trend 节点。 |
| `min_trend_sources` | `--min-trend-sources` | `2` | 生成 Trend 节点所需的最少来源数。 |
| `exclude_data` | `--exclude-data` | `false` | 跳过隐式的 `project_root/data` 自动包含。 |
| `no_vault_pull` | `--no-vault-pull` | `false` | 编译前不回拉已有的 vault 编辑。 |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 将先前的提取结果反馈进本次运行。 |
| `sessions_llm` | `--sessions-llm` | （auto） | LLM 会话提取模式（`auto`/`true`/`false`）。 |
| `sessions_model` | `--sessions-model` | （无） | 覆盖用于会话提取的 LLM 模型。 |

> **Cognee 已在 0.19 中移除。** cognee 后端在 0.18 中被降级，且从未真正
> 参与图谱构建。仍带有 `memory_backends.cognee` 配置段（或 `cognee_*`
> 编译选项）的配置依然可以加载——该段会被忽略，并给出一行提示。

> **一步式流水线。** `tesserae refresh` 在进程内运行整条循环——它导入任何新的 agent 会话、编译并同步 vault，一条命令完成。传入 `--changed-only` 启用可选的增量编译。

## 3. 构建并启动静态前端

`serve` 在站点缺失时会自动构建，因此一条命令就能得到可浏览的 Tesserae。**不带参数的 `serve` 会在一个服务器下服务每个已注册的项目**——`/` 是项目落地页，每个项目位于 `/<alias>/`，页头有一个 Projects 切换器可在项目间跳转。页内的 **ask 小组件在两种模式下都可实时使用**，会路由到你所在页面对应的项目：

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

打开：

```text
http://127.0.0.1:8765/
```

若要显式构建站点（例如部署而不启动服务），使用 `export site`；当你想浏览之前构建的站点而不重新构建时，给 `serve` 传入 `--no-build`：

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 保存时自动重建

将开发服务器与内置监视器搭配使用，使 `data/` 和 `docs/` 下的编辑触发增量重编译：

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` 每 2 秒轮询一次、去抖 1 秒，并运行 `compile --changed-only`。使用 `--once` 做 cron 风格的重建（对照 `.tesserae/.watch-cache.json` 做快照），`--paths <dir>` 添加自定义监视目录，`--interval` / `--debounce` 调整节奏。
<!-- END: subagent-r-watch -->

### 运行刷新守护进程

想要一个常驻的 engine 自行保持知识库新鲜——监视你的来源、合并编辑突发并自动重编译——就启动受监督的守护进程：

```bash
tesserae engine
```

`engine` 是长期运行的监督器：它每 2 秒轮询一次，并在每次重建前等待 1 秒的静默窗口。用 `--interval` 和 `--debounce` 调整节奏，用 `--project` 指向另一个项目，或传入 `--once` 运行单次确定性的排空周期后退出（适用于 cron 或 CI）。这是 `export site --watch` 的免操心版本：让它一直运行，图谱、vault 和站点就会随着你和你的 agent 的工作保持最新。

要查看每条可见路由的注释导览——home、sources、concepts、entities、papers、repos、topics、syntheses、questions、timeline、graph，以及各个 AI 兄弟文件——参见 [`docs/frontend-redesign.md`](frontend-redesign.zh.md)。

前端依赖极少，写出：

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. 导入本地 agent 会话历史

会话历史导入是显式的：普通的 compile/build 会读取已归一化的会话，但不会自行扫描私有的 Claude Code 或 Codex 转录存储。

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

导入的会话会出现在全局 Sessions 区、站点搜索和主页的 Browse 卡片中。会话详情页把 user/assistant 轮次渲染为可读的 markdown，把 tool-use 块附在前一条 assistant 轮次下方，并提供左侧轮次导航栏用于 `#turn-N` 跳转。隐私说明、导入格式和当前的转录排版映射见 [`docs/session-history.md`](session-history.zh.md)。

## 5. 对 wiki 做 lint

```bash
tesserae lint
```

遍历编译后的图谱 + wiki + 站点，标记孤儿论文、过期引用、图谱与 wiki/ 之间的漂移、幽灵合成输入等。写出 `.tesserae/lint-report.md` 和 `.tesserae/lint-report.json`。传入 `--fix-trivial` 应用安全的自动修复（缺失的 `implemented_in` 边、幽灵输入清理），传入 `--severity error` 使退出码只对错误失败。

对于图谱之外的工作区健康——注册表一致性、新鲜度、锁、LLM 登录、卫生——运行 `tesserae doctor`（`--fix` 只应用安全修复）。参见 [`docs/doctor.md`](doctor.zh.md)。

## 6. 对 wiki 提问与查询

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` 是答案界面：模型在编译后的图谱上规划检索，然后合成一个带引用的答案。它可以配合已登录的 `claude`/`codex` CLI（OAuth）或 `ANTHROPIC_API_KEY` 使用；传入 `--no-llm` 只返回排序后的搜索命中（此强制关闭优先于 `TESSERAE_QUERY_LLM=1`）。`TESSERAE_QUERY_DRY_RUN=1` 会演练提示词而不发出 API 调用。

`query` 是检索界面：在 `.tesserae/site/search-index.json` 上做 BM25/语义搜索，并从匹配的 `wiki/<kind>/<slug>.md` 提取 200 字符的摘录。传入 `--kind papers`（或 `concepts`、`repos` 等）缩小范围，`--top-k N` 扩大范围，`--json` 获取结构化输出；`--interactive` 打开一个 readline REPL——空行或 EOF 退出。显式内存后端也在这里：`--backend raganything` 直达该后端并透出它的错误。`query` 上没有 LLM 合成——那是 `ask` 的事。

## 7. 按需编译 agent 可用的上下文

v0.5.0 的头条是按需上下文编译器（On-Demand Context Compiler）：向编译后的图谱索取一份针对某个查询、带引用的单一上下文文档，大小适配 agent 的窗口。

```bash
tesserae context "How does session import work?"
```

它从匹配你查询的节点开始播种 Personalized PageRank（使用 `--seeds <node_id>` 显式指定种子），扩展邻域（`--depth`，默认 2），并组装一份带引用的文档，以字符数 `--budget` 为上限（默认 32000；传 `<= 0` 表示不设上限）。加上 `--llm` 可在其上叠加 LLM 撰写的摘要（需要 LLM 后端），用 `-o/--output <file>` 把文档写到磁盘而不是 stdout。

同一编译器通过 MCP 以 `compile_context` 工具的形式暴露给 agent，因此编码 agent 可以在对话中途拉取刚好够用、预算受限的项目上下文，无需手动导出。

## 8. 导出 agent harness 文件

```bash
tesserae export harness
```

支持的目标：

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

子集示例：

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. 导出 Obsidian vault

```bash
tesserae vault export
```

或写入一个已有的 vault：

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

vault 包含 markdown 投影、`.obsidian` 默认配置、图着色、`raw/assets/` 和一个 Dataview 仪表盘。使用 `tesserae vault sync` 使既有 vault 与最新编译对账（加 `--prune` 删除孤儿笔记）。

## 10. 配置 MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

把输出粘贴到 `~/.hermes/config.yaml` 的 `mcp_servers` 下，然后重启 Hermes/gateway。

## 11. Graphiti 导出 / 同步

无依赖的 episode 导出：

```bash
tesserae export graphiti
```

在未安装 Graphiti 的情况下做干跑同步冒烟：

```bash
tesserae export graphiti --sync --dry-run
```

实时同步需要 `graphiti_core` 和可达的 Neo4j 后端：

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. 部署到 GitHub Pages

将 `.tesserae/site/` 的编译站点推送到项目 git origin 的 `gh-pages` 分支：

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` 会先运行 `compile`，保证站点是新鲜的。`--enable-pages` 通过 `gh` CLI 开启 Pages（幂等；`gh` 缺失时跳过并给出提示）。使用 `--dry-run` 暂存并提交但不推送，`--branch` / `--remote` 覆盖默认值，`--force` 允许在工作树不干净时部署。

站点将在 `https://<owner>.github.io/<repo>/` 可访问。
