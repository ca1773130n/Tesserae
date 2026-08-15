# Claude Code 插件

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.fr.md">Français</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 提供了一个 [Claude Code](https://docs.claude.com/en/docs/claude-code) 插件,让你能够在 TUI 会话内运行完整的 Tesserae 工作流程 —— 斜杠命令、自动注册的 MCP 服务器、指导代理的技能,以及四个闭合代理↔项目内存循环的 hook。插件位于仓库的 `plugin/` 中。

## 安装

```bash
# 前提:已安装 `tesserae`(`pip install tesserae` 或 `pipx install tesserae`)。如通过 pipx 安装,确保 `~/.
/plugin install /path/to/Tesserae/
```

前提:已安装 `tesserae`(`pip install tesserae` 或 `pipx install tesserae`)。如通过 pipx 安装,确保 `~/.local/bin` 在 Claude Code 启动时继承的 PATH 中。

## 内含

* **9 个斜杠命令** —— 7 个 CLI 1:1 包装器(`/tesserae:compile`、`/tesserae:ask`、`/tesserae:sessions-import`、`/tesserae:build-site`、`/tesserae:serve`、`/tesserae:obsidian-sync`、`/tesserae:setup`)+ 两个工作流宏(`/tesserae:refresh` 链式执行 import + compile + obsidian-sync;`/tesserae:status` 显示图谱计数和上次编译)。
* **`tesserae` 服务器自动注册** —— 代理无需手动编辑配置即可以 `mcp__plugin_tesserae_tesserae__<tool>` 形式使用完整工具面:图谱查询(`search_nodes`、`node_context`、`graph_ppr`、`search_facts`)、按需 `compile_context` / `list_communities` / `fresh_insights` 编译器、会话记忆(`ask`、`list_sessions`、`find_session_findings`、`find_code_symbol_mentions`)以及引导式设置(`tesserae_setup_plan` / `tesserae_setup_apply`)。完整列表见 [mcp.zh.md](mcp.zh.md)。
* **`using-tesserae` 技能** —— 当你询问类型化图谱、过去会话回忆、wiki/vault 内容或任何 tesserae 工作流时自动加载。教会代理使用哪个 MCP 工具 vs 建议哪个斜杠命令。
* **5 个 hook** —— `SessionStart` 打印图谱摘要;`SessionEnd` 后台执行 import+compile,使本次对话的洞察成为下次会话的图谱节点;两个 `PostToolUse` hook 在 `Edit`/`Write`/`MultiEdit` 上触发 —— 一个在 docs/ 编辑时做可选的增量重编译,另一个对代码图谱同步做防抖(约 30 秒);`PreToolUse`(作用于 `Bash`)通过确认对话框对大图谱编译进行门控。

> **会话结束时的 compile 是尽力而为,并非有保证。** hook 在有 `setsid` 时用它分离
> 后台作业,否则回退到 `nohup`。macOS 不带 `setsid`,而 `nohup` 只是忽略 `SIGHUP`
> —— 它把作业留在会话的进程组里 —— 所以在会话关闭时回收整个进程组的 harness 依然
> 会把 compile 中途杀掉。此时留下的状态是「可恢复」,而不是「毫发无损」:`graph.json`
> 通过原子 rename 写入,永远不会只写一半;但生成物 `wiki/` 与 `site/` 投影会在写
> 工件之初被清空,SQLite 存储又写在 `graph.json` 之后,所以在这个窗口被杀掉会让它们
> 缺失或落后一次 compile。不过这绝不会悄无声息 —— `.tesserae/manifest.json` 只在工件
> 落盘后才给文档打上 `graphed`,因此下一次 `compile --changed-only` 会拒绝 no-op,
> 提示 `graph.json is not known to cover every tracked document`,并重新抽取整个语料,
> 顺带把投影重建回来。这个全语料重新抽取是再走一遍,不是再购买一遍。来自 codex 和 claude CLI 供应商的响应缓存在
> `~/.tesserae/llm_cache` 下,按实际发送的 prompt 摘要寻址,所以被杀掉的运行已经完成的每个文档
> 都从磁盘无代价地重放,修复只需为它未曾抵达的文档付费。杀掉一次运行的代价是运行的耗时,不是
> 它的提取。两件事会破坏这一点:删除缓存目录,以及使用直接 API 供应商,它只有 SDK 的短期 prompt
> 缓存,没有任何能在杀掉后活下来的东西。无论哪种情况,修复都要从供应商处全价重购整个语料。不要假设长时间 compile
> 能活得比启动它的会话更久 —— 请在前台运行,或使用 `tesserae engine`。
> 
> 无论哪种方式你都可以看到进度。一个没有终端附加的 compile —— 分离、重定向或在 CI 下 ——
> 在 `tesserae.compile` 频道向 stderr 记录每个文档一行日志,给出位置、路径和该文档是来自缓存还是花费了
> 一个模型调用;`--quiet` 可以关闭它。

完整细节、完整的命令/hook 表以及每个项目的 opt-out 说明在插件自己的 [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) 中。

## 为什么同时需要插件和 MCP 服务器?

角色不同:

- **MCP 工具** = 代理在对话中调用的只读图谱查询。始终开启,低摩擦。
- **斜杠命令** = 你明确调用的工作流操作(compile、refresh、obsidian-sync)。高杠杆但应该由你决定。

你可以仅使用 MCP 服务器(通过 `tesserae projects mcp-config` 手动编辑 `claude_desktop_config.json`)。插件只是将它与斜杠命令、技能和 hook 打包在一起,使安装变为一步。

## 验证安装

```
/plugin list
/mcp
/tesserae:status
```

## 卸载

```
/plugin uninstall tesserae
```

可逆。不会触碰任何项目的 `.tesserae/` 目录。

## 另请参阅

- [实施计划](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [设计规范](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [会话集成](sessions.zh.md) —— 插件 hook 闭合循环的会话图谱功能
