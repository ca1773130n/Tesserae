# Understand Anything コンパニオンワークフロー

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a> · <a href="understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) と Tesserae は補完的なプロジェクトです。

- Understand Anything はコードベースのナレッジグラフとインタラクティブなダッシュボードの生成に優れています。
- Tesserae は長寿命のエージェントメモリに焦点を当てています: ドキュメント、markdown/wiki コンパイル、静的パブリッシング、セッション履歴、エージェント向けエクスポート。

Tesserae は Understand Anything をベンダリングしたり吸収したりすべきではありません。有用なグラフ成果物を生成できる独立したコンパニオンとして扱ってください。

## なぜ両方を使うのか？

Understand Anything は次を書き出せます:

```text
.understand-anything/knowledge-graph.json
```

このグラフは、ファイル、関数、クラス、モジュール、概念、依存関係、レイヤー、ツアーといったコード構造を捉えます。

Tesserae はその成果物を、プロジェクトメモリの残りの部分と並べて保存できます:

- ソースドキュメントと markdown ページ;
- リポジトリファイル;
- 研究ノート;
- ローカルの Claude Code / Codex セッション履歴;
- 生成された静的 wiki ページ;
- 2D / 3D グラフウェブサイトビュー;
- `llms.txt`、`llms-full.txt`、`search-index.json`、`graph.json`、およびページ単位のエージェント向けシブリング。

## 現在の低摩擦ワークフロー

推奨パスはセットアップウィザードです:

```bash
tesserae init
```

コンパニオンツールのステップで Understand Anything を選んでください（**既定では OFF** です — そのリフレッシュはリモートのインストールスクリプトを実行します）。Tesserae は管理されたリフレッシュコマンドを `.tesserae/config.json` の `external_tools` の下に書き込みます。コンパイル時の自動リフレッシュも既定では OFF です（`auto_refresh: false`）。UA グラフが欠落している、または古いときに `tesserae compile` にラッパーを自動実行させたい場合は `true` に設定してください。

非対話の自動化では、`tesserae init --yes`（統合は OFF）を実行し、`.tesserae/config.json` で Understand Anything を有効化してから、次を実行します:

```bash
tesserae integrations refresh understand-anything --platform codex
tesserae compile
```

保存されるコマンドは Tesserae 管理のものであり、ユーザーが自作する必要はありません:

```bash
tesserae integrations refresh understand-anything --platform codex
```

コンパイル中、Tesserae は次を行います:

1. `.understand-anything/knowledge-graph.json` が存在し、メタデータが利用可能な場合に現在の git コミットと一致するかを確認する;
2. その `external_tools` エントリが `auto_refresh: true` を持ち、かつグラフが欠落/古い場合、またはリフレッシュが強制された場合にのみ、設定されたエージェントプラットフォーム（`codex`、`opencode`、または `claude`）を実行する;
3. グラフが書き込まれたことを検証する;
4. `.tesserae/external/understand-anything.md` をマテリアライズする;
5. 通常のメモリコンパイルを続行する。

コンパイル前に、設定済みのすべての外部リフレッシュコマンドを強制実行できます:

```bash
tesserae compile --refresh-integrations
```

Cognee も必要ですか？ Cognee も同様にオプトインです: `pip install tesserae[cognee]` でインストールし、`.tesserae/config.json` で `memory_backends.cognee.enabled: true` を設定してください（`tesserae query --backend cognee` で明示的にクエリします）。

## 手動での同等手順

管理されたセットアップパスが推奨です。意図的に Tesserae の外で UA を使いたい場合は、まずエージェント環境内で Understand Anything を実行してください:

```bash
/understand
```

その後セットアップウィザードを実行し、**プロンプトが出たら Understand Anything を有効化**して、
Tesserae に markdown 射影ソースを記録させてください。直接の JSON ファイルは、手入力の
ソースパスとしてではなく、生のコンパニオン成果物として保持されます。

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

非対話の自動化では、`tesserae init --yes`（統合は OFF）を実行し、
`.tesserae/config.json` で Understand Anything を有効化してから（ウィザードは統合を
`external_tools` キーの下に書き込みます）、コンパイル前に `tesserae integrations
refresh understand-anything` を実行してください。

ローカルのエージェントセッションメモリも欲しい場合:

```bash
tesserae sessions discover --import
tesserae export site
```

## ネイティブグラフ同期

Tesserae は現在、可読性のために markdown 射影を維持しつつ、設定されたツールが `sync_mode: native_graph` を使う場合にはコンパイル中に UA グラフをネイティブにインポートもします。

ネイティブアダプタは `.understand-anything/knowledge-graph.json` を読み込み、UA のノード/エッジを Tesserae の制御されたオントロジーにマッピングし、同期マニフェストを書き込みます:

```text
.tesserae/external/understand-anything-sync.json
```

現在のマッピング:

| Understand Anything | Tesserae 側 |
|---|---|
| `project` | リポジトリ/プロジェクトのメタデータ |
| `nodes[type=file]` | `SourceFile` ノード |
| `nodes[type=function]` / `method` | `CodeFunction` ノード |
| `nodes[type=class]` / `component` | `CodeClass` ノード |
| `nodes[type=module]` / `package` | `CodeModule` ノード |
| `nodes[type=concept]` / `topic` | 正規化された `Concept` ノード |
| `nodes[type=feature]` / `capability` | `Capability` ノード |
| `edges[type=imports]` | `imports` エッジ |
| `edges[type=contains]` | `contains` エッジ |
| `edges[type=calls]` | `calls` エッジ |
| 未知のエッジタイプ | `ua_edge_type` メタデータ付きの `shares_concept_with` |

概念の同期は、盲目的に複製されるのではなく正規化されます。UA が `Mermaid Rendering` を出力し、Tesserae に既に `Mermaid rendering` がある場合、コンパイルは 1 つの概念ノードを保持し、`metadata.external_refs` の下に UA の来歴を追加します。

Tesserae はメモリコンパイラであり続けます; UA は独立したコンパニオングラフ生成器であり続けます。

## 協業の原則

Tesserae を Understand Anything の置き換えとして位置づけないでください。

より良い位置づけ:

- Understand Anything は、開発者が*いま*コードベースを理解するのを助けます。
- Tesserae は、エージェントがプロジェクト知識を時間をかけて記憶・検索・引用・更新・公開するのを助けます。
