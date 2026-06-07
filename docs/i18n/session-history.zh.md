# Harness 会话历史

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae 可以导入本地 AI-agent transcript，并在静态站点的 `sessions/` 区域下把它们渲染为项目记忆。

此功能有意与 `export-agent-harness` 分开：

- `export-agent-harness` 是面向 Claude Code、Codex、Gemini、Cursor、Kiro、OpenCode 等工具的出站上下文。
- `project sessions ...` 是入站历史：它会为当前项目规范化既有 Claude Code/Codex 会话，将其存储在 `.tesserae/harness_sessions/` 下，并让 `project build-site` 发布会话索引/详情页。

## 两条入口：批量导入与实时监控

会话采集不再只有批量方式。进入同一个规范化存储有两条路径：

- **批量导入** —— `project sessions discover/import` 按需扫描 transcript root，一次性写入。本页下文说明该流程。
- **实时监控** —— supervisor daemon（`project engine`，别名 `project daemon`）运行 `SessionTailer`，监视*本项目自身的* Claude Code 和 Codex transcript，并在新 turn 落地时即时采集。每个 tick 都会 seek 到按文件持久化的 byte offset，仅读取新增的 byte，并在 enqueue 去抖后的重新编译**之前**将完整 turn 写入 SQLite `HarnessSessionsDB`（`.tesserae/sqlite.db`），因此编译始终读到一致状态。tailer 的范围被限制为项目自身的会话（Claude `projects/<slug>/*.jsonl`；Codex 按 cwd 过滤），重启后从已存 offset 恢复，不会重放 turn。

运行实时循环：

```bash
tesserae project engine        # 监视源、合并突发、自动重新编译
tesserae project engine --once # 单次 drain 周期后退出（确定性）
```

`tesserae project refresh` 在 in-process 中一次性运行同样的 ingest → compile → project 流水线，而不启动长驻 watcher（用 `--skip-sessions` 跳过 harness 会话 discovery 扫描）。

## 隐私模型

两条采集路径都是显式的。实时 tailer 仅在你保持 `project engine`/`daemon` 运行期间工作，批量 discovery 仅在 `--import` 时写入。普通的 `project compile` 或 `project build-site` 会读取 `.tesserae/harness_sessions/` 中已规范化的会话以及 `.tesserae/sqlite.db` 中的实时记录，但不会自行意外抓取私有 harness transcript 目录。

导入的会话记录是本地项目产物。发布公开站点前请先检查它们，尤其当 transcript 可能包含密钥、私有路径、客户数据或未发布代码时。

## 发现并导入本地会话

在项目根目录运行：

```bash
tesserae project sessions discover --import
```

Discovery 会扫描属于当前项目工作目录的本地 Claude Code 和 Codex transcript root。使用 `--root` 扫描指定配置目录，并重复 `--harness` 来限制 discovery：

```bash
tesserae project sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

不带 `--import` 时，discovery 只打印找到的内容，不写入规范化会话记录。

## 直接导入规范化 JSON

如果其他工具已经生成了规范化的 `HarnessSession` JSON，可以导入一个文件或一组文件：

```bash
tesserae project sessions import path/to/session.json path/to/more-sessions.json
```

每个输入可以包含一个会话对象或会话对象列表。

## 列出已导入会话

```bash
tesserae project sessions list
```

会话存储在：

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

实时监控的会话还会记录在 SQLite `HarnessSessionsDB`（`.tesserae/sqlite.db`）中，其中也持久化了 tailer 恢复所用的按文件 read offset。`project sessions list` 报告合并后的视图。

## 构建静态会话页面

导入会话后，重新构建站点：

```bash
tesserae project build-site
```

站点会输出：

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

生成的站点会从全局 rail、首页 Browse 卡片、搜索条目以及每个会话详情页的 breadcrumb trail 链接到 Sessions。

## 会话详情页布局

会话详情页使用共享的静态站点 shell，而不是独立的 transcript dump。它们包括：

- hero 和 stat strip；
- 高层摘要；
- timeline 和 size metadata；
- 存在时的 decisions、files、commands、tools 和 errors；
- 折叠的 subagent tree；
- 按 turn 展示的 user/assistant 对话；
- 附在前一个 assistant turn 下方的折叠 tool-use blocks；
- 链接到 `#turn-N` anchors 的左侧 conversation rail。

对话 markdown 通过站点 markdown renderer 渲染。inline code、显式 command/tag markup、paths、filenames、hashtags 等语义表面会装饰成紧凑 chip；随机大写名词不会自动变成 chip。

当前 transcript typography：

| Surface | Selector | Size |
|---|---|---|
| 对话 markdown prose | `.session-turn-text`, prose children | `8px` |
| 通用对话 code fences | `.session-turn-text pre` | `10px` |
| Bash/shell fenced code content | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| Tool details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| Tool-use header | `.session-tool-use-header` | `8px` |
| Tool payload text | `.session-tool-use-text` | `6px` |

## 会话发布清单

部署包含会话的公开站点之前：

1. 运行 `tesserae project sessions list` 并确认数量符合预期。
2. 检查 `.tesserae/harness_sessions/` 是否包含敏感内容。
3. 使用 `tesserae project build-site` 重新构建。
4. 在本地打开 `sessions/index.html` 和至少一个会话详情页。
5. 确认 tool blocks 默认折叠，并且 raw tool payload 可以发布。
6. 在 source tree 已提交后，使用 `tesserae project deploy --build` 部署。
