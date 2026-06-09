# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
単一のドキュメントファイルまたは URL をナレッジベースにマージします。

## 使い方

    tesserae ingest <input>...  [--title T] [--source-kind K] [--exact] [--dry-run]

`<input>` は 1 つ以上のローカルファイルパスまたは `http(s)` URL です。URL は取得され、
markdown に変換され、来歴を示す front-matter（`source_url`、`fetched_at`、`content_sha256`、
そして検出された場合は `arxiv_id`）とともに `data/ingested/<slug>.md` に保存されてからマージされます。
プロジェクト外部のローカルファイルは `data/ingested/` にコピーされ、追跡対象のソースになります
（後で完全コンパイルを行うとまったく同じものが再現されます）。

URL の取り込みにはオプションの追加パッケージが必要です。

    pip install tesserae[ingest-url]

## 仕組み

デフォルトでは、`ingest` は増分コンパイルによって新しいソースをマージします。コーパス全体を
再抽出することはなく、その結果は完全コンパイルとバイト単位で同一です（増分パスが処理できない
ケースに対しても、自動の完全再コンパイルフォールバックが正しさを保証します）。
コーパス全体の完全再コンパイルを強制するには `--exact` を渡します。

## フラグ

- `--exact` — コーパス全体の完全再コンパイルを強制します。
- `--dry-run` — 取り込まれる内容を取得して報告しますが、グラフは書き込みません。
- `--title` — タイトルのオーバーライド。素の URL に便利です。
- `--source-kind` — ソース分類をオーバーライドします。

## 関連コマンド

- `tesserae compile`（引数なし）は追跡対象のコーパス全体を再抽出します。
- `tesserae ingest <x>` は 1 つのソースを増分的に追加します。
- `tesserae code ingest` は Python ソースからコードグラフを生成します（別のコマンドです）。
