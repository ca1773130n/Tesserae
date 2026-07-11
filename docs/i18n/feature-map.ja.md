# 機能マップ

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
このドキュメントは、Tesserae に現在実装されている機能を、ステータス、ソースファイル、そしてどこで文書化されているかとともにまとめたものです。

Tesserae は 3 つの柱で動く**コンテキストエンジン**です: (1) セッションモニタリング、(2) 自律的でプロアクティブなナレッジの取り込み、(3) オンデマンドのドキュメント/コンテキスト。型付きグラフ、vault、静的サイトはナレッジベースのプロジェクションです。以下の機能は、それぞれがどの柱に貢献するかでグループ化されています。**v0.5.0** マイルストーン（2026 年 6 月）でエンジンのスパインと、柱 3 の目玉機能であるオンデマンドコンテキストコンパイラが出荷されました。

ステータス凡例: ✅ 出荷済み · ⚠ 進行中 / 部分的。

## クロスプロジェクト & UX — v0.11.0（2026 年 6 月）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| クロスプロジェクトフェデレーション | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` は複数の登録済みプロジェクトから 1 つのグラフを組み立て — アイデンティティマージ（同一の arxiv/repo/hash/symbol）+ オプトアウト可能な埋め込みベースの `shares_concept_with` リンク — 和集合に対して単一の相互参照付き・引用付き回答を返します（PPR + `compile_context`）。プロジェクトごとの `graph.json` は読み取り専用。アイデンティティのみの場合は決定論的です。 |
| スマート `ask` ルーター（アクティブプロジェクトなし） | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | 「アクティブプロジェクト」の概念は削除されました — すべての登録済みプロジェクトは対等です。素の `ask` は自らルーティングします（プロジェクト名を挙げる → そのプロジェクト。比較 → federated。フォローアップ → ルートを維持。それ以外 → federated フォールバック）。オプションの LLM タイブレーカーと会話ごとの継続性があります。プロジェクトごとの操作は cwd からプロジェクトを解決します。 |
| フェデレーションの検査 | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status`（プロジェクトごとのノード数、アイデンティティマージ、セマンティックリンク）と `federation explain <node>`（あるノードがなぜプロジェクトを橋渡しするか）。 |
| マルチプロジェクト serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | 素の `tesserae serve` は登録済みのすべてのプロジェクトを 1 つのサーバーで配信します（`/` にランディング、各プロジェクトは `/<alias>/`、ヘッダーに Projects スイッチャー、パスは閉じ込め済み）。`--project X` はライブ ask ウィジェット付きで 1 つを配信します。 |
| `compile` の LLM コンセプトレイヤー | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` は設定済みプロバイダ（`llm_provider` に従い codex/claude/api）経由で、コンセプト/クレームレイヤーを**デフォルトで**構築します（`--extractor llm`）。`--extractor deterministic` は構造のみのバイト安定なオプトアウト。`selective-llm --llm-include … --llm-limit N` はコスト意識型です。 |
| `tesserae setup`（インタラクティブ） | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | トップレベルの `tesserae setup` — デフォルトでインタラクティブ（LLM プロバイダ/effort + インストールするオプション依存関係）。フラグでプロンプトをスキップできます。pip なしの uv-tool 環境でもインストールが機能します（uv-pip フォールバック）。 |

## 相互運用・検索・セットアップ — v0.10.0（2026 年 6 月）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| Google **OKF v0.1** インポート/エクスポート | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`。Markdown + YAML frontmatter のバンドル。`x_tesserae` 名前空間により Tesserae 自身のバンドルはロスレスでラウンドトリップし、外部のバンドルはベストエフォートです。 |
| 高速トランスクリプト検索（memex） | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | Claude/Codex トランスクリプトに対する `nicosuave/memex` の BM25 インデックス。`GET /api/transcript-search` 経由で `tesserae serve` の sessions ダッシュボードに接続。オプションであり、ない場合もグレースフルです。 |
| 読み取り規律ハンドル | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` の `preview=N` は境界付きプレビュー + コンテンツをキーとするハンドルを返し、`get_handle` が残りをページングします。巨大なペイロードをエージェントのコンテキストの外に保ちます。 |
| 抽出品質シグナル | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | 検出項目ごとの `confidence` + `confidence_rationale` + `revisit_signals`（バイト安定。`fresh_insights` で表面化）。 |
| マシン全体のセットアップ + 依存関係 | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` はグローバルな LLM デフォルトを書き込み、オプション依存関係（memex、cognee、raganything）をインストールします。`tesserae config deps` は一覧/インストール。`tesserae init` は memex を提案します。プロジェクトごとの config は引き続き優先されます。 |

## コンテキストエンジン — v0.5.0（2026 年 6 月）

3 つの柱を駆動するエンジンのスパイン。エンジンスパインのモジュールマップ、自己改善メモリサイドカー、コンテキストコンパイラのデータフローは [`docs/architecture.md`](architecture.ja.md) を参照してください。

### エンジンスパイン（柱 1 & 2）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| `Pipeline` — `List[StepResult]` を返す再利用可能なリフレッシュチェーン | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | CLI、デーモン、MCP のすべてが呼ぶ 1 つのステップランナー。ステップごとに `Exception` を捕捉。最初の失敗で停止します。 |
| `Daemon` — 単一所有者の asyncio スーパーバイザー | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | ソース + vault + harness セッションディレクトリを監視。デバウンスされたキャンセル & 再スケジュールにより、バーストを 1 回の `Pipeline.run()` にまとめます。Pidfile 対応。処理中の例外にも耐えます。 |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`、`--debounce`、`--once`。`daemon` は `engine` のエイリアスです。 |
| `project refresh` — 一連のチェーン（ingest → compile → project） | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only`（オプトインの増分）、`--no-sessions`。 |
| ライブセッションモニター → 検出項目 | ✅ | `harness_sessions.py` + セッショングラフモジュール | インポートされたセッションがグラフに供給されます。`fresh_insights` / `find_session_findings` がそれらを表面化します。 |

### 自己改善メモリ（柱 2）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| `node_memory` SQLite サイドカー（decay / confidence / superseded） | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + ストア非依存のアクセサ。可変状態のみ。first-seen は別の `node_provenance` サイドカーにあります。 |
| Ebbinghaus 減衰スコア | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | セッションの検出項目を、最新かつ最もアクセスされたものを先頭にランク付けします（`fresh_insights` を駆動）。 |
| Supersede パス（**デフォルトで有効**） | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | 決定論的な判定により、古い近似重複のインサイトを新しいものに置き換えられたとしてマークし、`supersedes` エッジを追加します。 |
| インサイト → コードシンボルのリンク | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | セッションのインサイトから、それが参照するシンボルへの `discusses` エッジ。 |
| 強化 + 矛盾検出パス | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 同じサイドカー上でのアクセス強化 + 矛盾検出。 |
| 出力における数値的な再出現 confidence | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Temporal facts は `NodeMemoryRow.confidence` から `confidence` を刻印し、`infer_confidence` にフォールバックします。 |

### 検索 + 埋め込み（柱 2 & 3）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| ハイブリッドリトリーバー（BM25 + レキシカル + 埋め込み、RRF k=60） | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | ローカルファーストで完全に決定論的。 |
| Personalized PageRank（HippoRAG-2） | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | マルチホップのシード展開。深さ制限付きサブグラフ。 |
| 実際のデフォルト埋め込み（Track B、Phase 6） | ✅ | `retrieval/hybrid.py` | デフォルト = 決定論的なハッシュバケット疑似埋め込み（依存関係なし）。`sentence-transformers`（`all-MiniLM-L6-v2`）が優先され、インストール済みなら遅延ロード。`embedding_status` MCP ツールがアクティブなバックエンドを報告します。 |

### オンデマンドコンテキストコンパイラ（柱 3 — 目玉）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| `compile_context` — 引用付きのインメモリ `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | シード解決 → PPR 展開 → 予算制限付き選択 → 引用付き markdown → オプションの LLM 合成。`synthesize=true` でない限り決定論的。ディスクには何も書きません。 |
| `project context` CLI | ✅ | `cli.py` | `[query]`、`--seeds`、`--depth`（2）、`--budget`（32000。≤0 = 上限なし）、`--llm`、`--output`。 |
| `compile_context` MCP ツール | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | 同じパイプラインを MCP 経由で。`budget=0` は上限なし。 |
| トピックスコープのエクスポートスライス | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `compile_context` 経由のトピックスコープの `llms.txt` + `render_harness_context`。 |

### 増分コンパイル（Phase 4 — 実験的）

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| 来歴サイドカー（`node_provenance`、first-seen） | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | changed-only 削除の基盤。常に記録されます。 |
| `GraphStore` の削除サーフェス | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`、`delete_nodes_by_source`（来歴集合が空になったノードを削除。ファイル横断のコンセプトは生き残ります）。 |
| `url_resolver` によるランタイムストアディスパッチ | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`。 |
| `incremental_compile` フラグ | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **デフォルト OFF / 実験的。** いくつかの編集形状ではバイトパリティが証明済みですが、マルチオーナー/プロデューサーライフサイクルのギャップが残っています。完全コンパイルがデフォルトのままです。 |

## フロントエンド再設計 — 2026 年 4 月

ドキュメントファーストの階層的 wiki が、古いグラフダンプを置き換えます。ルートごとのツアーは [`docs/frontend-redesign.md`](frontend-redesign.ja.md)、3 レイヤーモデルは [`docs/architecture.md`](architecture.ja.md) を参照してください。

### Wiki レイヤー（L2 markdown）

| 機能 | ステータス | ソース | ドキュメントアンカー |
|---|---|---|---|
| `WikiPageStore`（冪等な body-hash 書き込み、frontmatter パーサー） | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.ja.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — wiki レイヤーのノードごとに 1 つの md ページ | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.ja.md#pipeline) |
| `sources/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.ja.md#sources) |
| `concepts/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.ja.md#concepts) |
| `entities/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.ja.md#entities) |
| `papers/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.ja.md#papers) |
| `repos/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.ja.md#repos) |
| `topics/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.ja.md#topics) |
| `questions/` ページ（Open questions） | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.ja.md#questions) |
| `syntheses/` ページ | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.ja.md#syntheses) |

### Synthesis の種類（L2 → 派生）

`SynthesisProjector` は 7 つの決定論的テンプレートを生成し、`Synthesis` ノード + `synthesizes` / `summarizes` エッジをグラフに追加します。

| 種類 | ステータス | ソース | 備考 |
|---|---|---|---|
| `pulse`（グローバルに 1 つ、`/` を駆動） | ✅ | `synthesis.py` | コンパイルごとに再構築。 |
| `daily_digest` | ✅ | `synthesis.py` | `data/research/daily/<date>/` ごとに 1 つ。 |
| `weekly` | ✅ | `synthesis.py` | `data/research/weekly/<iso-week>/` ごとに 1 つ。 |
| `topic` | ✅ | `synthesis.py` | 3 論文以上の `ResearchTopic` / `ApproachFamily` クラスターごとに 1 つ。 |
| `comparison` | ✅ | `synthesis.py` | 同じタスクで競合する `ApproachFamily` のペアごとに 1 つ。 |
| `field_overview` | ✅ | `synthesis.py` | `ResearchField` ごとに 1 つ。 |
| LLM でアップグレードされた要約（環境変数フラグ） | ⚠ | フックのみ | ヒューリスティックなベースラインを出荷。`TESSERAE_SYNTHESIS_LLM=1` フックはスタブのままです。 |

### 静的サイトのルート

| ルート | ステータス | ソース | 備考 |
|---|---|---|---|
| `/`（home、ヒーローの pulse） | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | 統計行 + キュレーションされた入口 + 最近のアクティビティ。 |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | ヒートマップ + 日リスト + synthesis レール。 |
| `/timeline/<YYYY-MM-DD>.html`（日別詳細） | ⚠ | まだなし | 暫定として、ヒートマップのセルはその日の `digest.md` ソースページにリンクします。Subagent P が `StaticSiteBuilder` を通して日別詳細ページを接続中です。 |
| `/graph/`（インタラクティブな 2D + 3D） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js、ホバーツールチップ、エッジラベル、カーソル基準のズーム。 |
| `/about.html` | ✅ | `pages.py::render_about` | スキーマ、ビルド情報。 |

### AI フレンドリーなエクスポート

| 成果物 | ステータス | ソース | 目的 |
|---|---|---|---|
| ページごとの `<page>.txt` シブリング | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | 1 ページのプレーンテキストビュー（ナビなし、スタイルなし）。 |
| ページごとの `<page>.json` シブリング | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`。 |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org の短いインデックス。 |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | すべてのページ本文、5 MB 上限。 |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org の `Dataset`、wiki レイヤーのノードのみ。 |
| `graph.json` | ✅ | `__init__.py::write_site` | 完全なグラフペイロード（ツーリング用のコードノードを含む）。 |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | パレット + ページ検索。wiki レイヤーの種類のみ。 |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | 出力されたすべてのルート、`lastmod` は frontmatter から。 |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | 直近 30 の syntheses。 |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | 許容的 — クロール + インデックス。 |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | 機械可読なサイトマップ。 |
| `manifest.json` | ✅ | `__init__.py::_manifest` | 出力されたすべてのファイルの sha256 + サイズ（冪等性ハーネス）。 |

### ビジュアルデザイン + UX

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| デザイントークン（light + dark テーマ、テラコッタのアクセント） | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | `assets/style.css` に 1 つの CSS バンドル。 |
| テーマトグル（永続化、フラッシュなし） | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `localStorage` の `data-theme="dark"`、描画前に適用。 |
| 検索パレット（`cmd+k` / `ctrl+k` / `/`） | ✅ | `js.py` | `search-index.json` に対するファジーマッチ。最近のページリスト。 |
| スティッキーな右側 TOC | ✅ | `pages.py` + `tokens.py` | デスクトップのみ。モバイルは `<details>` によるドロワー。 |
| 月 + 曜日ラベル付きアクティビティヒートマップ | ✅ | `components.py::heatmap_svg` | 26 週の SVG、セルはその日の `digest.md` にリンク。 |
| スパークライン（concept/entity ごと） | ✅ | `components.py::sparkline_svg` | 週次の言及数、直近 12 週。 |
| モバイルシェル（ドロワーレール、ボトムナビ、可変タイポグラフィ） | ✅ | `tokens.py` + `pages.py` | タッチターゲットは 44 px 以上。 |
| ページトランジション（120 ms の不透明度、prefers-reduced-motion 対応） | ✅ | `tokens.py` | |
| 3D + 2D グラフビュー（ホバー、エッジラベル、カーソル基準のズーム） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js、CDN スナップショットとして vendoring。 |
| ページごとの AI シブリングフッター | ✅ | `components.py::ai_siblings_footer` | 現在のページの `.txt` と `.json` へのインラインリンク。 |
| Harness セッション履歴ページ | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 明示的な Claude Code/Codex インポート。`/sessions/` のインデックスと詳細ページには、markdown のターン、左側ターンレール、折りたたまれた tool use、検索エントリがあります。 |

### パイプライン + CLI

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| `project compile` が synthesis + wiki + site を順に呼ぶ | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | 再設計プランの Phase 3。 |
| `project build-site` スタンドアロン | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | `wiki/` + `graph.json` を読み、`site/` を書きます。 |
| `project serve` ローカル HTTP | ✅ | `cli.py` | 素の stdlib サーバー。 |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | `gh-pages` への worktree プッシュ。`gh` CLI によるオプションの `--enable-pages`。`--build`、`--dry-run`、`--branch`、`--remote`、`--force`。 |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Claude Code/Codex のインバウンドセッション履歴。discovery は明示的で、プロジェクトの作業ディレクトリにスコープされます。 |
| `project watch` 変更時リビルド | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | スタンドアロンのポーリングウォッチャー: `--interval`、`--debounce`、`--once`、`--paths`、`--quiet`。マルチソースのスーパーバイザーは `project engine`/`daemon` にあります（コンテキストエンジンを参照）。 |
| `project context` — 引用付きコンテキストドキュメントをコンパイル | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 柱 3 の目玉。コンテキストエンジンのセクションを参照。 |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | 一連のリフレッシュチェーン + スーパーバイザーループ。コンテキストエンジンのセクションを参照。 |

## 既存の機能（変更なしで引き継ぎ）

### CLI とインストール

- ✅ `pyproject.toml` によるインストール可能な Python パッケージ。
- ✅ コンソールコマンド: `tesserae`、`tesserae`、`tesserae_mcp`。
- ✅ `curl | bash` インストール用の `scripts/install.sh`。
- ✅ 高速なローカル開発のためのデフォルトの editable インストール。

### 抽出

- ✅ 制御されたノード/エッジ語彙を持つ決定論的な研究ノートエクストラクタ。
- ✅ API キーなしで高品質な構造化抽出を行う Claude CLI/OAuth エクストラクタ。
- ✅ glob と予算制限による選択的 Claude ルーティング。
- ✅ Python プロジェクト向けの決定論的な開発コードエクストラクタ。
- ✅ コンテンツハッシュと `--changed-only` サポート付きのバッチ取り込み。
- ✅ 不正な UTF-8 に耐性のあるソース読み取り。

### グラフガバナンス

- ✅ 制御された `ResearchNodeType` リスト — 現在は `SYNTHESIS` を含む。
- ✅ 制御されたエッジタイプのホワイトリスト — 現在は `synthesizes`、`summarizes` を含む。
- ✅ スキーマドリフトを拒否するバリデーション。
- ✅ エイリアスの正規化。
- ✅ 曖昧な近似重複ノードのためのレビューキュー。
- ✅ レビュー決定テンプレートと merge/keep-separate ワークフロー。
- ✅ ファイルごとのグラフからのコーパストレンド要約。

### 永続化とレポート

- ✅ グラフ JSON エクスポート。
- ✅ SQLite グラフストア。
- ✅ オプションの Kuzu グラフストア。
- ✅ カウント、エビデンスカバレッジ、孤立ノード、日付バケット、エイリアスの多いノードを含むグラフレポート。
- ✅ MegaMem、Graphiti/Zep、MCP グラフサーバー、agentic RAG から吸収したアイデアを記述する競合レポート。

### プロジェクトローカルのワークフロー

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy`（GitHub Pages）
- ✅ `tesserae sessions discover/import/list`（明示的なローカルエージェント履歴のインポート）
- ✅ `tesserae export site --watch`（スタンドアロンのポーリングウォッチャー）
- ✅ `tesserae engine`（スーパーバイザーループ — v0.5.0）
- ✅ `tesserae refresh`（一連の ingest → compile → project チェーン — v0.5.0）
- ✅ `tesserae context`（オンデマンドコンテキストコンパイラ — v0.5.0）
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ すぐに開ける vault エクスポート。
- ✅ `.obsidian/app.json` とグラフ設定。
- ✅ Markdown プロジェクション。
- ✅ `raw/assets/` 構造。
- ✅ Dataview クエリ付きの `_meta/dashboard.md`。

### エージェントハーネス

以下のターゲットファイルを生成します:

- ✅ Claude Code: `CLAUDE.md`、`.claude/settings.json`
- ✅ Codex: `AGENTS.md`、`mcp.toml`
- ✅ Gemini: `GEMINI.md`、`.gemini/settings.json`
- ✅ Kiro: steering と MCP 設定
- ✅ Cursor: プロジェクトルールと MCP 設定
- ✅ OpenCode: `AGENTS.md`、`opencode.json`

### Graphiti / temporal facts

- ✅ 来歴、現在性、confidence、無効化フィールドを持つ temporal fact プロジェクション。
- ✅ 依存関係なしの Graphiti エピソード JSONL エクスポート。
- ✅ Graphiti をインストールせずに行う `sync-graphiti --dry-run` スモーク。
- ✅ `graphiti_core` と Neo4j によるオプションのライブ同期。

### Cognee

- ✅ Cognee JSONL バンドル（`nodes.jsonl`、`edges.jsonl`、`manifest.json`）。
- ✅ オプションの add-only 直接インポート。
- ✅ オプションの Codex CLI/OAuth ベースの Cognee cognify アダプター。
- ✅ API キーなしのスモーク/品質ワークフローのための決定論的および Ollama 埋め込みアダプターパス。

### MCP サーバー

- ✅ stdio JSON-RPC 上の `tesserae_mcp` / `python3 -m tesserae.mcp_server`。
- ✅ 検索/グラフツール: `schema`、`graph_summary`、`search_nodes`、`node_context`（`use_ppr` 付き）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`、`doctor_report`。
- ✅ コンテキストエンジンツール（v0.5.0）: `compile_context`、`embedding_status`、`fresh_insights`（減衰ランク付き）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`、`ask`。
- ✅ セットアップツール: `tesserae_setup_plan`、`tesserae_setup_apply`。
- ✅ マルチプロジェクトレジストリ: `list_projects`、`register_project`、`unregister_project`、`list_sessions`。`url_resolver` によるストア URL ディスパッチ。

## テスト

現在のスイートは以下をカバーします:

- ✅ オントロジーのガードレール（新しい `Synthesis` ノード + `synthesizes` / `summarizes` エッジを含む）;
- ✅ 決定論的抽出;
- ✅ Claude CLI ラッパーのパース/バリデーション;
- ✅ 選択的 Claude ルーティング;
- ✅ 正規化/レビューワークフロー;
- ✅ バッチ取り込み;
- ✅ レポート;
- ✅ SQLite/Kuzu の永続化;
- ✅ Cognee のバンドル/インポートパッチ;
- ✅ Graphiti のエクスポート/同期ドライラン;
- ✅ プロジェクト CLI ワークフロー;
- ✅ エージェントハーネスのエクスポート;
- ✅ Obsidian エクスポート;
- ✅ フロントエンド生成 + リンク整合性（`nodes/codeclass-*.html` なし）;
- ✅ wiki ストアの冪等性;
- ✅ synthesis プロジェクターのゴールデン + 冪等性;
- ✅ サイトのコンポーネント、ページ、エクスポート、関連性;
- ✅ AI シブリングの形状（ページごとの `.txt` + `.json`）;
- ✅ エンドツーエンドの 2 回コンパイル冪等性;
- ✅ エンジンスパイン: パイプライン、リフレッシュチェーン、デーモンのコア + ソース、`project engine` CLI;
- ✅ 自己改善メモリ: サイドカー、decay/supersede、supersede の抑制（MCP を含む）、reinforce/contradiction;
- ✅ 検索 + 埋め込み: ハイブリッド検索、PPR、実際のデフォルト埋め込み（Phase 6）;
- ✅ コンテキストコンパイラ: 形状/引用整合性/決定論性/予算/PPR フォールバック、`project context` CLI、MCP `compile_context`;
- ✅ 増分コンパイル（実験的）: differ、パリティゲート、来歴の準備状態、SQLite の来歴;
- ✅ パッケージインストールとインストーラの契約。
