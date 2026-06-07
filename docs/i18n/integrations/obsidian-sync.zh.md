# Obsidian 双向同步 —— 设计方案

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.fr.md">Français</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **状态：已交付（Tier 1，v0.5.0）。** 下面描述的覆盖层读取器、用户笔记追加区、watch 模式和孤儿页面清理，都已在 `tesserae project obsidian-sync` 背后实际运行。本页既是设计依据也是用户指南。多 vault 联邦（Tier 3）仍在范围之外。

[Obsidian 导出](obsidian.md)过去是严格单向的：`.tesserae/graph.json` 中的类型化图谱投影到 vault，而 `project compile` 会覆盖所有投影出的文件。`obsidian-sync` 加上了反方向 —— 在 Obsidian 中修改一段描述，它就能在重编译中存活下来。

本文档说明在不让数据模型陷入不一致的前提下，这件事是怎么做到的。

## 战略转变，说清楚

当前的 README 明确放弃了在线编辑：

> Tesserae 选择从源文件编译，而不是在线编辑。如果你想在 UI 中编辑笔记，请使用 Logseq 或 Obsidian。

双向同步**改变了**这一契约 —— 至少对某些字段是这样。值得明确地说出来。目标并不是让"Obsidian 成为编辑器"，而是让"用户在 Obsidian 中的编辑不会在重编译时被悄无声息地销毁"。

## 核心思路：覆盖层，而不是合并

与其试图把同一个节点的两份分叉副本合并起来，不如把 vault 视作投影之上的一层 **diff 层**：

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` 位于 `.tesserae/` 下，它是**计算**出来的，而非由人编写。每次编译时，Tesserae 会遍历 vault，把每一页投影内容与上一轮投影写入的内容做对比，并把每一处用户引入的改动记录为一条覆盖层条目。最终图谱即 `base_graph` 应用了这些覆盖层后的结果。下一轮投影再把结果写回磁盘。

整个回环是稳定的。若源端没有任何变动，重新编译同一个 vault 不会产生任何 diff。

## 字段级所有权

节点上的每个字段都有一个所有者。所有权决定了源与 vault 出现分歧时该怎么处理。

| 字段 | 源端所有 | vault 可覆盖 | 备注 |
|---|---|---|---|
| `id`, `type` | 是 | 否 | 由 schema 控制；归属于提取器 |
| `name` | 初始值 | 是 | 用户对规范名称的判断往往优于提取器 |
| `aliases` | 初始值 | 是 | 来自 vault 的条目仅做追加；vault 中的条目始终保留 |
| `description` | 初始值 | **是** | 这是 Obsidian 中最常见的编辑 |
| `source_path` | 是 | 否 | 来源溯源信息；不可被编辑掉 |
| `metadata`（已声明的键） | 初始值 | 是 | 例如 `arxiv_id`、`github_repo` —— 用户可纠正 |
| `metadata.user.*` | 不适用 | 是 | 为用户专属键预留的命名空间；提取器从不写入 |
| 出向边（类型化） | 是 | 否 | 边存在于本体之中，而非 vault |
| 用户新键入的 wikilink | 不适用 | 是 | 以 `edge_type=user_link` 浮现，写回到图谱 |
| `<!-- user-notes -->` 正文块 | 从不写入 | 始终保留 | 投影器永远不触碰的追加专用区 |

## 冲突场景与默认处理

| 场景 | 默认处理 | 原因 |
|---|---|---|
| vault 的 `description` 与重新提取的源端 `description` 不一致 | **vault 优先**，并在 `.tesserae/lint-report.md` 的"diverged fields"下记录 | 尊重用户编辑：用户显然意图保留这次修改。审计轨迹便于事后审阅。 |
| 源文件已被删除，但投影出的页面仍在 vault 中 | 从图谱中移除该节点，并在 `.tesserae/orphans.md` 中列出 | 是否存在以源端为准；孤儿日志让你决定是恢复还是接受删除 |
| 用户写下指向一个不存在 slug 的 wikilink | 创建一个墓碑节点（类型为 `Stub`），并在 lint 报告中浮现 | 不丢弃用户意图；把它标记出来等待清理 |
| 用户添加了 schema 不认识的 frontmatter 键 | 以 `metadata.user.<key>` 形式保留，从不覆盖 | 在不污染类型化图谱的前提下保持前向兼容 |
| 两台机器上的两份 vault 同时编辑了同一个节点，并都通过 Obsidian Sync 同步 | **v1 不在范围内。** 文件系统层面以最后写入者获胜。 | 真正的多 vault 联邦属于 Tier 3；等到出现真实用例再考虑 |

## 用户笔记追加区

每一页投影都会带有一段投影器从不触碰的围栏区：

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

两项实际效果：
1. 用户可以在任意页面写注（例如"参见我笔记的第 4 章"），无需担心重新构建时丢失。
2. 拉取阶段会扫描 user-notes 区块中的 wikilink，并把它们以本体类型化的 `user_link` 边浮现出来，让它们具备图谱可达性，同时不污染正式的边类型。

## 远程传输 —— 明确的非目标

Tesserae **不会**构建同步服务器、鉴权层、冲突解决守护进程或托管 vault。这里所谓"双向"，仅指"编译时会从 vault 读取" —— 至于 vault 如何到达执行编译的那台机器，是用户自己的事情，并且已有现成工具可以解决：

| 方案 | 成本 | 备注 |
|---|---|---|
| Obsidian Sync | 付费，$4-8/月 | 端到端加密，官方出品，极简易用 |
| iCloud / Dropbox / OneDrive | 操作系统自带 | 能用，但冲突的交互体验糟糕 |
| Syncthing | 免费，自托管 | 单人跨设备的最佳选择 |
| Git（vault 提交进仓库） | 免费 | 对技术用户来说冲突体验最佳 |
| LiveSync（CouchDB 插件） | 免费，需自建服务器 | 实时多设备 |

这五种方案都与覆盖层模型兼容，因为 Tesserae 把 vault 视为磁盘上的文件，而不是一连串变更事件。

## CLI 表面

`tesserae project obsidian-sync` 把 vault 编辑应用到类型化图谱上并重新投影：

```bash
# 应用一次覆盖层：拉取用户编辑，重新投影到 vault。
tesserae project obsidian-sync

# 先检查会改什么。写出 .tesserae/diverged-fields.md，
# 不应用也不重新投影。
tesserae project obsidian-sync --dry-run

# 为本次调用指定某个 vault（解析顺序：
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/）。
tesserae project obsidian-sync --vault ~/Documents/tesserae-vault

# 把该 vault 路径设为后续命令的默认值。
tesserae project obsidian-sync --vault ~/Documents/tesserae-vault --persist-vault

# 长驻 watch：每当 vault 变化就重新应用覆盖层。
# Ctrl-C 停止；用 --poll-interval 调节轮询节奏（默认 1.5 秒）。
tesserae project obsidian-sync --watch --poll-interval 1.5

# 删除其源节点已不存在的投影页面（投影器只覆盖，
# 从不删除）。除非同时传 --force-prune-with-notes，
# 否则带用户笔记的页面会被保留。
tesserae project obsidian-sync --prune-orphans
tesserae project obsidian-sync --prune-orphans --force-prune-with-notes
```

`/tesserae:obsidian-sync` 斜杠命令封装了它，而 `tesserae project refresh`
（以及 `/tesserae:refresh` 宏）在其 import → compile → sync 链的最后一步运行覆盖层。

## 交付状态

| 阶段 | 范围 | 状态 |
|---|---|---|
| **1a** | 覆盖层读取器：遍历 vault、构建 `vault_overrides.json`、在同步时应用。分歧写入 `.tesserae/diverged-fields.md`。 | 已交付 |
| **1b** | 用户笔记追加区：投影器从不触碰 `<!-- user-notes:start --> ... <!-- user-notes:end -->` 块。 | 已交付 |
| **2** | watch 模式：长驻 `obsidian-sync --watch` 随 vault 变化在轮询循环中重跑覆盖层。 | 已交付 |
| **3** | 多 vault 联邦：图谱记录每个 vault 的来源信息，支持跨多个已同步 vault 的并发编辑。 | 留待出现真实用例后再做 |

## 非目标（明确声明）

- 同步服务器 / 鉴权 / 托管后端。
- Obsidian 内的实时协同编辑（如有此需求请使用 LiveSync）。
- 重写提取器以让每个字段都能回环 —— 在覆盖表之外的一切，源 markdown 仍是规范来源。
- 静态 HTML 站点的同步（`build-site` 仍是单向投影）。

## 已确定的决策

下列事项在设计阶段是待决问题；已交付的 Tier 1–2 实现按如下方式给出了结论：

1. **Lint 报告形态。** 分歧字段作为专用文件 `.tesserae/diverged-fields.md`（由 `--dry-run` 以及每次应用时写出）单独输出，而非作为 `lint-report.md` 的一节，以便在 git 中做 diff。
2. **墓碑节点类型。** 是把 `Stub` 加为真正的 schema 类型，还是搭车 `OpenQuestion` 加上 `_kind: stub` 区分符？提案：作为真正类型，命名为 `Stub`，对公开索引隐藏。
3. **编译时拉取的默认值。** 默认开启还是默认关闭？提案：当配置路径下存在 vault 时默认开启，并在首次激活时一次性提示用户确认，让用户明确选择启用。
4. **diff 所对照的"上次投影"如何定义？** 存为 `.tesserae/vault_snapshot.json` 快照，还是每次编译时即时重新投影？提案：使用快照，在每次编译结束时写入。更便宜，且避免让提取器的非确定性渗入覆盖层。
5. **多语言 vault 投影。** 当前的投影是单语言的（对应源端语言）。覆盖层是否需要按 locale 区分（例如对韩文 vault 中 `description` 的编辑仅作用于韩文投影）？提案：v1 不在范围内；vault 保持单语言，与项目主语言一致。

## 这在 `obsidian.md` 中的呈现方式

面向用户的指南依旧聚焦于"你可以阅读和查询 vault"，然后用一句话总结链接到这里讲述往返的故事："在 Obsidian 中修改字段，它们会在重编译时存活。完整模型见 [obsidian-sync.md](obsidian-sync.md)。"
