# 快速开始

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
本页展示从已有项目目录到可浏览 Tesserae 的最短路径。

## 1. 运行设置向导

在你想索引的项目中：

```bash
cd /path/to/my-project
tesserae project setup
```

向导会检测 `README.md`、`docs`、`src`、`lib`、`app`、`packages`、`data` 等常见 source，然后写入 `.tesserae/config.json`。它还会配置默认 Cognee backend，使 `project ask` 可以先尝试 Cognee，再 fallback 到 compiled wiki search。

启用 Understand Anything 和 Cognee runtime memory 的全自动设置：

```bash
tesserae project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

它会做什么：

| Flag | Effect |
|---|---|
| `--with-understand-anything` | 将 UA graph projection 添加为 source。 |
| `--install-understand-anything` | 安装/更新 UA companion skills。 |
| `--understand-anything-platform codex` | 使用 Codex 运行 Tesserae 托管的 UA refresh wrapper。 |
| `--run-cognee` | 在 compile 期间尽力运行 Cognee runtime cognify。 |
| `--install-cognee` | 缺少 Cognee 时用当前 Python 安装它。 |

用户不需要知道 UA install path，也不需要输入 `/understand`；当 UA graph 缺失或过期时，`project compile` 会运行 `project refresh-understand-anything`。

## 2. 编译图和 projections

```bash
tesserae project compile
```

`project compile` 会写入持久产物：

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

首次运行后可使用 `--changed-only` 跳过未更改的 markdown 文件，并在没有文件变化时保留之前的 graph。如果启用了 Understand Anything，compile 会先刷新/物化 `.tesserae/external/understand-anything.md`；如果启用了 Cognee runtime，它也会在写入 `.tesserae/cognee_bundle/` 后尽力更新 Cognee。

> **一键流水线。** `tesserae project refresh` 会在进程内运行整个循环——用一条命令导入任何新的 agent session、compile 并 sync vault。可选的增量 compile 传 `--changed-only`，跳过较慢的 harness-session 发现扫描传 `--skip-sessions`。

## 3. 构建并提供静态 frontend

```bash
tesserae project build-site
tesserae project serve --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

<!-- BEGIN: subagent-r-watch -->
### 保存时自动重建

将开发服务器与 polling watcher 配合使用，让 `data/` 和 `docs/` 下的编辑触发增量 recompile：

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae project watch
```

`project watch` 每 2 秒 polling，一次 1 秒 debounce，然后运行 `compile --changed-only`。使用 `--once` 进行 cron 风格 rebuild（snapshots vs `.tesserae/.watch-cache.json`），用 `--paths <dir>` 添加自定义 watch 目录，用 `--interval` / `--debounce` 调整节奏。
<!-- END: subagent-r-watch -->

### 运行 refresh daemon

如果你想要一个常驻引擎，让它自行监视来源、合并成批的编辑并自动 recompile，从而持续让知识库保持最新，请启动 supervised daemon：

```bash
tesserae project engine
```

`project engine`（别名 `project daemon`）是长期运行的 supervisor：它每 2 秒 polling 一次，并在每次 rebuild 前等待 1 秒的静默窗口。用 `--interval` 和 `--debounce` 调整节奏，用 `--project` 指向其他项目，或传 `--once` 运行单次确定性 drain 周期后退出（适合 cron 或 CI）。这是 `project watch` 的无人值守版本：让它一直运行，graph、vault 和 site 就会在你和你的 agent 工作时保持最新。

所有可见 route（home、sources、concepts、entities、papers、repos、topics、syntheses、questions、timeline、graph 以及 AI siblings）的注释导览见 [`docs/frontend-redesign.md`](frontend-redesign.zh.md)。

Frontend 依赖很轻，会写入：

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. 导入本地 agent 会话历史

会话历史导入是显式操作：普通 compile/build 会读取已规范化的会话，但不会自行扫描私有 Claude Code 或 Codex transcript stores。

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae project sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae project sessions discover --import

# Confirm the imported set:
tesserae project sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae project build-site
```

导入的会话会出现在全局 Sessions 区域、站点搜索和首页 Browse 卡片中。会话详情页会把 user/assistant turns 渲染为可读 markdown，把 tool-use blocks 附在前一个 assistant turn 下，并提供用于 `#turn-N` 导航的左侧 turn rail。隐私说明、导入格式和当前 transcript typography map 见 [`docs/session-history.md`](session-history.zh.md)。

## 5. Lint wiki

```bash
tesserae project lint
```

遍历 compiled graph + wiki + site，并标记 orphan papers、stale citations、graph 与 wiki/ 之间的 drift、ghost synthesis inputs 等。写入 `.tesserae/lint-report.md` 和 `.tesserae/lint-report.json`。传入 `--fix-trivial` 可应用安全 auto-fixes（缺失的 `implemented_in` edges、ghost-input pruning），传入 `--severity error` 则只在 error 时让 exit code 失败。

## 6. 查询 wiki

```bash
tesserae project query "What is Gaussian Splatting?"
```

默认仅搜索：在 `.tesserae/site/search-index.json` 上做 BM25，并从匹配的 `wiki/<kind>/<slug>.md` 抽取 200 字符 excerpt。传入 `--kind papers`（或 `concepts`、`repos` 等）来缩小范围，`--top-k N` 来扩大结果，`--json` 获得结构化输出。添加 `--llm`（或设置 `TESSERAE_QUERY_LLM=1`）可请求 Claude 生成带 `[node_id]` citations 的综合答案；`--interactive` 打开 readline REPL，空行或 EOF 退出。`TESSERAE_QUERY_DRY_RUN=1` 可在不调用 API 的情况下演练 prompt。

## 7. 按需编译面向 agent 的 context

v0.5.0 的招牌功能是 On-Demand Context Compiler：向编译后的 graph 请求一份按 query 限定范围的、带引用的 context 文档，并按 agent 的 context window 调整大小。

```bash
tesserae project context "session import 是如何工作的？"
```

它从与 query 匹配的节点开始作为 Personalized PageRank 的种子（用 `--seeds <node_id>` 显式指定种子），扩展邻域（`--depth`，默认 2），然后组装一份受字符 `--budget` 限制的带引用文档（默认 32000；传 `<= 0` 表示不限制）。再叠加一份 LLM 撰写的摘要可加 `--synthesize`（需要 LLM 后端），把文档写入磁盘而非 stdout 可用 `-o/--output <file>`。

同一个 compiler 通过 MCP 以 `compile_context` 工具的形式暴露给 agent，因此编码 agent 可以在对话中途拉取恰到好处、受 budget 限制的项目 context，而无需手动导出。

## 8. 导出 agent harness 文件

```bash
tesserae project export-agent-harness
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
tesserae project export-agent-harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. 导出 Obsidian vault

```bash
tesserae project export-obsidian
```

或写入已有 vault：

```bash
tesserae project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

Vault 包含 markdown projections、`.obsidian` defaults、graph coloring、`raw/assets/` 和 Dataview dashboard。

## 10. 配置 MCP

```bash
tesserae project mcp-config --server-name my_project_wiki
```

将输出粘贴到 `~/.hermes/config.yaml` 的 `mcp_servers` 下，然后重启 Hermes/gateway。

## 11. Graphiti export / sync

无依赖 episode export：

```bash
tesserae project export-graphiti
```

不安装 Graphiti 的 dry-run sync smoke：

```bash
tesserae project sync-graphiti --dry-run
```

Live sync 需要 `graphiti_core` 和可访问的 Neo4j backend：

```bash
tesserae project sync-graphiti \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. 部署到 GitHub Pages

将 `.tesserae/site/` 中的 compiled site 推送到项目 git origin 的 `gh-pages` 分支：

```bash
tesserae project deploy --build --enable-pages
```

`--build` 会先运行 `project compile`，确保 site 是最新的。`--enable-pages` 通过 `gh` CLI 开启 Pages（幂等；如果缺少 `gh` 会提示并跳过）。用 `--dry-run` 在不 push 的情况下 stage 和 commit，用 `--branch` / `--remote` 覆盖默认值，用 `--force` 允许在 dirty working tree 中部署。

站点可通过 `https://<owner>.github.io/<repo>/` 访问。
