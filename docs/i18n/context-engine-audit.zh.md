# 作为上下文引擎的 Tesserae — 差距分析

<!-- translations:start -->
<p align="center"><a href="../context-engine-audit.md">English</a> · <a href="context-engine-audit.ko.md">한국어</a> · <a href="context-engine-audit.zh.md">中文</a> · <a href="context-engine-audit.ja.md">日本語</a> · <a href="context-engine-audit.ru.md">Русский</a> · <a href="context-engine-audit.es.md">Español</a> · <a href="context-engine-audit.fr.md">Français</a> · <a href="context-engine-audit.de.md">Deutsch</a></p>
<!-- translations:end -->
> **使命（2026-06-02）：** Tesserae 是一个*上下文引擎* —— 它通过三大支柱
> 重建一个**自我改进**的知识库，从而生成可直接交给智能体的上下文：
> **（1）会话监控**、**（2）自主主动摄取**、**（3）按需文档**。知识必须是
> **实时且不断演化的**，随时可以交给智能体。

本文档对照该使命审计当前代码库。它是四路并行评审（摄取/会话、自我改进、
输出/面向智能体、编排/生命周期）的产物。

## 一句话结论

今天的 Tesserae 是一个**机械上健康、测试充分的批处理 CLI 编译器**。对照
上下文引擎愿景，它在三大支柱上都是**手动触发 + 事后处理 + 受 git-HEAD
约束**的。构建引擎的机件已经作为原语存在 —— 缺失的是把它们组合起来的
**持续的、自我驱动的层**。

每个切面中最大的缺失部分：一个**长期运行的监督进程/守护进程，拥有单一事件
循环**，自主驱动会话跟踪、摄取、增量编译和发布。其余一切都是在其之上的增量
工作。

---

## 支柱 1 — 会话监控 → **事后而非实时**

| 状态 | 发现 | 所需 |
|---|---|---|
| 差距 | 会话捕获是事后扫描：`discover_harness_sessions()` 仅在有人运行 `sessions discover --import` 或 `compile` 时才遍历已完成的记录。`compile` 故意拒绝扫描 `~/.claude/projects/`（延迟）。 | 一个在会话运行时监视 harness JSONL 文件并按轮次摄取的**跟踪器（tailer）**。 |
| 差距 | 唯一真正的监视器（`watch.py WatchLoop`）覆盖**源 markdown**，每 2 秒轮询一次并触发整次 `compile`。它既不监视会话也不监视代码。 | 在一个监督进程下扩展到会话 + 源工具触发。 |
| 差距 | `vault_watch.py` 的"实时"循环作用于**输出**（Obsidian 反向同步），而非摄取。 | 不能替代实时知识拉取。 |
| 粗糙 | 会话重新提取按 `session_id` 做缓存键，但以**整个会话**为单位：一个新轮次使整个缓存失效并重跑完整 LLM 流程。 | 用于实时跟踪的轮次级增量性。 |
| 粗糙 | `harness_sessions` 存储是扁平 glob，每次 list/write 都全量重扫。 | 面向持续增长捕获集的索引式/追加式存储。 |
| 缺失 | 没有按节点的新鲜度/来源时间戳；时效性只在制品层面（git HEAD）跟踪。 | 用于"这有多新鲜？"的按事实新鲜度。 |

## 支柱 2 — 自我改进的知识库 → **一次性重提取，外挂式演化**

"演化"流程确实存在，但**只在单次 `compile` 内**运行（从零重提取），且大多数
**通过环境标志或手动 CLI 选择性开启**。事实在每次编译时重新计算，而非就地修订。

| 状态 | 发现 | 所需 |
|---|---|---|
| 差距 | **衰减（Decay）**（`memory/decay.py`，艾宾浩斯 14 天半衰期）只在*查询时*计算，编译时从不持久化或回写。 | 编译时衰减写入 + 持久化分数。 |
| 差距 | 衰减的访问循环是**死的**：`last_accessed_at == first_seen_at`，`access_count` 从不递增。"我一直在看它 → 它重要"的信号毫无作用。 | 一个访问记录面（MCP 读取 → 递增）。 |
| 差距 | **取代（Supersede）**（`memory/supersede.py`）受 `TESSERAE_SUPERSEDE_PASS=true` 门控（默认关闭），且只*追加*边 —— 从不降级/隐藏过时内容。信念修订只是表面文章。 | 默认开启 + 在输出中抑制被取代事实的消费者。 |
| 差距 | **矛盾（Contradictions）**被*检测*（`lint.py`，info 级，脆弱的字符串匹配）但从不被*解决*。没有置信度仲裁。 | 一个解决流程，而非仅仅探测。 |
| 差距 | **模式漂移（Schema drift）**（`schema_drift.py`）是手动 `schema-drift` 子命令，只写提案；模式从不自我精炼。 | 应用路径 + 流水线集成。 |
| 差距 | **规范化（Canonicalization）**只自动合并高置信度别名；其余排队等待人工 CLI 批准。 | 随时间推移由 LLM 仲裁的自动合并。 |
| 差距 | **反馈闭环半开**：确定性基线提取器*完全忽略指导*（`selective_extractor.py:43`）；只有可选的 LLM 路径消费纠正。关闭 LLM 时，人工纠正永不重新进入提取。 | 确定性路径遵循指导，或默认启用 LLM。 |
| 差距 | 没有**复发洞见强化**：当洞见跨会话再现时，没有任何东西增强其置信度。`temporal.infer_confidence` 是粗糙的字符串启发式。 | 跨会话频率 → 数值置信度。 |
| 粗糙 | 取代候选配对是**词法 Jaccard（0.55）**；词法重叠低的语义复述永远成不了候选。 | 基于嵌入的候选生成。 |
| 缺失 | **整个自我改进切面未经测试**（无 decay/supersede/feedback/drift/canonical/temporal 测试）。 | 在此处任何改动旁附带测试。 |

## 支柱 3 — 按需文档 → **尚不存在**

查询/检索管道很成熟（混合 RRF、PPR、约 20 个 MCP 工具、按页 ask、AI 导出）。
但**每个制品要么是静态的全语料投影，要么是单节点查找。** "用户问'给我关于 X
的上下文' → 量身定制的文档"尚未实现。构建它的原语全都存在，但从未被组合。

| 状态 | 发现 | 所需 |
|---|---|---|
| 缺失 | **按需文档生成（核心支柱 3 差距）。** 没有模块能从请求生成量身定制、查询范围的文档。`report.py` 是编译时 lint 摘要，不是知识制品。 | 新建 `context_compiler`：搜索 → PPR → 邻域遍历 → 正文组装 → 可选 LLM 综合。 |
| 差距 | `wiki_page` 返回一个预编译的节点正文；没有多节点、查询范围的组装工具。 | `compile_context(query|seeds, depth, budget)` MCP 工具。 |
| 差距 | `ask` 返回散文或结果列表，从不返回可下载/可交接的上下文制品。 | 发出结构化、带引用上下文捆绑包的回答模式。 |
| 差距 | `agent_harness.py` 是**静态**交接（硬编码前 12 个节点 + 固定列表），不是查询范围或按任务的。 | 接受主题/种子 → 渲染范围化简报。 |
| 差距 | `node_context` 是 1 跳、无排名。作为智能体上下文原语很弱。 | 通过 PPR 实现带排名的 k 跳上下文。 |
| 差距 | 导出（`llms.txt`、`graph.jsonld`）是整语料转储；没有按主题切片。 | 按主题范围子图 → llms-txt 切片。 |
| 粗糙 | 默认嵌入通道是**确定性哈希桶伪嵌入**（blake2b，128 维）；只有安装 `sentence-transformers` 才有真正的语义后端，且 `auto` 会静默降级。开箱即用的"语义"检索是假的。 | 真正的默认嵌入，或对哈希通道发出醒目警告。 |
| 粗糙 | 若缺少节点引用正则匹配，`query.answer()` 会**丢弃一个有效的 LLM 答案**。 | 保留答案；改为标记缺失的引用。 |
| 粗糙 | 静态托管 ask 小部件提供**罐装 `DEMO_QA`**；真正的 ask 只在 `serve` 下工作。公开 Pages 的"ask"是表演。 | 演示可接受；但在发布站点上智能体无法消费。 |
| 粗糙 | `ask` 的 `auto` 后端吞掉异常并不可见地降级为 BM25。 | 揭示哪个后端作答以及为何触发回退。 |

## 横切关注点 — 编排与生命周期 → **批处理 CLI，无引擎**

| 状态 | 发现 | 所需 |
|---|---|---|
| 差距 | **没有守护进程/引擎进程。** 扁平的一次性 argparse 分发器；每个子命令后进程退出。零 signal/SIGTERM/pidfile/launchd 处理；监视器在裸 `KeyboardInterrupt` 时死掉。 | 一个受监督的长期运行守护进程，拥有单一事件循环 + 优雅关停。 |
| 差距 | "持续" = `while True: time.sleep(interval)` markdown 轮询器。没有文件系统事件、背压或流式处理。 | 带单一调度器的事件驱动核心。 |
| 差距 | **"Refresh" 存在于斜杠命令 markdown 技能中**而非代码 —— 它把 `sessions discover --import` → `compile` → `obsidian-sync` 串联起来。 | 守护进程/CLI/MCP 共享的一等进程内流水线编排器。 |
| 粗糙 | `changed_only` 增量编译**脆弱且自称是权宜之计**：清单是 `{path: sha256}`；必须重载先前的图、剥离投影器/综合节点、驱逐重提取的源节点、再合并 —— 否则一次 21 文件编辑会把 2400 节点塌缩为 1700。 | 流经 `GraphStore` 端口的设计良好的增量/流式层。 |
| 粗糙 | `cli.py` 是约 2000 行的上帝分发器（`if args.command == ...` 阶梯）；`ask`/`wiki` 有各自手搭的解析器。 | 命令注册表 / 子命令模块。 |
| 粗糙 | 阶段门控标志出货了半成品表面：`--sessions-llm` 帮助说*"待 Phase 5 落地后生效"*。 | 完成或隐藏。 |
| 粗糙 | `graph_stores/url_resolver.py` 每次调用都用 `asyncio.run` 包裹异步存储 —— 每次 upsert 一个全新事件循环。对流式处理是病态的。 | 引擎上线后采用持久异步运行时。 |
| 粗糙 | `ports/` 六边形协议已定义，但独立流水线**绕过**它们直奔 JSON 制品。只有 HypePaper 使用端口。 | 让核心流水线一致地流经 `GraphStore`。 |
| 粗糙 | 三种持久化格式（JSON 制品、SQLite 存储、Kuzu）没有单一真相来源；Kuzu 适配器把每个字段 base64 包裹以规避 0.16 损坏 bug。 | 收敛到一个真相来源。 |
| 粗糙 | `serve`（`TCPServer.serve_forever`）和 `watch` 是**各自的阻塞进程** —— 无法同时 serve + 自动重编译。`deploy` 是手动 git push，解耦的。 | 把 serve + watch + deploy 统一到监督进程下以实现持续发布。 |
| 缺失 | `frontend.py` 是一个**已弃用的死模块**仍在出货，与 `tesserae/site/` 重复。 | 删除或迁移调用者。 |
| 粗糙 | `review_workflow.py` 人工评审循环发出供手工编辑的字符串型 `"action": "TODO: merge|keep_separate"` JSON；没有程序化应用路径。 | 接入编译的集成评审队列。 |
| 注 | TODO/FIXME 标记确实稀少 —— 真正的债务是**注释记录的权宜之计**（changed-only 合并、Kuzu base64、asyncio-per-call），而非零散的 TODO。 | — |

---

## 推荐构建顺序（架构增量 → 愿景）

1. **监督守护进程 + 进程内流水线编排器。** 一个事件循环、信号/关停，替换
   markdown 技能 refresh 链。*解锁其他每一根支柱。*
2. **实时会话监控器。** 跟踪 harness JSONL → 轮次级增量提取 → 喂给循环。
   （替换手动 `sessions discover --import`。）
3. **真正的增量/流式编译**，流经 `GraphStore` 端口，退役脆弱的
   `changed_only` 驱逐补丁。
4. **默认激活自我改进流程 + 持久化它们**：编译时衰减写入、MCP 读取时
   access-count 递增、supersede 开启（带抑制）、矛盾解决、复发洞见置信度。
5. **按需上下文编译器**（`compile_context` MCP 工具 + CLI）：查询 →
   PPR/混合 → 邻域遍历 → 组装、带引用、面向智能体的文档。
6. **真正的默认嵌入**（或醒目的降级警告），使语义检索不再是开箱即用的
   哈希桩。
7. **统一 serve + watch + deploy** 以实现持续发布；添加生命周期测试
   （愿景最依赖的层目前覆盖最少）。

## 值得保留的优势

确定性逐字节相同编译；批处理机件上广泛的测试覆盖；干净的混合 RRF 检索 +
深思熟虑的 PPR 边权重；广泛且正确划分（公开/私有）的 MCP 工具面；坚实的
静态导出（`llms.txt`、JSON-LD、RSS）和注重安全的 ask 小部件。地基坚固；
要做的工作是在其之上添加动态的、自我驱动的层。
