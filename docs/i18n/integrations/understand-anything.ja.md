# Understand Anything 連携ワークフロー

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) と Tesserae は相互補完的なプロジェクトです。

- Understand Anything は、コードベースの知識グラフとインタラクティブなダッシュボードの生成に優れています。
- Tesserae は、長期的なエージェントメモリに注力しています: ドキュメント、markdown/wiki コンパイル、静的公開、セッション履歴、エージェント向けエクスポート。

Tesserae は Understand Anything をベンダー化したり取り込んだりすべきではありません。有用なグラフ成果物を生成できる独立した連携ツールとして扱ってください。

## なぜ両方を使うのか？

Understand Anything は次を書き込めます:

```text
.understand-anything/knowledge-graph.json
```

このグラフは、ファイル、関数、クラス、モジュール、概念、依存関係、レイヤー、ツアーなどのコード構造を捉えます。

その後、Tesserae はその成果物をプロジェクトメモリの残りと一緒に保存できます:

- ソースドキュメントと markdown ページ;
- リポジトリファイル;
- 調査メモ;
- ローカルの Claude Code / Codex セッション履歴;
- 生成された静的 wiki ページ;
- 2D / 3D グラフ Web サイトビュー;
- `llms.txt`、`llms-full.txt`、`search-index.json`、`graph.json`、ページごとのエージェント sibling。

## 現在の低摩擦ワークフロー

推奨される経路はセットアップウィザードです:

```bash
tesserae init
```

連携ツールのステップで Understand Anything を選択します。Tesserae は要求に応じて連携 skills をインストール/更新し、管理された更新コマンドを `.tesserae/config.json` に書き込みます。以降の `tesserae compile` 呼び出しでは、UA グラフが存在しない、または古い場合に、そのラッパーが自動実行されます。

非対話型の自動化には次を使います:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
tesserae compile
```

保存されるコマンドはユーザーが考案するものではなく、Tesserae が所有するものです:

```bash
tesserae integrations refresh understand-anything --platform codex
```

コンパイル中、Tesserae は次を行います:

1. `.understand-anything/knowledge-graph.json` が存在し、メタデータが利用可能な場合は現在の git コミットと一致するか確認します;
2. グラフが欠落/古い場合、または更新が強制された場合にのみ、設定されたエージェントプラットフォーム（`codex`、`opencode`、または `claude`）を実行します;
3. グラフが書き込まれたことを検証します;
4. `.tesserae/external/understand-anything.md` を具体化します;
5. 通常のメモリコンパイルを続行します。

コンパイル前に、設定済みの外部更新コマンドをすべて強制実行できます:

```bash
tesserae compile --refresh-integrations
```

Cognee も必要ですか？同じ setup コマンドにランタイムメモリのフラグを追加してください:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## 手動での同等手順

管理されたセットアップ経路が推奨です。意図的に Tesserae の外で UA を使いたい場合は、まずエージェント環境内で Understand Anything を実行してください:

```bash
/understand
```

次にセットアップウィザードを実行し、**プロンプトが表示されたら Understand
Anything を有効化**して、Tesserae に markdown 投影ソースを記録させます。直接の
JSON ファイルは手入力のソースパスではなく、生の連携成果物として保持されます。

```bash
tesserae init
# ウィザードが尋ねたら Understand Anything を有効化する
tesserae compile
tesserae export site
```

非対話的な自動化では、`tesserae init --yes`（連携 OFF）を実行し、
`.tesserae/config.json` で Understand Anything を有効化し（ウィザードは連携を
`external_tools` キーの下に書き込みます）、コンパイル前に `tesserae integrations
refresh understand-anything` を実行します。

ローカルのエージェントセッションメモリも必要な場合:

```bash
tesserae sessions discover --import
tesserae export site
```

## ネイティブグラフ同期

Tesserae は読みやすい markdown projection を維持しつつ、設定されたツールが `sync_mode: native_graph` を使う場合は compile 中に UA グラフもネイティブに取り込みます。

ネイティブアダプターは `.understand-anything/knowledge-graph.json` を読み込み、UA のノード/エッジを Tesserae の制御された ontology にマッピングし、sync manifest を書き込みます:

```text
.tesserae/external/understand-anything-sync.json
```

現在のマッピング:

| Understand Anything | Tesserae の方向性 |
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
| unknown edge types | `ua_edge_type` metadata 付きの `shares_concept_with` |

Concept synchronization は重複を無条件に作るのではなく canonicalize します。UA が `Mermaid Rendering` を出力し、Tesserae にすでに `Mermaid rendering` がある場合、compile は 1 つの concept node を保持し、`metadata.external_refs` に UA provenance を追加します。

Tesserae は memory compiler のままで、UA は独立した companion graph generator のままです。

## 協業の原則

Tesserae を Understand Anything の置き換えとして位置づけないでください。

より良い位置づけ:

- Understand Anything は、開発者が今コードベースを理解するのを助けます。
- Tesserae は、エージェントが時間をかけてプロジェクト知識を記憶、検索、引用、更新、公開するのを助けます。
