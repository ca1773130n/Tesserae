# Understand Anything 配套工作流

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) 和 LLM-Wiki 是互补项目。

- Understand Anything 擅长生成代码库知识图谱和交互式仪表板。
- LLM-Wiki 专注于长期存在的智能体记忆：文档、markdown/wiki 编译、静态发布、会话历史以及面向智能体的导出。

LLM-Wiki 不应内置或吸收 Understand Anything。应将其视为一个独立的配套工具，用来生成有用的图谱产物。

## 为什么两者都用？

Understand Anything 可以写入：

```text
.understand-anything/knowledge-graph.json
```

该图谱捕获文件、函数、类、模块、概念、依赖、层和导览等代码结构。

随后 LLM-Wiki 可以将该产物与项目记忆的其余部分一起保存：

- 源文档和 markdown 页面；
- 仓库文件；
- 研究笔记；
- 本地 Claude Code / Codex 会话历史；
- 生成的静态 wiki 页面；
- 2D / 3D 图谱网站视图；
- `llms.txt`、`llms-full.txt`、`search-index.json`、`graph.json` 以及每页对应的智能体 sibling。

## 当前低摩擦工作流

推荐路径是设置向导：

```bash
llm_wiki project setup
```

在配套工具步骤中选择 Understand Anything。LLM-Wiki 会在请求时安装/更新配套 skills，并把受管理的刷新命令写入 `.llm-wiki/config.json`。之后调用 `llm_wiki project compile` 时，如果 UA 图谱缺失或过期，就会自动运行该包装命令。

对于非交互式自动化，请使用：

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
llm_wiki project compile
```

存储的命令由 LLM-Wiki 管理，不需要用户自行构造：

```bash
llm_wiki project refresh-understand-anything --platform codex
```

编译期间，LLM-Wiki 会：

1. 检查 `.understand-anything/knowledge-graph.json` 是否存在，并在元数据可用时确认它与当前 git 提交匹配；
2. 仅当图谱缺失/过期或强制刷新时，运行已配置的智能体平台（`codex`、`opencode` 或 `claude`）；
3. 验证图谱已经写入；
4. 生成 `.llm-wiki/external/understand-anything.md`；
5. 继续正常的记忆编译。

你可以在编译前强制运行所有已配置的外部刷新命令：

```bash
llm_wiki project compile --refresh-external-tools
```

也需要 Cognee？在同一 setup 命令中添加运行时记忆标志：

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## 手动等效流程

推荐使用受管理的设置路径。如果你有意在 LLM-Wiki 之外使用 UA，请先在你的智能体环境中运行 Understand Anything：

```bash
/understand
```

然后运行 `llm_wiki project setup --with-understand-anything`，让 LLM-Wiki 记录 markdown 投影源。直接的 JSON 文件会保留为原始配套产物，而不是手动输入的源路径。

```bash
llm_wiki project setup --with-understand-anything
llm_wiki project compile
llm_wiki project build-site
```

如果你还想要本地智能体会话记忆：

```bash
llm_wiki project sessions discover --import
llm_wiki project build-site
```

## 原生图同步

LLM-Wiki 仍然保留便于阅读的 markdown projection，同时当配置的工具使用 `sync_mode: native_graph` 时，会在 compile 期间原生导入 UA 图。

原生适配器读取 `.understand-anything/knowledge-graph.json`，把 UA 节点/边映射到 LLM-Wiki 的受控 ontology，并写入同步 manifest：

```text
.llm-wiki/external/understand-anything-sync.json
```

当前映射：

| Understand Anything | LLM-Wiki 方向 |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | 带有 `ua_edge_type` metadata 的 `shares_concept_with` |

Concept synchronization 会做 canonicalization，而不是盲目创建重复节点。如果 UA 输出 `Mermaid Rendering`，而 LLM-Wiki 已有 `Mermaid rendering`，compile 会保留一个 concept node，并在 `metadata.external_refs` 下添加 UA provenance。

LLM-Wiki 仍然是 memory compiler；UA 仍然是独立的 companion graph generator。

## 协作原则

不要把 LLM-Wiki 描述为 Understand Anything 的替代品。

更好的表述：

- Understand Anything 帮助开发者当下理解代码库。
- LLM-Wiki 帮助智能体长期记忆、搜索、引用、更新并发布项目知识。
