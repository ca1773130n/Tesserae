# RAG-Anything マルチモーダル連携ツール

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) は、MinerU/Docling/PaddleOCR を介して PDF、Office 文書、画像、数式を解析するマルチモーダル RAG フレームワーク（LightRAG ベース）です。Tesserae はこれを、マルチモーダル取り込みパイプライン（UA スタイルのネイティブグラフ投影）として、また Cognee と並ぶランタイムメモリバックエンドとして統合します。

## なぜ両方を使うのか？

- Tesserae — 長期的なエージェントメモリ、wiki コンパイル、グラフ投影。
- RAG-Anything — マルチモーダル取り込み + LightRAG ランタイム検索。

両者は補完し合います: RAG-Anything は Tesserae のテキスト優先のソースローダーが提供しない PDF/Office/画像の理解をもたらします; Tesserae はセッションをまたいで残り続ける長期的でクエリ可能なメモリを保持します。

## 現在の低摩擦ワークフロー

推奨される経路はセットアップウィザードです:

```bash
tesserae init
```

RAG-Anything は、CLI フラグの集合ではなく **対話型ウィザードのプロンプト**に
なりました。ウィザードの実行時に、連携のプロンプトに答えてください:

- 求められたら RAG-Anything を有効化する;
- 求められたらインストールする（`raganything` + `docling` をインストール）;
- パーサーに `mineru` を選ぶ;
- 提示されたらインストール後のリフレッシュ実行を有効化する。

その後コンパイルします:

```bash
tesserae compile
```

非対話的な自動化（CI）では、ウィザードをデフォルト（すべての任意連携を OFF）で
実行し、その後 `.tesserae/config.json` で RAG-Anything を有効化し（ウィザードは
連携設定を `external_tools` / `memory_backends` キーの下に書き込みます）、管理された
リフレッシュを実行します:

```bash
tesserae init --yes
# .tesserae/config.json で raganything を有効化する（external_tools キー）
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

Tesserae は、ユーザーが自分で考案するのではなく、管理された更新コマンドを保存します:

```bash
tesserae integrations refresh raganything --parser mineru
```

コンパイル中、Tesserae は次を行います:

1. `.tesserae/external/raganything/manifest.json` が存在し、現在の git コミットと一致するか（保存された `meta.json#gitCommitHash` 経由で）を確認します;
2. 欠落/古い場合、または `--refresh-external-tools` が渡された場合に、管理された更新ラッパーを実行します;
3. 非コードのソース（PDF、Office 文書、画像、markdown）を発見し、設定されたパーサーで解析します;
4. `manifest.json` + `meta.json` を書き込みます;
5. 通常のメモリコンパイルを続行します。

コンパイル前に、設定済みの外部更新コマンドをすべて強制実行できます:

```bash
tesserae compile --refresh-integrations
```

## 手動での同等手順

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## ネイティブグラフ同期

設定されたツールが `sync_mode: native_graph` を使う場合、Tesserae は compile 中に解析済み manifest をネイティブにインポートします。

ネイティブアダプターは `.tesserae/external/raganything/manifest.json` を読み込み、解析された各ドキュメントをマルチモーダルブロックメタデータ付きの `SourceFile` node に投影し、sync manifest を書き込みます:

```text
.tesserae/external/raganything-sync.json
```

現在のマッピング:

| RAG-Anything | Tesserae の方向性 |
|---|---|
| `documents[*]` | `SourceFile` node、`metadata.parser="raganything"` |
| `content_list[type=text]` | `SourceFile.description` に折り畳み; concepts は既存の抽出器経由 |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` と `metadata.equations[]`（LaTeX を保持） |

各ノードに provenance が保持されます:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

## ランタイムメモリバックエンド

`memory_backends.raganything`（`default_raganything_backend_config` によって生成されるデフォルト）は Cognee と共存します。`project ask` は優先順位の高いバックエンドから順に試行します; プロジェクトごとの優先順位は `memory_backends.priority` で設定できます。RAG-Anything はオプトインです（デフォルト `enabled: false`）; セットアップフラグ `--with-raganything` がこれを有効にします。

## システム前提条件

- **Python 3.10+**（RAG-Anything の要件; Tesserae 自体は 3.9+ を対象）。
- `.doc/.docx/.ppt/.pptx/.xls/.xlsx` の解析のための **LibreOffice** — プラットフォームのパッケージマネージャで個別にインストールしてください。LibreOffice がない場合、RAG-Anything は警告とともに Office 文書をスキップします。
- **MinerU のモデル重み**は最初の解析時にダウンロードされてキャッシュされます（数 GB）。以降の実行ではキャッシュを再利用します。
- ランタイムメモリバックエンドのための **OpenAI 互換 LLM/埋め込み/ビジョンキー**（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）。パーサーのみのモードではキーは不要です。

## 協業の原則

Tesserae は memory compiler のままです。RAG-Anything は独立した連携ツールのままです: マルチモーダルパーサー + LightRAG 検索エンジン。
