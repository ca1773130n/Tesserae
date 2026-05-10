# RAG-Anything マルチモーダル連携ツール

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) は、MinerU/Docling/PaddleOCR を介して PDF、Office 文書、画像、数式を解析するマルチモーダル RAG フレームワーク（LightRAG ベース）です。LLM-Wiki はこれを、マルチモーダル取り込みパイプライン（UA スタイルのネイティブグラフ投影）として、また Cognee と並ぶランタイムメモリバックエンドとして統合します。

## なぜ両方を使うのか？

- LLM-Wiki — 長期的なエージェントメモリ、wiki コンパイル、グラフ投影。
- RAG-Anything — マルチモーダル取り込み + LightRAG ランタイム検索。

両者は補完し合います: RAG-Anything は LLM-Wiki のテキスト優先のソースローダーが提供しない PDF/Office/画像の理解をもたらします; LLM-Wiki はセッションをまたいで残り続ける長期的でクエリ可能なメモリを保持します。

## 現在の低摩擦ワークフロー

推奨される経路はセットアップウィザードです:

```bash
llm_wiki project setup
```

自動化には次を使います:

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki は、ユーザーが自分で考案するのではなく、管理された更新コマンドを保存します:

```bash
llm_wiki project refresh-raganything --parser mineru
```

コンパイル中、LLM-Wiki は次を行います:

1. `.llm-wiki/external/raganything/manifest.json` が存在し、現在の git コミットと一致するか（保存された `meta.json#gitCommitHash` 経由で）を確認します;
2. 欠落/古い場合、または `--refresh-external-tools` が渡された場合に、管理された更新ラッパーを実行します;
3. 非コードのソース（PDF、Office 文書、画像、markdown）を発見し、設定されたパーサーで解析します;
4. `manifest.json` + `meta.json` を書き込みます;
5. 通常のメモリコンパイルを続行します。

コンパイル前に、設定済みの外部更新コマンドをすべて強制実行できます:

```bash
llm_wiki project compile --refresh-external-tools
```

## 手動での同等手順

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## ネイティブグラフ同期

設定されたツールが `sync_mode: native_graph` を使う場合、LLM-Wiki は compile 中に解析済み manifest をネイティブにインポートします。

ネイティブアダプターは `.llm-wiki/external/raganything/manifest.json` を読み込み、解析された各ドキュメントをマルチモーダルブロックメタデータ付きの `SourceFile` node に投影し、sync manifest を書き込みます:

```text
.llm-wiki/external/raganything-sync.json
```

現在のマッピング:

| RAG-Anything | LLM-Wiki の方向性 |
|---|---|
| `documents[*]` | `SourceFile` node、`metadata.parser="raganything"` |
| `content_list[type=text]` | `SourceFile.description` に折り畳み; concepts は既存の抽出器経由 |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` と `metadata.equations[]`（LaTeX を保持） |

各ノードに provenance が保持されます:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

## ランタイムメモリバックエンド

`memory_backends.raganything`（`default_raganything_backend_config` によって生成されるデフォルト）は Cognee と共存します。`project ask` は優先順位の高いバックエンドから順に試行します; プロジェクトごとの優先順位は `memory_backends.priority` で設定できます。RAG-Anything はオプトインです（デフォルト `enabled: false`）; セットアップフラグ `--with-raganything` がこれを有効にします。

## システム前提条件

- **Python 3.10+**（RAG-Anything の要件; LLM-Wiki 自体は 3.9+ を対象）。
- `.doc/.docx/.ppt/.pptx/.xls/.xlsx` の解析のための **LibreOffice** — プラットフォームのパッケージマネージャで個別にインストールしてください。LibreOffice がない場合、RAG-Anything は警告とともに Office 文書をスキップします。
- **MinerU のモデル重み**は最初の解析時にダウンロードされてキャッシュされます（数 GB）。以降の実行ではキャッシュを再利用します。
- ランタイムメモリバックエンドのための **OpenAI 互換 LLM/埋め込み/ビジョンキー**（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）。パーサーのみのモードではキーは不要です。

## 協業の原則

LLM-Wiki は memory compiler のままです。RAG-Anything は独立した連携ツールのままです: マルチモーダルパーサー + LightRAG 検索エンジン。
