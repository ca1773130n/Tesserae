# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
将单个文档文件或 URL 合并到知识库中。

## 用法

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` 是一个或多个本地文件路径或 `http(s)` URL。URL 会被抓取、转换为 markdown，
并带着溯源 front-matter（`source_url`、`fetched_at`、`content_sha256`，以及检测到时的
`arxiv_id`）持久化到 `data/ingested/<slug>.md`，然后再合并。
来自项目外部的本地文件会被复制到 `data/ingested/`，从而成为受跟踪的来源（之后的完整编译会
逐字节地复现它们）。

URL 摄取需要可选附加组件：

    pip install tesserae[ingest-url]

## 工作原理

默认情况下，`ingest` 通过增量编译合并新来源——它不会重新提取整个语料库——而结果与完整编译
逐字节相同（对于增量路径无法处理的任何情况，自动的完整重新编译回退机制可保证正确性）。
传入 `--exact` 可强制对整个语料库进行完整重新编译。

## 标志

- `--exact` — 强制对整个语料库进行完整重新编译。
- `--dry-run` — 抓取并报告将要摄取的内容；不写入图。
- `--title` — 覆盖标题，对于裸 URL 很有用。
- `--source-kind` — 覆盖来源分类。

## 相关命令

- `tesserae compile`（无参数）会重新提取整个受跟踪的语料库。
- `tesserae ingest <x>` 以增量方式添加一个来源。
- `tesserae code ingest` 从 Python 源代码生成代码图（这是另一个命令）。
