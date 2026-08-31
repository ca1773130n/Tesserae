# 调优参考 — 环境变量

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 从环境读取的每个旋钮、其默认值及实际何时更改它。
这里的任何内容都不是必需的：默认值的选择使得普通的 `tesserae compile` 能够正确运行。

LLM 后端设置也位于 `.tesserae/config.json` 和
`~/.tesserae/config.json` 中；下面的环境变量优先于两者运行，而 [LLM 后端](#llm-后端) 详述了完整顺序一次。

---

## 花钱的钩子

Claude Code 插件附带可以后台编译的钩子。任何花钱的**默认关闭**：

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # 选择性加入自动重新编译
```

受限：`posttooluse-edit.sh`（在每次 Edit/Write 上触发）和 `session-end.sh`。不受限，因为它们花费零：`session-start.sh` 运行 `tesserae code sync`，这是确定性的，`pretooluse-compile.sh` 仅拦截您自己输入的 `tesserae compile`。

这个默认值之所以存在是因为替代方案已经过测量。位于 `~/.tesserae` 的知识库使 `$HOME` 看起来像一个项目根目录，钩子解析器从工作目录向上走到它找到的第一个 `.tesserae/` — 因此任何在已注册项目外启动的会话都会解析为 `$HOME` 并编译整个主目录：15k 个文件，一个 795 MB 的图，**~10 小时的 LLM 花费**，来自一个比启动它的会话存活时间更长的分离进程。

`resolve_project_root()` 现在通过任一路径拒绝 `$HOME`，并返回空值而不是回退到工作目录，因此调用者不执行任何操作而不是猜测。后台运行模型工作的钩子应该被刻意打开，而不是在账单到达后关闭。

---

## 提取

### `TESSERAE_EXTRACT_TIMEOUT`

**默认 `1800`（秒），每次尝试。** 限制每个 codex/claude 提取
调用，以便 wedged CLI 子进程无法挂起编译。

这是真实发生的：一次编译在 0% CPU 下观察到 **5 h 43 m**
后面跟着一个空闲 **4 h 6 m** 的 `codex exec` 子进程，一直持有 `.tesserae/compile.lock`。
它已经在内存中构建了 32 个社区摘要，但从未持久化它们。

每次尝试，不是每个文档——超时后客户端轮换到下一个
`CODEX_HOME` / claude 配置目录，所以一个文档的最坏情况是
`timeout × 配置的配置文件`。

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # 为非常大的文档提供更多空间
export TESSERAE_EXTRACT_TIMEOUT=0      # 无截断——运行至完成
```

设置但无法使用的值（`10m`、`600s`、负数、`inf`）会在 stderr 上警告
并保持默认值。拼写错误不得以静默方式禁用安全阀。

### `TESSERAE_EXTRACT_CONCURRENCY`

**默认 `4`。** 并行提取的文档。每个都是一个阻塞 CLI
子进程，大约需要一分钟，所以顺序循环使得挂钟时间
是每个模型往返时间的总和——对 161 个文档测量为 ~2 h 40 m。

上限是提供商账户的速率限制，而不是您的机器，这就是为什么
默认值很低。设置 `1` 以实现严格的顺序行为。

并发永远不会改变输出：工作列表按路径顺序固定，
结果按索引收集，因此并行运行与
顺序运行字节相同。

### `TESSERAE_LLM_CACHE`

**默认打开。** CLI 提供商响应的内容寻址缓存，位于
`~/.tesserae/llm_cache` 下，由实际发送的提示的摘要以及模型和
推理努力进行键控——所以不同的问题会重新询问，切换模型会重新询问而不是提供先前
模型的答案。只存储可解析的响应，因此一个不良生成
无法成为永久的。

旧版条目按设计无法访问：键过去是
由调用阶段提供的标签而不是提示的摘要，因此
不相关的问题可能共享一个条目。没有任何东西迁移它们——该目录
可以安全删除，编译将重新填充它。

```sh
export TESSERAE_LLM_CACHE=0   # 始终重新询问
```

### `TESSERAE_LLM_CHUNK_CHARS`

当文档太大而无法进行一次调用时，每个块的字符数。除非您
在达到上下文限制，否则保持未设置。

---

## LLM 后端

哪个后端回答、通过哪条线路、用哪个凭证。下面的每个键都按同样的方式且仅按这一方式解析：

**`TESSERAE_*` 环境变量 → 项目 `.tesserae/config.json` → `~/.tesserae/config.json` → 内置默认值。**

| 配置键 | 环境变量 | 默认值 | 备注 |
|---|---|---|---|
| `llm_provider` | `TESSERAE_LLM_PROVIDER` | `claude` | `claude`、`codex`、`anthropic`、`openai`、`custom` 之一。其他任何东西都会按名称被拒绝——一个拼写错误过去会被无声地视为 `claude`，所以说 `openrouter` 的配置会针对 Anthropic 运行并报告一个你从未选择的模型的错误 |
| `llm_api_style` | `TESSERAE_LLM_API_STYLE` | `llm_provider` 为 `openai` 时为 `openai`，否则为 `anthropic` | 线路协议，这是与后端不同的问题。`anthropic` 通过 Anthropic SDK 发布到 `{base_url}/v1/messages`；`openai` 发布到 `{base_url}/chat/completions` |
| `llm_model` | `TESSERAE_LLM_MODEL` | `sonnet`（claude CLI）、`gpt-5.6-luna`（codex CLI）、`claude-sonnet-4-6`（anthropic 线路）、`gpt-4o-mini`（openai 线路） | 在两个 CLI 后端上按提供商作用域，所以 claude 形状的模型永远不会落在 codex 路径上。配置的端点提供商即使在提供商和模型在不同配置层中设置时也会保留其模型 |
| `llm_base_url` | `TESSERAE_LLM_BASE_URL`，然后 `ANTHROPIC_BASE_URL` | `https://api.anthropic.com`（anthropic 线路）、`https://api.openai.com/v1`（openai 线路） | 端点，被修剪为每条线路追加的内容——参见 [自定义端点](#自定义端点) |
| `llm_api_key` | `TESSERAE_LLM_API_KEY`，然后 `ANTHROPIC_API_KEY` | — | API 密钥凭证：anthropic 线路上的 `X-Api-Key`，openai 线路上的 `Authorization: Bearer` |
| `llm_auth_token` | `TESSERAE_LLM_AUTH_TOKEN`，然后 `ANTHROPIC_AUTH_TOKEN` | — | Bearer 凭证，两条线路上都是 `Authorization: Bearer`。设置这个**或** `llm_api_key`：在 anthropic 线路上令牌被传递给 SDK 作为 `auth_token=` 而不设置 api 密钥，所以两者永不冲突 |
| `llm_allow_fallback` | `TESSERAE_LLM_ALLOW_FALLBACK` | 关闭 | 允许配置的端点提供商转向另一个后端而不是失败——参见 [端点提供商是一份契约](#端点提供商是一份契约)。环境变量的任何非空值都会打开它 |
| `llm_claude_config_dirs` | `TESSERAE_CLAUDE_CONFIG_DIRS` | CLI 自身的默认值 | Claude 配置目录（按轮换顺序），在环境变量中以 `os.pathsep` 分隔——即重复 `--claude-config-dir` 的环境变量通道。只有*显式配置*的列表才具有权威性；环境中的 `CLAUDE_CONFIG_DIR` 刻意不具权威性，因为固定到它会让多账号轮换塌缩为单账号 |
| `llm_codex_homes` | `TESSERAE_CODEX_HOMES` | CLI 自身的默认值 | Codex homes，形状和推理同上。较旧的单数 `llm_codex_home` 仍然有效，意思是一个单一 home 的列表 |
| `llm_codex_reasoning_effort` | `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | 结构化提取不需要你可能为交互工作设置的 `xhigh`——`xhigh` 使多文档编译多倍变慢 |

`ANTHROPIC_*` 名称仍然有效，在 Tesserae 自有名称下面一级：
它们是环境的——任何 Claude Code 会话都会导出它们——所以它们不能胜过为 Tesserae 特别设置的值，但它们仍然胜过两个配置文件。

`tesserae config llm` 写入机器级文件；对于单个项目，在其 `.tesserae/config.json` 中放入相同的
`llm_*` 键。写入任一文件的凭证以**明文**存储，所以那两个请优先使用 `TESSERAE_LLM_API_KEY` /
`TESSERAE_LLM_AUTH_TOKEN`。

### 自定义端点

`llm_provider` 说明哪个后端；`llm_api_style` 说明使用哪种 HTTP 方言。
将两者分开是使非 Anthropic 端点可达的原因：`custom` 过去意味着 Anthropic 线路，
所以 OpenAI 兼容的服务器无处可配置。如果保持未设置，对于 `custom`，`llm_api_style`
仍然解析为 `anthropic`——在此之前配置的端点保持完全相同的行为。

**一个 OpenAI 兼容端点**——vLLM、LiteLLM、OpenRouter、Together、Ollama、
LM Studio：

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=openai
export TESSERAE_LLM_BASE_URL=http://localhost:8000/v1
export TESSERAE_LLM_MODEL=qwen2.5-coder-32b-instruct
export TESSERAE_LLM_AUTH_TOKEN=sk-...   # 或 TESSERAE_LLM_API_KEY——此处线路相同
tesserae config status
```

请求是 `POST {base_url}/chat/completions`。此线路是标准库 `urllib`，
所以它不需要额外安装，无密钥本地服务器根本不需要凭证——两个都保持未设置它仍然构建。

**一个 Anthropic 兼容端点**——一个网关说 Messages API：

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=anthropic
export TESSERAE_LLM_BASE_URL=https://gateway.internal.example   # no /v1
export TESSERAE_LLM_MODEL=claude-sonnet-4-6
export TESSERAE_LLM_API_KEY=...         # X-Api-Key；TESSERAE_LLM_AUTH_TOKEN 用于 bearer 网关
tesserae config status
```

请求通过 Anthropic SDK 到 `POST {base_url}/v1/messages`，它在此线路上的
`llm_provider: custom` 和 `llm_provider: anthropic` 都需要：

```bash
pip install "tesserae[synthesis-llm]"
```

**`/v1` 不是装饰。** SDK 自身追加 `/v1/messages`，所以每个网关自述中显示的
`https://host/v1` 产生 `/v1/v1/messages`——一个 404，读起来像是一个你从未选择的模型的错误。
现在在 anthropic 线路上自动去掉一个尾随 `/v1`，在 openai 线路上确保。仅那个尾随段
被触及，无论前面是什么——一个真正服务 `/anthropic/v1` 的代理也会失去那个 `/v1`——所以
改写在 INFO 处记录而不是无声完成，日志行就是你找到实际使用的 URL 的地方。

### 端点提供商是一份契约

`anthropic`、`openai` 和 `custom` 携带一个你选择的端点——一个 URL、一个模型名称、一个凭证。
当其中一个被配置时它单独被构建，失败会向上抬起 `LLMProviderConfigError` 命名提供商、线路、基础
URL、模型和哪种凭证类型被解析。

它过去是一个偏好：无法构建的自定义端点会转向 Claude CLI，它随后以 `--model sonnet`
针对你自己的基础 URL 生成并报告一个你从未配置的不支持模型，没有任何东西命名真实原因。
设置 `llm_allow_fallback: true` 来重新获得该链接。

两个 OAuth CLI 提供商仍然链接——彼此之间，和它们后面的 API 客户端。`claude` 和 `codex`
不接受基础 URL，它们的模型按提供商作用域，所以两者都不能携带一个你选择的端点到一个你没有
命名的后端，这是契约存在的唯一目的。

### 查看实际生效的内容

```bash
tesserae config status                 # 解析的后端 + 一个活性探针
tesserae config status --project .     # 因为此项目的 config.json 看到它
tesserae config status --no-ping       # 跳过探针，花费零
```

它打印提供商、线路、模型、基础 URL 和解析的凭证的*种类*——`api_key`、`auth_token` 或无，
永不秘密——各自标记赢得它的层，然后是回答的客户端的类和身份。该客户端是从实际运行使用的相同设置字典构建的，
探针永不缓存，所以一条通过的线意味着后端现在刚刚回答而不是在过去的某一点。

当调用确实失败时，失败会被分类而不是展平：`401` 和 `403` 被报告为认证，`404`——和一个命名
模型的 `400`——作为端点，各自命名产生它的端点。在此之前，一个错误配置的 URL 与根本没有 LLM 装置区分不开。

---

## 编译通道

| 变量 | 默认值 | 控制内容 |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **打开** | GraphRAG 风格的摘要通道。≥ 5 个成员的集群每个 LLM 调用 1 次，按成员资格摘要缓存。`false`/`0`/`no`/`off` 禁用 |
| `TESSERAE_ENABLE_LLM_PASSES` | 关闭 | 提取之外的可选 LLM 增强通道 |
| `TESSERAE_AGENT_DISTILL` | 关闭 | 每个代理 L1 专业知识工件（`tesserae distill`） |
| `TESSERAE_RUNBOOK_DISTILLATION` | 关闭 | Runbook/Gotcha 蒸馏内存节点 |
| `TESSERAE_SESSION_EVENT_PASS` | **打开** | 从会话记录生成的逐轮 `Event` 节点。不调用 LLM，字节级确定性，但每个有效轮次生成一个节点——语料库较长时规模可观。`false`/`0`/`no`/`off` 可禁用 |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | 打开 | 将会话洞察链接到代码符号 |
| `TESSERAE_SUPERSEDE_PASS` | 打开 | 修订声明之间的 `superseded_by` 边 |
| `TESSERAE_PROMPT_SIGNATURES` | 关闭 | 记录提示签名以进行漂移检测 |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | 在放弃前等待 `.tesserae/compile.lock` 的秒数 |

**关于社区摘要：** 编译通道急切地覆盖最粗粒度；
`graph_map` 另外在您第一次下降到冷作用域时懒惰地具体化一个摘要，
按级别缓存。关闭通道是合法的成本策略——您只为
实际访问的分支付费——但有一个警告：
**联合下降永远不会懒惰地具体化。** 兄弟项目的卡片只能
从其在图内摘要或已热缓存中命名，所以跨项目导航的项目
需要打开急切通道。

---

## 查询和综合

| 变量 | 默认值 | 备注 |
|---|---|---|
| `TESSERAE_QUERY_LLM` | 关闭 | `tesserae query` 的 LLM 计划程序 |
| `TESSERAE_QUERY_DRY_RUN` | 关闭 | 在不调用模型的情况下进行计划 |
| `TESSERAE_SYNTHESIS_LLM` | 关闭 | `tesserae ask` 中的散文综合 |
| `TESSERAE_SYNTHESIS_MODEL` | — | 覆盖综合模型 |
| `TESSERAE_SYNTHESIS_WORKERS` | — | 并行综合工作者 |
| `TESSERAE_SYNTHESIS_DRY_RUN` | 关闭 | 跳过模型，运行管道 |
| `TESSERAE_VERIFY_BAND` | 开启 | 在实测的 0.30–0.70 区间内用模型重新判定 `ask` 不确定的复核标记；`lo-hi` 覆盖它。`off` 只保留不花费令牌也不走网络的免费标记 |
| `TESSERAE_EMBEDDING_PREFER` | auto | 稠密通道编码器：`model2vec`（随包附带，静态，无需 torch）、`st`（经过训练的 sentence-transformers 模型）、`openai`、`hash`。未设置时按顺序选用第一个已安装的 |
| `TESSERAE_ST_MODEL` | `BAAI/bge-base-en-v1.5` | `st` 加载的 sentence-transformers 模型；任意 Hugging Face 名称 |

### `TESSERAE_VERIFY_BAND`

每个 `ask` 答案都带有不花钱的逐句复核标记。它不如直接问模型准确 —— 在 755 个留出句
子上是 0.870 对 0.926 —— 而且差距几乎全部来自对忠实改写的误报，这类句子与其来源共享
的词汇很少。

两者出错的句子并不相同，所以只在免费检查没有把握的地方付钱给模型，就能以一小部分成
本收回准确度。把覆盖率 0.30–0.70 交给模型，在 42% 的调用上得到 0.932：与逐句询问无法
区分（McNemar p=0.52），花费却只有 42%。

```bash
export TESSERAE_VERIFY_BAND=on          # 实测的 0.30-0.70 区间
export TESSERAE_VERIFY_BAND=0.40-0.60   # 更窄：22% 的调用，0.914
```

在 `ask` 内部默认开启：模型客户端已在手中，答案本身已经花费了令牌，所以准确标记的额
外成本很小。没有任何免模型的检查变体能独自缩小差距 —— 词干提取、字符 n-gram、稀有度加
权和本地嵌入都逐一测量过，没有一个胜过朴素覆盖率 —— 因此默认是级联，而不是更聪明的免
费检查。库函数 `check_against_evidence` 保持不变，仍然不花钱。信封会报告
`adjudicated`：级联未运行时为 `null`，运行时为计数。无法作答的模型会让免费判定保持原样
—— 一次失败的调用永远不能把被标记的句子变干净。

### `TESSERAE_EMBEDDING_PREFER`

`hybrid_search` 的稠密通道用 `active_embedding_backend` 最先找到的编码器做嵌入：
随包附带的 `model2vec` 静态模型（8 MB，无需 torch，离线），然后是
sentence-transformers，然后是哈希桩。静态模型让 `pip install tesserae`
保持小巧，在小语料上它的代价小到测不出来。在大语料上它就是瓶颈：在 148
篇论文上，按不同文档计的召回率，随包模型为 0.754 @10 / 0.914 @50，同一融合换成
`BAAI/bge-base-en-v1.5` 则为 0.791 / 0.962 —— 单独看稠密通道，从 0.473 升到
0.680 @10。对同样的分块建一个普通向量库，用 nomic-embed-text 得分是 0.784 /
0.942，用同一个 bge-base 是 0.775 / 0.944；图相对它的优势在 57 个问题上处于噪声范围内
（配对符号检验 p=1.0 @10，0.51 @50）。训练过的编码器让图与它持平，而不是落后。

```bash
uv pip install sentence-transformers          # torch, ~2 GB with the model
export TESSERAE_EMBEDDING_PREFER=st
export TESSERAE_ST_MODEL=BAAI/bge-base-en-v1.5   # the default; any Hugging Face name
```

`auto` 仍然优先选静态模型，因此从未设置该变量的安装行为与以前完全一致。
偏好只在首次解析后端时读取一次；指不到任何后端的值会被报告并忽略，而不是
悄悄落到哈希桩。训练过的编码器如果不缓存向量，每次查询都会重新嵌入全部节点 ——
`compile_context` 和 MCP 服务器已经传入项目的 `VectorCache`，它以后端为键，
所以切换模型绝不会提供过期向量。

---

## 路径和基础设施

| 变量 | 默认值 | 备注 |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | 项目注册表位置。**每一个**命令都遵守它——在 0.28.7 之前只有引擎的舰队模式会读它，所以在别处设置它会悄无声息地毫无效果，命令仍然使用真正的注册表 |
| `TESSERAE_HOST_ID` | 首次使用时生成到 `~/.tesserae/host_id` | 本机的身份标识。见[在一个项目上运行多台机器](#在一个项目上运行多台机器) |
| `TESSERAE_DISCOVERY_CACHE` | — | 会话发现缓存 |
| `TESSERAE_ARXIV_CACHE` | — | arXiv 元数据缓存 |
| `TESSERAE_NO_FEDERATION_CACHE` | 关闭 | 禁用联合图 LRU |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | 关闭 | 发出组合跨项目图 |
| `TESSERAE_FLEET_PIDFILE` | — | 引擎舰队 pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Web clipper 的共享密钥 |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | 关闭 | 在编译时应用 `.tesserae/schema-drift-proposals.json` 中的 **approved** 记录（确定性、无 LLM）。使用 `tesserae schema-drift` 编写提案；批准一个意味着首先编辑 `ResearchNodeType`，然后设置 `"approved": true` — 无法解析的名称不会重新键入任何内容。 |

---

## 谁读了这张图谱

| 变量 | 默认 | 说明 |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **关闭** | 记录移动访问计数的读取——`{tool, actor, node_ids, at, tesserae_version}`——在 `.tesserae/sqlite.db` 的 `read_audit` 表中，通过 `read_audit` 工具读回，附上按 actor 的计数。每当访问计数被碰撞的地方都写一行，因此行计数跟随表面而非调用：浮现节点列表的工具（`search_nodes`、`node_context`、`compile_context`、`graph_map`、`graph_ppr`、`ask` / `query`、`drill_down`、`find_session_findings`）写**每个调用一行**命名它计数的每个节点，而 `fresh_insights` 在自己的循环内碰撞因此写**每个所浮现节点一行**。不浮现任何东西的调用不写任何东西，而不读任何节点的工具——`schema`、`graph_summary`——永远不抵达审计，因为一行若不命名节点就无法解释任何访问计数。默认关闭是因为跨越每一个读取面的常开审计会把每一次读都变成一次写；这道开关位于打开存储之前，因为创建这张表本身就是一次写。它的任何内容都不会进入 `graph.json` |
| `TESSERAE_ACTOR` | — | 当调用不带 agent view 时，将一次读归属于谁。actor 是调用解析的 `agent` 参数，否则就是这个；未设置则将读记录为匿名而不是虚拟一个名字 |

关掉 `TESSERAE_READ_AUDIT` 会停止记录，而不擦除已记录的东西，无需重启服务器即可生效。审计的*目的*是[由于不用而遗忘](agent-memory.zh.md#遗忘--永不删除)：访问计数驱动了什么被吸收或降级，没有 actor 的情况下，一个闹腾 agent 轮询节点与一个人读一次是同样的输入。

---

## 在一个项目上运行多台机器

本节针对的形态：多台服务器各自运行一个编码 agent，各自拥有自己本地的会话转录，并且共享一块磁盘——因此它们看到的是同一个项目目录、同一个 `.tesserae/`。

**把编译交给一台主机，其余的只做采集。**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` 把那台机器本地的转录 tail 进共享的会话存储，并且从不去拿项目的编译锁。它是把争用消除掉，而不是去仲裁争用，这正是它胜过调超时的原因。

**当你确实想排队而不是失败时**，传入 `--wait`：

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

不加它时，一次发现锁已被持有的编译会以 2 退出——这对钩子是正确的，对人却令人抓狂。`--wait` 是一个显式标志，而不是从 stdout 是否为终端推断出来的东西，因为同一条命令在 `tee` 下、在 tmux 捕获里、在 CI 中都不能改变行为。`TESSERAE_COMPILE_LOCK_WAIT=<seconds>` 为整棵进程树做同样的事。

**用一次调用让每个项目保持新鲜：**

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

一个项目失败不会让其余的停下。只要有失败就退出 `2`，只要有被另一次运行锁住的就退出 `1`，全部跑完则退出 `0`。`--jobs` 默认是 1，因为编译是 LLM 重操作，把它调高就是在并行消耗配额。

### 是什么让这件事安全

每台机器各自的状态过去存放在一个共享的名字下，并被每台主机读取。下面每一项现在都按主机 id 分区：

| 状态 | 位置 | 为什么必须按主机分开 |
|---|---|---|
| 会话记录 | `.tesserae/harness_sessions/` | 一台主机只删除自己采集的记录。否则主机 B 会删掉主机 A 的会话并报告成功——每台主机的扫描都戳上同样的生产者，它们的 `~/.claude` 路径也解析得一模一样，没有别的东西能区分它们 |
| 引擎 pidfile | `.tesserae/daemon.<host>.pid` | 存活判断是对**本地**进程表执行 `os.kill(pid, 0)`；另一台机器写下的 pid 会被拿去和一个毫不相干的本地进程比对 |
| Codex 扫描下界 | `.tesserae/harness_sessions.db` | 一条共享的水位线意味着最后运行的那台主机会把它推过另一台还没读过的转录——那些转录根本就没被导入过 |

主机 id 在 `~/.tesserae/host_id` 中生成一次（按机器，**不在**共享的项目目录里），并可以用 `TESSERAE_HOST_ID` 固定。之所以是持久化的 id 而不是主机名，是因为由同一个镜像构建出来的机群会重复使用主机名，而一次冲突会把一台机器的记录交给另一台。

### 你应该亲自测试的那个假设

以上一切都假设 `flock(2)` 被承载 `.tesserae/` 的那个文件系统**强制执行**。在 NFS 和 SMB 上这取决于配置，而没有可用的 lock daemon 时，`flock` 可能静默退化为空操作——那时两台主机会同时编译同一个项目，各自都以为自己独占持有那把锁。

`tesserae doctor` 会在项目位于网络文件系统上时告警，但单台主机**无法**证明跨主机的强制生效。请在真实硬件上直接测试：在主机 A 上持有一把锁，确认主机 B 被拒绝。

---

## 恢复降级的语料库

当文档提取失败时，它由确定性基线提供服务和
在 `.tesserae/manifest.json` 中**标记**。没有标记，它无法
与干净提取区分，所以 `--changed-only` 会永远跳过它，
降级将是永久的，直到文件自身内容改变。

```sh
tesserae compile --changed-only --retry-fallbacks
```

仅重新尝试标记的文档；干净的保持跳过。

### 在后处理阶段之前编译的图

两项修复改变了已编译图的形态，却不改变模型提取的内容：每个文档一个锚点而不是每个分块一
个（按分块编译的论文带有 9.4 个），以及每个实体名一个节点而不是每种拼写和类型一个。二者
都在 `compile` 内部运行，因此磁盘上已有的图在重新编译之前两者都没有。`graph-repair` 把
同样的规则应用到图的字节上 —— 不用模型，不走网络，几秒钟 —— 修复后的图与重新编译的图一
致。

```sh
tesserae graph-repair --dry-run     # 只报告将改变什么，不写入任何内容
tesserae graph-repair               # 就地重写 .tesserae/graph.json
```

站点和仓库是投影，这里不重建；如果你对外提供站点，之后运行 `export site`。

## 检查层次结构

```sh
tesserae graph-map                          # 根地图
tesserae graph-map --scope <scope_id>       # 下降
tesserae graph-map --scope '<alias>::'      # 兄弟注册项目
```

每张卡从层次结构 sidecar 报告 `size` 和 `leaf_member_count`，
加上 `live_member_count`——*当前*图实际携带的成员数。
一个 `0` 那里意味着作用域是死的（sidecar/图 skew）：跳过它
而不是下降。

## 代理写入图表

`graph_write` (MCP) 采用模式验证的类型化节点和边，带有强制性出处，因此代理将发现记录为*结构*，而不是提取器必须猜测类型的散文。

它拒绝而不是强制：无类型边、受控词汇外的节点或边类型、悬空端点和缺少出处的写入都被拒绝。重复写入是幂等的。代理写入的节点可以存活完整重新编译、删除的 `graph.json`、`--limit` 和完整语料库删除。

## 对照图验证声明

`verify_claim` (MCP) 回答图是否许可三元组。它接受 `(subject, predicate, object)` — **没有自然语言参数**，设计上出于这个原因，因为解析器使前一个版本对它所支持的声明的否定回答 SUPPORTED。

判定是图字节的纯函数：没有 LLM、没有嵌入、决策路径上没有模糊匹配。

| 判定 | 含义 |
|---|---|
| `SUPPORTED` | 边存在、自带证据、该文本已重新接地至源文件 |
| `PRESENT_UNEVIDENCED` | 边存在但没有文件支持 |
| `CONTRADICTED` | 同一两个端点之间有文件支持的 `contradicts_claim` |
| `DISPUTED_UNEVIDENCED` | 主张分歧，无证据 |
| `CONFLICTING` | 两者都有文件支持 — 工具拒绝裁定 |
| `ABSENT` | 此图不声称三元组。不是驳斥 |
| `NOT_RESOLVABLE` | 端点或谓词无法精确解析 |

它故意不会做两件事。它从不将 `supersedes` 视为驳斥 — 该关系说一个*节点*被替换，而不是说三元组为假。代理写入只能*削弱*出处类，永远不能升级，所以代理声称的任何东西都不能呈现为文件接地。

值得在阅读结果时了解：在一个有 15,284 条边的真实图上，约 40% 的 `SUPPORTED` 判定是同义反复 — `evidenced_by` 边其引用的跨度是边自身的目标。真的，但无信息。

## 路由问题

`tesserae ask` 根据问题形状选择检索路径：单实体查询去廉价后端，多跳 / "什么变了" / "为什么" / 语料库范围问题去图。这个划分编码的是**假设，而非测量**：我们预期遍历在多跳、时间和综合问题上能收回成本，在简单事实查询上则是浪费。本仓库没有任何东西检验过这一点 — 这里没有检索基准，路由表背后也没有已发表的数字，所以请把它当作值得覆盖的默认值，而不是结论。

决定出现在返回的信封中，因此廉价答案是可审计的。用 CLI 上的 `--route` 或 MCP 工具上的 `route` 参数覆盖它。
