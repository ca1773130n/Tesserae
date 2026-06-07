# Self-dogfood 演示

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
此项目可以索引自身。self-dogfood 流程证明 Tesserae 可以被安装、在自己的仓库内设置、摄取自己的 docs/source/tests/scripts、可选地刷新 Understand Anything 和 Cognee、编译图谱产物，并构建静态 Web 前端。

它还会运行自我改进循环。每次编译都会把可变的记忆状态 —— `decay_score`、`access_count`、`confidence` 以及 `superseded` 标志 —— 重新导出到 `.tesserae/sqlite.db` 内部的 **`node_memory` sidecar** 表中。这些标量*仅*存在于 sidecar 中，绝不进入 `graph.json`，因此重新进行 dogfood 编译时图谱是 byte-identical 的，而 sidecar 则跟踪 decay 与 recurrence。在 `>= 3` 个不同会话中复现的 insight 会以 `(0, 1]` 范围内的数值 confidence 得到强化（3 个会话 → `0.5`，4 → `0.75`，5+ → `1.0`，封顶），写入 sidecar 并由 MCP `fresh_insights` 工具暴露；该工具默认隐藏被更新的 near-duplicate 取代（superseded）的 finding。

## 命令

从仓库根目录：

```bash
# 确保 shell 命令已安装。
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# （可选）安装默认的语义嵌入后端。
pip install 'tesserae[semantic]'

# 将此仓库设置为 Tesserae 项目。
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# 编译已配置的源。
tesserae compile

# 显式重建静态前端。
tesserae export site

# 在本地提供服务（如有需要会先自动构建站点）。
tesserae serve --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

## 生成的工作区

self-demo 会把生成的产物写入：

```text
.tesserae/
```

关键产物：

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # 类型化图谱 + node_memory sidecar + 实时 HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

生成的工作区默认有意不提交。它可以通过上面的命令从仓库源复现。

## 最新已验证运行

已于 `2026-04-27 11:11:23 KST` 从 Tesserae 仓库自身验证。

集成选项（Understand Anything、cognee）现在是**交互式向导提示**，而不是 CLI
标志。下面的非交互式等效流程运行 `tesserae init --yes`（集成关闭），在
`.tesserae/config.json` 中启用集成（向导会将它们写入 `memory_backends` 和
`external_tools` 键下——确切的键请参阅集成文档），然后在编译前刷新每一个。

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # 然后在 .tesserae/config.json 中启用 Understand Anything + cognee 并运行:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

最终产物计数：

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

主要节点类型：

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

浏览器验证：

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## 这展示了什么

- 公开安装路径可用。
- `tesserae` shell 命令可用。
- 仓库可以附加一个项目本地的 `.tesserae` 工作区。
- 研究/文档 markdown 和开发代码图谱节点可以共存。
- Markdown、Obsidian、frontend、Graphiti、Cognee、SQLite、report 和 agent-harness 投影由同一条图谱流水线生成。
- 静态 HTML 前端可以在没有 JavaScript 构建步骤的情况下浏览项目图谱。
- 自我改进循环会运行并持久化：decay、access count、recurrence confidence 和 supersede 标志写入 `node_memory` sidecar，而不会扰动 `graph.json`。
- 当安装了 `tesserae[semantic]` 时，混合检索会解析到真正的语义后端（默认 `auto` 顺序：model2vec → sentence-transformers → hash-bucket 桩）；未安装时，嵌入检索降级为非语义的 hash-bucket 桩并发出醒目的警告。
