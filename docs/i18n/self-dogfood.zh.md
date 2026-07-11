# Self-dogfood 演示

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
这个项目可以索引自己。self-dogfood 流程证明了 Tesserae 可以被安装、在自己的仓库内完成设置、摄取自己的 docs/source/tests/scripts、（可选地）刷新 RAG-Anything、编译图产物，并构建静态 Web 前端。

同一流程还兼作多模态冒烟测试。在安装了 RAG-Anything（`tesserae setup --install raganything`）并在 `.tesserae/config.json` 中启用（`memory_backends.raganything.enabled: true`）之后，dogfood 编译会把 RAG-Anything 指向 Tesserae 自己的 `docs/` markdown，加上 `docs/assets/` 和项目级 `assets/` 图片。这就在一个真实的、项目自有的非代码语料上验证了多模态流水线——覆盖了文本优先的来源加载器会跳过的截图和示意图——而无需发明一套单独的测试夹具。

它还演练了自我改进循环。每次编译都会把可变的记忆状态——`decay_score`、`access_count`、`confidence` 和 `superseded` 标志——重新推导进 `.tesserae/sqlite.db` 内的 **`node_memory` 边车**表。这些标量*只*存在于边车中，从不进入 `graph.json`，因此新的 dogfood 编译在图谱上逐字节相同，而边车则跟踪衰减和复现。在 `>= 3` 个不同会话中复现的洞见会以 `(0, 1]` 区间内的数值置信度得到强化（3 个会话 → `0.5`，4 个 → `0.75`，5 个以上 → `1.0`，封顶），写入边车并由 MCP `fresh_insights` 工具呈现，该工具默认隐藏被更新的近重复项取代的发现。

## 命令

在仓库根目录下：

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

打开：

```text
http://127.0.0.1:8765/
```

## 生成的工作区

self-demo 把生成的产物写在：

```text
.tesserae/
```

关键产物：

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
```

生成的工作区刻意默认不提交。它可以用上述命令从仓库源码中复现。

## 最近验证的运行

于 `2026-04-27 11:11:23 KST` 在 Tesserae 仓库自身上验证。

集成的启用（RAG-Anything）现在是**交互式向导提示**，不再是 CLI 标志。下面的非交互式等价流程先运行 `tesserae init --yes`（集成 OFF），再在 `.tesserae/config.json` 中启用这些集成（向导把它们写在 `memory_backends` 和 `external_tools` 键下——确切的键见各集成文档），然后在编译前逐一刷新。

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable the optional integrations in .tesserae/config.json and run:
                 #   tesserae integrations refresh raganything
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
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

排名前列的节点类型：

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

## 这证明了什么

- 公开安装路径可用。
- `tesserae` shell 命令可用。
- 一个仓库可以挂接一个项目本地的 `.tesserae` 工作区。
- 研究/文档 markdown 与开发代码图节点可以共存。
- Markdown、Obsidian、前端、Graphiti、SQLite、报告和 agent-harness 投影都由同一条图流水线产出。
- 静态 HTML 前端无需 JavaScript 构建步骤即可浏览项目图谱。
- 自我改进循环运行并持久化：衰减、访问计数、复现置信度和取代标志都落入 `node_memory` 边车而不扰动 `graph.json`。
- 当安装了 `tesserae[semantic]` 时，混合检索解析到真正的语义后端（默认 `auto` 顺序：model2vec → sentence-transformers → 哈希桶存根）；没有它，嵌入检索退化到非语义的哈希桶存根并发出显眼的警告。
