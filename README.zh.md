# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae 图谱视图，展示概念、论文、仓库、合成内容和实体围绕焦点节点聚集" width="100%" />
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

> 一款上下文引擎，为你的项目维护自我改进的知识库，并按需编译智能体可直接使用的上下文。

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="三步操作的录屏演示：tesserae init → compile → ask，基于 135 个文档的演示语料库录制" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">在线演示</a> ·
  <a href="docs/">文档</a> ·
  <a href="docs/release-notes/">版本说明</a> ·
  <a href="docs/integrations/mcp.md">MCP 配置</a> ·
  <a href="docs/tuning.md">调优</a> ·
  <a href="docs/integrations/obsidian.md">Obsidian 导出</a>
</p>

## 是什么

将 Tesserae 指向一个包含 Markdown、源代码以及可选 PDF/Office 文档/图片的目录，它会重建该项目的**类型化知识图谱**并保持其最新状态，让智能体始终能获得有据可查、来源清晰的上下文。三大支柱：

1. **会话监控** —— 你与 Claude Code / Codex 关于项目的对话，在产生的那一刻就成为一级图谱节点（决策、洞见、问题、TODO）。
2. **自主摄入** —— 一个受监督的引擎监视源文件和会话，合并突发变更并重新编译；自我改进的边车（sidecar）强化反复出现的发现，同时取代（supersede）过时内容。
3. **按需上下文** —— 上下文编译器为任意查询或种子节点组装一份带引用的定制上下文文档（在字符预算内做 Personalized PageRank 扩展），可直接粘贴到任何智能体中。

类型化图谱、Obsidian vault 和静态站点都是同一知识库的*投影*。所有内容本地运行；这是一个构建步骤加实时引擎，而非托管服务。

## 快速开始

需要 **Python 3.10+**。

```bash
pip install tesserae          # 加上 [semantic] 可启用真正的向量嵌入
# 或: pipx install tesserae   # 最简洁的 PATH 安全安装方式
# 或: npx @jokerized/tesserae # 同一 CLI 的 Node 包装器

cd /path/to/my-project
tesserae init --yes           # 向导；--yes 接受检测到的默认值
tesserae compile              # 构建知识图谱
tesserae ask "Mermaid 渲染是在哪里实现的？"

# 为查询编译定制的带引用上下文文档：
tesserae context "解析器如何处理 arXiv ID？" --budget 32000 -o context.md

tesserae serve --port 8765    # 在本地浏览图谱 + wiki
```

LLM 支持的功能默认通过 OAuth 使用 `codex` / `claude` CLI —— 常规路径**无需 API 密钥**。详见 [docs/quickstart.md](docs/quickstart.md) 和 [docs/installation.md](docs/installation.md)。

<details>
<summary><strong>安装后出现 <code>tesserae: command not found</code>？Linux 注意事项？</strong></summary>

在任何平台上最可靠的方法是使用 [`pipx`](https://pipx.pypa.io/)：

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # 将 ~/.local/bin 加入 PATH；之后请打开新的 shell
pipx install tesserae
```

使用普通 `pip install tesserae` 时 Ubuntu 的常见问题：

| 错误 | 原因 | 解决方法 |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 —— 系统 Python 被锁定 | 使用 `pipx`（见上文）或 venv |
| `pip install --user …` 后 `command not found` | `~/.local/bin` 不在 `PATH` 中 | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| 旧发行版上的 `ModuleNotFoundError` | 系统 `python3` 低于 3.10 | `sudo apt install python3.11 python3.11-venv`，然后用 `python3.11 -m pip` 安装 |

</details>

<details>
<summary><strong>操作演示 GIF</strong> —— 基于内置 135 个文档演示语料库，演示每个快速开始步骤</summary>

<details>
<summary>1. 初始化 —— 指向研究目录，生成项目 wiki 脚手架</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research 非交互式运行并写入 .tesserae/ 的过程" width="100%" />
</details>

<details>
<summary>2. 编译 + 构建站点 —— 确定性，无需 LLM 调用</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile 后接 tesserae export site，生成 graph.json 和静态站点树" width="100%" />
</details>

<details>
<summary>3. Ask —— 从 CLI 查询已编译的 wiki</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki 返回带分数、类型和出站关系的前 3 个结果" width="100%" />
</details>

使用 `vhs docs/screencasts/<name>.tape` 可重新生成任意 GIF。

</details>

## 常用命令

运行 `tesserae --help` 查看完整分组列表，`tesserae <cmd> --help` 查看各命令的参数。

| 命令 | 说明 |
|---|---|
| `tesserae init` | 设置向导 → `.tesserae/config.json`。`--yes` 非交互式，`--bare` 最小配置。 |
| `tesserae compile` | 重建知识图谱和所有工件。`compile <paths>` 可临时摄入额外文件。 |
| `tesserae ingest <file\|url>` | 将单个文档或网页合并到知识库，无需完整重新编译（奇偶门控增量快速路径）。 |
| `tesserae context "<query>"` | **按需上下文编译器**：在 `--budget` 内通过 PPR 扩展生成带引用的上下文文档；`--synthesize` 添加 LLM 摘要。 |
| `tesserae ask "<question>"` | 查询已编译的知识库（`--scope all-registered` 可跨所有项目扇出）。 |
| `tesserae engine` | 当前项目的受监督刷新守护进程：监视、防抖、重新编译。 |
| `tesserae engine --all` | **Fleet 模式**：单进程保持*所有*已注册项目最新 —— 注册表热重载，`--compile-slots` 节流。 |
| `tesserae refresh` | 一次性流水线：导入新会话 → 编译 → 同步 vault。 |
| `tesserae sessions discover --import` | 查找并导入此项目的本地 Claude Code / Codex 会话历史。 |
| `tesserae export site` | 构建静态站点（`--deploy`，`--watch`）。 |
| `tesserae serve` | 使用内联 ask 小部件在本地提供服务（`/api/ask`）。 |
| `tesserae projects …` | 多项目注册表：`register`、`list`、`activate`、`mcp-config`。 |
| `tesserae integrations refresh …` | 重新运行配套工具（Understand-Anything、RAG-Anything）。 |

## 自动保持最新

引擎是让知识库实现*自我改进*而非一次性构建的关键：

```bash
# 单个项目：监视源文件 + 实时会话，变更时重新编译。
tesserae engine

# 所有已注册项目，单进程（v0.8.0）：
tesserae engine --all --compile-slots 1
```

Fleet 模式每 10 秒与 `~/.tesserae/registry.json` 对账 —— 注册或移除项目无需重启即可生效 —— 并将各项目的编译序列化，避免并发 LLM 提取抢占共享账户的速率限制。首次运行时会扫描一次会话历史（日志中会注明）；重启后从持久化的基准位置恢复。

## 编译后的输出

```text
.tesserae/
  graph.json              # 类型化节点/边（知识库）
  sqlite.db               # 可查询的图谱存储
  markdown_projection/    # 人类可读的 wiki 页面
  obsidian_vault/         # 可直接拖入 Obsidian
  site/                   # 静态站点（图谱视图 + wiki + 搜索）
  harness_sessions/       # 已导入的 Claude/Codex 会话记忆
  agent_harness/          # 各智能体上下文配置（Claude/Codex/Gemini/...）
  cognee_bundle/          # 准备好供 Cognee 摄入的 JSONL
  config.json · manifest.json · report.md · …
```

## MCP 服务器

`tesserae projects mcp-config` 会打印供 Claude Code、Codex 或任何 MCP 客户端使用的服务器条目。主要工具：

- **`compile_context`** —— 针对查询或种子节点的定制带引用上下文文档
  （除非 `synthesize=true` 否则具有确定性），由 `graph_ppr` 支撑。
- **图谱 + wiki**：`search_nodes`、`node_context`、`graph_summary`、
  `wiki_page`、`raw_source`、`timeline`、`search_facts`、`lint_report`、`ask`。
- **会话记忆**：`list_sessions`、`find_session_findings`、
  `fresh_insights`（衰减排名，去重）。
- **注册表**：`list_projects`、`register_project`、`activate_project`。

## 多项目

`~/.tesserae/registry.json` 中的注册表在 CLI、MCP 和 fleet 引擎中统一解析项目名称：

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # 跨所有项目扇出
```

一个项目中的 Markdown 可以通过 `wiki://<alias>/<kind>/<slug>` 深度链接另一个项目中的节点；编译时这些链接会成为图谱视图中的桥接节点。详见 [docs](docs/)。

## 集成（均为可选）

- **Claude Code 插件** —— 一次 `/plugin install` 即可获得斜杠命令、会话钩子、技能和 MCP 自动注册。
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **会话图谱** —— Claude Code / Codex 对话 → Insight / Decision /
  Question / TODO 节点，链接到接触过的文档。无需 API 密钥。
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** —— 代码知识图谱摄入。
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** —— 多模态摄入（通过 MinerU/Docling 处理 PDF/Office/图片）和 LightRAG 问答后端。
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** —— 图谱+向量记忆后端；编译总是写入 Cognee 就绪的 bundle，运行时 cognify 为尽力服务。
- **Obsidian** —— 带用户编辑叠加的双向 vault 同步。
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## 横向对比

<details>
<summary>与 Quartz、Logseq、Cognee、Foam 的功能矩阵对比</summary>

| 功能 | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| 静态 HTML 输出 | 是 | 是 | 部分（导出） | 否 | 部分（发布） |
| 内置图谱视图 | 是 | 是 | 是 | 是（独立 UI） | 是（VSCode） |
| 类型化节点模式 | 是（41 种类型） | 否 | 部分（标签） | 是 | 否 |
| 从源文件提取概念 | 是（LLM） | 否 | 否 | 是 | 否 |
| 多模态摄入（PDF/图片） | 是（通过 RAG-Anything） | 否 | 部分（嵌入） | 是 | 否 |
| 代码图谱摄入 | 是 | 否 | 否 | 部分 | 否 |
| MCP 服务器 | 是 | 否 | 否 | 是 | 否 |
| 按需带引用上下文编译器 | 是（PPR + 预算） | 否 | 否 | 否 | 否 |
| 实时会话监控 → 图谱 | 是 | 否 | 否 | 否 | 否 |
| 多项目注册表 | 是 | 否 | 是（图谱） | 部分 | 否 |
| 多项目守护进程（fleet） | 是 | 否 | 否 | 否 | 否 |
| 无 API 密钥（OAuth）运行 | 是 | 不适用 | 不适用 | 否 | 不适用 |
| 确定性字节一致编译 | 是 | 是 | 不适用 | 否 | 不适用 |
| 实时编辑 | 否 | 部分 | 是 | 不适用 | 是 |
| 实时协作 | 否 | 否 | 是（DB 测试版） | 否 | 否 |

</details>

Tesserae 选择从源文件编译而非实时编辑。如果你想在 UI 中编辑笔记，请使用 Logseq 或 Obsidian。如果你想要一个既是构建工具*又是实时引擎*的知识图谱工具，那就是这个项目。

**适合使用的情况：** 你想要在项目的文本密集型源文件上建立持久、可检查的知识图谱；需要一个基于自有文件的本地 MCP 服务器；或者想要无需编写胶水代码即可获得整洁的 Cognee/Obsidian bundle。

**不适合使用的情况：** 你只需要对小目录做向量搜索；想要带编辑 UI 的托管 wiki；或者期望开箱即用的"什么都能问"智能体 —— Tesserae 构建基础设施，你来决定接入哪个智能体。

## 身份验证与 LLM 提供商

常规路径**无需 API 密钥**：

- **Codex CLI**（默认）和 **Claude Code CLI** 通过 OAuth 使用，支持多账户轮换。
- **嵌入**：原生混合检索通过 `pip install "tesserae[semantic]"`（`model2vec`）使用离线、无需 torch 的语义通道。Cognee/RAG-Anything 后端默认使用确定性提供商；切换到 Ollama 或任何兼容 OpenAI 的端点可获得更好的召回率。

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 若存在会自动读取，从不强制要求。

## 现状与限制

当前版本请参阅[版本说明](docs/release-notes/)。已知限制：

- 对大型语料库（数千个文件）的首次编译需要数分钟；编译时间大致线性增长。增量编译（`--changed-only`）已发布但处于实验阶段，默认关闭。
- 没有 `semantic` 扩展时，混合检索会降级为非语义存根（并发出醒目警告）。
- RAG-Anything 视觉功能（图片描述）尚未端到端打通。
- Cognee 运行时 cognify 为尽力服务：缺失的提供商会被记录并跳过，不会导致致命错误。
- MCP 工具集已稳定；图谱模式可能仍会新增节点类型。

## 项目结构

```text
tesserae/        # 包本身（CLI、编译器、引擎、MCP 服务器、适配器）
docs/            # 英文文档 + docs/i18n/ 下的其他七种语言
ontology/        # 编译器验证所用的节点/边模式
prompts/         # 提取和合成提示词
tests/           # pytest 测试套件
evals/           # 图谱质量评估工具
examples/        # 录屏演示使用的演示语料库
```

## 本地化文档

[English](./README.md) ·
[한국어](./README.ko.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

长文档镜像于 `docs/i18n/` 下。

## 许可证

MIT。详见 [LICENSE](LICENSE)。
