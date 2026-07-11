# `tesserae doctor` — 项目健康检查

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` 会端到端地检查一个 Tesserae 工作区——初始化、图谱完整性、注册表一致性、新鲜度、锁、LLM 登录状态以及磁盘卫生——并打印一份检查清单。它**默认只读**；`--fix` 只应用可安全重复执行的修复，绝不会破坏活动状态。

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## 检查内容

二十项检查，按类别分组：

| 检查 | 类别 | 验证内容 | `--fix` 动作 |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` 存在且看起来像一个 Tesserae 工作区 | 仅报告（建议运行 `tesserae init`） |
| `graph_parse` | core | `graph.json` 可解析且形状符合预期 | 仅报告（建议运行 `tesserae compile`） |
| `config_valid` | core | `.tesserae/config.json` 可解析并通过 init 模板校验 | 仅报告 |
| `vault_configured` | core | 配置的 vault 路径可以解析 | **SAFE**：当解析出的 vault 目录位于项目内部时创建该目录 |
| `registry_consistent` | registry | `~/.tesserae/registry.json` 的条目指向真实存在的项目根目录 | **SAFE**：清理根目录已消失的条目，删除遗留的 `active` 键；图谱缺失时仅报告 |
| `graph_staleness` | freshness | 自上次编译记录的 `git_head` 以来的 git 增量 | 仅报告（建议运行 `tesserae refresh` —— 编译开销较大） |
| `site_search_index` | freshness | 静态站点 / `search-index.json` 比 `graph.json` 更新 | **SAFE**：重建站点 |
| `backend_artifacts` | freshness | RAG-Anything 产物是最新的 | 仅报告（它们的刷新是 LLM/网络重操作） |
| `session_chunks` | freshness | [每日 session-chunk](session-chunks.zh.md) 覆盖率在近期窗口内没有缺口 | 仅报告（建议运行 `tesserae sessions chunk-backfill`） |
| `wiki_lint` | graph | 图谱 ⇄ wiki 漂移 + 可轻易修复的 lint 发现 | **SAFE**：应用 lint 的琐碎修复（`fix_trivial`） |
| `compile_lock` | processes | 是否有活动的编译锁被持有，以及被哪个 pid 持有 | 仅报告 —— doctor **绝不杀掉进程也绝不移除活动锁** |
| `daemon_pid` | processes | `daemon.pid` 指向一个存活的 engine 进程 | **SAFE**：当持有者已死亡时删除该 pidfile |
| `llm_login` | environment | 配置的 LLM 后端确实可用（claude/codex CLI 已登录，或存在 API key） | 仅报告（建议运行 `claude /login` / `codex login`） |
| `optional_deps` | environment | 可选依赖的状态（memex、cognee、raganything） | 仅报告（安装需要联网） |
| `embedding_backend` | environment | 有真正的语义嵌入后端可用 | 仅报告（建议 `pip install tesserae[semantic]`） |
| `environment` | environment | 整体环境检测摘要 | 仅报告的小节 |
| `build_history` | hygiene | `.build-history` 的大小和形状 | **SAFE**：裁剪它，且始终保留最新的 `git_head` 条目（新鲜度检查依赖它） |
| `idempotence` | hygiene | 输出快照的 `idempotence_suspect` 触发线 | 仅报告（这是 bug 信号，不应自动修复） |
| `orphan_worktrees` | hygiene | 陈旧的 `git worktree` 注册项 | **SAFE**：`git worktree prune`；删除目录仅报告 |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` 的增长 | **SAFE**：轮转/截断超过 10 MB 的日志 |

崩溃的检查会作为一条 error 级发现被报告——doctor 本身永不抛出异常。

## `--fix` 策略

- `--fix` **只**运行上表标记为 SAFE 的检查，然后重新检测，使报告反映修复后的状态。
- 每个修复都是幂等的：连续运行两次 `doctor --fix`，第二次运行结果是干净的。
- Doctor **绝不杀掉进程，也绝不移除活动的编译锁**——被持有的锁会连同持有它的 pid 一并报告，并保持原样。
- 重型或联网操作（重新编译、依赖安装、后端刷新）绝不会被折叠进 `--fix`；doctor 会打印出命令供你自己运行。

## 退出码

与 `tesserae lint` 同一约定：

| 退出码 | 含义 |
|---|---|
| `0` | 健康 —— 没有高于 OK 的发现 |
| `1` | 存在警告 |
| `2` | 存在错误 |

## 报告产物

每次运行都会把两种形式的报告写入工作区：

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` 额外把 JSON 报告打印到 stdout，替代 markdown 检查清单。`--all` 遍历注册表中的每个项目（忽略 `--project`），并按项目分别报告。

## MCP：`doctor_report`

MCP 服务器以 `doctor_report` 工具的形式暴露同一份报告（对照 `lint_report`，包括其返回内容的字节上限），因此 agent 可以在对话中途检查工作区健康状况而无需调用 shell。它需要一个项目根目录——传入 `graph_path`/`project`，或配置一个默认图谱。
