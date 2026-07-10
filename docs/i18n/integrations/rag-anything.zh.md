# RAG-Anything 多模态配套工具

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) 是一个基于 LightRAG 的多模态 RAG 框架，通过 MinerU/Docling/PaddleOCR 解析 PDF、Office 文档、图像和公式。Tesserae 同时把它作为多模态摄取流水线（UA 风格的原生图投影）和与 Cognee 并行的运行时记忆后端进行集成。

## 为什么两者都用？

- Tesserae —— 长期存在的智能体记忆、wiki 编译、图谱投影。
- RAG-Anything —— 多模态摄取 + LightRAG 运行时检索。

两者互补：RAG-Anything 带来 Tesserae 文本优先源加载器无法提供的 PDF/Office/图像理解；Tesserae 维持跨会话仍然存在的、可查询的长期记忆。

## 当前低摩擦工作流

推荐路径是设置向导：

```bash
tesserae init
```

RAG-Anything 现在是**交互式向导提示**，而不是一组 CLI 标志。向导运行时，请回答
集成提示：

- 在提示时启用 RAG-Anything；
- 在询问时安装它（安装 `raganything` + `docling`）；
- 选择解析器 `mineru`；
- 在提供时启用安装后的刷新运行。

然后编译：

```bash
tesserae compile
```

对于非交互式自动化（CI），使用默认值运行向导（所有可选集成均关闭），然后在
`.tesserae/config.json` 中启用 RAG-Anything——向导会把集成配置写入
`external_tools` / `memory_backends` 键下（见本文引用的这些键）——并运行受管理的刷新：

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

设置向导会同时安装 `raganything` 和 `docling`。MinerU 保持可选：只有当你有 PDF 或图像需要摄取时，才用 `pip install 'mineru[core]'` 安装它。

Tesserae 存储一个受管理的刷新命令，而不是要求用户自行构造：

```bash
tesserae integrations refresh raganything --parser mineru
```

编译期间，Tesserae 会：

1. 检查 `.tesserae/external/raganything/manifest.json` 是否存在并与当前 git 提交匹配（通过存储的 `meta.json#gitCommitHash`）；
2. 在缺失/过期或传入 `--refresh-external-tools` 时运行受管理的刷新包装命令；
3. 发现非代码源（PDF、Office 文档、图像、markdown）并通过配置的解析器进行解析；
4. 写入 `manifest.json` + `meta.json`；
5. 继续正常的记忆编译。

你可以在编译前强制运行所有已配置的外部刷新命令：

```bash
tesserae compile --refresh-integrations
```

## 手动等效流程

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## 编译时 vs 运行时

Tesserae 把这项集成干净地拆成两部分：

- **编译时解析**（`refresh-raganything` 和 `compile`）：直接运行解析器——对 `.md/.txt/.rst` 做原生读取，其余一切交给 `docling.DocumentConverter`。这里*不会*调用 RAG-Anything 的完整流水线，因此编译成功不需要任何 LLM/嵌入/视觉密钥。
- **运行时查询**（`project ask`）：`raganything_query.py` 用项目配置的 LLM/嵌入/视觉函数实例化 `RAGAnything`，并针对 LightRAG 的存储运行 `aquery`。这条路径需要 API 密钥。

这种拆分意味着 `compile` 快速、确定且无需密钥；只有检索时的操作才消耗 LLM token。

## 原生图同步

当配置的工具使用 `sync_mode: native_graph` 时，Tesserae 会在 compile 期间原生导入解析后的 manifest。

原生适配器读取 `.tesserae/external/raganything/manifest.json`，把每个解析后的文档投影为一个带有多模态块元数据的 `SourceFile` node，并写入 sync manifest：

```text
.tesserae/external/raganything-sync.json
```

当前映射：

| RAG-Anything | Tesserae 方向 |
|---|---|
| `documents[*]` | `SourceFile` node，`metadata.parser="raganything"` |
| `content_list[type=text]` | 折入 `SourceFile.description`；concepts 通过现有提取器生成 |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]`（`img_path`、`caption`） |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]`（`table_body`、`caption`） |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` 和 `metadata.equations[]`（保留 LaTeX） |

每个节点都保留 provenance：

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

注意：交互式图谱视图默认隐藏 `sources` 组的节点，以聚焦于概念和实体——投影出来的 raganything SourceDocument 仍留在 `graph.json` 中（MCP、Cognee、搜索、每页 wiki 视图仍能看到它们），只是不会淹没画布。在 `.tesserae/config.json` 中设置 `graph_view.show_sources = true` 可恢复密集视图。

## 运行时记忆后端

`memory_backends.raganything`（由 `default_raganything_backend_config` 生成的默认配置）与 Cognee 共存。`project ask` 按优先级顺序尝试各后端；每个项目的优先级可以通过 `memory_backends.priority` 设置。RAG-Anything 是可选启用的（默认 `enabled: false`）；设置标志 `--with-raganything` 会将其打开。

### LLM 提供方（无需 API 密钥）

RAG-Anything 的运行时后端需要一个 LLM 来回答查询。Tesserae 默认使用其既有的基于 OAuth 的 CLI 集成——无需 API 密钥：

| 提供方 | 认证方式 | 设置标志 |
|---|---|---|
| `codex`（默认） | `codex` CLI OAuth（你用 `codex login` 登录过一次） | `--raganything-llm-provider codex` |
| `claude` | `claude -p` CLI；尊重 `CLAUDE_CONFIG_DIR` 以支持多账户设置 | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

对于多账户 Claude 设置（例如 `~/.claude-personal1`、`~/.claude-personal2`），在设置时传入 `--raganything-claude-config-dir <path>`。运行时后端会在每次调用前导出 `CLAUDE_CONFIG_DIR=<path>`，从而使用所选账户的认证，而不触碰你默认的 `~/.claude`。

### 嵌入

| 提供方 | 何时使用 |
|---|---|
| `deterministic`（默认） | 无外部依赖。基于哈希；语义质量低，但足以让 LightRAG 构建索引。适合作为验证集成可用的基线。 |
| `ollama` | 本地运行的 Ollama 且带有嵌入模型（例如 `nomic-embed-text`）。传入 `--raganything-embedding ollama`；后端默认使用 `http://localhost:11434`。 |

v1 中这些标志没有直接接入 OpenAI 嵌入——持有 OpenAI 密钥的用户可以设置 `OPENAI_API_KEY` 并直接在 `.tesserae/config.json` 中覆盖 `memory_backends.raganything.embedding.provider`（RAGAnything 会通过 LightRAG 的默认行为读取该环境变量）。

### 从 CLI 调用

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend cognee
tesserae query "..." --backend wiki
```

> **Cognee 1.0 搜索类型。** Cognee 1.0 移除了旧的 `INSIGHTS` 检索器，因此
> Tesserae 将 cognee 后端默认设为 `GRAPH_COMPLETION`（在知识图谱之上合成的
> 答案）。如需原始检索而非生成式答案，传入
> `--cognee-search-type CHUNKS`（或 `SUMMARIES`）。

`tesserae query --backend raganything` 直接调用 `tesserae.raganything_query.query`。`memory_backends.raganything` 中的相对 `working_dir` 会在调用前基于项目根目录解析。

### 顶层 `ask`（使用多项目注册表）

对于希望跨多个已注册 Tesserae 项目提问而无需逐一 `cd` 进入的工作流，顶层 `tesserae ask` 命令通过与 MCP 服务器共享的持久注册表解析项目：

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

分派逻辑——`--project > --name > router`——实现在顶层 ask 处理器中，答案格式化通过 `tesserae.query.ask_project` 与 MCP `ask` 工具共享（记忆后端只能通过 `tesserae query --backend …` 触达）。注册表是文件支持的（默认 `~/.tesserae/registry.json`），因此它跨会话持久，并与 MCP 服务器的项目列表保持同步。

#### 跨多个 vault 查询（`--scope all-registered`）

Bet B2——当你有多个已注册项目（研究 vault、工作 vault、副业 vault），想对它们全部问同一个问题时，使用 `--scope all-registered`：

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

处理器按字母序遍历已注册项目，对每个项目调用 `ask_project`，并聚合各项目的返回信封。单个项目的失败——缺少配置、未启用 RAG-Anything、Cognee 宕机——会以 `{"error": "..."}` 的形式记录在该别名的槽位中，绝不会中止其余的扇出。MCP 的 `ask` 工具接受同样的 `scope` 参数，因此由 MCP 驱动的编码智能体无需额外管道即可获得同样的扇出能力。

### 多项目注册表（`tesserae projects`）

| 命令 | 用途 |
| --- | --- |
| `tesserae projects list [--json]` | 显示已注册项目（一律平等——没有"活动"项目）。 |
| `tesserae projects register <path> [--name <alias>]` | 把项目加入注册表；别名默认为净化后的目录名。 |
| `tesserae projects unregister <name>` | 从注册表中移除一个条目。 |

这些命令直接操作 `tesserae.mcp_server.ProjectRegistry`——没有 MCP 往返——因此无需运行 MCP 服务器即可脚本化。

### 从 MCP 调用

stdio MCP 服务器暴露了带有相同后端选择器的 `ask` 工具：

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

分派顺序（`raganything` → `cognee` → 编译后的 wiki 搜索）和 `working_dir` 解析与 CLI 处理器完全一致，因此编码智能体和人工操作者会收敛到相同的答案。

## 系统先决条件

- RAG-Anything 需要 **Python 3.10+**（上游 `raganything` 包 ≥1.3.0 传递依赖 `mineru[core]`，后者要求 Python 3.10+）。在更老的 Python 上，Tesserae 会以清晰的警告禁用该集成，而不是悄悄安装一个损坏的占位符。
- 用于解析 `.doc/.docx/.ppt/.pptx/.xls/.xlsx` 的 **LibreOffice** —— 通过你平台的包管理器单独安装。缺少 LibreOffice 时，RAG-Anything 会跳过 Office 文档并发出警告。
- **MinerU 模型权重**会在首次解析时下载并缓存（约数 GB）。后续运行复用缓存。
- 运行时记忆后端所需的 **OpenAI 兼容 LLM/嵌入/视觉密钥**（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）。仅解析模式不需要密钥。

## 解析器路由

Tesserae 按文件扩展名把源自动路由到合适的解析器：

| 扩展名 | 解析器 | 原因 |
|---|---|---|
| `.md`、`.markdown`、`.txt`、`.rst` | `docling` | 轻量；无需下载 MinerU 模型。 |
| `.doc`、`.docx`、`.ppt`、`.pptx`、`.xls`、`.xlsx` | `docling` | 按上游说法能更好地保留 Office 结构。 |
| `.pdf`、`.png`、`.jpg`、`.jpeg`、`.gif`、`.bmp`、`.tiff`、`.webp` | 配置的默认值（`--raganything-parser`，默认 `mineru`） | OCR + 表格提取。 |

受管理的 `tesserae integrations refresh raganything` 包装器暴露 `--parser`（PDF/图像的配置默认值）、`--parse-method {auto,ocr,txt}`、`--root`（可重复，限制到某个子树）、`--force` 和 `--full`。文本/Office 两个桶的路由是固定的（都默认为 `docling`）。若要显式覆盖文本或 Office 解析器，直接调用底层模块——`python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>`——它暴露这两个额外标志。配置的默认值仍然适用于 PDF 和图像。

在解析循环开始前，Tesserae 会探测每个所需解析器的 Python 包是否可导入（`importlib.import_module(...)`），并快速失败，用一条聚合错误列出每个缺失的解析器及其安装命令。我们有意不使用上游的 `RAGAnything.check_parser_installation()`，因为它只检查实例上配置的那个解析器，并混入了不适合预检阶段的模型权重就绪检查。

Tesserae 还会根据实际的路由分布（被选中次数最多的解析器获胜）来挑选 `RAGAnything` 构造时的解析器，而不是直接采用 `--raganything-parser`。这避免了一种失败模式：`RAGAnything.__init__` 尝试初始化一个模型权重尚未落盘的重型解析器（例如 `mineru`），在每次调用的 `parser=` 覆盖生效之前就让整个运行崩溃。`--raganything-parser` 标志仍然控制非文本、非 Office 源（PDF、图像）的默认值。

### 解析器包

编译时解析路径对每个非文本源直接使用 `docling.DocumentConverter`；安装一次即全覆盖：

| 解析器 | 安装命令 |
|---|---|
| `docling`（除原生文本外一切内容的编译时默认值） | 运行 `--with-raganything --install-raganything` 时随附安装（或单独 `pip install docling`） |
| `paddleocr`（可选的 OCR 替代方案） | `pip install 'raganything[paddleocr]>=1.3.0'` 加上 `pip install paddlepaddle`（平台特定的 wheel） |

> 注意：`mineru` 当前**不在编译时调用**。编译路径绕过了 RAG-Anything 的完整流水线（那需要 LLM/嵌入/视觉可调用对象），把每个非文本源直接路由给 docling。MinerU 支持保留给未来的直接导入路径，用于摄取外部生成的 `content_list.json`。

当配置的解析器缺失时，`refresh-raganything` 会快速失败——用一条错误列出每个缺失的解析器及正确的安装命令——而不是级联出逐文件的失败。

### 每页 ask 小组件

每个详情页（concept、paper、repo、synthesis、entity、topic、question、source）都包含一个内联的"就本页提问"小组件。它向本地 `tesserae serve` 实例上的 `/api/ask` 发 POST 请求，后者调用 `tesserae.query.ask_project` 并把答案内联渲染出来。与 CLI 不同（`tesserae ask` 默认使用 LLM），出于小组件延迟考虑，`/api/ask` 默认为**非 LLM 检索**；在负载中发送 `{"llm": true}` 可选择进入规划/合成的答案。小组件会把当前页面的节点名作为自然语言上下文提示前置到用户问题上（例如 `` About `<NodeName>`: <question> ``）；未来的 PR 可以把真正的子图作用域接入 `ask_project` 本身。

小组件在加载时通过 `/api/ask/health` 检测后端可用性。当 wiki 以静态方式提供（GitHub Pages、`file://`、S3、任何普通静态主机）时，小组件会折叠为一行提示，把读者指向 `tesserae serve` 做本地交互使用。没有请求会失败，也没有任何东西阻塞页面渲染——小组件是一个延迟加载的 JS 孤岛，与更重的图谱 bundle 分离。

把它与多项目注册表（`tesserae projects register`）搭配使用，你就可以从任意已注册项目的任何详情页向该项目的 wiki 提问。

## 协作原则

Tesserae 仍然是 memory compiler。RAG-Anything 仍然是独立的配套工具：多模态解析器 + LightRAG 检索引擎。
