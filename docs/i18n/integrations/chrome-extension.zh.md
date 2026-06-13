# 网页剪辑器（Chrome 扩展程序）

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.ko.md">한국어</a> · <a href="chrome-extension.ja.md">日本語</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.es.md">Español</a> · <a href="chrome-extension.fr.md">Français</a> · <a href="chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

将任何网页或仅选定的文本直接剪辑到您的 Tesserae 知识库中。剪辑器会将页面 POST 到本地的 `tesserae serve` 实例，该实例会将带有出处时间戳的 Markdown 文件写入项目语料库，并运行增量编译，使剪辑内容作为类型化节点出现在您的图表、保险库和站点中。

这是"自主、主动知识摄入"支柱的一键实现：看到值得保留的内容，剪辑它，它就成了代理可用的上下文。

---

## 它的工作原理

1. 您浏览到一个页面并点击剪辑器（工具栏按钮或键盘快捷键）。
2. 扩展程序获取页面的 `url`、`title`、页面元数据，以及**完整的可读内容**或者，如果您突出显示了文本，则获取您的**选择**。您可以添加可选的**笔记**和**标签**，并切换**TL;DR** 生成。
3. 它将该有效载荷 POST 到您运行的 `tesserae serve` 上的 `http://localhost:<port>/api/clip`。
4. 服务器解析正在提供的项目，写入 `data/ingested/<slug>.md`，可选地添加单次 LLM TL;DR，并调用 CLI 使用的相同摄入路径（`ingest_sources`），它将新源增量编译到图表中。
5. 您将获得 JSON 报告（`status`、`path`、`tldr`、`node_count`、`edge_count`）。

剪辑的 Markdown 看起来像：

```markdown
---
clipped_at: 2026-06-13T00:00:00Z
note: read later
source: web-clip
tags: python, web
title: An Article
url: https://example.com/article
---

## TL;DR

两句摘要（仅当启用 TL;DR 且成功时出现）。

## Note

read later

## Content

剪辑的页面文本（或您的选择）。
```

TL;DR 是**尽力而为**的：它使用 CLI 支持的 Claude 层（不需要 API 密钥）。如果 `claude` CLI 不可用或调用失败，剪辑仍会被摄入——只是没有 `## TL;DR` 部分。

---

## 安装（加载未打包的扩展）

> 扩展程序在存储库的 `extension/` 下提供（开发期间加载未打包的扩展；Chrome Web Store 列表正在审核中）。

1. 打开 `chrome://extensions`。
2. 切换**开发者模式**（右上角）。
3. 点击**加载未打包的扩展程序**并选择 `extension/` 目录。
4. 将 Tesserae 剪辑器固定到您的工具栏。

扩展程序默认与 `http://localhost:8765` 通信；在扩展选项中设置端口以匹配您传递给 `tesserae serve` 的端口。

---

## 运行服务器

编译您的项目，然后提供服务：

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` 在同一来源上公开静态站点**加上**两个 JSON 路由：

- `POST /api/ask` — 问题回答（参见 [mcp.md](mcp.md)）
- `POST /api/clip` — 网页剪辑摄入（此功能）

在浏览时让它保持运行；每个剪辑都会点击 `/api/clip`。

---

## `/api/clip` 合约

`POST /api/clip` 使用 JSON 主体：

| 字段        | 类型      | 必需 | 注释 |
|-------------|-----------|------|-------|
| `url`       | string    | yes  | 源页面 URL（出处 + 文件名 slug）。 |
| `title`     | string    | no   | 页面标题；退回到派生的标题。 |
| `content`   | string    | yes* | 完整页面文本。 |
| `selection` | string    | no   | 如果存在，**覆盖** `content` 值 — 仅剪辑突出显示的文本。 |
| `meta`      | object    | no   | 传递的额外页面元数据。 |
| `note`      | string    | no   | 您的自由文本注释 → `## Note`。 |
| `tags`      | string[]  | no   | 前置元数据标签。 |
| `tldr`      | boolean   | no   | 默认值 `true`。设置 `false` 以跳过 TL;DR 生成。 |

\* `content` 或 `selection` 中的任何一个都必须非空。

**响应** `200 OK`：

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "A two-sentence summary…",
  "node_count": 142,
  "edge_count": 287
}
```

错误返回 `400`（错误的请求/空主体）或 `500`（摄入失败），带有 `{"error": "..."}` 消息。

### CORS

因为剪辑器是一个浏览器扩展，击中 `localhost`，端点支持 CORS——但仅限于受信任的调用者，因此您访问的任意网站无法 POST 到您的图表中：

- `OPTIONS /api/clip` 返回预检头。
- 服务器验证请求 `Origin`，并**仅反映**浏览器扩展（`chrome-extension://…`）和本地回路（`http://localhost`、`http://127.0.0.1`）来源。外部网站源被拒绝，返回 `403`，永远不会到达摄入路径。
- 允许的响应发送 `Access-Control-Allow-Origin: <that origin>`、`Access-Control-Allow-Methods: POST, OPTIONS` 和 `Access-Control-Allow-Headers: Content-Type`。
- Chrome 的**私有网络访问**预检被接受：当请求携带 `Access-Control-Request-Private-Network: true` 时，服务器回复 `Access-Control-Allow-Private-Network: true`，以便 Web Store 扩展可以到达 `localhost`。
- 请求主体在读取前被限制为 5 MB。

---

## MCP `ingest` 工具

相同的摄入路径通过 Tesserae MCP 服务器作为 `ingest` 工具向代理公开，因此代理可以剪辑它在没有浏览器的情况下找到的内容：

| 输入      | 必需 | 注释 |
|----------|------|-------|
| `content` | yes  | 要摄入的文本。 |
| `url`     | no   | 源 URL（出处 + slug）。 |
| `title`   | no   | 文档标题。 |
| `note`    | no   | 注释 → `## Note`。 |
| `tags`    | no   | 前置元数据标签。 |
| `tldr`    | no   | 默认值 `true`。 |

它摄入到**活跃项目**（使用 `activate_project` 解析或传递 `project`）并返回相同的 `{status, path, tldr, node_count, edge_count}` 报告。有关 MCP 设置，请参见 [mcp.md](mcp.md)。

---

## TL;DR 切换

TL;DR 默认开启。当您想要快速、确定性剪辑而不需要 LLM 调用时，在扩展程序弹出窗口中按剪辑关闭它（或发送 `"tldr": false`）——例如，剪辑到空隙项目或当 `claude` 不在 PATH 中时。启用它后，失败的/丢失的摘要化程序永远不会阻止剪辑；您只是不会获得 `## TL;DR` 部分。

---

## 键盘快捷键

剪辑器注册一个命令，您可以在 `chrome://extensions/shortcuts` 下绑定。默认值为：

- **剪辑当前页面/选择：** `Ctrl+Shift+S`（macOS：`Cmd+Shift+S`）

如果它与另一个扩展程序冲突，请在那里重新绑定它。
