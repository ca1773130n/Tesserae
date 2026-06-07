# 機能マップ

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
このドキュメントは、Tesserae に現在実装されている機能を、状態、ソースファイル、ドキュメント上の場所とともにまとめたものです。

Tesserae は 3 本の柱の上で動作する**コンテキスト エンジン**です。(1) セッション監視、(2) 自律的・能動的なナレッジ取り込み、(3) オンデマンド ドキュメント/コンテキスト。型付きグラフ、ボールト、静的サイトはナレッジ ベースの投影です。以下の機能は、どの柱に資するかで分類しています。**v0.5.0** マイルストーン（2026 年 6 月）はエンジン スパインと、柱 3 の目玉機能であるオンデマンド コンテキスト コンパイラを出荷しました。

状態凡例: ✅ 出荷済み · ⚠ 進行中 / 部分実装。

## コンテキスト エンジン — v0.5.0 (2026 年 6 月)

3 本の柱を駆動するエンジン スパイン。エンジン スパインのモジュール マップ、自己改善メモリ サイドカー、コンテキスト コンパイラのデータフローについては [`docs/architecture.md`](architecture.ja.md) を参照してください。

### エンジン スパイン（柱 1 & 2）

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| `Pipeline` — `List[StepResult]` を返す再利用可能なリフレッシュ チェーン | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | CLI、デーモン、MCP がすべて呼び出す 1 つのステップ ランナー。ステップごとに `Exception` を捕捉し、最初の失敗で停止。 |
| `Daemon` — 単一所有者の asyncio スーパーバイザー | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | ソース + ボールト + ハーネス セッション ディレクトリを監視; デバウンスされたキャンセル・再スケジュールが一連を 1 回の `Pipeline.run()` にまとめる。pidfile; 実行中の例外でも生存。 |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`、`--debounce`、`--once`。`daemon` は `engine` のエイリアス。 |
| `project refresh` — 散文的チェーン（取り込み → コンパイル → 投影） | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only`（オプトイン増分）、`--skip-sessions`。 |
| ライブ セッション監視 → 発見 | ✅ | `harness_sessions.py` + セッション グラフ モジュール | 取り込まれたセッションがグラフに供給される; `fresh_insights` / `find_session_findings` が表面化。 |

### 自己改善メモリ（柱 2）

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| `node_memory` SQLite サイドカー（減衰 / 信頼度 / 取って代わられた） | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + ストア非依存アクセサ; 可変状態のみ。初回観測は別の `node_provenance` サイドカーに存在。 |
| エビングハウス減衰スコア | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | セッション発見を最新 + 最もアクセスされた順にランク付け（`fresh_insights` を駆動）。 |
| 取って代わりパス（**既定 ON**） | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | 決定的判定が古い近似重複インサイトを新しいものに取って代わられたと印付け; `supersedes` エッジを追加。 |
| インサイト → コード シンボル リンク | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | セッション インサイトから参照シンボルへの `discusses` エッジ。 |
| 強化 + 矛盾パス | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py)、[`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 同じサイドカーに対するアクセス強化 + 矛盾検出。 |
| 出力の数値再発信頼度 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | 時間事実が `confidence` を `NodeMemoryRow.confidence` からスタンプし、なければ `infer_confidence` にフォールバック。 |

### 検索 + 埋め込み（柱 2 & 3）

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| ハイブリッド検索器（BM25 + 語彙 + 埋め込み、RRF k=60） | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | ローカル優先、完全に決定的。 |
| パーソナライズド PageRank（HippoRAG-2） | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | マルチホップ シード展開; 深さ制限付きサブグラフ。 |
| 実際の既定埋め込み（Track B、フェーズ 6） | ✅ | `retrieval/hybrid.py` | 既定 = 決定的ハッシュ バケット疑似埋め込み（依存なし）; インストール時は `sentence-transformers`（`all-MiniLM-L6-v2`）を優先し遅延ロード。`embedding_status` MCP ツールがアクティブ バックエンドを報告。 |

### オンデマンド コンテキスト コンパイラ（柱 3 — 目玉）

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| `compile_context` — 引用付きメモリ内 `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | シード解決 → PPR 展開 → 予算制約付き選択 → 引用付き Markdown → オプションの LLM 合成。`synthesize=true` でない限り決定的。ディスクには書き込まない。 |
| `project context` CLI | ✅ | `cli.py` | `[query]`、`--seeds`、`--depth`（2）、`--budget`（32000; ≤0 = 無制限）、`--synthesize`、`--output`。 |
| `compile_context` MCP ツール | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP 経由の同じパイプライン; `budget=0` は無制限。 |
| トピック範囲のエクスポート スライス | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | トピック範囲の `llms.txt` + `compile_context` 経由の `render_harness_context`。 |

### 増分コンパイル（フェーズ 4 — 実験的）

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| 来歴サイドカー（`node_provenance`、初回観測） | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | 変更分削除の基盤; 常に記録。 |
| `GraphStore` 削除面 | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`、`delete_nodes_by_source`（来歴集合が空になるノードを削除; ファイル横断的な概念は生存）。 |
| `url_resolver` 実行時ストア ディスパッチ | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`。 |
| `incremental_compile` フラグ | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **既定 OFF / 実験的。** いくつかの編集形態でバイト一致が実証済みだが、複数所有者/プロデューサー ライフサイクルのギャップが残るため、フル コンパイルが既定のまま。 |

## フロントエンド再設計 — 2026年4月

従来のグラフダンプを、ドキュメント中心の階層型 wiki が置き換えます。ルートごとのツアーは [`docs/frontend-redesign.md`](frontend-redesign.ja.md)、3層モデルは [`docs/architecture.md`](architecture.ja.md) を参照してください。

### Wiki レイヤー (L2 markdown)

| 機能 | 状態 | ソース | ドキュメントアンカー |
|---|---|---|---|
| `WikiPageStore`（冪等な body-hash 書き込み、frontmatter パーサー） | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § モジュールマップ](architecture.ja.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — wiki レイヤーノードごとに 1 つの md ページ | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § パイプライン](architecture.ja.md#pipeline) |
| `sources/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.ja.md#sources) |
| `concepts/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.ja.md#concepts) |
| `entities/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.ja.md#entities) |
| `papers/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.ja.md#papers) |
| `repos/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.ja.md#repos) |
| `topics/` ページ | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.ja.md#topics) |
| `questions/` ページ（未解決の質問） | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.ja.md#questions) |
| `syntheses/` ページ | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.ja.md#syntheses) |

### 合成の種類 (L2 → 派生)

`SynthesisProjector` は 7 つの決定的テンプレートを生成し、`Synthesis` ノードと `synthesizes` / `summarizes` エッジをグラフへ戻します。

| 種類 | 状態 | ソース | 注記 |
|---|---|---|---|
| `pulse`（グローバルに 1 つ、`/` を駆動） | ✅ | `synthesis.py` | compile のたびに再構築されます。 |
| `daily_digest` | ✅ | `synthesis.py` | `data/research/daily/<date>/` ごとに 1 つ。 |
| `weekly` | ✅ | `synthesis.py` | `data/research/weekly/<iso-week>/` ごとに 1 つ。 |
| `topic` | ✅ | `synthesis.py` | 3 本以上の papers を含む `ResearchTopic` / `ApproachFamily` クラスターごとに 1 つ。 |
| `comparison` | ✅ | `synthesis.py` | 同じタスクで競合する `ApproachFamily` のペアごとに 1 つ。 |
| `field_overview` | ✅ | `synthesis.py` | `ResearchField` ごとに 1 つ。 |
| LLM 強化サマリー（環境フラグ） | ⚠ | hook のみ | ヒューリスティックなベースラインは出荷済み。`TESSERAE_SYNTHESIS_LLM=1` hook は stub として残されています。 |

### 静的サイトルート

| ルート | 状態 | ソース | 注記 |
|---|---|---|---|
| `/`（ホーム、hero pulse） | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | 統計行 + キュレーション済み入口 + 最近の活動。 |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | ヒートマップ + 日付リスト + synthesis レール。 |
| `/timeline/<YYYY-MM-DD>.html`（日別詳細） | ⚠ | まだ n/a | ヒートマップセルは暫定的にその日の `digest.md` ソースページへリンクします。Subagent P が `StaticSiteBuilder` 経由で日別詳細ページを配線中です。 |
| `/graph/`（インタラクティブ 2D + 3D） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js、ホバーツールチップ、エッジラベル、カーソル基準ズーム。 |
| `/about.html` | ✅ | `pages.py::render_about` | スキーマ、ビルド情報。 |

### AI フレンドリーなエクスポート

| アーティファクト | 状態 | ソース | 目的 |
|---|---|---|---|
| ページごとの `<page>.txt` 兄弟ファイル | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | 1 ページのプレーンテキスト表示（ナビゲーション、スタイルなし）。 |
| ページごとの `<page>.json` 兄弟ファイル | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`。 |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org の短いインデックス。 |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | 全ページ本文、5 MB 上限。 |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`、wiki レイヤーノードのみ。 |
| `graph.json` | ✅ | `__init__.py::write_site` | 完全なグラフペイロード（ツール用コードノードを含む）。 |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | パレット + ページ検索。wiki レイヤー種別のみ。 |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | 出力された全ルート、`lastmod` は frontmatter 由来。 |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | 最新 30 件の syntheses。 |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | 許可的 — クロール + インデックス。 |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | 機械可読サイトマップ。 |
| `manifest.json` | ✅ | `__init__.py::_manifest` | 出力された各ファイルの sha256 + サイズ（冪等性ハーネス）。 |

### ビジュアルデザイン + UX

| 機能 | 状態 | ソース | 注記 |
|---|---|---|---|
| デザイントークン（ライト + ダークテーマ、テラコッタのアクセント） | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | `assets/style.css` の単一 CSS バンドル。 |
| テーマ切替（永続化、ちらつきなし） | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `localStorage` 内の `data-theme="dark"`、描画前に適用。 |
| 検索パレット（`cmd+k` / `ctrl+k` / `/`） | ✅ | `js.py` | `search-index.json` に対するファジーマッチ。最近ページリスト。 |
| 右側固定 TOC | ✅ | `pages.py` + `tokens.py` | デスクトップのみ。モバイルは `<details>` によるドロワー。 |
| 月 + 曜日ラベル付き活動ヒートマップ | ✅ | `components.py::heatmap_svg` | 26 週 SVG、セルはその日の `digest.md` へリンク。 |
| Sparkline（概念/エンティティごと） | ✅ | `components.py::sparkline_svg` | 週次メンション数、直近 12 週。 |
| モバイルシェル（ドロワーレール、下部ナビ、流動的な文字サイズ） | ✅ | `tokens.py` + `pages.py` | タッチターゲット ≥ 44 px。 |
| ページ遷移（120 ms 不透明度、prefers-reduced-motion） | ✅ | `tokens.py` | |
| 3D + 2D グラフ表示（ホバー、エッジラベル、カーソル基準ズーム） | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js、CDN スナップショットとして vendored。 |
| ページごとの AI 兄弟ファイルフッター | ✅ | `components.py::ai_siblings_footer` | 現在ページの `.txt` と `.json` へのインラインリンク。 |
| ハーネスセッション履歴ページ | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | 明示的な Claude Code/Codex インポート。markdown ターン、左側ターンレール、折りたたみツール使用、検索エントリを備えた `/sessions/` インデックスと詳細ページ。 |

### パイプライン + CLI

| 機能 | 状態 | ソース | 注記 |
|---|---|---|---|
| `project compile` は synthesis + wiki + site を順に呼び出す | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | 再設計計画のフェーズ 3。 |
| `project build-site` 単体実行 | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | `wiki/` + `graph.json` を読み、`site/` を書き出す。 |
| `project serve` ローカル HTTP | ✅ | `cli.py` | 素の stdlib サーバー。 |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | `gh-pages` への worktree push。`gh` CLI 経由の任意 `--enable-pages`。`--build`, `--dry-run`, `--branch`, `--remote`, `--force`。 |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Claude Code/Codex 用のインバウンドセッション履歴。発見は明示的で、プロジェクト作業ディレクトリにスコープされる。 |
| `project watch` 変更時再ビルド | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | スタンドアロンのポーリング watcher: `--interval`、`--debounce`、`--once`、`--paths`、`--quiet`。マルチソース スーパーバイザーは `project engine`/`daemon` にあり（コンテキスト エンジン参照）。 |
| `project context` — 引用付きコンテキスト ドキュメントをコンパイル | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 柱 3 の目玉; コンテキスト エンジンの節を参照。 |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | 散文的リフレッシュ チェーン + スーパーバイザー ループ; コンテキスト エンジンの節を参照。 |

## 既存機能（変更なしで継続）

### CLI とインストール

- ✅ `pyproject.toml` によるインストール可能な Python パッケージ。
- ✅ コンソールコマンド: `tesserae`, `tesserae`, `tesserae_mcp`。
- ✅ `curl | bash` インストール用の `scripts/install.sh`。
- ✅ 高速なローカル開発のため、デフォルトで editable install。

### 抽出

- ✅ 制御されたノード/エッジ語彙を持つ決定的な研究ノート抽出器。
- ✅ API キーなしで高品質な構造化抽出を行う Claude CLI/OAuth 抽出器。
- ✅ glob と予算上限による選択的 Claude ルーティング。
- ✅ Python プロジェクト向けの決定的な開発コード抽出器。
- ✅ コンテンツハッシュと `--changed-only` 対応のバッチ ingest。
- ✅ 不正な UTF-8 に寛容なソース読み取り。

### グラフガバナンス

- ✅ 制御された `ResearchNodeType` リスト — 現在は `SYNTHESIS` を含む。
- ✅ 制御されたエッジタイプ許可リスト — 現在は `synthesizes`, `summarizes` を含む。
- ✅ スキーマドリフトを拒否する検証。
- ✅ エイリアスの正規化。
- ✅ あいまいな近重複ノード用レビューキュー。
- ✅ レビュー判断テンプレートとマージ/分離維持ワークフロー。
- ✅ ファイル別グラフからのコーパストレンド要約。

### 永続化とレポート

- ✅ Graph JSON エクスポート。
- ✅ SQLite グラフストア。
- ✅ 任意の Kuzu グラフストア。
- ✅ 件数、証拠カバレッジ、孤立ノード、日付バケット、エイリアスの多いノードを含むグラフレポート。
- ✅ MegaMem、Graphiti/Zep、MCP graph servers、agentic RAG から吸収したアイデアを説明する競合レポート。

### プロジェクトローカルワークフロー

- ✅ `tesserae project init`
- ✅ `tesserae project ingest`
- ✅ `tesserae project compile`
- ✅ `tesserae project mcp-config`
- ✅ `tesserae project build-site`
- ✅ `tesserae project serve`
- ✅ `tesserae project deploy`（GitHub Pages）
- ✅ `tesserae project sessions discover/import/list`（明示的なローカル agent-history インポート）
- ✅ `tesserae project watch`（スタンドアロンのポーリング watcher）
- ✅ `tesserae project engine` / `tesserae project daemon`（スーパーバイザー ループ — v0.5.0）
- ✅ `tesserae project refresh`（散文的 取り込み → コンパイル → 投影 チェーン — v0.5.0）
- ✅ `tesserae project context`（オンデマンド コンテキスト コンパイラ — v0.5.0）
- ✅ `tesserae project export-agent-harness`
- ✅ `tesserae project export-obsidian`
- ✅ `tesserae project export-graphiti`
- ✅ `tesserae project sync-graphiti`

### Obsidian

- ✅ すぐ開ける vault エクスポート。
- ✅ `.obsidian/app.json` とグラフ設定。
- ✅ Markdown 投影。
- ✅ `raw/assets/` 構造。
- ✅ Dataview クエリ付き `_meta/dashboard.md`。

### エージェントハーネス

生成対象ファイル:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering と MCP 設定
- ✅ Cursor: プロジェクトルールと MCP 設定
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / 時間的事実

- ✅ provenance、currentness、confidence、invalidation フィールドを持つ時間的事実投影。
- ✅ 依存なしの Graphiti episode JSONL エクスポート。
- ✅ Graphiti 未インストールでの `sync-graphiti --dry-run` スモーク。
- ✅ `graphiti_core` と Neo4j による任意のライブ同期。

### Cognee

- ✅ Cognee JSONL バンドル（`nodes.jsonl`, `edges.jsonl`, `manifest.json`）。
- ✅ 任意の add-only 直接インポート。
- ✅ 任意の Codex CLI/OAuth ベース Cognee cognify アダプター。
- ✅ API キーなしのスモーク/品質ワークフロー向け、決定的および Ollama embedding アダプターパス。

### MCP サーバー

- ✅ stdio JSON-RPC 上の `tesserae_mcp` / `python3 -m tesserae.mcp_server`。
- ✅ 検索/グラフ ツール: `schema`、`graph_summary`、`search_nodes`、`node_context`（`use_ppr` 付き）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`。
- ✅ コンテキスト エンジン ツール（v0.5.0）: `compile_context`、`embedding_status`、`fresh_insights`（減衰ランク付け）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`、`ask`。
- ✅ セットアップ ツール: `tesserae_setup_plan`、`tesserae_setup_apply`。
- ✅ マルチプロジェクト レジストリ: `list_projects`、`register_project`、`activate_project`、`unregister_project`、`list_sessions`。`url_resolver` 経由のストア URL ディスパッチ。

## テスト

現在のスイートの対象:

- ✅ ontology guardrails（新しい `Synthesis` ノード + `synthesizes` / `summarizes` エッジを含む）;
- ✅ 決定的抽出;
- ✅ Claude CLI wrapper の解析/検証;
- ✅ 選択的 Claude ルーティング;
- ✅ 正規化/レビューワークフロー;
- ✅ バッチ ingest;
- ✅ レポート;
- ✅ SQLite/Kuzu 永続化;
- ✅ Cognee bundles/import patches;
- ✅ Graphiti export/sync dry-run;
- ✅ プロジェクト CLI ワークフロー;
- ✅ agent harness export;
- ✅ Obsidian export;
- ✅ フロントエンド生成 + リンク整合性（`nodes/codeclass-*.html` なし）;
- ✅ wiki store 冪等性;
- ✅ synthesis projector golden + 冪等性;
- ✅ サイトコンポーネント、ページ、エクスポート、関連性;
- ✅ AI 兄弟ファイル形状（ページごとに `.txt` + `.json`）;
- ✅ end-to-end compile-twice 冪等性;
- ✅ エンジン スパイン: パイプライン、リフレッシュ チェーン、デーモン コア + ソース、`project engine` CLI;
- ✅ 自己改善メモリ: サイドカー、減衰/取って代わり、取って代わり抑制（MCP 含む）、強化/矛盾;
- ✅ 検索 + 埋め込み: ハイブリッド検索、PPR、実際の既定埋め込み（フェーズ 6）;
- ✅ コンテキスト コンパイラ: 形状/引用整合性/決定性/予算/PPR フォールバック、`project context` CLI、MCP `compile_context`;
- ✅ 増分コンパイル（実験的）: ディファー、一致ゲート、来歴準備性、SQLite 来歴;
- ✅ パッケージインストールとインストーラー契約。
