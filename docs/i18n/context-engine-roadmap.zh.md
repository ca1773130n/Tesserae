# Tesserae → 上下文引擎 — 分阶段路线图

<!-- translations:start -->
<p align="center"><a href="../context-engine-roadmap.md">English</a> · <a href="context-engine-roadmap.ko.md">한국어</a> · <a href="context-engine-roadmap.zh.md">中文</a> · <a href="context-engine-roadmap.ja.md">日本語</a> · <a href="context-engine-roadmap.ru.md">Русский</a> · <a href="context-engine-roadmap.es.md">Español</a> · <a href="context-engine-roadmap.fr.md">Français</a> · <a href="context-engine-roadmap.de.md">Deutsch</a></p>
<!-- translations:end -->
派生自 [`context-engine-audit.md`](./context-engine-audit.zh.md)。把这 7 步
构建顺序转化为带有依赖、具体范围和验收标准的有序阶段。

**北极星：** 一个持续运行的引擎，监控会话、自主摄取知识、自我改进其知识库，
并提供按需、面向智能体的上下文 —— 取代今天的手动批量编译 CLI。

## 依赖形态

```
P0 流水线编排器（把 refresh 链提取为代码）
        │
P1 监督守护进程（循环）──────────────────────┐
        │                                      │
   ┌────┴─────────────── 轨道 A ───────┐   ┌── 轨道 B（可并行）──┐
   P2 实时会话监控器                   │   P5 真正的默认嵌入
   P3 增量/流式编译                    │   P6 按需上下文编译器
   P4 自我改进持久化 ──────────────────┘   （P6 依赖 P5）
        │                                      │
        └──────────────► P7 统一 serve+watch+deploy ◄──┘
```

轨道 A（实时摄取）和轨道 B（面向智能体的输出）在 P1 落地后可并行进行。
P7 使二者收敛。

---

## 阶段 0 — 流水线编排器（去风险基础）

**目标：** 把 refresh 流水线从斜杠命令 markdown 中移出，成为守护进程、CLI
和 MCP 都调用的一等进程内编排器。

- **为何现在：** 后续每个阶段都需要 `ingest → compile → project → publish` 的
  一条共享代码路径。今天该序列只作为技能里的散文存在。在它可调用之前，
  其他一切都无法自动化。
- **范围：** 新建 `tesserae/engine/pipeline.py`（一个包裹当前
  `sessions discover --import → compile → obsidian-sync` 链的 `Pipeline`
  对象）。把 `cli.py` 子命令通过它路由。开始把约 2000 行的 `project_main`
  上帝分发器分解为命令表（机械的，无行为变更）。
- **交付物：** `Pipeline.run(steps, changed_only=…)`；CLI 委派给它；步骤排序 +
  失败传播的单元测试。
- **验收：** `tesserae project refresh` 作为代码（而非技能）存在，并在演示
  语料上逐字节复现 markdown 链。
- **风险：** 低。纯重构；现有测试守护行为。
- **关闭的审计发现：**"refresh 存在于技能中"、"cli 上帝分发器"。

## 阶段 1 — 监督守护进程（引擎循环）

**目标：** 一个受监督的长期运行进程，拥有单一事件循环，根据触发驱动
`Pipeline`，并具备真正的生命周期处理。

- **为何现在：** 这是脊柱。审计中最大的单一缺口。一切"持续/自主"都挂在它上。
- **范围：** 新建 `tesserae/engine/daemon.py` —— 事件循环、触发队列、
  去抖/合并、`SIGTERM`/`SIGINT` 优雅关停、pidfile、结构化日志。
  `tesserae engine` / `tesserae daemon` CLI 入口。通过把 `watch.py`/
  `vault_watch.py` 折叠为喂给队列的*触发源*，替换其裸 `KeyboardInterrupt` 死亡。
- **交付物：** 无限期运行、把突发合并为一次流水线运行、干净关停的守护进程；
  launchd/systemd 示例单元。
- **验收：** 编辑一个源文件 → 守护进程在去抖窗口内合并并运行一次
  `compile(changed_only)`；`SIGTERM` 以 0 退出且无孤儿线程；在流水线异常下
  存活而不死。
- **风险：** 中 —— 并发/关停正确性。用单线程 asyncio 核心 + 显式任务监督来缓解。
- **关闭的审计发现：**"无守护进程"、"持续 = sleep 轮询器"、监视器
  `KeyboardInterrupt` 死亡、无信号处理。

## 阶段 2 — 实时会话监控器（支柱 1）

**目标：** 跟踪实时 harness 记录，在会话运行时摄取轮次，替换事后的
`sessions discover --import`。

- **为何现在：** 需要 P1 的循环来喂。交付"会话监控"支柱。
- **范围：** 新的会话跟踪触发源（监视 `~/.claude` / `~/.codex` JSONL 追加事件）
  → 入队。在 `session_graph*.py` 中实现轮次级增量提取，使新轮次不会让整会话
  缓存失效。为 `harness_sessions` 建索引/追加存储（退役全重扫 glob）。
- **交付物：** 守护进程在轮次被写入后数秒内摄取它们；`test_session_tailer.py`。
- **验收：** 在受监视项目中启动实时智能体会话 → 无需任何手动命令，新发现
  即出现在图中；测得轮次级缓存命中率 > 整会话重提取。
- **风险：** 中 —— JSONL 格式在各 harness 间不同；部分行读取。
- **关闭的审计发现：** 事后会话扫描、整会话缓存失效、扁平 glob 存储。

## 阶段 3 — 经 GraphStore 端口的增量/流式编译

**目标：** 用流经 `ports/graph_store.py` 的设计良好的增量层，替换脆弱的
`changed_only` 图驱逐补丁。

- **为何现在：** 持续摄取（P2）使当前的 reload-strip-evict-merge 权宜之计成为
  正确性隐患（记录在案的"2400→1700 节点"陷阱）。自我改进（P4）需要按节点
  upsert。
- **范围：** 让独立流水线流经 `GraphStore`（今天它绕过端口直奔 JSON）。带来源 +
  新鲜度时间戳的按节点 upsert/delete。把持久化收敛到一个真相来源（审计：
  JSON 制品 vs SQLite vs Kuzu）。修复 `url_resolver.py` 的
  `asyncio.run`-每调用（持久异步运行时）。
- **交付物：** 仅正确添加/更新/移除变更节点的增量编译；按节点
  `first_seen_at`/`last_updated_at`。
- **验收：** 一次 21 文件编辑恰好更新受影响的节点（无塌缩）；逐字节相同的全
  编译对等性作为黄金测试保留。
- **风险：** 高 —— 触及核心数据模型。用功能标志门控；在受信任之前与全编译
  输出做 diff。
- **关闭的审计发现：** 脆弱的 `changed_only`、端口被绕过、asyncio 每调用、
  三种持久化格式、无按节点新鲜度。

## 阶段 4 — 激活并持久化自我改进（支柱：自我改进）

**目标：** 让知识库真正就地、默认开启、在编译时持久化地演化。

- **为何现在：** 依赖 P3 的按节点 upsert。关闭最未测试的切面。
- **范围：** 在编译时持久化**衰减**分数（`memory/decay.py` 不再仅查询时）；
  在 MCP 读取时递增 `access_count`/`last_accessed_at`。默认开启**取代**并对
  过时内容做下游*抑制*（不仅是边追加）。添加**矛盾解决**（把 `lint.py` 的检测
  升级为置信度仲裁的流程）。**复发洞见强化**（跨会话频率 → 数值置信度）。
  把**模式漂移**应用路径和**反馈指导**接入提取（确定性路径目前忽略它）。
  基于嵌入的取代候选生成（退役词法 Jaccard）。
- **交付物：** 每个流程在默认流水线中运行并回写；覆盖
  decay/supersede/feedback/drift/contradiction 的新 `tests/` 套件。
- **验收：** 跨会话重述一个事实会提高其置信度；被取代的事实不再出现在上下文
  输出中；衰减分数持久化并随运行而移动；自我改进套件为绿（当前零测试）。
- **风险：** 中 —— 提取输出的行为变更；用黄金固件守护。
- **关闭的审计发现：** 整个支柱 2 表格。

## 阶段 5 — 真正的默认嵌入（轨道 B 基础）

**目标：** 停止把确定性哈希桶伪嵌入作为默认"语义"通道出货。

- **为何现在：** P6 的上下文编译器只与检索一样好。独立于守护进程 —— P0 落地后
  即可开始。
- **范围：** 出货一个真正的默认嵌入后端（或让 `auto` 大声失败，而不是在
  `retrieval/hybrid.py` 中静默降级为 blake2b）。一旦嵌入是真的，就让嵌入通道
  生成候选（而非仅重排）。
- **交付物：** 默认安装产出真正的语义检索，或一个明确、可见的"运行在哈希桩上"
  警告。
- **验收：** 释义/同义词查询浮现 BM25 错过的相关节点；在小型标注集上相对哈希
  基线度量检索质量。
- **风险：** 中 —— 依赖重量 / 离线安装。提供分层默认。
- **关闭的审计发现：** 哈希桶默认、嵌入通道候选门。

## 阶段 6 — 按需上下文编译器（支柱 3）

**目标：** 头条功能 ——"给我关于 X 的上下文"→ 一份量身定制、带引用、面向
智能体的文档。

- **为何现在：** 依赖 P5（检索质量）。受益于 P4（更干净的库）。产品的核心
  价值主张。
- **范围：** 新建 `tesserae/context_compiler.py`：查询/种子 → PPR + 混合搜索 →
  带排名的 k 跳邻域遍历 → 组装 wiki 正文 → 可选 LLM 综合 → 一份带引用 + 预算
  控制的范围化 markdown 文档。作为 MCP `compile_context(query|seeds, depth,
  budget)` 和 `tesserae context …` CLI 暴露。使 `agent_harness` 按主题范围化；
  把 `node_context` 经由 PPR 路由；按主题范围的 `llms.txt` 导出切片。
- **交付物：** 一个为任意查询返回可下载、带引用上下文捆绑包的工具；断言捆绑包
  形状 + 引用完整性的测试。
- **验收：** `compile_context("X")` 返回一份连贯的多节点文档，其引用全部可解析；
  harness 简报按主题重新生成，而非硬编码前 12。
- **风险：** 中 —— 综合质量；保留一个确定性无 LLM 组装模式。
- **关闭的审计发现：**"按需文档生成不存在"、查询范围综合、静态 harness、
  无排名 `node_context`、整语料导出。

## 阶段 7 — 统一 serve + watch + deploy + 生命周期测试

**目标：** 一个受监督进程服务站点、变更时重编译并持续发布；生命周期层获得
测试覆盖。

- **为何现在：** 收敛。需要 P1（守护进程）和输出侧（P6）才值得持续发布。
- **范围：** 把 `serve.py`（阻塞 `TCPServer`）和 `deploy.py`（手动 git push）
  折叠进守护进程，使 serve + watch + publish 共享一个监督进程。持续/去抖发布。
  添加缺失的 `test_watch`/`test_serve`/守护进程生命周期测试。删除已弃用的
  `frontend.py` 死模块。把 `review_workflow.py` 的字符串型 TODO 循环接入真正的
  应用路径。
- **交付物：** `tesserae engine --serve --publish` 运行完整循环；生命周期测试
  套件；移除死代码。
- **验收：** 一次源编辑无需手动命令即传播到实时服务的页面并（可选）一次发布
  部署；生命周期测试为绿。
- **风险：** 低–中 —— 主要是集成。
- **关闭的审计发现：** serve/watch/deploy 分裂、手动 deploy、已弃用
  `frontend.py`、评审循环桩、缺失生命周期测试。

---

## 排序摘要

| 阶段 | 主题 | 依赖 | 可并行对象 |
|---|---|---|---|
| P0 | 流水线编排器 | — | — |
| P1 | 监督守护进程 | P0 | — |
| P2 | 实时会话监控器 | P1 | P5 |
| P3 | 增量编译 | P1 | P5 |
| P4 | 自我改进持久化 | P3 | P5, P6 |
| P5 | 真正的嵌入 | P0 | P2, P3, P4 |
| P6 | 按需上下文编译器 | P5 | P2, P3, P4 |
| P7 | 统一 serve/watch/deploy | P1, P6 | — |

**最小可行引擎：** P0 + P1 + P2 + P3 —— 一个监视实时会话并增量编译的运行
守护进程。**差异化产品：** 加上 P5 + P6（按需智能体上下文）。**打磨：** P4 + P7。
