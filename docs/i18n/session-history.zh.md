# Harness 会话历史

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae 可以导入本地 AI-agent 转录，并把它们作为项目记忆渲染在静态站点的 `sessions/` 区之下。

此功能刻意与 `export harness` 分离：

- `export harness` 是面向 Claude Code、Codex、Gemini、Cursor、Kiro、OpenCode 等工具的出站上下文。
- `sessions ...` 是入站历史：它归一化当前项目此前的 Claude Code/Codex 会话，存储在 `.tesserae/harness_sessions/` 下，并让 `export site` 发布会话索引/详情页。

## 两条入口：批量导入与实时监控

会话摄取不再仅限批量。有两条路径通往同一个归一化存储：

- **批量导入**——`sessions discover/import` 按需扫描转录根目录并一次性写入。本页下文记录该流程。
- **实时监控**——监督守护进程（`tesserae engine`）运行一个 `SessionTailer`，它监视*本项目自己的* Claude Code 和 Codex 转录，并在新 turn 落盘时摄取它们。每个 tick 会定位到持久化的按文件字节偏移量、只读取新字节，并把完整的 turn 存入 SQLite 的 `HarnessSessionsDB`（`.tesserae/sqlite.db`），**然后**才入队一次去抖的重编译，因此编译总是读取一致的状态。tailer 的范围限定于项目自己的会话（Claude 的 `projects/<slug>/*.jsonl`；Codex 按 cwd 过滤），重启后从存储的偏移量恢复而不重放 turn。

运行实时循环：

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` 在进程内一次性运行同样的 ingest → compile → project 流水线，而不启动长驻的监视器（传入 `--no-sessions` 可跳过 harness 会话发现扫描）。

## 隐私模型

两条摄取路径都是显式的：实时 tailer 只在你保持 `tesserae engine` 存活时运行，批量发现只在带 `--import` 时写入。普通的 `tesserae compile` 或 `tesserae export site` 会读取 `.tesserae/harness_sessions/` 中已归一化的会话和 `.tesserae/sqlite.db` 中的实时记录，但绝不会自行意外抓取私有的 harness 转录目录。

导入的会话记录是本地项目产物。在发布公开站点之前请先审查它们，尤其当你的转录可能包含密钥、私有路径、客户数据或未发布的代码时。

## 发现并导入本地会话

在项目根目录下：

```bash
tesserae sessions discover --import
```

发现过程会扫描属于当前项目工作目录的本地 Claude Code 和 Codex 转录根目录。使用 `--root` 扫描特定的配置目录，重复 `--harness` 来限定发现范围：

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

不带 `--import` 时，发现过程只打印找到的内容，而不写入归一化的会话记录。

## 直接导入归一化 JSON

如果另一个工具已经产出了归一化的 `HarnessSession` JSON，可以导入单个文件或文件列表：

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

每个输入可以包含一个会话对象或一个会话对象列表。

## 存储的写入方式

两个入口点都会写入 `.tesserae/harness_sessions/`，但写入方式不同：

- `sessions import <path>` **合并**。保留现有记录；同名文件的记录会被原地覆盖。
- `sessions discover --import` **在其扫描的范围内进行替换**。仅当记录的转录既位于扫描的 harness 根目录下，*并且*其 harness 是此次运行扫描的那个时，记录才会被删除——因此重命名文件名方案或去重导入不会留下孤立的页面和搜索条目，而其他任何内容都会被保留。两个条件都会形成限制：`--harness codex` 即使扫描了 `~/.claude` 也会保留 claude-code 记录，而没有本地转录的记录完全超出范围。无法读取文件的记录也会被保留，因为无法识别记录所有者的扫描没有理由删除它。

这个范围的划分在你从本地 harness 惯例之外提供 Tesserae 数据时很重要——一个 orchestrator 导出自己的 agent 会话、一个 CI 作业从另一台机器导入转录、一个迁移脚本。这些记录携带本地扫描无法推断的来源属性，而本地扫描对它们没有权限。在 0.28.5 之前，一次非空发现会删除*整个*存储，因此它们会被无声地删除，而插件的 `SessionEnd` 钩子在每次会话关闭时都会运行发现过程（[#104](https://github.com/ca1773130n/Tesserae/issues/104)）。

值得了解的两点行为：

- 空发现永远不会删除。一次没有找到任何东西的扫描——错误的 `HOME`、断开的 harness 根目录——会进行合并而不是清空。
- 删除记录的发现过程会在导入计数旁边打印计数，因此存储不会在只报告增长的一行中缩小。

## 列出已导入的会话

```bash
tesserae sessions list
```

会话存储在以下位置：

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

实时监控的会话还会被记录在 SQLite 的 `HarnessSessionsDB`（`.tesserae/sqlite.db`）中，它同时持久化 tailer 用于恢复的按文件读取偏移量。`tesserae sessions list` 报告合并后的视图。

## 构建静态会话页面

导入会话后，重建站点：

```bash
tesserae export site
```

站点会发出：

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

生成的站点从全局导航栏、主页 Browse 卡片、搜索条目以及每个会话详情页的面包屑路径链接到 Sessions。

## 快速转录搜索（memex）

当你用 `tesserae serve` 服务站点时，**sessions 仪表盘**会获得一个覆盖所有已索引 Claude/Codex 转录的全文搜索框，由 [`nicosuave/memex`](https://github.com/nicosuave/memex)（BM25）支持。结果显示 `project · role · date · score` 以及匹配的片段。

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

它是**可选且优雅降级的**：没有 `memex` 二进制（或索引）时，搜索框会显示一条清晰、可行动的消息，仪表盘的其余部分不受影响。搜索端点（`GET /api/transcript-search`）仅限同源/回环调用方，因此你访问的网页无法探测你的本地历史。

## 会话详情页布局

会话详情页使用共享的静态站点外壳，而不是独立的转录转储。它们包括：

- hero 与统计条；
- 高层摘要；
- 时间线与大小元数据；
- 存在时显示的决策、文件、命令、工具和错误；
- 折叠的 subagent 树；
- 逐轮的 user/assistant 对话；
- 附在前一条 assistant 轮次下方的折叠 tool-use 块；
- 链接到 `#turn-N` 锚点的左侧对话导航栏。

对话 markdown 通过站点 markdown 渲染器渲染。行内代码、显式命令/标签标记、路径、文件名和话题标签等语义表面会被装饰为紧凑的 chip；随意的大写名词不会被自动 chip 化。

当前的转录排版：

| 表面 | 选择器 | 大小 |
|---|---|---|
| 对话 markdown 正文 | `.session-turn-text`，正文子元素 | `8px` |
| 通用对话代码围栏 | `.session-turn-text pre` | `10px` |
| Bash/shell 围栏代码内容 | `.session-code-block code.language-bash`、`.language-sh`、`.language-shell`、`.language-zsh` | `11px` |
| 工具 details/summary | `.session-tool-details`、`.session-tool-details > summary` | `10px` |
| Tool-use 头部 | `.session-tool-use-header` | `8px` |
| 工具载荷文本 | `.session-tool-use-text` | `6px` |

## 包含会话的发布检查清单

在部署包含会话的公开站点之前：

1. 运行 `tesserae sessions list` 并确认数量符合预期。
2. 检查 `.tesserae/harness_sessions/` 中是否有敏感内容。
3. 用 `tesserae export site` 重建。
4. 在本地打开 `sessions/index.html` 和至少一个会话详情页。
5. 确认工具块默认折叠，且原始工具载荷可以接受发布。
6. 在源码树已提交后用 `tesserae export site --deploy` 部署。
