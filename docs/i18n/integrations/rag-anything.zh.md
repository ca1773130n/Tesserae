# RAG-Anything 多模态配套工具

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) 是一个基于 LightRAG 的多模态 RAG 框架，通过 MinerU/Docling/PaddleOCR 解析 PDF、Office 文档、图像和公式。LLM-Wiki 同时把它作为多模态摄取流水线（UA 风格的原生图投影）和与 Cognee 并行的运行时记忆后端进行集成。

## 为什么两者都用？

- LLM-Wiki —— 长期存在的智能体记忆、wiki 编译、图谱投影。
- RAG-Anything —— 多模态摄取 + LightRAG 运行时检索。

两者互补：RAG-Anything 带来 LLM-Wiki 文本优先源加载器无法提供的 PDF/Office/图像理解；LLM-Wiki 维持跨会话仍然存在的、可查询的长期记忆。

## 当前低摩擦工作流

推荐路径是设置向导：

```bash
llm_wiki project setup
```

对于自动化：

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki 存储一个受管理的刷新命令，而不是要求用户自行构造：

```bash
llm_wiki project refresh-raganything --parser mineru
```

编译期间，LLM-Wiki 会：

1. 检查 `.llm-wiki/external/raganything/manifest.json` 是否存在并与当前 git 提交匹配（通过存储的 `meta.json#gitCommitHash`）；
2. 在缺失/过期或传入 `--refresh-external-tools` 时运行受管理的刷新包装命令；
3. 发现非代码源（PDF、Office 文档、图像、markdown）并通过配置的解析器进行解析；
4. 写入 `manifest.json` + `meta.json`；
5. 继续正常的记忆编译。

你可以在编译前强制运行所有已配置的外部刷新命令：

```bash
llm_wiki project compile --refresh-external-tools
```

## 手动等效流程

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## 原生图同步

当配置的工具使用 `sync_mode: native_graph` 时，LLM-Wiki 会在 compile 期间原生导入解析后的 manifest。

原生适配器读取 `.llm-wiki/external/raganything/manifest.json`，把每个解析后的文档投影为一个带有多模态块元数据的 `SourceFile` node，并写入 sync manifest：

```text
.llm-wiki/external/raganything-sync.json
```

当前映射：

| RAG-Anything | LLM-Wiki 方向 |
|---|---|
| `documents[*]` | `SourceFile` node，`metadata.parser="raganything"` |
| `content_list[type=text]` | 折入 `SourceFile.description`；concepts 通过现有提取器生成 |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` 和 `metadata.equations[]`（保留 LaTeX） |

每个节点都保留 provenance：

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

## 运行时记忆后端

`memory_backends.raganything`（由 `default_raganything_backend_config` 生成的默认配置）与 Cognee 共存。`project ask` 按优先级顺序尝试各后端；每个项目的优先级可以通过 `memory_backends.priority` 设置。RAG-Anything 是可选启用的（默认 `enabled: false`）；设置标志 `--with-raganything` 会将其打开。

## 系统先决条件

- **Python 3.10+**（RAG-Anything 的要求；LLM-Wiki 本身面向 3.9+）。
- 用于解析 `.doc/.docx/.ppt/.pptx/.xls/.xlsx` 的 **LibreOffice** —— 通过你平台的包管理器单独安装。缺少 LibreOffice 时，RAG-Anything 会跳过 Office 文档并发出警告。
- **MinerU 模型权重**会在首次解析时下载并缓存（约数 GB）。后续运行复用缓存。
- 运行时记忆后端所需的 **OpenAI 兼容 LLM/嵌入/视觉密钥**（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）。仅解析模式不需要密钥。

## 协作原则

LLM-Wiki 仍然是 memory compiler。RAG-Anything 仍然是独立的配套工具：多模态解析器 + LightRAG 检索引擎。
