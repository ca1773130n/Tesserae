# 分层代理内存 — 按代理知识图

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

没有人能记住所有事情，任何代理的上下文窗口也装不下所有信息。Tesserae 的解决方案是一个**分层知识库**：每个代理从自己的会话中积累自己的记忆，该记忆定期**提炼**（组织、压缩、打磨、精化——并安全地遗忘），管理者只看到其报告的提炼层。管理者的管理者看到进一步的汇总。就像真实的组织一样，没有任何单一的读者需要整个档案。

下面的所有内容都是选择加入且附加的：从不运行 `tesserae distill` 的项目的行为与之前完全相同。

## 各层

- **L0 — 项目图** (`.tesserae/graph.json`)。不变，仍然保持字节幂等性。编译的结构化阶段现在为每个观察到的代理生成一个 `Agent` 节点，以及从每个会话到该节点的 `performed_by` 边——原始属性，零 LLM 成本。
- **L1 — 每个代理一个工件** (`.tesserae/agents/<key>/distilled.graph.json`)。由 `tesserae distill` 写入。普通图文件限制在**单个 48k 读取**内，因此任何代理都可以在单个调用中加载其整个提炼的内存。
- **L2 — 管理者汇总。** 提炼具有报告的代理时，汇总报告的 L1：按血统去重，按共享原始证据分组，**逐字**执行最佳笔记——LLM 重新汇总深度限制为 1，因此摘要永远不会是摘要的释义。同一阶段递归到任何组织深度。

## 代理身份

代理键为 `harness:account:role`——角色级别，因此 `reviewer` 子代理和 `planner` 子代理即使在一台机器上也会发展出*不同的*专业知识。角色来自成绩单中的子代理描述符，然后来自声明性注册表匹配规则，最后回退到 `default`。

```bash
tesserae agents init         # 扫描会话，推断组织，写入 .tesserae/agents/registry.json
tesserae agents tree         # 组织图，带会话计数 + 压缩陈旧
tesserae agents list         # 观察到的键、标签、父级、会话计数
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # 原子性迁移工件目录 + 注册表
```

`init` 从角色信号推断层次结构：子代理角色
（`claude-code:me:reviewer`）被父化为生成它的主代理
（`claude-code:me:default`），因此一个命令给你一个工作的多级组织
——无需 `set-parent`。传递 `--flat` 以强制旧的所有-在-根下图表。`set-parent` 仅适用于更深的手工设计的层次结构。无配置：在没有注册表的情况下，每个代理隐式报告给 `org:root`，
`agent="org"` 是完整的团队概览。

## 提炼

```bash
tesserae distill                      # 每个代理，叶子优先，管理者最后
tesserae distill --agent <key>        # 单个代理
tesserae distill --dry-run            # 估算 LLM 调用，不写入任何内容
tesserae distill --max-llm-calls 50   # 硬预算；限制的运行在重新运行上汇合
tesserae distill --retry-fallbacks    # 重新尝试退回的集群
tesserae distill --full               # 忽略水位线，从头重新提炼
```

该阶段对代理的发现进行集群化、汇总每个集群（引用列入白名单和忠实性封闭），并生成提炼的笔记，其身份是**血统键**——底层原始 L0 证据的哈希，永远不是 LLM 的措辞。缓存是激进的且共享的：未更改的输入被水位线跳过，成长的集群增量折叠、提供商故障被断路器处理并生成确定性结构退回（已标记、可重试、永远不会作为成功缓存）。

提炼是**选择加入的**：设置 `TESSERAE_AGENT_DISTILL=1`（或 `config.json` 中的 `{"agent_distill": {"enabled": true}}`）。启用后，`tesserae refresh` 也会自动提炼——但仅提炼*内存压力*下的代理（其未提炼的发现不再适应上下文读取的一半），MemGPT 风格的合并触发。

## 遗忘 — 永不删除

- **吸收**：衰减、低信度发现被高质量 llm 提炼覆盖时，将其折叠到其中（`absorbed_refs`）并在默认读取中抑制——但仍可通过 `include_superseded` 和 `drill_down` 访问。
- **降级**：所有其他内容最坏情况下从完整正文降至代理索引注记中的标题+参考行。仅年龄永远不会使知识不可见。
- **账本**：每个升级/降级/吸收都追加到遗忘账本，由 `tesserae lint` 呈现（`AGENT_FORGET_LEDGER`），以及每个代理的未提炼积压指标（`AGENT_UNDISTILLED_BACKLOG`）。

## 读取作用域视图

从 **CLI**，`--agent KEY` 作用于 `query`、`ask` 和 `context`：

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # 工作者视图
tesserae ask "what does my team know about deploys?" --agent org      # 整个团队
tesserae agents show claude-code:me:manager    # 模式、成员、陈旧
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

通过 **MCP**，每个图形读取工具接受相同的 `agent=`。在两种情况下
键解析为以下之一：

- **工作键** → 自己的原始体验 ∪ 自己的提炼笔记，提炼首选（吸收的原始在加载时由导出的覆盖自动抑制——没有任何内容被写回 `graph.json`）。
- **管理键** → 仅限报告的 L1 工件的联合。原始发现永远不会向上泄露。
- **`org`** → 所有提炼工件，零配置。

支持工具：`agents show` / `agent_view_explain`（成员 + `distilled_through` 陈旧水位线——每份报告的专业知识有多旧）和 `agents drill` / `drill_down`（将提炼笔记的 `member_refs` 解析回原始 L0 证据，包括活跃/已更改/已吸收/已消失状态——每个调用审计记录）。`compile_context --multi-pool` 为提炼笔记和专业知识档案预留预算插槽，并标记输出中的陈旧或退回质量知识。

## 增长循环

- **按代理的工具**：`write_harness` 代理模式为每个代理发出一个工具目录，其 MCP 配置到达该代理的解决视图，加上从其专业知识档案生成的一次性种子 `purpose.md` 任务页面。
- **按代理的指导**：通过 `.tesserae/extraction-guidance-<key>.md` 在项目级 `.tesserae/distill-guidance.md` 之上分层来指导一个代理的提炼。编辑一个代理的流只会重新提炼该代理。
- **语义网桥**（选择加入）：在管理器/组织视图中用 `shares_concept_with` 边链接*相关的*提炼——边，永不合并。
- **主题地图**：`agent_topics` 将代理的提炼集合卷成确定性 `topics.md`——代理的目录。
- **子代理升级**：类型化子代理运行在子代理自己的键下生成发现，因此委派工作累积到委派人的专业知识中。

## 确定性保证

项目图保持字节幂等；提炼工件在给定（图字节、注册表、缓存目录、先前工件、选项）时是确定性的。时间总是**语料库时钟**——会话本身中最新的时刻，递归地对于管理者来说最新的子水位线——永远不是挂钟。节点身份不依赖于 LLM 散文。Lint 探针拒绝代理层节点上的时间戳/计数器形状的元数据，因为这正是破坏字节幂等性的状态类。

完整的设计基本原理：`docs/superpowers/specs/2026-07-19-layered-agent-kg.md`。
