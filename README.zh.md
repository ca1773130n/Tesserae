# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae 图谱视图" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

[在线演示](https://ca1773130n.github.io/Tesserae) · [文档](docs/) · [MCP 配置](docs/i18n/integrations/mcp.zh.md) · [Obsidian 导出](docs/i18n/integrations/obsidian.zh.md)

Tesserae 是一个项目记忆编译器。把一个包含 Markdown、源代码以及可选 PDF/Office 文档/图片的目录交给它，它会提取一个类型化的知识图谱，写出一份可查询的 wiki，并产出可移植的工件：Markdown 投影、面向 Cognee 的 bundle、智能体 harness，以及一个 MCP 服务器——你可以把它接到 Claude Code、Codex 或任何 MCP 客户端。它是为项目上下文准备的构建步骤，而不是托管服务。

## 何时使用（以及何时不要使用）

适合在以下情况使用：

- 你希望为单个项目中以文本为主的来源（文档、代码、研究笔记）建立一个持久、可检视的知识图谱。
- 你需要一个本地 MCP 服务器，依据自己的文件来回答问题。
- 你想给 Cognee 灌入一份干净的 bundle，或者把 Markdown 投影放进 Obsidian，而不必自己写胶水代码。

下列情况请跳过：

- 只需要在一个小目录上做向量检索 —— `ripgrep` 加一个 embedding 库就够了。
- 想要一个带编辑界面的托管 wiki。这里的静态站点是只读的。
- 期望开箱即用的高质量语义 embeddings。默认的 RAG-Anything embedding 是确定性的（见[状态](#状态)）。
- 期望一个一键式的"什么都问"智能体。这套工具构建的是底座，最终接入哪个智能体仍由你决定。

## 状态

这是一个不断演进的研究/智能体工具项目。已知限制：

- 编译时间大致与语料规模成线性关系。在大型 Markdown 树（数千个文件）上的首次编译可能需要几分钟。
- RAG-Anything 默认的 embedding provider 是 `deterministic`。它可复现且无外部依赖，但语义召回有限。要获得更好的检索质量，请切换到 `ollama`（例如 `qwen3-embedding:0.6b`）或 OpenAI 兼容端点 —— 见 [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)。
- RAG-Anything 的视觉支持（图像内容抽取）尚未端到端打通。图像文件只做结构化解析，不会被描述。
- Cognee runtime cognify 是 best-effort：缺失的 provider、付费 API 密钥或网络故障只会被记录并跳过，不会中断构建。
- MCP 服务器暴露的工具集合是稳定的，但底层图谱 schema 仍可能继续扩充。

## 快速开始

需要 Python 3.9 以上。若启用 RAG-Anything，则需要 Python 3.10 以上。

```bash
pip install tesserae

cd /path/to/my-project
tesserae project setup
tesserae project compile
tesserae project ask "Where is Mermaid rendering implemented?"
tesserae project build-site && tesserae project serve --port 8765
```

setup 向导会检测常见来源（`README.md`、`docs/`、`src/`、`data/`），并写入 `.tesserae/config.json`。涉及 LLM 调用的功能默认使用基于 OAuth 的 `codex` CLI，因此常规路径无需 API key。更详细的内容见 [docs/quickstart.md](docs/quickstart.md) 和 [docs/installation.md](docs/installation.md)。

> [!tip]
> **安装后 `tesserae: command not found`?** `pip` 把二进制文件放在了 shell 不搜索的位置。**任何平台**上最可靠的方法是 [`pipx`](https://pipx.pypa.io/) — 它把 CLI 工具安装到隔离的 venv 中并自动管理 `PATH`:
>
> ```bash
> # macOS — `brew install pipx`
> # Ubuntu / Debian — `sudo apt install pipx`
> # 其他 — `python3 -m pip install --user pipx`
> pipx ensurepath          # 将 ~/.local/bin 加入 PATH;之后请打开新 shell
> pipx install tesserae
> ```
>
> **Ubuntu 23.04+** 使用普通 `pip install tesserae` 时常遇到的问题:
>
> | 错误 | 原因 | 解决方法 |
> |---|---|---|
> | `error: externally-managed-environment` | PEP 668 — 系统 Python 被锁定 | 使用 `pipx`(如上),或 `pip install --user --break-system-packages tesserae`(不优雅),或 venv |
> | `pip install --user …` 后 `tesserae: command not found` | `~/.local/bin` 不在 `PATH` 中 | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
> | Ubuntu 20.04 上 `ModuleNotFoundError: pydantic` | 系统 `python3` 是 3.8,tesserae 需要 ≥3.9 | `sudo apt install python3.11 python3.11-venv` 然后 `python3.11 -m pip install --user tesserae` |


## 编译后你会得到什么

```text
.tesserae/
  config.json
  graph.json              # 类型化的节点/边
  manifest.json           # 源文件指纹（被 --changed-only 使用）
  sqlite.db               # 可查询的图存储
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/    # 人类可读的 wiki 页面
  obsidian_vault/         # 可直接放进 Obsidian 的 vault
  agent_harness/          # 各智能体的配置（Claude/Codex/Gemini/Cursor/...）
  harness_sessions/       # 导入的 Claude/Codex 会话记忆
  cognee_bundle/          # 可供 Cognee ingest 的 JSONL
  site/                   # build-site 生成的静态站点
  external/               # 配套工具的产物（UA、RAG-Anything）
```

`project compile` 之后用 `ls .tesserae/` 即可确认实际生成的内容。

## CLI 概览

日常使用的命令。完整 flag 用 `tesserae <subcommand> --help` 查看。

| 命令 | 作用 |
|---|---|
| `tesserae project setup` | 交互式向导。写出 `.tesserae/config.json`。接受 `--with-understand-anything`、`--with-raganything`、`--run-cognee` 等。 |
| `tesserae project compile` | 读取已配置的来源，运行配套工具刷新，把所有工件写入 `.tesserae/`。增量重建请使用 `--changed-only`。 |
| `tesserae project build-site` | 在 `.tesserae/site/` 构建静态前端。 |
| `tesserae project serve --port 8765` | 本地提供静态站点。 |
| `tesserae project refresh-understand-anything` | 运行 Tesserae 托管的 Understand Anything 刷新包装器。 |
| `tesserae project refresh-raganything --parser mineru` | 通过 RAG-Anything 重新解析非代码来源（PDF、Office、图像）。 |
| `tesserae project ask "<question>"` | 向已配置的后端（`auto`/`raganything`/`cognee`/`wiki`）提问。 |
| `tesserae project mcp-config` | 打印可以粘贴到 Claude Code、Codex 或 Hermes 的 MCP 服务器配置片段。 |
| `tesserae wiki register <path> --name <alias>` | 把项目注册到共享 registry。 |
| `tesserae wiki list` / `tesserae wiki activate <name>` | 列出已注册项目；设置当前激活项目。 |
| `tesserae ask "<question>" [--wiki <name>]` | 通过 registry 解析的顶层 ask 命令。 |

## 集成

所有集成都是可选的（opt-in）。在普通的 Markdown/代码项目上使用 Tesserae，它们都不是必需的。

- **Understand Anything** —— 一个独立项目（[Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)），会在 `.understand-anything/knowledge-graph.json` 写出代码知识图谱。用 `--with-understand-anything` 启用。Tesserae 会保存一个托管的刷新包装器，使 `project compile` 始终保持该图谱最新。见 [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)。
- **RAG-Anything** —— 多模态摄入（[HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything)），通过 MinerU/Docling/PaddleOCR 处理 PDF、Office 文档和图像。用 `--with-raganything` 启用。也作为运行时问答后端（LightRAG）。需要 Python 3.10 以上。见 [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)。
- **Cognee** —— 图+向量记忆后端。用 `--run-cognee --install-cognee` 启用。普通 compile 始终写出 `.tesserae/cognee_bundle/`；运行时 `cognify` pass 是 best-effort，只有在显式启用时才执行。

## 多项目 registry

位于 `~/.tesserae/registry.json` 的持久 registry 让顶层 `ask` CLI 和 MCP 服务器无需每次都加 `--project`，就能把项目名解析为实际路径。

```bash
tesserae wiki register /path/to/my-project --name myproj
tesserae wiki activate myproj
tesserae ask "Where is the parser entry point?"
```

MCP 服务器读取同一份 registry，所以 MCP 客户端可以对任意已注册 wiki 调用 `list_projects`、`activate_project`、`ask`。

## MCP

`tesserae project mcp-config` 会打印可以粘贴到 Claude Code、Codex 或任何 MCP 兼容客户端的服务器条目。服务器暴露以下工具：`schema`、`graph_summary`、`search_nodes`、`node_context`、`search_facts`、`timeline`、`wiki_page`、`raw_source`、`lint_report`、`ask`，以及 registry 相关工具 `list_projects` / `register_project` / `activate_project` / `unregister_project`。需要明确项目的工具，会使用与 CLI 相同的 registry 解析。

## 认证与 LLM provider

常规路径不需要 API key：

- **Codex CLI**（默认）使用 OAuth。`--raganything-llm-provider codex` 是默认值；Cognee 的 `codex_cognify` 模式会把 Cognee 的 LLM 客户端补丁到 Codex CLI。
- **Claude Code CLI** 使用 OAuth。把 RAG-Anything 运行时查询切换到 Claude，请设置 `--raganything-llm-provider claude`。多账号场景使用 `--raganything-claude-config-dir ~/.claude`（Tesserae 会在每次调用前导出 `CLAUDE_CONFIG_DIR`）。
- **Embeddings** 默认采用进程内的确定性 provider。可以切到 Ollama：`--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b`；也可以接 OpenAI 兼容端点 —— 两种方式都在集成文档中有说明。

如果设置了 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`，相应路径会自动识别，但它们不是必需。

## 项目布局

```text
tesserae/        # 包本体（CLI、编译器、MCP 服务器、各 adapter）
docs/            # 英文文档 + 六种其他语言的 docs/i18n/
ontology/        # 编译器据以校验的节点/边 schema
prompts/         # 抽取与综合 prompt
scripts/         # 维护脚本
tests/           # pytest 套件
evals/           # 图谱质量评测 harness
data/            # 自我 dogfood 所用的示例研究笔记
```

## 本地化文档

[English](./README.md) ·
[한국어](./README.ko.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md)

长文档分别镜像在 `docs/i18n/` 与 `docs/i18n/integrations/`。

## 许可证

MIT。见 [LICENSE](LICENSE)。
