# Harness 会话历史

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae 可以导入本地 AI-agent 转录，并把它们作为项目记忆渲染在静态站点的 `sessions/` 区之下。

此功能刻意与 `export harness` 分离：

- `export harness` 是面向 Claude Code、Codex、Gemini、Cursor、Kiro、OpenCode 等工具的出站上下文。
- `sessions ...` 是入站历史：它归一化当前项目此前的 Claude Code/Codex 会话，存储在 `.tesserae/harness_sessions/` 下，并让 `export site` 发布会话索引/详情页。

## 两条入口：批量导入与实时监控

会话摄取不再仅限批量。有两条路径通往同一个归一化存储：

- **批量导入**——`sessions discover/import` 按需扫描转录根目录并一次性写入。本页下文记录该流程。
- **实时监控**——监督守护进程（`tesserae engine`）运行一个 `SessionTailer`，它监视*本项目自己的* Claude Code 和 Codex 转录，并在新 turn 落盘时摄取它们。每个 tick 会定位到持久化的按文件字节偏移量、只读取新字节，并把完整的 turn 存入 SQLite 的 `HarnessSessionsDB`（`.tesserae/sqlite.db`），**然后**才入队一次去抖的重编译，因此编译总是读取一致的状态。tailer 的范围限定于项目自己的会话（Claude 的 `projects/<slug>/*.jsonl`；Codex 按 cwd 过滤），重启后从存储的偏移量恢复而不重放 turn。

运行实时循环：

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` 在进程内一次性运行同样的 ingest → compile → project 流水线，而不启动长驻的监视器（传入 `--no-sessions` 可跳过 harness 会话发现扫描）。

## 隐私模型

两条摄取路径都是显式的：实时 tailer 只在你保持 `tesserae engine` 存活时运行，批量发现只在带 `--import` 时写入。普通的 `tesserae compile` 或 `tesserae export site` 会读取 `.tesserae/harness_sessions/` 中已归一化的会话和 `.tesserae/sqlite.db` 中的实时记录，但绝不会自行意外抓取私有的 harness 转录目录。

导入的会话记录是本地项目产物。在发布公开站点之前请先审查它们，尤其当你的转录可能包含密钥、私有路径、客户数据或未发布的代码时。

轮次文本会被复制进节点名称与描述，而它们会被序列化进 `graph.json` 及其所有
投影——因此**主目录在进入时就被遮蔽**。`/Users/<name>` 和 `/home/<name>` 永远不会
抵达图谱。这一点重要，是因为路径是几乎每份转录里都会出现、却没人有意写进去的那
一类个人信息。

## 一个会话轮次会变成什么

会话中每发生一次*有意义的*转变——一次工具调用，或一次实质性的助手动作，而非闲
聊——不依赖 LLM 的 `Event` 流程都会铸造一个节点，记录 `{turn_id, actor, action,
简短的状态变化}`，并用 `precedes` 边把相邻事件串起来，于是一次会话的动态状态可以
按顺序重放。这个流程从不调用模型，遇到坏输入也从不抛异常，并且逐字节幂等：铸造
出的每个 id、正文和 `first_seen_at` 都由内容派生，重跑一次会得到完全相同的节点
与边。

**工具结果也是一个轮次。** 退出码和错误标志能挺过摄取并落到 `Event` 节点上，于
是图谱能把*失败*的命令与仅仅被运行过的命令区分开。在此之前，一个读自己历史的智
能体只知道自己跑过 `pytest`，却不知道测试是否通过——而这正是日志与记忆之间的差
别。

### `recovers` 边

从同一会话中两个**被观测到**的结果出发，Tesserae 推导出其词汇表里唯一的因果边：
一次报告失败的工具调用，以及其后一次报告成功的调用——同一工具、同一程序家族、同
一工作目录、同一操作对象，且期间没有观测到该操作对象上的任何成功。成功的
`Event` 是源，失败的那个是目标；两个轮次 id 都写进证据，`metadata["basis"]` 则
列出这两次调用必须一致的每一个维度。

`CAUSAL_EDGE_TYPES` 恰好只有一个成员，这是刻意的。对四个领先的智能体记忆系统的
调研发现，没有任何一个真正推导出因果边：两个把最强的链接建立在共现之上，一个不
加验证地采信 LLM 给出的开放关系标签，还有一个根本没有边。这种克制要避免的失败模
式，就是发布一条实际上是 `happened_near` 的 `caused_by`——在图谱里两者无法区分，
而错的那个会被当成证据来读。

锚点是**操作对象**，不是命令，因为命令会在无关紧要的地方变化（参数、顺序），而
被作用的那个东西才是重试真正在重试的目标。

## 发现并导入本地会话

在项目根目录下：

```bash
tesserae sessions discover --import
```

发现过程会扫描属于当前项目工作目录的本地 Claude Code 和 Codex 转录根目录。使用 `--root` 扫描特定的配置目录，重复 `--harness` 来限定发现范围：

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

不带 `--import` 时，发现过程只打印找到的内容，而不写入归一化的会话记录。

## 直接导入归一化 JSON

如果另一个工具已经产出了归一化的 `HarnessSession` JSON，可以导入单个文件或文件列表：

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

每个输入可以包含一个会话对象或一个会话对象列表。

## 存储的写入方式

`.tesserae/harness_sessions/` 中的每条记录都携带一个**生产者(`producer`)**——写入它的导入工具。`sessions discover --import` 会戳上 `tesserae:discover`；`sessions import <path>` 会戳上 `tesserae:import`。**写入者只能触碰自己生成的记录**：它只删除自己的，也不会覆盖同一会话中另一个生产者的记录——传入的写入会被跳过并报告为 `Left alone (written by another producer)`。

这条规则存在是因为来源(provenance)是唯一能真正区分导入工具的东西。其中两个通常描述*同一*会话：Tesserae 的本地扫描从 `~/.claude` 下的转录生成一条普通记录，而一个编排器导出同一会话时携带的是只有它知道的代理身份。两者都从会话 id 推导出同样的文件名，所以它们会碰撞。转录的位置和 harness 名字都分不出它们——这就是为什么之前针对 [#104](https://github.com/ca1773130n/Tesserae/issues/104) 的根作用域修复没有起作用，以及为什么 0.28.6 仍然以两种方式丢失这样的记录：扫描不再找到转录时被删除，或在找到时被悄悄覆盖。

如果你从自己的工具向这个存储写入，使用 `tesserae sessions import <file>` 就能从那时起保护你的记录。不需要别的。

作用域作为第二道关卡进一步收缩：只有当记录的转录也存在于本次运行扫描的根目录下*且*其 harness 是本次运行扫描过的时，该记录才会被删除。因此 `--harness codex` 即使 `~/.claude` 被扫描过，也会独自留下 claude-code 记录。

### 多台机器共用同一个项目目录

每条记录还携带一个**主机(`host`)**——采集它的那台机器。**一台主机只删除自己采集的记录。**

这与 `producer` 是真正不同的一个维度，上面那两道关卡替代不了它。当多台服务器各自运行 Claude Code 并共享一块磁盘时，它们也共享 `.tesserae`——但每台只看得见自己本地的转录。每台主机的扫描都戳上同样的 `tesserae:discover`，每台主机的 `~/.claude` 也解析成同一个路径字符串，于是在一台从未见过该转录的机器上，生产者关卡和作用域关卡*双双放行*。它随后删掉另一台机器的记录，并报告成功。现在记录会携带采集它的主机，删除则要求主机匹配。

主机 id 存放在 `~/.tesserae/host_id`——按机器存放，不在共享的项目目录里——并在首次使用时生成一次。用 `TESSERAE_HOST_ID` 覆盖它。这里刻意用一个持久化的 id 而不是主机名：由同一个镜像构建出来的机群会重复使用主机名，而主机名冲突会悄无声息地把一台机器的记录交给另一台。

**写入**路径则刻意对主机无感。只有当两台主机都能看见同一份转录时，它们才可能写入同一个会话，所以写入是幂等的，只是把归属重新戳给最后一次证明自己能看见它的那台主机。反过来按主机给写入设卡，只会把一台已退役机器的记录永远冻结，且没有任何办法收回。

在这个字段出现之前写入的记录不带主机。它们在这个维度上无主，能在任何主机的删除中存活，直到 `--adopt-unowned` 认领它们——这与 `producer` 已经在用的规则相同；而它在这里之所以要紧，是因为 0.28.7 写入的*每一条*记录都带生产者、不带主机，于是生产者关卡会弃权，而没有别的东西能保护它们。

值得了解的三点行为：

- **0.28.7 之前写入的记录不携带生产者。** 它们无主，所以没有导入工具会删除或覆盖它们——安全，但 discovery 也不会刷新它们。`sessions discover --import --adopt-unowned` 为 discovery 声明它们。如果 Tesserae 自己的扫描是唯一写入这个存储的东西，就运行一次；如果另一个工具也在这里写入，**不要**运行，因为这会把你的记录交给 discovery。
- 空发现永远不会删除。一次没有找到任何东西的扫描——错误的 `HOME`、断开的 harness 根目录——会进行合并而不是清空。
- 删除或保留记录的发现过程会在导入计数旁边同时打印两个计数，因此存储不会在只报告增长的一行中改变大小。

## 列出已导入的会话

```bash
tesserae sessions list
```

会话存储在以下位置：

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

实时监控的会话还会被记录在 SQLite 的 `HarnessSessionsDB`（`.tesserae/sqlite.db`）中，它同时持久化 tailer 用于恢复的按文件读取偏移量。`tesserae sessions list` 报告合并后的视图。

## 构建静态会话页面

导入会话后，重建站点：

```bash
tesserae export site
```

站点会发出：

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

生成的站点从全局导航栏、主页 Browse 卡片、搜索条目以及每个会话详情页的面包屑路径链接到 Sessions。

## 快速转录搜索（memex）

当你用 `tesserae serve` 服务站点时，**sessions 仪表盘**会获得一个覆盖所有已索引 Claude/Codex 转录的全文搜索框，由 [`nicosuave/memex`](https://github.com/nicosuave/memex)（BM25）支持。结果显示 `project · role · date · score` 以及匹配的片段。

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

它是**可选且优雅降级的**：没有 `memex` 二进制（或索引）时，搜索框会显示一条清晰、可行动的消息，仪表盘的其余部分不受影响。搜索端点（`GET /api/transcript-search`）仅限同源/回环调用方，因此你访问的网页无法探测你的本地历史。

## 会话详情页布局

会话详情页使用共享的静态站点外壳，而不是独立的转录转储。它们包括：

- hero 与统计条；
- 高层摘要；
- 时间线与大小元数据；
- 存在时显示的决策、文件、命令、工具和错误；
- 折叠的 subagent 树；
- 逐轮的 user/assistant 对话；
- 附在前一条 assistant 轮次下方的折叠 tool-use 块；
- 链接到 `#turn-N` 锚点的左侧对话导航栏。

对话 markdown 通过站点 markdown 渲染器渲染。行内代码、显式命令/标签标记、路径、文件名和话题标签等语义表面会被装饰为紧凑的 chip；随意的大写名词不会被自动 chip 化。

当前的转录排版：

| 表面 | 选择器 | 大小 |
|---|---|---|
| 对话 markdown 正文 | `.session-turn-text`，正文子元素 | `8px` |
| 通用对话代码围栏 | `.session-turn-text pre` | `10px` |
| Bash/shell 围栏代码内容 | `.session-code-block code.language-bash`、`.language-sh`、`.language-shell`、`.language-zsh` | `11px` |
| 工具 details/summary | `.session-tool-details`、`.session-tool-details > summary` | `10px` |
| Tool-use 头部 | `.session-tool-use-header` | `8px` |
| 工具载荷文本 | `.session-tool-use-text` | `6px` |

## 包含会话的发布检查清单

在部署包含会话的公开站点之前：

1. 运行 `tesserae sessions list` 并确认数量符合预期。
2. 检查 `.tesserae/harness_sessions/` 中是否有敏感内容。
3. 用 `tesserae export site` 重建。
4. 在本地打开 `sessions/index.html` 和至少一个会话详情页。
5. 确认工具块默认折叠，且原始工具载荷可以接受发布。
6. 在源码树已提交后用 `tesserae export site --deploy` 部署。
