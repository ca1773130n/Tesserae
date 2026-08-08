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

各项检查，按类别分组：

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
| `compile_lock` | processes | 是否有活动的编译锁被持有，以及被哪个 pid **和哪台主机**持有 | 仅报告 —— doctor **绝不杀掉进程也绝不移除活动锁** |
| `filesystem_locking` | processes | `.tesserae/` 是否位于网络文件系统上——在那里 `flock(2)` 可能是一个静默的空操作 | 仅报告（它无法证明跨主机的强制生效——见下文） |
| `daemon_pid` | processes | `daemon.<host>.pid` 指向一个存活的 engine 进程 | **SAFE**：当持有者已死亡时删除**本机自己的** pidfile；其他机器的只报告，绝不触碰 |
| `llm_login` | environment | 项目真正会用到的那些配置目录是否存在 | 仅报告 —— **不验证凭据**（见下文） |
| `optional_deps` | environment | 可选依赖的状态（memex、raganything） | 仅报告（安装需要联网） |
| `embedding_backend` | environment | 有真正的语义嵌入后端可用 | 仅报告（建议 `pip install tesserae[semantic]`） |
| `environment` | environment | 整体环境检测摘要 | 仅报告的小节 |
| `build_history` | hygiene | `.build-history` 的大小和形状 | **SAFE**：裁剪它，且始终保留最新的 `git_head` 条目（新鲜度检查依赖它） |
| `idempotence` | hygiene | 输出快照的 `idempotence_suspect` 触发线 | 仅报告（这是 bug 信号，不应自动修复） |
| `orphan_worktrees` | hygiene | 陈旧的 `git worktree` 注册项 | **SAFE**：`git worktree prune`；删除目录仅报告 |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` 的增长 | **SAFE**：轮转/截断超过 10 MB 的日志 |
| `code_scope_leftovers` | hygiene | 已退役代码层的残留：`code-graph*.json`、`sqlite.db` 中的代码类型行 | 仅报告 —— 清理是批量删除，因此单独作为一个动词（见下文） |

崩溃的检查会作为一条 error 级发现被报告——doctor 本身永不抛出异常。

## `llm_login` 告诉你什么，又不告诉你什么

它报告的是某个配置目录存在。它**不**报告目录里的 CLI 是否持有有效令牌，而且它在自己的发现文本里就是这么说的。

这个区分不是咬文嚼字。这项检查过去仅凭 `~/.claude/history.jsonl` 之类的文件就报告 `credentialed LLM CLI: claude, codex`——那些文件只能证明该 CLI 被*用过*，而不是它*现在*能通过认证。在同一秒内背靠背地运行，`tesserae compile` 打印了 `Claude CLI not logged in (tried 1 config dir)`，而 doctor 打印了一个绿色的勾。一个与你正身处其中的故障相互矛盾的诊断，比没有诊断更糟。

验证凭据意味着每次 `tesserae doctor` 都要花掉一次真实的 LLM 调用，而这不是这项检查会自作主张承担的成本。所以它只陈述自己检查过的东西。权威答案请用 `tesserae compile` 拿。

这项检查的范围限定在项目真正会去尝试的那些目录上，并且经由 `ProjectWiki._build_json_client` 所用的同一条路径解析——当项目的 provider 是 `codex` 时，它对 claude 的配置目录只字不提。

## 共享磁盘与 `flock(2)`

Tesserae 中的每一条并发保证——首先就是上面那把编译锁——都建立在 `flock(2)` 被承载 `.tesserae/` 的那个文件系统真正强制执行之上。在 NFS 和 SMB 上，这取决于配置：没有一个可用的 lock daemon，`flock` 可能静默退化为空操作，于是两台主机会同时编译同一个项目，而各自都以为自己独占持有那把锁。

`filesystem_locking` 报告单台主机能够确定的东西：承载项目的文件系统类型、它是否是网络文件系统，以及一次 `flock` 获取是否根本能够成功。位于网络文件系统上时它会告警。

它**无法**证明跨主机的强制生效，也不自称能证明。一台主机拿到了锁，这并不能说明第二台主机会被阻止拿到它。如果你从多台机器对着共享存储运行 Tesserae，请在真实硬件上直接测试这一点，然后再去依赖那把编译锁。

## `tesserae doctor migrate-code-scope`

针对源代码退出 Tesserae 范围之前编译过的工作区的一次性清理。新的编译不再产生代码层，
但较早的工作区仍然带着它，而其中大部分只有在你主动要求时才会被清理。

```bash
tesserae doctor migrate-code-scope            # 演练 —— 只报告，不删除
tesserae doctor migrate-code-scope --apply    # 真正删除
```

按此顺序移除：

* `.tesserae/markdown_projection/` 下自身 `type:` frontmatter 指向已退役代码类型的
  投影页面；
* Obsidian 仓库中的同类页面 —— 已配置的仓库和项目内默认仓库都包括在内，因为后来指向
  真实仓库的项目会把旧的那个原封不动地留在那里。`user-notes` 内容非空的代码页面会被
  保留并计数，绝不删除；
* `code-graph.json` 和 `code-graph-cache.json`；
* 节点或边已不存在的 SQLite 附属表行（`node_provenance`、`edge_provenance`、
  `node_memory`），随后执行 `VACUUM`。

有两点需要知道。

**看幸存数，而不是删除数。** 投影目录绝大部分来自代码 —— 此处实测为 224,876 个页面中的
218,796 个 —— 因此一个把所有东西都删掉的谓词缺陷和一次正确的运行，从删除数量上几乎看不
出区别。报告首先给出有多少非代码页面幸存，那才是谓词出错时会崩塌的数字。判定严格按文件
逐个进行，依据该文件自身的 frontmatter。

**先编译，再迁移。** `nodes` / `edges` 表和 provenance 附属表由每次编译整体重写，所以
把这些行变成垃圾的是编译；这个动词负责回收空间，因为 SQLite 不会因 `DELETE` 而收缩。
提前运行也无害 —— 它会说明这一点并报告没有可回收的内容。`VACUUM` 绝不在编译内部执行：
它会取得排他锁，并需要与数据库文件相当的可用磁盘空间；当磁盘无法容纳重建时会带着说明
跳过。

它被刻意排除在 `--fix` 之外，因为 `--fix` 的文档承诺只做安全修复。

## `--fix` 策略

- `--fix` **只**运行上表标记为 SAFE 的检查，然后重新检测，使报告反映修复后的状态。
- 每个修复都是幂等的：连续运行两次 `doctor --fix`，第二次运行结果是干净的。
- Doctor **绝不杀掉进程，也绝不移除活动的编译锁**——被持有的锁会连同持有它的 pid 和主机一并报告，并保持原样。
- Doctor **绝不触碰另一台机器的 pidfile。** 在共享存储上，本地进程表对另一台主机写下的 pid 什么也说明不了，因此 `daemon.<other-host>.pid` 会被报告并无条件跳过——甚至不会被读来判断存活。只有本机自己的 pidfile 才有资格被删除。
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
