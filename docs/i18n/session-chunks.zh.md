# 每日 session chunk —— `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.ko.md">한국어</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a> · <a href="session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
带窗口的会话查询——`tesserae summary`、`tesserae decisions`，以及 `ask` 规划器的 activity 动作——以前每次调用都会重新解析窗口内的每一份 Claude Code / Codex 转录。每日 chunk 存储把每个归一化后的 turn **只持久化一次**，按 KST 日期标签分桶，因此已完整覆盖的过去某一天会直接从 SQLite 提供，而不是重新做原始扫描。在一个真实的数千会话语料上实测，这使带窗口的摘要**快约 20 倍**。

该存储是单个 SQLite 文件 `.tesserae/session_chunks.db`（WAL，每次操作使用短生命周期连接）：一张按天索引的 `turns` 表、一张记录哪些 `(day, harness)` 组合已完整的 `day_coverage` 表，以及一张带模式版本的 `meta` 表。

## 谁写入它

1. **实时 —— engine 的 tailer。** 当 `tesserae engine` 运行时，会话 tailer 在每次轮询中边跟踪边把 turn 追加到存储里，并为受影响的日期 upsert 覆盖记录（`source: "tailer"`）。写路径是仅追加的，对重复投递的 turn 幂等，且绝不会把异常抛进 daemon 循环。这里刻意**没有 SessionEnd 钩子写入器**——后台化的 SessionEnd 写入器会堆积（一个有记录的失败模式）。
2. **回填。** 两个入口会遍历既有转录并补齐历史（`source: "backfill"`）：
   - `tesserae refresh` 在其 sessions-import 步骤中自动运行一次回填，所以升级后的第一次 refresh 无需额外操作即可填充该存储。
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` 显式运行回填；`--since` 限定回溯多远（默认：完整历史）。

   回填在 `.tesserae/session_chunks.lock` 上获取一个**非阻塞** flock，语义为持有即跳过——并发回填（或已持有该锁的 engine）会让第二个调用者干净地跳过而不是排队。回填的 upsert 以 `(session_path, ts, role, hash(text))` 为键，因此 tailer 行与回填行永远不会互相重复。增量回填有一天的重叠窗口，用来修复在某天覆盖首次声明之后才落盘的 turn。

## 谁读取它

快速路径位于唯一的扫描咽喉点（`activity_summary.iter_project_transcripts` / `scan_messages`），因此下游的一切都透明地继承它：

- `tesserae summary`（包括其内嵌的 decisions 收集）
- `tesserae decisions`
- `tesserae ask` —— 规划器的 `activity_summary` / `decisions` 动作
- MCP 的 `activity_summary` 和 `query_decisions`
- 实时会话视图

## 覆盖规则：今天永远走原始扫描

只有当以下条件**全部**成立时，一个窗口才会由 chunk 提供：

1. 它是一个精确对齐 KST 的单日；
2. 该日**严格早于今天**——今天仍在写入中，因此始终走原始转录扫描；
3. 该日**每一个**被请求的 harness 都存在 `day_coverage` 行。

其余任何情况下，该窗口都回退到原始扫描。

## 原始扫描回退保证

chunk 存储是加速器，绝非事实来源：

- 任何 DB 错误、文件缺失/损坏，或 `schema_version` 不匹配，都会让 chunk 路径返回**空**——调用方的原始转录扫描照常进行。模式不匹配会丢弃并重建为空的存储；覆盖记录随之消失，因此回退始终正确。
- 没有覆盖记录的日期（例如 engine 没有运行且未做过回填）会静默地走慢路径。结果正确，但加速消失——`tesserae doctor` 会报告近期窗口内的覆盖缺口，并指向 `tesserae sessions chunk-backfill`（见 [doctor.md](doctor.zh.md)）。
- **一致性不变量：**对已完整覆盖的一天，chunk 提供的 turn 与原始扫描将产出的结果完全相等（相同的时间戳、角色、名称、文本、会话键和 harness）。

## 运维备注

- 保持 `tesserae engine` 运行，过去的日期就会实时保持覆盖；否则偶尔运行一次 `tesserae refresh`（或显式的 `chunk-backfill`）即可补上缺口。
- 该存储按项目独立，位于 `.tesserae/` 下，随时可以安全删除——下一次回填会重建它，期间读取方会回退到原始扫描。
