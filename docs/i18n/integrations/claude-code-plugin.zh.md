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
* **`tesserae_mcp` 服务器自动注册** —— 代理无需手动编辑配置即可以 `mcp__plugin_tesserae_tesserae__<tool>` 形式调用 `ask`、`search_nodes`、`list_sessions`、`find_session_findings` 等。
* **`using-tesserae` 技能** —— 当你询问类型化图谱、过去会话回忆、wiki/vault 内容或任何 tesserae 工作流时自动加载。教会代理使用哪个 MCP 工具 vs 建议哪个斜杠命令。
* **4 个 hook** —— `SessionStart` 打印图谱摘要;`SessionEnd` 后台执行 import+compile,使本次对话的洞察成为下次会话的图谱节点;`PostToolUse`(可选)在 docs/ 编辑时增量重编译;`PreToolUse` 通过确认对话框对大图谱编译进行门控。

完整细节、完整的命令/hook 表以及每个项目的 opt-out 说明在插件自己的 [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) 中。

## 为什么同时需要插件和 MCP 服务器?

角色不同:

- **MCP 工具** = 代理在对话中调用的只读图谱查询。始终开启,低摩擦。
- **斜杠命令** = 你明确调用的工作流操作(compile、refresh、obsidian-sync)。高杠杆但应该由你决定。

你可以仅使用 MCP 服务器(通过 `tesserae project mcp-config` 手动编辑 `claude_desktop_config.json`)。插件只是将它与斜杠命令、技能和 hook 打包在一起,使安装变为一步。

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
