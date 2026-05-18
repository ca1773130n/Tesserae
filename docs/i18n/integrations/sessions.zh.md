# 会话图谱

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.fr.md">Français</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 的会话图谱将关于项目的 Claude Code / Codex 对话转换为类型化知识图谱中的一级节点,并链接回讨论中出现的文档。编译后,你可以问 `tesserae project ask "我们对 3D Gaussian Splatting 做了什么决定?"`,并获得具体的 Insight / Decision / Question / TODO / Hypothesis / Takeaway 节点,带有产生它们的会话的来源。

## 工作原理

每个会话两个阶段:

1. **结构化**(始终运行,无 LLM)。读取 `tesserae sessions discover --import` 写入 `.tesserae/harness_sessions/` 的规范化 `HarnessSession` 记录。为每个会话铸造一个 `Session` 信封节点,为代理打开的每个文档发出 `discussed_in` 边,并将现有 `decisions` 字段转换为 `SessionDecision` 节点。
2. **LLM**(可选,配置 `ANTHROPIC_API_KEY` 时运行)。将规范化的对话回合(`metadata["turns"]` 字段 — 不是原始转录文件)发送到 Claude,使用仅 JSON 的发现模式。返回六种发现,每种引用回特定回合和当前图谱中的特定文档节点 ID。通过 content_hash + project_root_hash 缓存,因此未更改的会话在下次编译时跳过调用。

## 设置

```bash
# 将此项目的会话导入 `.tesserae/harness_sessions/`。按 cwd 过滤,因此只导入在此项目内运行的会话。
tesserae sessions discover --import

# 编译。结构化通道免费运行;`claude` CLI 已登录时 LLM 通道自动运行 — 无需 API 密钥。
tesserae project compile
```

在没有会话的情况下编译(例如,在没有任何工具历史记录的服务器上):

```bash
tesserae project compile --no-sessions
```

强制仅结构化(即使设置了密钥也跳过 LLM 调用):

```bash
tesserae project compile --sessions-llm=false
```

## 配置

`.tesserae/config.json` 接受 `sessions` 块:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

CLI 标志覆盖配置。`llm_enabled = "auto"`(默认)在 `claude` CLI 已登录或设置了 `ANTHROPIC_API_KEY` 时运行 LLM 通道;两者都没有时,仅运行结构化通道(无错误,无出站调用)。

## 查询

在现有搜索/wiki 工具之上添加了两个 MCP 工具:

* `list_sessions(since?, limit?)` — 活动项目的 Session 信封(id、started_at、title、发现计数)。
* `find_session_findings(node_id, kinds?)` — 通过 `discussed_in` 或 `references` 链接到 `node_id` 的所有会话派生发现,可选地过滤到 insight / decision / question / todo / hypothesis / takeaway。

从 CLI:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## 隐私

* `claude` CLI 未登录且没有 `ANTHROPIC_API_KEY`(或使用 `--sessions-llm=false`)时,零出站网络调用。仅运行结构化通道。
* 当 LLM 通道运行时,会发送尚未缓存会话的**完整规范化对话回合**。转录文件本身保留在磁盘上;只有 LLM 的 JSON 输出持久化到图谱和每会话缓存。
* 缓存文件位于 `.tesserae/session_findings/<session_id>.findings.json`,带有 `content_hash` 和 `project_root_hash`。在项目之间复制的缓存文件在读取时被拒绝 — 没有跨项目重放。
* 加载后通过 `session_matches_project` 过滤会话,因此 `cwd` 是兄弟项目的转录从不会在此项目的图谱中产生节点。

## Vault 布局

发现在 Obsidian vault 下渲染为每个发现一个页面,按会话分组:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

发现页面上 `<!-- user-notes:start -->` … `<!-- user-notes:end -->` 块内的用户笔记在重新编译后仍然存在 — 与每个其他 vault 页面相同的契约。

## 故障排除

* **编译后没有 Session 节点出现。**你先运行了 `tesserae sessions discover --import` 吗?编译路径仅消耗 `.tesserae/harness_sessions/`;它不会自动扫描 `~/.claude/projects/`(在有数千个历史会话的机器上,扫描可能需要几分钟)。
* **LLM 成本担忧。**缓存意味着每个会话每个 content-hash 最多发送到 LLM 一次。长会话在 `max_turns_per_chunk`(默认 30)处分块,有 5 个回合的重叠。要限制总成本,降低 `max_turns_per_chunk`、降低 `include_doc_id_context`,或设置 `--sessions-llm=false`。
* **发现引用了不存在的节点 ID。**编排器对照活动文档图谱验证每个引用的引用,并静默丢弃未知。如果你在日志中看到警告,LLM 幻觉了一个引用 — 幸存的引用仍然可信。

## 规范

完整设计位于 [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md)。实施计划是 [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md)。
