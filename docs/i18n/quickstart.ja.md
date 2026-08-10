# クイックスタート

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
このページでは、既存のプロジェクトディレクトリから閲覧可能な Tesserae に至る最短経路を示します。

## コマンド概要

CLI はグループ化されています: 最上位にはいくつかの日常的な動詞、残りはグループ
（`sessions`、`vault`、`export`、`code`、`config`、`projects`、`agents`、`domains`、`integrations`、
`lab`）です。`tesserae --help` を実行するとツリー全体が表示されます:

```text
tesserae 0.31.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

個々のコマンドのフラグは `tesserae <command> --help`（例: `tesserae compile --help`）で
確認できます。

## 1. セットアップウィザードを実行する

インデックスしたいプロジェクトから:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` は唯一のオンボーディングステップです。ウィザードは `README.md`、`docs`、`src`、`lib`、`app`、`packages`、`data` などの一般的なソースを検出し、どの LLM CLI がインストール済みで**かつログイン済み**かを調べ、LLM プロバイダを選択させ、`.tesserae/config.json` を書き込みます。オプションの RAG-Anything メモリバックエンドは**デフォルトで無効**です。後で config の `memory_backends` で有効化し、`tesserae query --backend raganything` で明示的にクエリしてください。

非インタラクティブなセットアップ（CI、スクリプト）では、`--yes` を渡すとプロンプトなしで
検出されたデフォルトを受け入れます（すべてのオプション統合は OFF）:

```bash
tesserae init --yes
```

### LLM プロバイダの設定

ウィザードでのプロバイダ選択（または同等のフラグ）は、以下の config キーを永続化します:

| Config キー | フラグ | 内容 |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | LLM クライアントのバックエンド: `claude`/`codex` は OAuth でログイン済みの CLI を使用。`anthropic` は API を直接使用。`custom` は任意の claude 互換エンドポイントを対象とする。 |
| `llm_model` | `--llm-model` | synthesis/insights LLM クライアントのモデル。 |
| `llm_base_url` | `--llm-base-url` | `anthropic`/`custom` 用のエンドポイントベース URL。 |
| `llm_api_key` | `--llm-api-key` | `anthropic`/`custom` 用の API キー。 |

> **平文の警告。** `llm_api_key` は `.tesserae/config.json` に**平文**で
> 保存されます。代わりに環境変数を優先してください:
> `ANTHROPIC_API_KEY`（キー）、`ANTHROPIC_BASE_URL`（エンドポイント）、
> `TESSERAE_LLM_MODEL`（モデル）。解決順序は env → プロジェクト config →
> マシン全体の config（`~/.tesserae/config.json`、`tesserae setup` が書き込む）
> → 組み込みデフォルトです。

既存のプロジェクトで `init` を再実行すると**マージ**されます — 設定済みの `sources`
と `memory_backends` は保持され、上書きされません。

非インタラクティブなプロバイダ設定の例:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

> **ウィザードをスキップする。** `tesserae init --bare` は、ソース検出やバックエンドの
> プローブを行わずに最小限の `.tesserae/config.json` を書き込みます — 最初のコンパイルの前に
> config を手で編集したいときに便利です。

## 2. グラフとプロジェクションをコンパイルする

```bash
tesserae compile
```

`compile` は永続的な成果物を書き込みます:

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
```

初回実行後は `--changed-only` を使うと、変更されていない markdown ファイルをスキップし、ファイルに変更がない場合は以前のグラフを保持します。

設定済みのソースに触れずに追加パスをアドホックに取り込むには、位置引数として渡します:
`tesserae compile path/to/extra.md docs/`。

### 統合のつまみは今や config にあります

`tesserae compile` は意図的に日常的なフラグに限定されています（位置引数のパスに加え、
`--project`、`--changed-only`、`--limit`、`--refresh-integrations`、
`--sessions`/`--no-sessions`、そして 3 つの LLM フラグ）。それ以外のかつての compile
フラグはすべて `.tesserae/config.json` の `compile_options` ブロックに移動しました。
古い argparse のデフォルトは引き続きフォールバックです。挙動を変えるには、そこにキーを設定します:

| `compile_options` キー | 旧フラグ | デフォルト | 動作 |
|---|---|---|---|
| `source_kind` | `--source-kind` | (none) | 設定されたソース種別を上書きする。 |
| `trends` | `--trends` | `false` | コーパスレベルの Trend ノードを追加する。 |
| `min_trend_sources` | `--min-trend-sources` | `2` | Trend ノードに必要な最小ソース数。 |
| `exclude_data` | `--exclude-data` | `false` | 暗黙の `project_root/data` 自動インクルードをスキップする。 |
| `no_vault_pull` | `--no-vault-pull` | `false` | コンパイル前に既存の vault 編集をプルバックしない。 |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 以前の抽出結果を実行にフィードバックする。 |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM セッション抽出モード（`auto`/`true`/`false`）。 |
| `sessions_model` | `--sessions-model` | (none) | セッション抽出に使う LLM モデルを上書きする。 |

> **Cognee は 0.19 で削除されました。** cognee バックエンドは 0.18 で降格され、
> グラフに寄与することはありませんでした。`memory_backends.cognee` セクション
> （または `cognee_*` の compile オプション）をまだ含む config も引き続き読み込めます —
> そのセクションは 1 行の注記とともに無視されます。

> **ワンショットパイプライン。** `tesserae refresh` はループ全体をインプロセスで実行します — 新しいエージェントセッションのインポート、コンパイル、vault の同期を 1 つのコマンドで行います。オプトインの増分コンパイルには `--changed-only` を渡してください。

## 3. 静的フロントエンドをビルドして配信する

`serve` はサイトが存在しない場合に自動でビルドするため、1 つのコマンドで閲覧可能な
Tesserae が手に入ります。**素の `serve` は登録済みのすべてのプロジェクトを** 1 つの
サーバーで配信します — `/` にプロジェクトのランディング、各プロジェクトは `/<alias>/`、
そしてヘッダーの Projects スイッチャーで行き来できます。ページ内の **ask ウィジェットは
どちらのモードでもライブで動作**し、表示中のページのプロジェクトにルーティングされます:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

開く:

```text
http://127.0.0.1:8765/
```

サイトを明示的にビルドするには（例: 配信せずデプロイする場合）`export site` を使います。
以前ビルドしたサイトを再ビルドせずに閲覧したい場合は、`serve` に `--no-build` を
渡してください:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 保存時の自動リビルド

開発サーバーを組み込みのウォッチャーと組み合わせると、`data/` と `docs/` 配下の編集が増分再コンパイルをトリガーします:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` は 2 秒ごとにポーリングし、1 秒のデバウンスを行い、`compile --changed-only` を実行します。cron スタイルのリビルドには `--once` を（`.tesserae/.watch-cache.json` に対するスナップショット比較）、カスタムの監視ディレクトリの追加には `--paths <dir>` を、頻度の調整には `--interval` / `--debounce` を使ってください。
<!-- END: subagent-r-watch -->

### リフレッシュデーモンを実行する

ナレッジベースを自律的に新鮮に保つ常時稼働のエンジン — ソースを監視し、編集のバーストをまとめ、自動的に再コンパイルする — には、監督付きデーモンを起動します:

```bash
tesserae engine
```

`engine` は長時間稼働するスーパーバイザーです: 2 秒ごとにポーリングし、各リビルドの前に 1 秒の静穏ウィンドウを待ちます。頻度は `--interval` と `--debounce` で調整し、`--project` で別のプロジェクトを指し、`--once` を渡すと単一の決定論的なドレインサイクルを実行して終了します（cron や CI に便利）。これは `export site --watch` の「手放し」版です: 動かしたままにしておけば、あなたとエージェントが作業する間、グラフ、vault、サイトが最新に保たれます。

表示されるすべてのルート — home、sources、concepts、entities、papers、repos、topics、syntheses、questions、timeline、graph、そして AI siblings — の注釈付きツアーは [`docs/frontend-redesign.md`](frontend-redesign.ja.md) を参照してください。

フロントエンドは依存関係が軽く、以下を書き込みます:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. ローカルのエージェントセッション履歴をインポートする

セッション履歴のインポートは明示的です: 通常の compile/build はすでに正規化されたセッションを読み取りますが、プライベートな Claude Code や Codex のトランスクリプトストアを勝手にスキャンすることはありません。

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

インポートされたセッションはグローバルな Sessions セクション、サイト検索、ホームの Browse カードに表示されます。セッション詳細ページは user/assistant のターンを読みやすい markdown でレンダリングし、tool-use ブロックを直前の assistant ターンの下に付け、`#turn-N` ナビゲーション用の左側ターンレールを提供します。プライバシーに関する注意、インポート形式、現在のトランスクリプトタイポグラフィマップは [`docs/session-history.md`](session-history.ja.md) を参照してください。

## 5. wiki を lint する

```bash
tesserae lint
```

コンパイル済みのグラフ + wiki + サイトを走査し、孤立した論文、古くなった引用、グラフと wiki/ の間のドリフト、ゴーストの synthesis 入力などをフラグします。`.tesserae/lint-report.md` と `.tesserae/lint-report.json` を書き込みます。`--fix-trivial` を渡すと安全な自動修正（欠落した `implemented_in` エッジ、ゴースト入力の剪定）を適用し、`--severity error` を渡すとエラーの場合にのみ終了コードを失敗させます。

グラフ自体を超えたワークスペースの健全性 — レジストリの一貫性、鮮度、ロック、LLM ログイン、衛生状態 — については `tesserae doctor` を実行してください（`--fix` は安全な修復のみを適用）。[`docs/doctor.md`](doctor.ja.md) を参照してください。

## 6. wiki に ask / query する

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` は回答のためのインターフェースです: モデルがコンパイル済みグラフに対する検索を計画し、引用付きの回答を合成します。ログイン済みの `claude`/`codex` CLI（OAuth）または `ANTHROPIC_API_KEY` で動作します。`--no-llm` を渡すとランク付けされた検索ヒットのみになります（この強制オフは `TESSERAE_QUERY_LLM=1` に優先します）。`TESSERAE_QUERY_DRY_RUN=1` は API 呼び出しなしでプロンプトを検証します。

`query` は検索のためのインターフェースです: `.tesserae/site/search-index.json` に対する BM25/セマンティック検索で、マッチした `wiki/<kind>/<slug>.md` から 200 文字の抜粋を取得します。`--kind papers`（または `concepts`、`repos` など）で絞り込み、`--top-k N` で広げ、`--json` で構造化出力を得られます。`--interactive` は readline の REPL を開きます — 空行または EOF で終了します。明示的なメモリバックエンドもここにあります: `--backend raganything` はそのバックエンドに直行し、そのエラーを表面化します。`query` に LLM 合成はありません — それは `ask` の役割です。

## 7. エージェント対応コンテキストをオンデマンドでコンパイルする

v0.5.0 の目玉はオンデマンドコンテキストコンパイラです: コンパイル済みグラフに対して、クエリにスコープされ、エージェントのウィンドウに収まるサイズの、引用付きの単一コンテキストドキュメントを要求できます。

```bash
tesserae context "How does session import work?"
```

クエリにマッチするノードから Personalized PageRank をシードし（`--seeds <node_id>` で明示的にシード）、近傍を展開し（`--depth`、デフォルト 2）、文字数の `--budget`（デフォルト 32000。`<= 0` を渡すと上限なし）でキャップされた引用付きドキュメントを組み立てます。`--llm` を追加すると LLM による要約が上に付きます（LLM バックエンドが必要）。`-o/--output <file>` で stdout の代わりにディスクへ書き込みます。

同じコンパイラは MCP 経由で `compile_context` ツールとしてエージェントに公開されているため、コーディングエージェントは手動エクスポートなしで、会話の途中で必要十分な、予算制限付きのプロジェクトコンテキストを取得できます。

## 8. エージェントハーネスファイルをエクスポートする

```bash
tesserae export harness
```

サポートされるターゲット:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

サブセットの例:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Obsidian vault をエクスポートする

```bash
tesserae vault export
```

または既存の vault に書き込む:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

vault には markdown プロジェクション、`.obsidian` のデフォルト、グラフの色付け、`raw/assets/`、Dataview ダッシュボードが含まれます。既存の vault を最新のコンパイルと整合させるには `tesserae vault sync` を使ってください（孤立ノートを削除するには `--prune` を追加）。

## 10. MCP を設定する

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

出力を `~/.hermes/config.yaml` の `mcp_servers` の下に貼り付け、Hermes/gateway を再起動します。

## 11. Graphiti のエクスポート / 同期

依存関係なしのエピソードエクスポート:

```bash
tesserae export graphiti
```

Graphiti をインストールせずに行うドライラン同期のスモーク:

```bash
tesserae export graphiti --sync --dry-run
```

ライブ同期には `graphiti_core` と到達可能な Neo4j バックエンドが必要です:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. GitHub Pages にデプロイする

`.tesserae/site/` のコンパイル済みサイトを、プロジェクトの git origin の `gh-pages` ブランチにプッシュします:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` は先に `compile` を実行するため、サイトは新鮮です。`--enable-pages` は `gh` CLI 経由で Pages を有効にします（冪等。`gh` がない場合はヒントとともにスキップ）。プッシュせずにステージとコミットのみ行うには `--dry-run` を、デフォルトの上書きには `--branch` / `--remote` を、作業ツリーがダーティな状態でのデプロイを許可するには `--force` を使ってください。

サイトは `https://<owner>.github.io/<repo>/` で到達可能になります。
