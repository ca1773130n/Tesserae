# クイックスタート

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
このページは、既存のプロジェクトディレクトリから閲覧可能な Tesserae までの最短ルートを示します。

## コマンド概要

CLI はグループ化されています。トップレベルにいくつかの日常的な動詞があり、残りは
グループ（`sessions`、`vault`、`export`、`code`、`config`、`projects`、
`integrations`、`lab`）にまとめられています。ツリー全体を見るには `tesserae --help` を実行します。

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

個々のコマンドの flag を見るには、`tesserae <command> --help`（例: `tesserae compile --help`）を実行します。

## 1. セットアップウィザードの実行

インデックス化したいプロジェクトで:

```bash
cd /path/to/my-project
tesserae init
```

ウィザードは `README.md`、`docs`、`src`、`lib`、`app`、`packages`、`data` といった一般的な source を検出し、`.tesserae/config.json` を書き込みます。さらにデフォルトの Cognee backend を構成し、`tesserae ask` が Cognee を試したうえで、コンパイル済み wiki search に fallback できるようにします。

非対話的なセットアップ（CI、スクリプト）には `--yes` を渡すと、プロンプトなしで検出されたデフォルトを受け入れます。

```bash
tesserae init --yes
```

Understand Anything と Cognee runtime memory を有効にした完全自動セットアップ:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

各オプションの効果:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | UA graph projection を source として追加します。 |
| `--install-understand-anything` | UA companion skills をインストール/更新します。 |
| `--understand-anything-platform codex` | Codex を使って Tesserae 管理の UA refresh wrapper を実行します。 |
| `--with-raganything` | RAG-Anything による multimodal ingestion を有効にします。 |
| `--install-raganything` | セットアップ中に raganything[all] をインストールします。 |
| `--raganything-parser` | パーサーの選択: mineru（デフォルト）、docling、paddleocr。 |
| `--run-raganything` | compile のたびに RAG-Anything を自動 refresh します。 |
| `--run-cognee` | compile 中にベストエフォートで Cognee runtime cognify を実行します。 |
| `--install-cognee` | Cognee がなければ現在の Python でインストールします。 |

ユーザーは UA のインストールパスを知る必要も `/understand` と入力する必要もありません。UA graph が無いか古い場合、`tesserae compile` が `tesserae integrations refresh understand-anything` を実行します。

> **ウィザードをスキップ。** `tesserae init --bare` は source 検出や backend 探索を行わず、最小限の `.tesserae/config.json` だけを書き込みます。最初の compile 前に config を手で編集したいときに便利です。

## 2. グラフと projection のコンパイル

```bash
tesserae compile
```

`compile` は永続的な artifact を書き込みます。

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
  cognee_bundle/
```

初回実行後は `--changed-only` を使って変更されていない markdown ファイルをスキップしつつ、ファイル変更が無いときは前回の graph を保持できます。Understand Anything が有効なら、compile はまず `.tesserae/external/understand-anything.md` を refresh/materialize します。Cognee runtime が有効なら、`.tesserae/cognee_bundle/` を書き込んだ後にベストエフォートで Cognee も更新します。

構成済みの source に触れずに追加パスを ad-hoc で ingest するには、それらを位置引数として渡します: `tesserae compile path/to/extra.md docs/`。

### 統合関連のスイッチは config に移動しました

`tesserae compile` は意図的に日常的な flag（位置引数 paths と `--project`、
`--changed-only`、`--limit`、`--refresh-integrations`、`--sessions`/`--no-sessions`、
そして 3 つの LLM flag）に絞られています。それ以外の従来の compile flag はすべて
`.tesserae/config.json` の `compile_options` ブロックへ移りました。従来の argparse の
デフォルトが引き続き fallback です。挙動を変えるにはそこにキーを設定します。

| `compile_options` キー | 旧 flag | デフォルト | 説明 |
|---|---|---|---|
| `source_kind` | `--source-kind` | （なし） | 構成済みの source kind を上書きします。 |
| `trends` | `--trends` | `false` | corpus レベルの Trend ノードを追加します。 |
| `min_trend_sources` | `--min-trend-sources` | `2` | Trend ノード生成に必要な最小 source 数。 |
| `exclude_data` | `--exclude-data` | `false` | 暗黙の `project_root/data` 自動取り込みをスキップします。 |
| `no_vault_pull` | `--no-vault-pull` | `false` | compile 前に既存の vault 編集を pull しません。 |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | 以前の extraction 結果を今回の実行に再投入します。 |
| `sessions_llm` | `--sessions-llm` | （auto） | LLM セッション抽出モード（`auto`/`true`/`false`）。 |
| `sessions_model` | `--sessions-model` | （なし） | セッション抽出に使う LLM モデルを上書きします。 |
| `cognee_add` | `--cognee-add` | `false` | Cognee bundle を dataset に追加します（cognify なし）。 |
| `cognee_cognify` | `--cognee-cognify` | `false` | bundle を追加し Cognee cognify を実行します。 |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Cognee の LLM client を Codex にパッチして cognify を実行します。 |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | `cognee_codex_cognify` 用の Codex CLI モデル。 |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Codex CLI 呼び出しごとの timeout（秒）。 |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Cognee dataset 名。 |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Cognee lane の embedding provider。 |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Ollama embedding モデル。 |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Ollama `/api/embed` endpoint。 |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Ollama embedding リクエストの timeout（秒）。 |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | ローカル embedding の次元数。 |
| `cognee_system_root` | `--cognee-system-root` | （なし） | 隔離された Cognee system root ディレクトリ。 |
| `cognee_data_root` | `--cognee-data-root` | （なし） | 隔離された Cognee data root ディレクトリ。 |

> **ワンショットパイプライン。** `tesserae refresh` は全ループをプロセス内で実行します。新しい agent session の import、compile、vault の sync を 1 つのコマンドで行います。任意のインクリメンタル compile には `--changed-only` を渡します。

## 3. 静的フロントエンドのビルドと提供

`serve` は site が無ければ自動的にビルドするため、1 つのコマンドで閲覧可能な Tesserae が得られます。

```bash
tesserae serve --port 8765
```

開く:

```text
http://127.0.0.1:8765/
```

site を明示的にビルドするには（例: 提供せずにデプロイする場合）`export site` を使います。既にビルド済みの site をリビルドせずに閲覧したいときは、`serve` に `--no-build` を渡します。

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### 保存時の自動リビルド

開発サーバーを組み込みの watcher と組み合わせると、`data/` と `docs/` 配下の編集がインクリメンタル recompile をトリガーします。

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` は 2 秒ごとにポーリングし、1 秒の debounce 後に `compile --changed-only` を実行します。cron スタイルのリビルドには `--once`（スナップショットを `.tesserae/.watch-cache.json` と比較）、カスタム監視ディレクトリの追加には `--paths <dir>`、cadence の調整には `--interval` / `--debounce` を使います。
<!-- END: subagent-r-watch -->

### refresh デーモンの実行

ソースを監視し、編集のバーストをまとめ、自動で recompile して知識ベースを自ら常に最新に保つ常駐エンジンが欲しい場合は、監督付きデーモンを起動します。

```bash
tesserae engine
```

`engine` は長時間稼働する supervisor です。2 秒ごとにポーリングし、各リビルドの前に 1 秒の静穏ウィンドウを待ちます。cadence は `--interval` と `--debounce` で調整し、別プロジェクトを対象にするには `--project`、単一の決定的な drain サイクルを実行して終了するには（cron や CI に有用）`--once` を使います。これは `export site --watch` の無人版です。実行したままにしておけば、あなたとあなたの agent が作業する間、graph・vault・site が最新に保たれます。

表示されるすべてのルート（home、sources、concepts、entities、papers、repos、topics、syntheses、questions、timeline、graph、そして AI siblings）の注釈付きツアーは [`docs/frontend-redesign.md`](frontend-redesign.ja.md) を参照してください。

フロントエンドは依存が軽く、次を書き込みます。

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. ローカルの agent セッション履歴をインポート

セッション履歴のインポートは明示的です。通常の compile/build は正規化済みのセッションを読みますが、プライベートな Claude Code や Codex の transcript ストアを自分でスキャンすることはありません。

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

インポートされたセッションはグローバルな Sessions セクション、site 検索、ホームの Browse カードに表示されます。セッション詳細ページは user/assistant のターンを読みやすい markdown でレンダリングし、tool-use ブロックを直前の assistant ターンの下に付け、`#turn-N` ナビゲーション用の左側ターンレールを提供します。プライバシーに関する注意、インポート形式、現在の transcript タイポグラフィマップは [`docs/session-history.md`](session-history.ja.md) を参照してください。

## 5. wiki の lint

```bash
tesserae lint
```

コンパイル済みの graph + wiki + site を巡回し、orphan papers、stale citations、graph と wiki/ 間の drift、ghost synthesis inputs などを示します。`.tesserae/lint-report.md` と `.tesserae/lint-report.json` を書き込みます。安全な自動修正（欠落した `implemented_in` edges、ghost-input の刈り込み）を適用するには `--fix-trivial`、error のときだけ終了コードを失敗させるには `--severity error` を渡します。

## 6. wiki への問い合わせ

```bash
tesserae query "What is Gaussian Splatting?"
```

デフォルトは検索のみ — `.tesserae/site/search-index.json` 上の BM25 と、一致した `wiki/<kind>/<slug>.md` から取り出した 200 文字の抜粋を使います。絞り込むには `--kind papers`（または `concepts`、`repos` など）、広げるには `--top-k N`、構造化出力には `--json` を渡します。`[node_id]` 引用付きの合成された回答を Claude に求めるには `--llm` を追加する（または `TESSERAE_QUERY_LLM=1` を設定する）と良いでしょう。`--interactive` は readline REPL を開き、空行または EOF で終了します。`TESSERAE_QUERY_DRY_RUN=1` は API 呼び出しなしで prompt を試します。

## 7. オンデマンドで agent 向け context をコンパイル

v0.5.0 の目玉は On-Demand Context Compiler です。コンパイル済みの graph に、query にスコープされた単一の引用付き context ドキュメントを要求し、agent のウィンドウに収まるサイズに調整します。

```bash
tesserae context "How does session import work?"
```

query に一致するノードから Personalized PageRank を seed し（明示的に seed するには `--seeds <node_id>`）、近傍を拡張し（`--depth`、デフォルト 2）、文字数 `--budget`（デフォルト 32000、`<= 0` で無制限）で上限を設けた引用付きドキュメントを組み立てます。その上に LLM が書いた要約を追加するには `--synthesize`（LLM backend が必要）、ドキュメントを stdout ではなくディスクに書き込むには `-o/--output <file>` を使います。

同じ compiler は MCP 経由で `compile_context` ツールとして agent に公開されるため、コーディング agent は手動 export なしで、会話の途中でちょうど必要なだけの、budget で制限されたプロジェクト context を引き出せます。

## 8. agent harness ファイルのエクスポート

```bash
tesserae export harness
```

対応 target:

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

## 9. Obsidian vault のエクスポート

```bash
tesserae vault export
```

または既存の vault へ書き込む:

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

vault には markdown projections、`.obsidian` defaults、graph coloring、`raw/assets/`、Dataview dashboard が含まれます。既存の vault を最新の compile に合わせるには `tesserae vault sync` を使います（孤立したノートを削除するには `--prune` を追加）。

## 10. MCP の構成

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

出力を `~/.hermes/config.yaml` の `mcp_servers` の下に貼り付け、Hermes/gateway を再起動します。

## 11. Graphiti エクスポート / sync

依存なしの episode エクスポート:

```bash
tesserae export graphiti
```

Graphiti を入れずに dry-run sync のスモークテスト:

```bash
tesserae export graphiti --sync --dry-run
```

ライブ sync には `graphiti_core` と到達可能な Neo4j backend が必要です。

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. GitHub Pages へのデプロイ

`.tesserae/site/` のコンパイル済み site を、プロジェクトの git origin の `gh-pages` ブランチへ push します。

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` はまず `compile` を実行して site を最新にします。`--enable-pages` は `gh` CLI 経由で Pages を有効化します（冪等。`gh` が無ければヒントを出してスキップ）。push せずに stage と commit するには `--dry-run`、デフォルトを上書きするには `--branch` / `--remote`、作業ツリーが汚れていてもデプロイを許可するには `--force` を使います。

サイトは `https://<owner>.github.io/<repo>/` でアクセスできるようになります。
