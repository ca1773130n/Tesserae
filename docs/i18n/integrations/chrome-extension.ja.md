# ウェブクリッパー（Chrome拡張機能）

<!-- translations:start -->
<p align="center"><a href="../../integrations/chrome-extension.md">English</a> · <a href="chrome-extension.ko.md">한국어</a> · <a href="chrome-extension.zh.md">中文</a> · <a href="chrome-extension.ru.md">Русский</a> · <a href="chrome-extension.es.md">Español</a> · <a href="chrome-extension.fr.md">Français</a> · <a href="chrome-extension.de.md">Deutsch</a></p>
<!-- translations:end -->

任意のウェブページをクリップしたり、選択したテキストだけをクリップしたりして、Tesserae のナレッジベースに直接保存できます。クリッパーはページをローカルの `tesserae serve` インスタンスに POST します。サーバーはプロバイナンス（由来）タイムスタンプ付きのマークダウンファイルをプロジェクトコーパスに書き込み、インクリメンタルコンパイルを実行して、クリップがグラフ、ボルト、サイト内の型付きノードとして表示されるようにします。

これは「自律的でプロアクティブな知識取り込み」という柱が 1 クリックで実現されたものです。保存する価値のあるものを見つけたら、クリップして、エージェント対応のコンテキストになります。

---

## 動作内容

1. ページを閲覧してクリッパーを起動します（ツールバーボタンまたはキーボードショートカット）。
2. 拡張機能がページの `url`、`title`、ページメタデータ、および**フルリーダブルコンテンツ**か、テキストがハイライトされている場合は**選択部分**をキャプチャします。オプションで**メモ**と**タグ**を追加でき、**TL;DR** 生成をトグルできます。
3. そのペイロードを実行中の `tesserae serve` の `http://localhost:<port>/api/clip` に POST します。
4. サーバーはサービス中のプロジェクトを解決し、`data/ingested/<slug>.md` を書き込み、オプションで 1 回の呼び出し LLM TL;DR を前置し、CLI が使用するのと同じ取り込みパス（`ingest_sources`）を呼び出して、新しいソースを段階的にグラフにコンパイルします。
5. JSON レポート（`status`、`path`、`tldr`、`node_count`、`edge_count`）が返されます。

クリップされたマークダウンは以下のようになります：

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

2 文の要約（TL;DR が有効で成功した場合にのみ表示）。

## Note

read later

## Content

クリップされたページテキスト（または選択範囲）。
```

TL;DR は**ベストエフォート**です。CLI でバックアップされた Claude レイヤーを使用します（API キーは不要）。`claude` CLI が利用できないか、呼び出しが失敗した場合でも、クリップは取り込まれます。ただし `## TL;DR` セクションはありません。

---

## インストール（未梱包をロード）

> 拡張機能はリポジトリの `extension/` ディレクトリに付属しています（開発中に未梱包をロード。Chrome ウェブストアのリストはレビュー中です）。

1. `chrome://extensions` を開きます。
2. **開発者モード**（右上）をオンにします。
3. **未梱包拡張機能を読み込む** をクリックし、`extension/` ディレクトリを選択します。
4. Tesserae クリッパーをツールバーにピン留めします。

拡張機能はデフォルトで `http://localhost:8765` と通信します。拡張機能オプションでポートを設定して、`tesserae serve` に渡すポートと一致させます。

---

## サーバーを実行

プロジェクトをコンパイルしてから、サービス提供します：

```bash
python3 -m tesserae serve --project /path/to/project --port 8765
```

`tesserae serve` は静的サイト**プラス** 2 つの JSON ルートを同じオリジンで公開します：

- `POST /api/ask` — 質問応答（[mcp.md](mcp.md) を参照）
- `POST /api/clip` — ウェブクリップ取り込み（この機能）

ブラウジング中は実行したままにします。各クリップは `/api/clip` にヒットします。

---

## `/api/clip` コントラクト

`POST /api/clip` を JSON ボディで実行：

| フィールド  | タイプ    | 必須 | 注釈 |
|-------------|-----------|------|-------|
| `url`       | string    | はい | ソースページ URL（プロバイナンス + ファイル名スラグ）。 |
| `title`     | string    | いいえ | ページタイトル。派生したタイトルにフォールバックします。 |
| `content`   | string    | はい* | フルページテキスト。 |
| `selection` | string    | いいえ | 存在する場合、`content` を**オーバーライド**します。ハイライトされたテキストのみをクリップします。 |
| `meta`      | object    | いいえ | 追加ページメタデータ（パススルー）。 |
| `note`      | string    | いいえ | フリーテキスト注釈 → `## Note`。 |
| `tags`      | string[]  | いいえ | フロントマター タグ。 |
| `tldr`      | boolean   | いいえ | デフォルト `true`。TL;DR 生成をスキップするには `false` に設定します。 |

\* `content` または `selection` のいずれかが空でない必要があります。

**レスポンス** `200 OK`：

```json
{
  "status": "ok",
  "path": "/path/to/project/data/ingested/example-com-article.md",
  "tldr": "2 文の要約…",
  "node_count": 142,
  "edge_count": 287
}
```

エラーは `400`（不正なリクエスト／空のボディ）または `500`（取り込み失敗）を返し、`{"error": "..."}` が含まれます。

### CORS

クリッパーはブラウザ拡張機能であり `localhost` にヒットするため、エンドポイントは CORS に対応します。ただし、信頼されたコーラーのみを対象としており、訪問した任意のウェブサイトがグラフに POST することはできません：

- `OPTIONS /api/clip` はプリフライトヘッダーを返します。
- サーバーはリクエストの `Origin` を検証し、ブラウザ拡張機能（`chrome-extension://…`）とループバック（`http://localhost`、`http://127.0.0.1`）オリジンのみを**リフレクト**します。外部ウェブサイトのオリジンは `403` で拒否され、取り込みパスに到達しません。
- 許可されたレスポンスは `Access-Control-Allow-Origin: <that origin>`、`Access-Control-Allow-Methods: POST, OPTIONS`、および `Access-Control-Allow-Headers: Content-Type` を送信します。
- Chrome の**プライベートネットワークアクセス**プリフライトが尊重されます。リクエストが `Access-Control-Request-Private-Network: true` を含む場合、サーバーは `Access-Control-Allow-Private-Network: true` で応答し、Web ストア拡張機能が `localhost` に到達できるようにします。
- リクエストボディは読み込み前に制限されます（5 MB）。

---

## MCP `ingest` ツール

同じ取り込みパスは、Tesserae MCP サーバーを通じて `ingest` ツールとしてエージェントに公開されるため、エージェントはブラウザなしで見つけたコンテンツをクリップできます：

| 入力      | 必須 | 注釈 |
|-----------|------|-------|
| `content` | はい | 取り込むテキスト。 |
| `url`     | いいえ | ソース URL（プロバイナンス + スラグ）。 |
| `title`   | いいえ | ドキュメント タイトル。 |
| `note`    | いいえ | 注釈 → `## Note`。 |
| `tags`    | いいえ | フロントマター タグ。 |
| `tldr`    | いいえ | デフォルト `true`。 |

**アクティブプロジェクト**（`activate_project` で解決するか `project` を渡します）に取り込み、同じ `{status, path, tldr, node_count, edge_count}` レポートを返します。MCP セットアップについては [mcp.md](mcp.md) を参照してください。

---

## TL;DR トグル

TL;DR はデフォルトでオンです。LLM 呼び出しなしで高速で決定論的なクリップが必要な場合（たとえば、エアギャップ付きプロジェクトにクリップしたり、`claude` が PATH にない場合）は、拡張機能ポップアップでクリップごとにオフにするか、`"tldr": false` を送信します。オンにすると、失敗/欠落している要約器がクリップをブロックすることはありません。単に `## TL;DR` セクションは取得しません。

---

## キーボードショートカット

クリッパーは `chrome://extensions/shortcuts` で任意にバインドできるコマンドを登録します。デフォルトは：

- **現在のページ／選択をクリップ：** `Ctrl+Shift+S`（macOS：`Cmd+Shift+S`）

別の拡張機能と競合する場合は、そこで再バインドしてください。
