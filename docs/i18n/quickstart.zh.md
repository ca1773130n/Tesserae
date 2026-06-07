# 快速开始

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
本页展示从一个已有的项目目录到可浏览的 Tesserae 的最短路径。

## 命令概览

CLI 采用分组结构：顶层是少数几个日常动词，其余则归入分组
（`sessions`、`vault`、`export`、`code`、`config`、`projects`、`integrations`、
`lab`）。运行 `tesserae --help` 查看整棵命令树：

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

要查看任一命令的 flag，运行 `tesserae <command> --help`（例如 `tesserae compile --help`）。

## 1. 运行设置向导

在你想要索引的项目中：

```bash
cd /path/to/my-project
tesserae init
```

向导会检测常见的 source，例如 `README.md`、`docs`、`src`、`lib`、`app`、`packages` 和 `data`，然后写入 `.tesserae/config.json`。它还会配置默认的 Cognee backend，使 `tesserae ask` 可以先尝试 Cognee，再 fallback 到编译后的 wiki 搜索。

如需非交互式设置（CI、脚本），传入 `--yes` 即可在不提示的情况下接受检测到的默认值：

```bash
tesserae init --yes
```

启用 Understand Anything 和 Cognee runtime memory 的全自动设置：

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

各项作用：

| Flag | Effect |
|---|---|
| `--with-understand-anything` | 将 UA graph projection 添加为 source。 |
| `--install-understand-anything` | 安装/更新 UA companion skills。 |
| `--understand-anything-platform codex` | 使用 Codex 运行 Tesserae 托管的 UA refresh wrapper。 |
| `--with-raganything` | 启用通过 RAG-Anything 的 multimodal ingestion。 |
| `--install-raganything` | 在设置期间安装 raganything[all]。 |
| `--raganything-parser` | 解析器选择：mineru（默认）、docling、paddleocr。 |
| `--run-raganything` | 在每次 compile 时自动 refresh RAG-Anything。 |
| `--run-cognee` | 在 compile 期间运行 best-effort 的 Cognee runtime cognify。 |
| `--install-cognee` | 若缺失则用当前 Python 安装 Cognee。 |

用户无需知道 UA 安装路径，也无需输入 `/understand`；当 UA graph 缺失或过期时，`tesserae compile` 会运行 `tesserae integrations refresh understand-anything`。

> **跳过向导。** `tesserae init --bare` 会写入一个最小化的 `.tesserae/config.json`，不进行 source 检测或 backend 探测——当你想在首次 compile 前手动编辑 config 时很方便。

## 2. 编译图谱与投影

```bash
tesserae compile
```

`compile` 会写入持久化产物：

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

首次运行后，使用 `--changed-only` 跳过未改动的 markdown 文件，并在没有文件变化时保留之前的 graph。若启用了 Understand Anything，compile 会先 refresh/materialize `.tesserae/external/understand-anything.md`；若启用了 Cognee runtime，则在写入 `.tesserae/cognee_bundle/` 后以 best-effort 方式更新 Cognee。

要在不触动已配置 source 的情况下临时 ingest 额外路径，可将其作为位置参数传入：`tesserae compile path/to/extra.md docs/`。

### 集成相关开关现在位于 config 中

`tesserae compile` 被有意限制为日常 flag（位置参数 paths，加上 `--project`、
`--changed-only`、`--limit`、`--refresh-integrations`、`--sessions`/`--no-sessions`
以及三个 LLM flag）。其余所有旧的 compile flag 都移到了
`.tesserae/config.json` 的 `compile_options` 块中；旧的 argparse 默认值仍作为
fallback。在该处设置某个键即可改变行为：

| `compile_options` 键 | 旧 flag | 默认值 | 作用 |
|---|---|---|---|
| `source_kind` | `--source-kind` | （无） | 覆盖已配置的 source kind。 |
| `trends` | `--trends` | `false` | 添加 corpus 级别的 Trend 节点。 |
| `min_trend_sources` | `--min-trend-sources` | `2` | 生成 Trend 节点所需的最少 source 数。 |
| `exclude_data` | `--exclude-data` | `false` | 跳过隐式的 `project_root/data` 自动包含。 |
| `no_vault_pull` | `--no-vault-pull` | `false` | compile 前不再 pull 现有 vault 编辑。 |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 将先前的 extraction 结果反馈回本次运行。 |
| `sessions_llm` | `--sessions-llm` | （auto） | LLM 会话提取模式（`auto`/`true`/`false`）。 |
| `sessions_model` | `--sessions-model` | （无） | 覆盖用于会话提取的 LLM 模型。 |
| `cognee_add` | `--cognee-add` | `false` | 将 Cognee bundle 添加到 dataset（不 cognify）。 |
| `cognee_cognify` | `--cognee-cognify` | `false` | 添加 bundle 并运行 Cognee cognify。 |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | 将 Cognee 的 LLM client 打补丁为 Codex 后运行 cognify。 |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | 用于 `cognee_codex_cognify` 的 Codex CLI 模型。 |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | 每次 Codex CLI 调用的 timeout（秒）。 |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Cognee dataset 名称。 |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Cognee lane 的 embedding provider。 |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Ollama embedding 模型。 |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Ollama `/api/embed` endpoint。 |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Ollama embedding 请求 timeout（秒）。 |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | 本地 embedding 维度。 |
| `cognee_system_root` | `--cognee-system-root` | （无） | 隔离的 Cognee system root 目录。 |
| `cognee_data_root` | `--cognee-data-root` | （无） | 隔离的 Cognee data root 目录。 |

> **一站式流水线。** `tesserae refresh` 在进程内运行整个循环——导入任何新的 agent session、compile 并 sync vault，一条命令搞定。传入 `--changed-only` 以启用可选的增量 compile。

## 3. 构建并提供静态前端

`serve` 在 site 缺失时会自动构建，因此一条命令即可得到可浏览的 Tesserae：

```bash
tesserae serve --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

要显式构建 site（例如仅部署而不提供服务），使用 `export site`；当你想浏览之前已构建的 site 而不重新构建时，给 `serve` 传入 `--no-build`：

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 保存时自动重建

将开发服务器与内置 watcher 搭配使用，使 `data/` 和 `docs/` 下的编辑触发增量 recompile：

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` 每 2 秒轮询一次，debounce 1 秒后运行 `compile --changed-only`。cron 风格的 rebuild 使用 `--once`（快照对比 `.tesserae/.watch-cache.json`），添加自定义监视目录使用 `--paths <dir>`，调整节奏使用 `--interval` / `--debounce`。
<!-- END: subagent-r-watch -->

### 运行 refresh 守护进程

如果你想要一个始终在线的引擎，让它自行保持知识库的新鲜——监视你的 source、合并成批的编辑并自动 recompile——请启动受监督的守护进程：

```bash
tesserae engine
```

`engine` 是长期运行的 supervisor：每 2 秒轮询一次，并在每次 rebuild 前等待 1 秒的静默窗口。用 `--interval` 和 `--debounce` 调整节奏，用 `--project` 指向另一个项目，或传入 `--once` 运行一次确定性的 drain 周期后退出（对 cron 或 CI 有用）。这是 `export site --watch` 的无人值守版本：让它持续运行，在你和你的 agent 工作时，graph、vault 与 site 都会保持最新。

要查看每个可见路由（home、sources、concepts、entities、papers、repos、topics、syntheses、questions、timeline、graph 以及 AI siblings）的带注释导览，请参阅 [`docs/frontend-redesign.md`](frontend-redesign.zh.md)。

前端依赖很轻，并写入：

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. 导入本地 agent 会话历史

会话历史导入是显式的：普通的 compile/build 会读取已规范化的会话，但不会自行扫描私有的 Claude Code 或 Codex transcript 存储。

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

导入的会话会出现在全局 Sessions 区块、site 搜索和首页 Browse 卡片中。会话详情页将 user/assistant 轮次渲染为可读的 markdown，把 tool-use 块附在前一个 assistant 轮次之下，并提供左侧的轮次导航栏用于 `#turn-N` 跳转。隐私说明、导入格式以及当前的 transcript 排版映射，请参阅 [`docs/session-history.md`](session-history.zh.md)。

## 5. 对 wiki 进行 lint

```bash
tesserae lint
```

遍历编译后的 graph + wiki + site，标记 orphan papers、stale citations、graph 与 wiki/ 之间的 drift、ghost synthesis inputs 等。写入 `.tesserae/lint-report.md` 和 `.tesserae/lint-report.json`。传入 `--fix-trivial` 应用安全的自动修复（缺失的 `implemented_in` edges、ghost-input 修剪），传入 `--severity error` 使仅在出现 error 时让退出码失败。

## 6. 查询 wiki

```bash
tesserae query "What is Gaussian Splatting?"
```

默认仅搜索——对 `.tesserae/site/search-index.json` 的 BM25，并从匹配的 `wiki/<kind>/<slug>.md` 中提取 200 字符的摘录。传入 `--kind papers`（或 `concepts`、`repos` 等）以缩小范围，`--top-k N` 以扩大范围，`--json` 输出结构化结果。添加 `--llm`（或设置 `TESSERAE_QUERY_LLM=1`）让 Claude 给出带 `[node_id]` 引用的合成答案；`--interactive` 打开 readline REPL——空行或 EOF 退出。`TESSERAE_QUERY_DRY_RUN=1` 在不调用 API 的情况下演练 prompt。

## 7. 按需编译面向 agent 的 context

v0.5.0 的核心是 On-Demand Context Compiler：向编译后的 graph 请求一份按 query 限定范围的、带引用的单一 context 文档，其大小适配 agent 的窗口。

```bash
tesserae context "How does session import work?"
```

它从与你 query 匹配的节点出发为 Personalized PageRank 提供 seed（要显式 seed 用 `--seeds <node_id>`），扩展邻域（`--depth`，默认 2），并组装出一份受字符 `--budget` 限制的带引用文档（默认 32000，传入 `<= 0` 表示不限）。添加 `--synthesize` 在其上叠加 LLM 撰写的摘要（需要 LLM backend），用 `-o/--output <file>` 将文档写入磁盘而非 stdout。

同一个 compiler 还通过 MCP 以 `compile_context` 工具暴露给 agent，因此编码 agent 可以在对话中途拉取恰到好处、受 budget 限制的项目 context，无需手动 export。

## 8. 导出 agent harness 文件

```bash
tesserae export harness
```

支持的 target：

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

或写入现有 vault：

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

Vault 包含 markdown projections、`.obsidian` defaults、graph coloring、`raw/assets/` 以及 Dataview dashboard。使用 `tesserae vault sync` 将现有 vault 与最新 compile 对齐（添加 `--prune` 以删除孤立笔记）。

## 10. 配置 MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

将输出粘贴到 `~/.hermes/config.yaml` 的 `mcp_servers` 之下，然后重启 Hermes/gateway。

## 11. Graphiti 导出 / sync

无依赖的 episode 导出：

```bash
tesserae export graphiti
```

在未安装 Graphiti 的情况下进行 dry-run sync 冒烟测试：

```bash
tesserae export graphiti --sync --dry-run
```

实时 sync 需要 `graphiti_core` 和一个可访问的 Neo4j backend：

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. 部署到 GitHub Pages

将 `.tesserae/site/` 中编译好的 site push 到项目 git origin 的 `gh-pages` 分支：

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` 会先运行 `compile`，使 site 保持最新。`--enable-pages` 通过 `gh` CLI 开启 Pages（幂等；若缺少 `gh` 则给出提示并跳过）。使用 `--dry-run` 进行 stage 与 commit 而不 push，用 `--branch` / `--remote` 覆盖默认值，用 `--force` 允许在工作区不干净时部署。

站点将可在 `https://<owner>.github.io/<repo>/` 访问。
