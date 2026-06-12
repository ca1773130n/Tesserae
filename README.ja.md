# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="概念・論文・リポジトリ・合成・エンティティがフォーカスノードの周囲にクラスタされた Tesserae グラフビュー" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

> プロジェクトの自己改善型ナレッジベースを維持し、エージェントがすぐに使えるコンテキストをオンデマンドでコンパイルするコンテキストエンジンです。

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="3ステップのスクリーンキャスト：tesserae init → compile → ask、135個のドキュメントのデモコーパスで録画" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">ライブデモ</a> ·
  <a href="docs/">ドキュメント</a> ·
  <a href="docs/release-notes/">リリースノート</a> ·
  <a href="docs/integrations/mcp.md">MCP セットアップ</a> ·
  <a href="docs/integrations/obsidian.md">Obsidian エクスポート</a>
</p>

## 概要

Markdown、ソースコード、そして必要に応じて PDF/Office 文書/画像が入ったディレクトリを Tesserae に指定してください。プロジェクトの**型付き知識グラフ**を再構築し常に最新の状態に保つことで、エージェントは常に根拠のある引用付きコンテキストを利用できます。3つの柱で動作します：

1. **セッションモニタリング** — Claude Code / Codex との会話が発生した瞬間に、ファーストクラスのグラフノード（決定・インサイト・質問・TODO）として取り込まれます。
2. **自律的な取り込み** — 監督付きエンジンがソースとセッションを監視し、バーストをまとめて再コンパイルします。自己改善のサイドカーが繰り返し現れる発見を強化し、古いものを置き換えます（supersede）。
3. **オンデマンドコンテキスト** — コンテキストコンパイラが任意のクエリまたはシードノードに対して、引用付きの仕立てられたコンテキスト文書を組み立てます（文字数バジェット内で Personalized PageRank 展開）。任意のエージェントにそのまま貼り付けられます。

型付きグラフ、Obsidian vault、静的サイトはひとつの知識ベースの*プロジェクション*です。すべてローカルで動作し、ホスティングサービスではなくビルドステップ兼ライブエンジンです。

## クイックスタート

**Python 3.10+** が必要です。

```bash
pip install tesserae          # 本格的な埋め込みには [semantic] を追加
# または: pipx install tesserae   # PATH セーフで最も簡単なインストール
# または: npx @jokerized/tesserae # 同じ CLI の Node ラッパー

cd /path/to/my-project
tesserae init --yes           # ウィザード；--yes は検出されたデフォルトを承認
tesserae compile              # 知識グラフを構築
tesserae ask "Mermaid レンダリングはどこに実装されていますか？"

# クエリに対する仕立てられた引用付きコンテキスト文書をコンパイル：
tesserae context "パーサーは arXiv ID をどう処理しますか？" --budget 32000 -o context.md

tesserae serve --port 8765    # グラフ + wiki をローカルでブラウズ
```

LLM バックの機能はデフォルトで OAuth 経由の `codex` / `claude` CLI を使用します — 一般的なパスでは **API キーは不要**です。[docs/quickstart.md](docs/quickstart.md) と [docs/installation.md](docs/installation.md) を参照してください。

<details>
<summary><strong>インストール後に <code>tesserae: command not found</code>？Linux の注意点は？</strong></summary>

どのプラットフォームでも最も確実な方法は [`pipx`](https://pipx.pypa.io/) です：

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # ~/.local/bin を PATH に追加；新しいシェルを開いてください
pipx install tesserae
```

通常の `pip install tesserae` を使った場合の Ubuntu での一般的な問題：

| エラー | 原因 | 対処法 |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — システム Python がロックされている | `pipx`（上記）または venv を使用 |
| `pip install --user …` 後の `command not found` | `~/.local/bin` が `PATH` にない | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| 古いディストロでの `ModuleNotFoundError` | システムの `python3` が 3.10 未満 | `sudo apt install python3.11 python3.11-venv`、その後 `python3.11 -m pip` でインストール |

</details>

<details>
<summary><strong>ウォークスルー GIF</strong> — バンドルされた 135 個のドキュメントのデモコーパスで各クイックスタートステップを実演</summary>

<details>
<summary>1. セットアップ — リサーチディレクトリを指定してプロジェクト wiki のスキャフォールドを生成</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research が非インタラクティブに実行され .tesserae/ を書き出す様子" width="100%" />
</details>

<details>
<summary>2. コンパイル + サイト構築 — 決定論的、LLM 呼び出しなし</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile に続いて tesserae export site を実行し、graph.json と静的サイトツリーを出力する様子" width="100%" />
</details>

<details>
<summary>3. Ask — CLI からコンパイル済み wiki をクエリ</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki がスコア・種類・アウトバウンド関係とともに上位 3 件を返す様子" width="100%" />
</details>

`vhs docs/screencasts/<name>.tape` で任意の GIF を再生成できます。

</details>

## 日常的なコマンド

完全なグループ化一覧は `tesserae --help`、フラグの詳細は `tesserae <cmd> --help` で確認できます。

| コマンド | 説明 |
|---|---|
| `tesserae init` | セットアップウィザード → `.tesserae/config.json`。`--yes` で非インタラクティブ、`--bare` で最小構成。 |
| `tesserae compile` | 知識グラフとすべての成果物を再構築。`compile <paths>` で追加ファイルをアドホック取り込み。 |
| `tesserae ingest <file\|url>` | 完全な再コンパイルなしで単一のドキュメントまたは Web ページを知識ベースにマージ（パリティゲート付き増分高速パス）。 |
| `tesserae context "<query>"` | **オンデマンドコンテキストコンパイラ**：`--budget` 内で PPR 展開による引用付きコンテキスト文書を生成；`--synthesize` で LLM 要約を追加。 |
| `tesserae ask "<question>"` | コンパイル済み知識ベースに質問（`--scope all-registered` でプロジェクト全体にファンアウト）。 |
| `tesserae engine` | 現在のプロジェクト向けの監督付きリフレッシュデーモン：監視・デバウンス・再コンパイル。 |
| `tesserae engine --all` | **フリートモード**：ひとつのプロセスで*すべての*登録プロジェクトを最新に保つ — レジストリのホットリロード、`--compile-slots` スロットリング。 |
| `tesserae refresh` | ワンショットパイプライン：新しいセッションをインポート → コンパイル → vault 同期。 |
| `tesserae sessions discover --import` | このプロジェクトのローカル Claude Code / Codex セッション履歴を検索してインポート。 |
| `tesserae export site` | 静的サイトを構築（`--deploy`、`--watch`）。 |
| `tesserae serve` | インライン ask ウィジェット（`/api/ask`）付きでサイトをローカルにサーブ。 |
| `tesserae projects …` | マルチプロジェクトレジストリ：`register`、`list`、`activate`、`mcp-config`。 |
| `tesserae integrations refresh …` | コンパニオンツール（Understand-Anything、RAG-Anything）を再実行。 |

## 自動的に最新状態を維持する

エンジンこそが知識ベースを一度きりのビルドではなく*自己改善*させる仕組みです：

```bash
# 単一プロジェクト：ソース + ライブセッションを監視し、変更時に再コンパイル。
tesserae engine

# すべての登録済みプロジェクト、1プロセス（v0.8.0）：
tesserae engine --all --compile-slots 1
```

フリートモードは 10 秒ごとに `~/.tesserae/registry.json` を突き合わせるため、プロジェクトの登録・削除は再起動なしで即座に反映されます。また、プロジェクト間のコンパイルをシリアライズすることで、並行 LLM 抽出が共有アカウントのレート制限を侵食しません。初回実行時はセッション履歴を一度スイープし（ログに表示）、再起動後は永続化されたフロアから再開します。

## コンパイル後に得られるもの

```text
.tesserae/
  graph.json              # 型付きノード/エッジ（知識ベース）
  sqlite.db               # クエリ可能なグラフストア
  markdown_projection/    # 人が読めるwikiページ
  obsidian_vault/         # Obsidian にそのままドロップ可能
  site/                   # 静的サイト（グラフビュー + wiki + 検索）
  harness_sessions/       # インポートされた Claude/Codex セッションメモリ
  agent_harness/          # エージェントごとのコンテキスト設定（Claude/Codex/Gemini/...）
  cognee_bundle/          # Cognee 取り込み用の JSONL
  config.json · manifest.json · report.md · …
```

## MCP サーバ

`tesserae projects mcp-config` は Claude Code、Codex、または任意の MCP クライアント向けのサーバエントリを出力します。主要ツール：

- **`compile_context`** — クエリまたはシードノードに対する仕立てられた引用付きコンテキスト文書
  （`synthesize=true` でない限り決定論的）、`graph_ppr` を基盤とする。
- **グラフ + wiki**：`search_nodes`、`node_context`、`graph_summary`、
  `wiki_page`、`raw_source`、`timeline`、`search_facts`、`lint_report`、`ask`。
- **セッションメモリ**：`list_sessions`、`find_session_findings`、
  `find_code_symbol_mentions`、`fresh_insights`（減衰ランキング・重複排除済み）。
- **レジストリ**：`list_projects`、`register_project`、`activate_project`。

## マルチプロジェクト

`~/.tesserae/registry.json` のレジストリが CLI、MCP、フリートエンジン全体でプロジェクト名を解決します：

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # すべてのプロジェクトにファンアウト
```

あるプロジェクトの Markdown が `wiki://<alias>/<kind>/<slug>` で別のプロジェクトのノードをディープリンクでき、コンパイル時にグラフビューのブリッジノードになります。詳細は [docs](docs/) を参照してください。

## インテグレーション（すべてオプトイン）

- **Claude Code プラグイン** — スラッシュコマンド・セッションフック・スキル・MCP 自動登録が一度の `/plugin install` で揃います。
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **セッショングラフ** — Claude Code / Codex の会話 → Insight / Decision /
  Question / TODO ノード、接触したドキュメントにリンク。API キー不要。
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — コード知識グラフの取り込み。
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — マルチモーダル取り込み（MinerU/Docling 経由の PDF/Office/画像）と LightRAG 質問バックエンド。
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — グラフ+ベクターメモリバックエンド；コンパイルは常に Cognee 対応バンドルを書き出し、ランタイムの cognify はベストエフォート提供。
- **Obsidian** — ユーザー編集オーバーレイ付きの双方向 vault 同期。
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## 比較

<details>
<summary>Quartz、Logseq、Cognee、Foam との機能マトリクス</summary>

| 機能 | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| 静的 HTML 出力 | あり | あり | 部分的（エクスポート） | なし | 部分的（パブリッシュ） |
| 内蔵グラフビュー | あり | あり | あり | あり（別 UI） | あり（VSCode） |
| 型付きノードスキーマ | あり（41 種類） | なし | 部分的（タグ） | あり | なし |
| ソースからの概念抽出 | あり（LLM） | なし | なし | あり | なし |
| マルチモーダル取り込み（PDF/画像） | あり（RAG-Anything 経由） | なし | 部分的（埋め込み） | あり | なし |
| コードグラフ取り込み | あり | なし | なし | 部分的 | なし |
| MCP サーバ | あり | なし | なし | あり | なし |
| オンデマンド引用付きコンテキストコンパイラ | あり（PPR + バジェット） | なし | なし | なし | なし |
| ライブセッションモニタリング → グラフ | あり | なし | なし | なし | なし |
| マルチプロジェクトレジストリ | あり | なし | あり（グラフ） | 部分的 | なし |
| マルチプロジェクトデーモン（フリート） | あり | なし | なし | なし | なし |
| API キーなし（OAuth）で動作 | あり | 該当なし | 該当なし | なし | 該当なし |
| 決定論的バイト同一コンパイル | あり | あり | 該当なし | なし | 該当なし |
| ライブ編集 | なし | 部分的 | あり | 該当なし | あり |
| リアルタイムコラボレーション | なし | なし | あり（DB ベータ） | なし | なし |

</details>

Tesserae はライブ編集よりもソースからのコンパイルを選んでいます。UI でノートを編集したい場合は Logseq または Obsidian をご利用ください。ナレッジグラフのためのビルドツール*かつライブエンジン*が欲しい場合は、このプロジェクトが適しています。

**向いているケース：** プロジェクトのテキスト中心のソースに対して永続的で検査可能な知識グラフが欲しい場合、自分のファイルに基づくローカル MCP サーバが必要な場合、グルーコードを書かずに Cognee/Obsidian 用の整ったバンドルが欲しい場合。

**向いていないケース：** 小さなディレクトリへのベクター検索だけが必要な場合、編集 UI 付きのホスティング wiki が欲しい場合、ターンキーの「何でも聞いて」エージェントを期待している場合 — Tesserae は基盤を作ります；それをどのエージェントに繋げるかはあなたが決めます。

## 認証と LLM プロバイダ

一般的なパスでは **API キーは不要**です：

- **Codex CLI**（デフォルト）と **Claude Code CLI** は OAuth 経由で使用、マルチアカウントローテーション対応。
- **埋め込み**：ネイティブハイブリッド検索は `pip install "tesserae[semantic]"`（`model2vec`）でオフライン・torch 不要のセマンティックレーンを使用。Cognee/RAG-Anything バックエンドはデフォルトで決定論的プロバイダを使用；より良いリコールのために Ollama や任意の OpenAI 互換エンドポイントに切り替え可能。

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` は存在すれば読み込まれますが、必須ではありません。

## 現状と制限

現在のリリースは[リリースノート](docs/release-notes/)を参照してください。既知の制限：

- 大規模コーパス（数千ファイル）の初回コンパイルには数分かかります；コンパイル時間はほぼ線形に増加します。増分コンパイル（`--changed-only`）は実装済みですが実験的でデフォルトはオフです。
- `semantic` エクストラなしでは、ハイブリッド検索が非セマンティックのスタブに劣化します（目立つ警告が表示されます）。
- RAG-Anything のビジョン機能（画像説明）はまだエンドツーエンドで繋がっていません。
- Cognee ランタイムの cognify はベストエフォート提供：不足しているプロバイダはログに記録されてスキップされ、致命的エラーにはなりません。
- MCP ツールセットは安定していますが、グラフスキーマはまだノードタイプが追加される可能性があります。

## プロジェクト構成

```text
tesserae/        # パッケージ本体（CLI、コンパイラ、エンジン、MCP サーバ、アダプタ）
docs/            # 英語ドキュメント + docs/i18n/ に他の 7 言語
ontology/        # コンパイラが検証するノード/エッジスキーマ
prompts/         # 抽出・合成プロンプト
tests/           # pytest テストスイート
evals/           # グラフ品質評価ハーネス
examples/        # スクリーンキャストで使用するデモコーパス
```

## ローカライズドキュメント

[English](./README.md) ·
[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

長文のドキュメントは `docs/i18n/` 以下にミラーされています。

## ライセンス

MIT。[LICENSE](LICENSE) を参照してください。
