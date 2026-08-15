# 分层代理内存 — 每个代理的知识图

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

没有人记得一切——没有任何代理的上下文窗口能容纳一切。
Tesserae 的答案是一个**分层知识库**：每个代理从自己的会话中成长自己的内存，该内存定期被**蒸馏**(组织、压缩、精炼、完善——并且安全地遗忘)，管理者只看到他们报告的蒸馏层。管理者的管理者看到进一步的汇总。像真正的组织一样，单一读者永远不需要整个档案。

下面的一切都是可选的和附加的：从不运行 `tesserae distill` 的项目的行为与以前完全相同。

## 各层

- **L0 — 项目图** (`.tesserae/graph.json`)。不变。仍然
  字节幂等。编译的结构过程现在对每个观察到的代理铸造一个 `Agent` 节点，加上从每个会话到 `performed_by` 边——原始归属，零 LLM 成本。
- **L1 — 每个代理一个工件** (`.tesserae/agents/<key>/distilled.graph.json`)。
  由 `tesserae distill` 编写。一个普通的图形文件，受限于**单次 48k
  读取**，因此任何代理都可以在单个调用中加载其整个蒸馏内存。
- **L2' — 管理者汇总。** 蒸馏具有报告的代理时，汇总报告的 L1：按谱系去重、按共享的原始证据分组，并逐字保留最佳注释——LLM 重新摘要深度上限为 1，因此摘要永远不是摘要的改写。相同的通过递归到任何组织深度。

## 代理身份

代理由 `harness:account:role` 键控——角色等级，所以 `reviewer` 子代理和 `planner` 子代理即使在同一台机器上也会发展*不同的*专业知识。角色来自记录中的子代理描述符，然后是声明性注册表匹配规则，然后回退到 `default`。

```bash
tesserae agents init         # 扫描会话，推断组织，写入 .tesserae/agents/registry.json
tesserae agents tree         # 组织结构图，带有会话数 + 蒸馏过时
tesserae agents list         # 观察到的键、标签、父项、会话数
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # 原子地迁移工件目录 + 注册表
```

`init` 从角色信号推断层次结构。子代理角色(`claude-code:me:reviewer`)被分配给生成它的主代理(`claude-code:me:default`)，因此一个命令给你一个有效的多级组织——不需要 `set-parent`。传递 `--flat` 强制旧的"所有人在根下"图表。`set-parent` 仅用于更深层、手工设计的层次结构。零配置仍然有效：没有注册表，每个代理报告给 `org:root`，`agent="org"` 是平面团队概览。

## 蒸馏

```bash
tesserae distill                      # 每个代理，叶子优先，管理者最后
tesserae distill --agent <key>        # 一个代理
tesserae distill --dry-run            # 估计 LLM 调用，不写任何内容
tesserae distill --max-llm-calls 50   # 硬预算；上限运行在重新运行上收敛
tesserae distill --retry-fallbacks    # 重新尝试回退的集群
tesserae distill --full               # 忽略水位标，从头重新蒸馏
```

该通过将代理的发现聚类、摘要每个集群(引用白名单和真实性检查)，并铸造蒸馏的笔记，其身份是**谱系键**——底层原始 L0 证据的哈希，永远不是 LLM 的措辞。缓存是主动的且共享的。未更改的输入被水位标跳过、生长的集群递增折叠、提供者故障被断路并产生确定性结构回退(标记、可重试、永远不作为成功缓存)。

蒸馏是**可选的**：设置 `TESSERAE_AGENT_DISTILL=1`(或 `config.json` 中的 `{"agent_distill": {"enabled": true}}`)。启用时，`tesserae refresh` 也会自动蒸馏——但仅限于**内存压力**下的代理(其未蒸馏的发现不再适合半个上下文读取)，MemGPT 风格的整合触发器。

## 自动整合(睡眠周期)

你不必记得蒸馏。像大脑在休息中整合记忆一样，始终在线的 `tesserae engine` 守护程序在项目**闲置**时自动整合(几分钟内没有编辑或会话)，加上定期的上限，因此持续繁忙的项目仍然会整合。每次运行执行五项操作：它**压缩和遗忘**(下面的蒸馏通过)，让未检索的知识**因不使用而衰减**(上面的 LRU 衰减)，**发现存活下来的内容之间的新连接**，然后花费两个小的每 tick LLM 预算来**预热**代理读取的总结——`graph_map` 下降的作用域的社区总结以及宪章的活跃领域的领域摘要。蒸馏步骤完全包装上面描述的 `maybe_distill_on_refresh` 触发器——相同的可选择门、每个代理水位标和内存压力检查——因此循环是无操作，除非 `TESSERAE_AGENT_DISTILL` 被设置、在编译门下运行且不干扰确定性工件。

完整行为、CLI 标志(`--consolidate-idle` / `--consolidate-every` / `--consolidate-check` / `--summarize-budget` / `--brief-budget`)和车队笔记：
[docs/engine-consolidation.md](engine-consolidation.zh.md)。

## 遗忘 — 永不删除

- **吸收**：一个衰减的、低置信度的发现被 llm 质量蒸馏液覆盖，会被折叠到它 (`absorbed_refs`) 并在默认读取中被抑制——但通过 `include_superseded` 和 `drill_down` 仍可到达。
- **降级**：其他所有东西最坏的情况下从完整正文下降到代理索引注释中的标题+参考行。仅年龄永远不会使知识不可见。
- **因不使用 (LRU)**：衰减由*检索最近性*驱动，而不仅仅是创建年龄。读取表面记录访问——`last_accessed_at` / `access_count`——到一个 `node_memory` sidecar(绝不进入 `graph.json`)。蒸馏在计算衰减**之前**将该活动访问状态合并到其工作视图中，因此从未检索到的发现衰减并变得有资格被吸收或降级，而最近读过的发现则无论年龄如何都保持。空 sidecar 完全再现了旧的仅年龄行为。
- **分类账**：每个升级/降级/吸收都追加到遗忘分类账并由 `tesserae lint` 表面化(`AGENT_FORGET_LEDGER`)，以及每个代理的未蒸馏积压指标(`AGENT_UNDISTILLED_BACKLOG`)。
- **谁读过它**（需显式开启）：上面的访问计数只能说明某个节点被读过，说不出是谁读的 —— 于是不停轮询某个
  节点的话痨智能体，和只读了一次的人类，对遗忘来说是同一种输入。在 MCP 服务端设置
  `TESSERAE_READ_AUDIT=1`，每次读取还会以 `{tool, actor, node_ids, at, tesserae_version}`
  记录进同一个 `.tesserae/sqlite.db` 边车，通过 `read_audit` 工具连同按 actor 的统计一起读取。
  当调用解析出智能体视图时，actor 就是 `agent` 参数，否则取 `TESSERAE_ACTOR`；两者都没有时，这次读取
  记为匿名，而不是归给一个编造出来的名字。**默认关闭是刻意的** —— 覆盖每一个读取面的常开账本，会把每一次
  读取都变成一次写入。关掉它只是停止记录，不会抹掉已记录的内容，而且这些内容永远不会进入 `graph.json`。

## 发现的连接

除了压缩和遗忘外，整合还会**发现蒸馏笔记之间的新连接**——跨项目内的代理，而不仅仅是在一个代理内。它嵌入笔记并将接近的对链接为 `shares_concept_with` 边(带有 `federation_semantic` 标记)。发现被**嵌入门控**——仅当配置了真实嵌入后端时运行，并跳过哈希存根——因此它永远不会制造虚假链接。边被写入到 `.tesserae` 下累积的**边车覆盖层**，*永远*不会进入 `graph.json`，并在查询/PPR/联合读取时在内存中合并(完全像范围视图覆盖)。每个整合周期对之前周期发现的内容进行去重和扩展。有关运行它的睡眠周期操作，请参见
[docs/engine-consolidation.md](engine-consolidation.zh.md)。

## 读取作用域视图

从**CLI**，`--agent KEY` 对 `query`、`ask` 和 `context` 进行范围界定。

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # 工人视图
tesserae ask "what does my team know about deploys?" --agent org      # 整个团队
tesserae agents show claude-code:me:manager    # 模式、成员、过时
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

在**MCP**，每个图形读取工具都接受相同的 `agent=`。在两种情况下，密钥解析为以下之一：

- **工人密钥** → 自己的原始经验 ∪ 自己的蒸馏笔记，蒸馏液首选(吸收的原始由加载时派生的覆盖层自动抑制——永远不会写回 `graph.json`)。
- **管理者密钥** → 仅报告 L1 工件的联合。原始发现永远不会向上泄露。
- **`org`** → 所有蒸馏工件，零配置。

支持工具：`agents show` / `agent_view_explain`(成员 + `distilled_through` 陈旧水位标——每份报告的专业知识有多旧)，以及 `agents drill` / `drill_down`(将蒸馏笔记的 `member_refs` 解析回原始 L0 证据，状态为"活跃/已更改/已吸收/已消失"——每个调用均被审计记录)。`compile_context --multi-pool` 为蒸馏笔记和专业知识档案预留预算插槽，并在输出中标记陈旧或回退质量的知识。 只有生产者真正创建的节点才能占用插槽——蒸馏流程、会话事件流程，或代理自己的 `graph_write`——因此仅由文档抽取填充的类型，其池会保持为空；CLI 和 `knobs.pool_reservations` 都会指出哪些池什么也没返回。

## 增长循环

- **每个代理一个线束**：`write_harness` 代理模式为每个代理发出一个线束目录，其 MCP 配置到达该代理的已解析视图，加上一个仅播种一次的 `purpose.md` 任务页面，从其专业知识档案生成。
- **每个代理指导**：通过 `.tesserae/extraction-guidance-<key>.md` 指导一个代理的蒸馏，分层位于项目级别 `.tesserae/distill-guidance.md` 上。编辑一个代理的流只重新蒸馏该代理。
- **语义桥**(可选)：在管理者/组织视图中用 `shares_concept_with` 边链接*相关的*蒸馏液——边，不是合并。
- **主题映射**：`agent_topics` 将代理的蒸馏液集合转换成确定性 `topics.md` ——代理的目录。
- **子代理晋升**：类型化子代理运行在子代理自己的键下生成发现，因此委派的工作积累到代表的专业知识。

## 确定性保证

项目图保持字节幂等；蒸馏工件在给定(图字节、注册表、缓存目录、先前工件、选项)时是确定性的。时间总是**语料时钟**——会话本身的最新时刻，递归地为管理者提供最新的子水位标——永远不是挂钟时间。节点标识不取决于 LLM 散文。Lint 探针拒绝代理层节点上的时间戳/计数器形元数据，因为那个精确的状态类之前已经打破了字节幂等。

完整设计基本原理：`docs/superpowers/specs/2026-07-19-layered-agent-kg.md`。
