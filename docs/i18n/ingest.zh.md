# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
将单个文档文件或 URL 合并到知识库中。

## 用法

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>` 是一个或多个本地文件路径或 `http(s)` URL。URL 会被抓取、转换为 markdown，并带着溯源 front-matter（`source_url`、`fetched_at`、`content_sha256`，以及检测到时的 `arxiv_id`）持久化到 `data/ingested/<slug>.md`，然后再合并。来自项目外部的本地文件会被复制到 `data/ingested/`，从而成为受跟踪的来源（之后的完整编译会逐字节地复现它们）。

URL 摄取需要可选附加组件：

    pip install tesserae[ingest-url]

## 工作原理

默认情况下，`ingest` 通过增量编译合并新来源——它不会重新提取整个语料——且结果与完整编译逐字节相同（一个自动的完整重编译回退保证了增量路径无法处理的任何情况下的正确性）。传入 `--full` 强制对整个语料做完整重编译。

## 标志

- `--full` —— 强制对整个语料做完整重编译。
- `--dry-run` —— 抓取并报告将会摄取什么；不写任何图。
- `--title` —— 覆盖标题，对裸 URL 很有用。
- `--source-kind` —— 覆盖来源分类。

## 概念层（`--extractor`）

Tesserae 是一个 LLM wiki，因此 `compile` **默认构建概念/断言层**（`--extractor llm`）：它通过你配置的 LLM 提供方——**codex / claude / Anthropic API**，取决于 `llm_provider`——阅读每份文档，并生成概念、断言、能力、技术术语、证据片段，以及它们之间的类型化边。正是这一层让图谱能回答*"这是什么想法，它如何关联"*，而不只是*"哪个文件说了它"*。

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

如果没有配置/认证 LLM 后端，compile 会降级到**确定性**提取器（仅结构化——来源、章节、显式链接）并发出警告。你也可以显式要求它——它快速、无需密钥、字节稳定，是 CI / 可复现模式：

    tesserae compile --extractor deterministic

### 选择消耗哪些账户（`llm_claude_config_dirs`）

使用 `claude` 提供方时，Tesserae 会在你已登录的 Claude CLI 账户之间轮换：某个账户
触及速率限制时会切换到下一个，而不是让本次运行的剩余部分退化为确定性提取。默认会
自动发现所有 `~/.claude*` 目录。

**codex** 提供方的工作方式相同：在已认证的 `~/.codex*` 主目录之间轮换（目录必须
包含 `auth.json` 才算数），通过 `llm_codex_homes` 配置。每个提供方使用各自的键，是因为
它们在磁盘上的账户布局不同——Claude CLI 配置目录和 Codex 主目录并不通用：

| 提供方 | 配置键 | 列出的内容 |
|---|---|---|
| `claude` | `llm_claude_config_dirs` | Claude CLI 配置目录（`~/.claude*`） |
| `codex`  | `llm_codex_homes`        | Codex 主目录（`~/.codex*`） |

若要精确控制可以消耗哪些账户以及顺序，请在 `.tesserae/config.json`（项目级）或
`~/.tesserae/config.json`（全局）中设置 `llm_claude_config_dirs`：

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

该列表具有最终权威——列表之外的账户一律不会被尝试。它同样**优先于环境中的
`CLAUDE_CONFIG_DIR`**：该变量会被 Claude Code 会话派生的每个进程继承，否则会把整次
编译锁定在那一个会话的配额上。若未做任何配置，`CLAUDE_CONFIG_DIR` 仍会作为首个尝试
的账户。

当所有已配置账户都报告用量上限时，编译会在本次运行的剩余部分停止调用 LLM，而不是
逐个文档重复询问，并将这些文档标记为 `fallback: true` 并告知你。限额重置后，无需
重新编译全部内容即可恢复：

    tesserae compile --changed-only --retry-fallbacks


**成本感知（`selective-llm`）**——只把匹配的文档路由给 LLM，其余走确定性提取：

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

同样的标志适用于 `tesserae extract <paths>`（独立运行）和 `tesserae compile <paths>`（临时路径摄取）。

**调优：**

- `--llm-provider codex|claude|anthropic` —— 覆盖提供方（默认：配置中的 `llm_provider`）。
- `--llm-model` —— 提取器使用的模型（默认：提供方的默认模型）。
- `--llm-include <glob>` —— 对 `selective-llm`，哪些文件经过 LLM（可重复多次；模式匹配绝对路径中的任意位置，例如 `"*docs/superpowers*"`）。
- `--llm-limit N` —— 限制到达 LLM 的文件数（其余保持确定性提取）。

**没有默认超时。** 一份大型设计文档会生成大量 JSON，可能需要数分钟；提取会运行到完成而不是被悄悄截断（超时仅为可选项）。

**在真实语料上稳健。** 一份嘈杂或缓慢的文档绝不会中止整个编译：某份文档上的 LLM 失败（认证、错误、无法解析的生成结果）会让*该*文档回退到确定性基线，受控词汇表之外的边或节点类型会被丢弃，而按内容哈希的缓存意味着重新编译未变化的文档会复用先前的提取结果。

> `claude-cli` / `selective-claude` 提取器名称（以及 `--claude-*` 标志）是 `llm` / `selective-llm`（以及 `--llm-*`）的已弃用别名；它们仍然可用，但会发出弃用提示。

## 管理编译范围（`sources`）

`tesserae compile`（无参数）编译项目 `sources` 列表中的目录。用 `sources` 子命令管理该列表——**本地或全局**：

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

项目内部的路径以项目相对形式存储（可移植）；项目外部的任何路径以绝对形式存储。两者都在编译时解析，因此全局来源的编译方式与本地来源完全一样。（添加时按解析后的位置去重，因此同一目录的绝对形式和 `../` 相对形式永远不会被重复计数。）

## 相关命令

- `tesserae compile`（无参数）重新提取整个受跟踪语料。
- `tesserae ingest <x>` 增量添加一个来源。
- `tesserae code ingest` 从 Python 源码生成代码图（一个不同的命令）。适用于通过 `codegraph` 的 `external_tools` 条目启用代码层的项目。
