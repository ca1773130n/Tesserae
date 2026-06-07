# MCP —— 把 Tesserae 接入 Claude Code、Codex、Cursor

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.fr.md">Français</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 自带一个 [Model Context Protocol](https://modelcontextprotocol.io) stdio 服务器，把编译好的类型化图谱暴露给任何支持 MCP 的客户端：Claude Code、Codex CLI、Cursor、Claude Desktop 等。该服务器同时提供三个完整的 MCP 表面 —— **tools**、**resources** 和 **prompts** —— 因此客户端既可以按需查询图谱，也可以从规范化的 URI 廉价地预热上下文。

## 先决条件

服务器从 `.tesserae/graph.json` 读取数据，因此需要先做一次编译：

```bash
cd /path/to/your-project
tesserae project setup    # interactive; or --yes for non-interactive
tesserae project compile  # deterministic, no LLM calls, no API keys
```

源文件改动后可以随时重新编译。服务器会在下一次工具调用时自动读取新图谱，不需要重启。

## 1) 生成客户端配置

```bash
tesserae project mcp-config
```

会输出大致如下的 JSON 片段：

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

具体路径会根据当前项目自动填入。如果想让服务器条目用 `tesserae` 之外的名字，可以传 `--name <alias>`。

## 2) 粘贴到你的 MCP 客户端

| 客户端 | 配置位置 |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → paste JSON |
| Hermes | `~/.hermes/config.toml` (use the TOML-equivalent block printed by `mcp-config --format hermes`) |

修改后请重启客户端。下一会话就会建立连接并发现 Tesserae 表面。

## 3) 客户端能看到什么

### Tools —— 由模型调用

每个 tool 都接受可选的 `graph_path` 或 `project`（注册表别名），因此单个服务器可在每次调用时解析任意已注册的 vault。省略时回退到活动项目。

**图谱查询与检索**

| Tool | 用途 |
|---|---|
| `schema` | 受控的 node、edge 和 wiki-kind 词汇表 |
| `graph_summary` | 当前项目的节点 + 边计数以及类型分布 |
| `search_nodes` | 按 `query`、`type`/`types`、`kind`、`limit`、混合 `mode`/`weights` 过滤公开图节点；`include_superseded` 可显示已废弃节点 |
| `node_context` | 一个节点 + 它的相邻边 + 邻居节点。`use_ppr` 用个性化 PageRank 而非 1 跳游走对邻居排序；`include_superseded`、`limit` 限定结果 |
| `embedding_status` | 报告驱动混合检索的活动嵌入后端 |
| `search_facts` | 从图谱投影出的时序事实（Graphiti 风格）；`current_only` 仅过滤当前事实 |
| `timeline` | 按 `valid_from` 排序的事实，用于纵向视图 |
| `graph_ppr` | 从一个或多个 `seed_node_id` 出发的个性化 PageRank，返回最相关的 top-K 节点；可调 `alpha`、`directed`、`edge_type_weights` |
| `wiki_page` | 节点对应的、已编译的 wiki 页面正文，以及它引用的内部链接 |
| `raw_source` | 原始 markdown 源文（上限 16 KB） |
| `lint_report` | 最近一次编译期的 lint 结果（上限 64 KB） |

**按需上下文编译器**（Phase 7）

| Tool | 用途 |
|---|---|
| `compile_context` | 为 `query` 或显式 `seeds` 编译一份量身定制的**带引用**上下文文档。遍历深度受限的子图（`depth`，1–10，默认 2），用 PPR 排序，并填充字符 `budget`（默认 32000；传 `0` 表示不限）。默认确定性；设 `synthesize: true` 可生成由 LLM 撰写的叙述式 "topic" 切片。返回 `body`、`citations`、`selected_node_ids` 和 `char_budget_used` |
| `list_communities` | 列出后编译阶段生成的 `COMMUNITY_SUMMARY` 节点，按成员数排序（`min_size`、`limit`）；通过 `node_context` 沿 `summarizes` 边回溯到成员 |
| `fresh_insights` | 按艾宾浩斯式衰减分数（最新 + 访问最多优先）排序的会话发现；过滤掉被更新近似重复项取代的发现。可选 `kind`、`limit`、`include_superseded` |

**会话记忆**（见 [sessions.md](sessions.md)）

| Tool | 用途 |
|---|---|
| `list_sessions` | 活动项目的会话信封（id、started_at、title、files_touched、发现计数）；`since`、`limit` |
| `find_session_findings` | 通过 `discussed_in` / `references` 关联到 `node_id` 的所有会话发现，可按 `kinds`（insight / decision / question / todo / hypothesis / takeaway）过滤 |
| `find_code_symbol_mentions` | 将一个会话发现扩展为它提及的 `CodeFunction`/`CodeClass`/`CodeMethod` 符号（使用可选启用的 insight↔symbol 关联阶段生成的 `discusses` 边） |

**问答与注册表**

| Tool | 用途 |
|---|---|
| `ask` | 通过配置的记忆后端进行自然语言问答（raganything、cognee 或 compiled wiki）。`backend`、`top_k`；通过 `scope`/`scope_aliases` 跨 vault 扇出；`claude_config_dir` 用于多账户路由 |
| `list_projects` / `register_project` / `activate_project` / `unregister_project` | 多项目注册表控制 |

**引导式设置**

| Tool | 用途 |
|---|---|
| `tesserae_setup_plan` | 检测环境并以 JSON 形式提出设置计划。只读 —— 绝不触碰 `.tesserae/` |
| `tesserae_setup_apply` | 应用（可能已编辑的）计划：写入 `.tesserae/config.json` 并执行受控的安装/运行动作。受 `confirm_install_actions` / `confirm_run_actions` 限制 |

### Resources —— 自动加载到模型的上下文

客户端可以通过资源选择器拉取的 URI，不消耗一次工具调用：

- `tesserae://graph/schema` —— 与 `schema` 工具相同的载荷，作为静态上下文随时可用
- `tesserae://graph/summary` —— 当前活动项目的摘要
- `tesserae://lint-report` —— 最新 lint 报告，以 markdown 呈现

另外，客户端可以按需构造以下 URI 模板：

- `tesserae://wiki/{kind}/{slug}` —— 任意已编译的 wiki 页面正文
- `tesserae://raw/{source_path}` —— 任意原始 markdown 源文

### Prompts —— 一键式研究模板

它们会出现在客户端的斜杠菜单里（例如 Claude Code 的 `/` 调色板）：

| Prompt | 参数 | 作用 |
|---|---|---|
| `summarize-paper` | `slug` (required) | 调用 `node_context` + `wiki_page` + 可选的 `raw_source`，再返回结构化摘要：贡献、方法概要、关键结果、局限性、相关节点 |
| `find-related-work` | `topic` (required), `limit` | 串联 `search_nodes` + `node_context`，给出 top-K 相关条目及相关性说明 |
| `compare-approaches` | `a`, `b` (both required) | 对两者分别拉取 `node_context`，并通过 `search_facts` 获取性能声明；返回带综合分析的并排对比 |
| `gap-analysis` | `topic` (optional) | 浮现未解决的开放问题、缺失的基准、证据不足的声明 |
| `triage-open-questions` | _none_ | 列出每一个 `OpenQuestion` 节点，按主题分组，并给出优先级建议 |

每个 prompt 都会渲染为一条用户消息，明确告诉模型应该把哪些 Tesserae 工具串起来，模型就不必每次重新摸索表面。

## 多项目：在同一台服务器下注册多个 vault

`~/.tesserae/registry.json` 处的持久化注册表，让同一台 MCP 服务器可以按名称解析任意已注册项目：

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

注册之后，每一个接受 `project` 或 `graph_path` 的工具都会把 `project: "research"` 解析到注册表，而不需要提供完整路径。服务器甚至会校验注册的 `graph_path` 是否仍然存在，如果需要重新编译，会返回清晰的错误信息。

### 一次扇出到所有已注册 vault

`ask` 工具接受 `scope: "all-registered"`，可以并行查询每一个已注册项目并返回聚合结果：

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

通过 `scope_aliases: ["research", "notes"]` 可以限定到子集。

## 多账号 Claude CLI

如果你的 `ask` 工具走的是 Claude CLI，并且你有多个账号（例如 `~/.claude` 和 `~/.claude-personal2`），可以在每次调用时传入 `claude_config_dir`：

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

服务器只在该次调用期间导出 `CLAUDE_CONFIG_DIR`，调用结束后恢复之前的值。调用之间不会互相泄漏。

## 验证

重启 MCP 客户端后，确认连接是否建立：

- Claude Code：`/mcp` 应当列出 `tesserae` 以及工具数量。
- Cursor：聊天栏的 MCP 图标应当显示 `tesserae: connected`，并附带 tool/resource/prompt 计数。
- Codex / Hermes：按名称调用任意工具（例如 `schema`），并检查响应。

如果什么都看不到，请反复确认 `--graph` 指向的是存在的 `.tesserae/graph.json` —— 服务器现在会在启动时以及每次工具调用时都做校验，所以你会看到清晰的错误信息，而不是静默的 500。

## 它在整个体系中的位置

MCP 服务器是类型化图谱的 **读接口**。**写路径**（摄取源文件、重新编译、刷新 RAG-Anything 或 Understand-Anything 等配套工具）请直接使用 CLI。两者是解耦的：CLI 更新 `.tesserae/`，MCP 服务器在下一次工具调用时读取其中的内容。
