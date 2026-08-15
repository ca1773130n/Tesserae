# `.tesserae/` — 里面有什么，删掉会付出什么代价

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
一个成熟的项目会在 `.tesserae/` 下积累约六十个条目，而目录列表本身并不能告诉你：
哪些编译一次就能免费重建，哪些重建要付出一次 LLM 调用的代价，哪些承载着任何东西
都无法重构的成果。`compile.lock` 和一个零字节的孤儿 tmp 文件，看上去与承载着人工
判定的 `candidate-same-as.json` 一模一样。

本页就是那个答案，并按后果排序。分类本身位于 `tesserae/sidecars.py` —— 每个文件
一条注册表记录，写明它的归属、种类，以及删除它会失去什么。注册表是事实来源，本页
是它可读的投影，而 `tesserae doctor` 会打印出实时的那一份。

每个条目都带有两个彼此独立的字段：

| 种类 | 字节从哪里来 |
|---|---|
| `derived` | 由一次编译从源重新发布 |
| `accumulated` | 随时间累积；没有任何编译能重新推导 |
| `cache` | 一个可以重新提问的问题的存档答案 |
| `scratch` | 进程记账：锁、pid 文件、tmp 残骸 |

种类只说明字节的来源，**并不**说明删除是否安全 —— `safe_to_delete` 是另一个字段，
而且两者的分歧频繁到足以要命：答案来自模型的 `cache` 删不得，而一个 `derived`
文件也可能承载人工审批。下面的章节按第二个字段排序，因为那才是你真正想知道的。

## 可以放心回收 —— 编译会重建它们

删掉其中任何一个，下一次编译都会逐字节地把它放回去，且不调用任何模型：

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` 出现在这份清单里是有意为之。编译出的图是源加上下文那些累积型附属文件
的纯函数 —— 正因如此，需要保护的是*那些*，也正因如此，「干脆把 `.tesserae/` 删了
重新编译」这种直觉是错的，哪怕其中最显眼的文件确实是一次性的。

## 要付出一次模型调用 —— 并且会改变 `graph.json` 的字节

这些是 LLM 给出的答案的存档。重建它们要付出一次调用，而模型不会两次给出相同的
措辞，因此下游的一切字节也会随之改变。

| 条目 | 种类 | 重建的代价 |
|---|---|---|
| `session_findings` | `cache` | 最尖锐的一例：这些发现会成为图中的**节点**，所以丢掉缓存就会重跑一个非确定性抽取器，下一份 `graph.json` 的字节随之改变 —— 这个仓库已经栽过四次的字节幂等性破坏 |
| `community_summaries` | `cache` | 以成员哈希为键、由 LLM 写出的社区摘要 |
| `distill_cache` | `cache` | 智能体蒸馏结果 |
| `distillation_cache` | `cache` | 蒸馏结果 |
| `extraction_guidance_cache` | `cache` | 每个反馈聚类一条由 LLM 措辞的要点 |
| `schema_drift_cache` | `cache` | 按宿主类型给出的 LLM 子类型提案 |
| `supersede_cache` | `cache` | LLM 的取代（supersede）裁定 |
| `schema-drift-proposals.json` | `derived` | 字节是派生的，内容却不可派生：同一条记录里既有人工的 `approved` 关卡，也有可编辑的 `proposed_type`，所以重建既要付出一次调用，**还会**丢掉那些审批 |

## 不可恢复 —— 没有任何东西能重建它们

这里没有一样是编译能重新推导出来的。删掉其中一个是数据丢失，不是耽误时间。

| 条目 | 种类 | 你会失去什么 |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | 人工的 same-as 判定。找不到它的编译并不会报错 —— 它会默默地重问一个人类已经回答过的问题，被否决的配对又原封不动地回来了 |
| `sqlite.db` | `accumulated` | 混合型；见下文 |
| `agent-writes.jsonl` | `accumulated` | 由智能体撰写的叠加层，在每次编译时作为第五个生产者重放；删掉它就抹掉了每一次智能体写入 |
| `vault_snapshot.json` | `accumulated` | `vault_pull` 用来做差分的基线。在编辑途中删掉它，下一次编译就无法把你的编辑与它自己先前的投影区分开 —— 那正是 vault 覆盖机制的全部依据 |
| `obsidian_vault` | `accumulated` | 双向且归用户所有：你在这里的编辑会被拉回图中，所以它不是一个可以随手重画的投影 |
| `config.json` | `accumulated` | 项目配置，包含 `obsidian.vault_path` —— 属于用户输入，永不重新生成 |
| `charter` | `derived` | 每次编译都会从 `graph.json` 派生它，但重建并不能还原它：slug 由重建当时选中的锚点铸成，删掉它就会让每个 domain 以新名字重新创设，弄断所有已固定的挂载路径，并丢掉唯一记录旧名去向的墓碑 |
| `agents` | `accumulated` | 每个智能体的 `registry.json`，以及手写的 `purpose.md` |
| `discovered_links.json` | `accumulated` | 关联叠加层跨多次运行累积带评分的链接；单次运行重构不出来 |
| `extraction-feedback.jsonl` | `accumulated` | 在 vault 叠加与 review-apply 过程中收集的人工修正 |
| `extraction-guidance.md` | `accumulated` | 手工编辑的指引，evolve 流程会往里合并 |
| `harness_sessions` | `accumulated` | 导入的会话状态 |
| `harness_sessions.db` | `accumulated` | 导入的智能体会话，其上游记录会轮转消失，所以重新导入并不能把它们还原 |
| `session_chunks.db` | `accumulated` | 由守护进程的 tailer 实时写入的规范化轮次，来源记录不会一直存在 |
| `manifest.json` | `accumulated` | 按来源记录的摄取状态；没有它，下一批会把所有东西重新摄取一遍，并对已经读过的来源重跑抽取 |
| `.build-history.jsonl` | `accumulated` | 每次构建一行，记录编译时的 `git_head`；删掉它，图的陈旧程度就永远无从得知 |

### `sqlite.db` 是混合型，按其中最有价值的表分类

里面的图镜像是派生的，`node_vectors` 是可丢弃的向量缓存 —— 但同一个文件还装着
`node_memory`（衰减、访问计数、被强化的置信度）、`fact_observed`（事务时间，一个
只会向前走的真实挂钟）和 `read_audit`，这些都无法恢复。为了回收向量缓存而删掉整
个文件，会把每一条事实的「我们何时得知」重置为现在。要回收空间，请用会执行 vacuum
的 `tesserae doctor --fix`，而不是删掉数据库。

## 锁、pid 文件与残骸

| 条目 | 种类 | 删除之前 |
|---|---|---|
| `compile.lock` | `scratch` | 编译互斥锁。**任何**自动路径都不会删除它 —— 有记录的失败模式是 SessionEnd 编译堆积，doctor 的 `compile_lock` 检查只报告不处理也是同一个原因 |
| `.recompile.lock.d` | `scratch` | 基于 mkdir 的钩子互斥锁；删掉一个正被持有的，会让两次重编译互相竞争 |
| `session_chunks.lock` | `scratch` | 回填用的「被持有就跳过」flock；删掉一个正被持有的，会让两次回填写入同一天 |
| `daemon*.pid` | `scratch` | 引擎 pid 文件，按主机划分，形如 `daemon.<host>.pid`。只有在确认记录的持有者**在本机**已经死亡之后，doctor 才会删除 |
| `graph.json.bak-*` | `scratch` | Tesserae 没有任何代码路径会写出它们。它们是某次恢复作业中手工复制的副本 —— 只报告，绝不删除，因为那是人做的 |
| `*.tmp*` | `scratch` | tmp+replace 写入留下的孤儿一半，命名为 `<target>.tmp.<pid>.<hex>`。只有在持有它的 pid 消失之后才可删除：活着的写入方正处在 rename 中途 |
| `.*-hook.log*` | `scratch` | shell 钩子的诊断日志；过大的由 doctor 轮转 |

## `~/.tesserae/` —— 全机范围，同名不同物

用户范围的目录与项目目录同名，含义却不同。`config.json` 两边都有：在项目里它是
项目配置，在这里它是本机上每个项目共用的 LLM 配置。

| 条目 | 种类 | 你会失去什么 |
|---|---|---|
| `registry.json` | `accumulated` | 项目注册表。删掉它，本机上每个项目都会被注销 |
| `config.json` | `accumulated` | 全机范围的 LLM 配置；属于用户输入 |
| `host_id` | `accumulated` | 本机的身份标识。重新生成它，会让共享存储上每一个按主机划分的 pid 文件和会话记录都变成「外来的」 |
| `harness_sessions` | `accumulated` | 全机范围的会话导入状态 |
| `llm_cache` | `cache` | 缓存的 LLM 响应；重建会调用模型，且无法复现原样 |
| `federation` | `cache` | 跨项目的链接与向量缓存 —— 可以安全丢弃 |
| `wiki` | `derived` | 全机范围的 serve 临时目录 —— 可以安全丢弃 |
| `engine.pid` | `scratch` | 舰队 pid 文件；曾经有一个陈旧的文件握着一个死了六天的 pid，这正是 pidlock 选择验证而非信任的原因 |
| `engine.pid.lock` | `scratch` | 舰队 pid 文件的互斥锁；删掉一个正被持有的，会让两个舰队同时启动 |
| `*.bak*` | `scratch` | `registry.json` 与 `config.json` 迁移前的副本。没有代码路径会写出它们，所以它们存在是因为有人想留着 |

## 查看实时的分类

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

`sidecars` 检查会拿你真实的 `.tesserae/` 与注册表比对，并分别报告三类：孤儿 tmp
一半、手工制作的 `graph.json.bak-*` 副本，以及注册表中无人认领的条目。`--fix`
只删除第一类，而且仅当写入方的 pid 已死、文件超过 24 小时时才删 —— 因为活着的写入
方正卡在 `write_text` 与 `replace` 之间，而当多台主机可能挂载同一个 `.tesserae/`
时，`os.kill(pid, 0)` 只对本地进程表作答。

**未分类的条目只会被报告，永不被触碰。** 注册表不认领的条目，更可能是别人的文件
—— 你的笔记、另一个工具的缓存 —— 而不是 Tesserae 的 bug，所以发现它时的正确做法是
把它点名，而不是把它删掉。这也是一个漏了注册的新 Tesserae 附属文件得以现形的途径。

Tesserae 没有批量 `reset` 动词。分类使这样一个命令成为可能；但在写下分类的同一次
改动里就顺手交付一条针对它的破坏性命令，顺序是反的。
