<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
＃ 建築

Tesserae は**コンテキスト エンジン**です。プロジェクトから自己改善型のナレッジ ベースを再構築し、それをエージェントがすぐに使えるコンテキストとして提供します。3 本の柱の上で動作します。(1) **セッション監視** — ライブのエージェント/作業セッションを観察し、発生したその場で発見を捕捉します。(2) **自律的・能動的なナレッジ取り込み** — パイプライン + スーパーバイザー ループが知識を継続的に取り込み再抽出し、指示を待たずにベースを改善し続けます。(3) **オンデマンド ドキュメント/コンテキスト** — 同じベースからコンパイルされたユーザー要求の成果物です。型付きグラフ、Markdown ボールト、静的サイトはナレッジ ベースの*投影*であり、エンジンはそれらを新鮮に保ちエージェントに供給するループです。

その下層で、Tesserae は、ソース素材のディレクトリを制御された型付きナレッジ グラフに変換し、耐久性のあるマークダウン Wiki レイヤーを介してグラフを作成し、静的で AI フレンドリーな Web サイトを作成するプロジェクトを作成します。 2026 年 4 月の再設計では、投影側を Karpathy の 3 層モデルを中心に再編成しました。生の証拠は生のままで、型付きグラフがオントロジーを管理し、マークダウン Wiki レイヤーがグラフとレンダリングされた出力の間に配置されます。静的サイトは、[`tesserae/research_graph.py`](../../tesserae/research_graph.py) 内の制御されたオントロジーをスキーマとして使用し、グラフの直接ダンプではなく、その Wiki レイヤーの *レンダラー* です。**v0.5.0** マイルストーン（2026 年 6 月）は、3 本の柱すべてを駆動するエンジン スパインを追加しました — 下記の*エンジン スパイン*および*オンデマンド コンテキスト コンパイラ*を参照してください。

## Karpathy の 3 層モデル

Andrej Karpathy による LLM フレンドリーなナレッジ ベースの構成は 3 つの層に分かれており、それぞれに独自の耐久性が保証されています。

|レイヤー |懸念事項 |リポジトリの場所 |オーナー |
|---|---|---|---|
| L1 — 生のソース |ユーザーが作成または収集したリテラル バイト。追加のみ。 | `data/`、`docs/`、`.tesserae/config.json` で参照されるプロジェクト ツリー |ユーザー |
| L2 — ウィキ | YAML フロントマターを使用した型付きマークダウン ページ (ソース、コンセプト、エンティティ、論文、リポジトリ、トピック、合成、質問)。冪等: コンパイルごとに再生成されますが、コンテンツのハッシュが変更された場合にのみ書き換えられます。 | `.tesserae/wiki/` | `WikiPageStore`、`WikiLayerProjector`、`SynthesisProjector` |
| L3 — レンダリング済み |静的 HTML サイト、AI 兄弟エクスポート、検索インデックス、サイトマップ、JSON-LD。コンパイルごとに消去および再書き込みされますが、再実行後もバイトは安定しています。 | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

スキーマは、別個の軸として 3 つのレイヤーすべてにまたがっています。`graph.json` の `ResearchGraph` は、L2 ページがリンクする制御されたオントロジーであり、[`tesserae/research_graph.py`](../../tesserae/research_graph.py) の `ResearchNodeType` / エッジ ホワイトリストは、そもそもどのようなタイプが存在するのかの信頼できる情報源です。

再設計により、L2 が明示的に追加されました。 2026 年 4 月以前は、静的サイトは `graph.json` から直接投影されていました。 wiki レイヤーは Obsidian ボールト エクスポート内にのみ存在していました。それを分割すると次のようになりました。

- 人間が編集可能な単一のサーフェス (Obsidian または任意のマークダウン エディターで `.tesserae/wiki/` を開きます)。
- 冪等なリビルド: `project compile` を再実行すると、ソース コンテンツが変更されない限り、ファイルの差分はゼロになります。
- 進化ログ: 合成ページは時間の経過とともに蓄積され、プロジェクト自体が物語るようになります。

## パイプライン

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

すべてのステップは段階的に行われます。グラフ抽出プログラムは、`manifest.json` コンテンツ ハッシュを使用して、変更されていないソース ファイルをスキップします。本体ハッシュがすでにディスク上にあるものと一致する場合、`WikiPageStore.write_page` は `False` を返します (書き込みをスキップします)。 `StaticSiteBuilder` は `.tesserae/site/` を消去して書き換えますが、その出力は決定的です。以下の「冪等性の話」を参照してください。

## コンテキスト コンパイラのデータフロー

オンデマンド コンテキスト コンパイラ（[`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)）は柱 3 の目玉となる経路です。クエリおよび/または明示的なシード ノード id が与えられると、`compile_context` はグラフから直接、調整済みで**引用付き**の Markdown バンドルを構築し、メモリ内に返します — `.tesserae/` 配下には何も書き込みません。

```
query / seeds
     │
     ▼  1. シード解決
        明示的シード（グラフに存在する場合のみ保持）+ hybrid_search() ヒット、重複排除、安定順
     │
     ▼  2. PPR 展開
        retrieval.ppr.personalized_pagerank が深さ制限付き k ホップ近傍をランク付け;
        結果が空（シードが非連結）→ シード順にフォールバック（バンドルは決して空にならない）
     │
     ▼  3. 予算制約付き選択
        PPR 順にたどり、次の本文が `budget` 文字を超過する直前まで各ノードの引用本文を含める
        （budget <= 0 = 無制限; 単語境界に超過マーカー）
     │
     ▼  4. 引用付き Markdown 組み立て
        選択された各ノードにつき 1 セクション + 末尾の `## Citations` ブロック。
        本文は（store と公開 wiki 種別が存在する場合）投影された wiki ページを優先し、
        なければノード説明、それもなければ最小スタブを使用。LLM なしの本文は壁時計の
        タイムスタンプを一切埋め込まない → 同じ (graph, query, seeds, depth, budget) でバイト一致。
     │
     ▼  5. オプションの LLM 合成  （synthesize=true かつ ANTHROPIC_API_KEY が設定されている場合のみ）
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

既定値: `depth=2`、`budget=32000`。決定的な組み立て（ステップ 1〜4）が契約であり、LLM 合成は純粋に付加的です。同じパイプラインが `project context` CLI コマンド、`compile_context` MCP ツール、およびトピック範囲のエクスポート スライス（`slice_export_context_for_topic`、トピック範囲の `llms.txt`）を支えています。

## モジュールマップ

### Wiki + 総合 (L2)

|モジュール |責任 |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` データクラス、ファイルシステム I/O の場合は `WikiPageStore`。 Stdlib 専用の YAML サブセット フロントマター パーサー。ボディハッシュ冪等性。 |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: Wiki レイヤー タイプの各 `ResearchGraph` ノードを、適切な `kind/` フォルダー内のマークダウン ページにマップします。 |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: パルス、デイリーダイジェスト、ウィークリー、トピック、比較、フィールド概要の決定的テンプレート。 `Synthesis` ノードと `synthesizes` / `summarizes` エッジをグラフに追加します。 |

### グラフ + オントロジー

|モジュール |責任 |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` 列挙型 (`SYNTHESIS` を含む)、エッジタイプのホワイトリスト (`synthesizes`、`summarizes` を含む)、検証。 |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) |エイリアスの正規化 + ほぼ重複したレビュー キュー。 |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) |開発スライス用の決定論的 Python AST エクストラクター。 |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth 選択的エクストラクター。 |

### サイトレンダラー (L3)

|モジュール |責任 |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: サイトをワイプ + 再構築し、すべてのルートを歩き、エクスポート + AI 兄弟 + マニフェストを生成します。 |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) |ルートごとに 1 つのレンダラー (ホーム、インデックス、詳細ページ、タイムライン、グラフ、概要)。 `SiteContext` は事前計算されたインデックスを保持するため、レンダラーは純粋な状態を保ちます。 |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML プリミティブ: `breadcrumbs`、`card`、`badge`、`node_table`、`edge_list`、`sparkline_svg`、`heatmap_svg`、`toc`、`page_shell`、`ai_siblings_footer`。 |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) |デザイントークン — CSS変数、ライトテーマとダークテーマ、レイアウト、タイポグラフィー、ここでスタイル設定されたすべてのコンポーネント。 |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) |クライアント JS バンドル: 検索パレット、テーマ切り替え、シグマ + 3D フォース グラフ ビュー。 |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Stdlib 専用のマークダウン レンダラー (リンク、自動リンク、コード、強調、見出し)。外部依存性はありません。 |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) |すべての `Related` セクションで使用される 4 つのシグナル関連性スコアリング (ダイレクト リンク、ソース オーバーラップ、Adamic-Adar、タイプ アフィニティ)。 |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json`ビルダー。 Wiki レイヤーの種類のみ。 |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) |インポートされたハーネス履歴のセッション インデックス/詳細レンダラー: プロジェクト メモリ概要セクション、会話ターン レール、マークダウン トランスクリプト レンダリング、および折りたたまれたツール使用ブロック。 |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、ページごとの `.txt`/`.json` の兄弟。 |

### パイプラインオーケストレーション

|モジュール |責任 |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: 抽出 → グラフ → メモリ パス → Wiki レイヤー → サイトを駆動。 `ProjectPaths`（`config`、`graph`、`manifest`、`wiki`、`site`など）所有。来歴（provenance）駆動の増分コンパイルが適格かを事前に判断（`incremental_compile` でゲート、既定 OFF）。 |
| [`tesserae/cli.py`](../../tesserae/cli.py) | フラット動詞の CLI ディスパッチ（レガシーの `project`/`wiki` サブコマンド群を削除した後で約 2,732 行）。動詞 — `init`、`compile`、`context`、`ask`、`refresh`、`serve`、`engine`、`export`、`vault`、`code`、`lab`、`config`、`projects`、`integrations` — は [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) にメタデータとして宣言され、手動登録ではなくそのツリーから配線されます。 |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: ワークツリー経由で `.tesserae/site/` を `gh-pages` ブランチにプッシュし、オプションで `gh` 経由でページを有効にします。 |

### エンジン スパイン (v0.5.0 — 柱 1 & 2)

エンジン スパインは、セッション監視と自律的な再取り込みを駆動するインプロセス ループです。同じ `Pipeline.run()` が、CLI、スーパーバイザー デーモン、そして（後の）MCP サーバーがすべて呼び出す単一のリフレッシュ経路です。

| モジュール | 責務 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: 逐次ステップ ランナー。散文的なリフレッシュ チェーン（取り込み → コンパイル → 投影/公開）をインポート可能なオブジェクトとして定式化し、表示して終了する代わりに構造化された `List[StepResult]` を返すため、各呼び出し元が結果の提示方法を自分で決められます。`run()` はステップごとに `Exception` を捕捉し（`KeyboardInterrupt`/`SystemExit` は通す）、最初の失敗で停止します。 |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: 単一所有者の asyncio スーパーバイザー。ソース ディレクトリ、Obsidian ボールト、ハーネス セッション ディレクトリを監視し、キャンセル・再スケジュールのデバウンスにより一連の `TriggerEvent` をちょうど 1 回の `Pipeline.run()` にまとめます。既存の `watch.py` / `vault_watch.py` ウォッチャーを再利用（書き換えはしない）し、pidfile を書き、実行中の例外でも生き延びます。`engine`（`--interval`、`--debounce`、`--once`）として公開。 |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | スタンドアロンの `export site --watch` コマンドとデーモンのソース/ボールト レーンが共通で再利用するポーリング ウォッチャー。 |

### 自己改善メモリ (v0.5.0 — 柱 2)

フェーズ 5 は永続的な自己改善を有効化しました。ノードごとの可変状態は `node_memory` SQLite サイドカー（`.tesserae/sqlite.db` 内部）に存在し、不変の `node_provenance.first_seen_at` 初回観測スタンプ（フェーズ 4 サイドカー）とは分離されています。コンパイルはグラフに対して一連の決定的なパスを駆動します。

| モジュール | 責務 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + `node_memory` テーブルへのストア非依存アクセサ（`read_memory`、`write_memory`、`bump_access`）— `decay_score`、`last_accessed_at`、`confidence`、`superseded`。どの呼び出し箇所も生の SQL を埋め込みません。 |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: セッション発見をランク付けするエビングハウス式の鮮度スコア（最新 + 最もアクセスされた順）。 |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass`（**既定 ON**）: 古い近似重複インサイトを新しいものに取って代わられたと印付ける決定的判定で、`supersedes` エッジを追加。 |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: セッション インサイトを、それが論じるコード シンボルに `discusses` エッジで結び付け。 |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 同じサイドカーに対するアクセス強化と矛盾検出のヘルパー。 |

再発信頼度は出力で数値です。時間投影は各事実の `confidence` を `NodeMemoryRow.confidence`（SQLite ではテキスト、`temporal.py` 経由で提示）からスタンプし、保存値がない場合のみ `infer_confidence` にフォールバックします。

### 検索 (v0.5.0 — 柱 2 & 3)

| モジュール | 責務 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: 3 つのレーン — Okapi BM25（k1=1.5、b=0.75）、大小文字を無視する語彙/FTS 風部分文字列、プラグ可能な埋め込みレーン — を逆順位融合（RRF、k=60）で融合するローカル優先のハイブリッド検索器。完全に決定的。 |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: グラフ上での HippoRAG-2 風（arXiv:2502.14802）パーソナライズド PageRank によるマルチホップ シード展開 — 1 ホップ近傍だけでなく、シードから数ホップ離れていても良く結ばれたノードを表面化。 |
| 埋め込みバックエンド (フェーズ 6, Track B) | ハイブリッド埋め込みレーンの既定バックエンドは追加依存を必要としない決定的なハッシュ バケット疑似埋め込みです。オプション依存がインストールされている場合は `sentence-transformers`（`all-MiniLM-L6-v2`）が優先され、遅延ロードされます。`embedding_status` MCP ツールがアクティブなバックエンドを報告します。 |

### オンデマンド コンテキスト コンパイラ (v0.5.0 — 柱 3 の目玉)

| モジュール | 責務 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: 柱 3 の目玉機能。クエリ/シード集合に対する調整済みの**引用付き**コンテキスト バンドルをグラフから直接コンパイル — 下記*コンテキスト コンパイラのデータフロー*を参照。メモリ内 `ContextBundle`（`ContextCitation` を含む）を返し、ディスクには何も書き込みません。`project context` CLI コマンドと `compile_context` MCP ツールとして公開。 |

### 永続化ポート + グラフ ストア

| モジュール | 責務 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` プロトコル: `upsert_node`/`upsert_edge`、`get_node`、`iterate_nodes`、`query_subgraph`、`find_canonical`、そしてフェーズ 4 の削除面 — `delete_node` と `delete_nodes_by_source`（指定したソース パスを除いた後に来歴集合が空になるノードを削除するため、ファイル横断的な概念は生き残る）。 |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: スタンドアロンのバッキング ストア; `node_provenance` と `node_memory` のサイドカー テーブルを所有。 |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | ストア URL（`sqlite:///…`、`hypepaper-postgres://…`）を適切な `GraphStore` に解決し、MCP サーバーが実行時に任意のバッキング ストアを指せるようにします。 |

### 外部アダプター (今回は変更なし)

|モジュール |責任 |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian ボールト投影 (グラフの色分け、データビュー ダッシュボード、生のアセット)。 |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude コード / Codex / Gemini / Kiro / Cursor / OpenCode ハーネスのエクスポート。 |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) |インバウンド Claude コード/Codex セッションの検出、正規化、`.tesserae/harness_sessions/` での保存、および編集されたマークダウンの概要。 |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) |時間的事実の JSONL + オプションのライブ Graphiti 同期。 |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | Cognee ノード/エッジ JSONL バンドルと直接追加/認識パス。 |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio サーバー。検索/グラフ: `schema`、`graph_summary`、`search_nodes`、`node_context`（`use_ppr` 付き）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`。コンテキスト エンジン（v0.5.0）: `compile_context`（オンデマンド コンテキスト コンパイラ）、`embedding_status`、`fresh_insights`（減衰ランク付けされたセッション発見）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`。さらに `ask`、マルチプロジェクト レジストリ ツール（`list_projects`、`register_project`、`activate_project`、`unregister_project`、`list_sessions`）、`tesserae_setup_plan` / `tesserae_setup_apply`。 |

## プロジェクトワークスペースのレイアウト

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; node_provenance（初回観測、フェーズ 4）と
                              node_memory（減衰 / 信頼度 / 取って代わられた、フェーズ 5）サイドカー テーブルも所有
  temporal_facts.jsonl        Graphiti-style temporal projection（数値の再発信頼度）
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

各ファイルは手動で編集できます。次のコンパイルでは、本体ハッシュがプロジェクターが書き込むものと異なる限り、ユーザーの編集が尊重されます。 (本文のみの編集が有効です。フロントマターの編集は次のコンパイル時に失われます。これは、フロントマターが再生成されるためです。) Obsidian ユーザーは、`.tesserae/wiki/` を直接開くことができます。既存の `obsidian_vault/` アダプターは別個の投影であり、代替品ではありません。

### `.tesserae/site/` (L3)

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## 意図的に除外されたもの

再設計では、明示的な境界線が引かれました。 コード クラス ノードとコード関数ノードは `graph.json` に残ります (つまり、MCP、Cognee、Graphiti のコンシューマーには引き続き表示されます) が、HTML ページを取得したり、`search-index.json` に表示されたり、ナビゲーションに表示されたりすることはありません。これがユーザー側の契約です。Wiki はドキュメントファーストの知識ベースであり、関数ブラウザーではありません。

具体的には、`StaticSiteBuilder` は、タイプが L2 wiki 種類マップ (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`) にないノードをスキップします。

- L2 + L3 から除外: `CodeClass`、`CodeFunction`、`CodeModule`、`Dependency`、`EvidenceSpan`、`SourceFile`、すべての `Claim` バリアント (`Claim`、`ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim`)。
- それらが引き続き表示される場所を表示します: 箇条書き、バッジ、近隣カウント、または証拠の抜粋として、関連する Wiki ページのインラインで、および下流のツール用に `graph.json` で表示されます。

コードレベルの参照が必要な場合は、LSP / コールグラフ ツールをソース ツリーに直接指定します。これは、「このプロジェクトが知っていることの Wiki」とは別の問題です。

## 冪等性の話

再設計は、*変更されていない入力に対する 2 つの連続した `project compile` 実行でバイトが同一の出力**を目指しています。ピース:

1. **ソース抽出**では、`manifest.json` コンテンツ ハッシュを使用します。変更されていないファイルはスキップされるため、グラフは安定したままになります。
2. **Wiki レイヤーの書き込み**は本体レベルで冪等です。 `WikiPageStore.write_page` は、既存のファイルを読み取り、フロントマターを削除し、ボディを sha256 で実行し、新しいボディのハッシュが同じであれば、新しいフロントマターの `generated_at` タイムスタンプが異なる場合でもショートサーキットします。これは、リビルド時に git diff を厳密に保つための重要なトリックです。
3. **合成出力** は前付に `content_hash: sha256-…` を持ちます。本体ハッシュは `generated_at` を使用せずに計算されるため、同じグラフでコンパイルを繰り返すと同じハッシュが生成され、`Synthesis` ノードはグラフ メタデータに同じ `content_hash` を保持します。
4. **サイト レンダリング** は、`write_site` の開始時に `site/` をワイプし、決定的に書き込みます。ルートはソートされ、辞書は `sort_keys=True` でダンプされ、`manifest.json` は `sorted(rglob("*"))` を経由してウォークされます。 2 回実行すると、マニフェストを含むバイト同一のファイルが生成されます。

これは、`tests/test_site_pages.py` および `tests/test_project_e2e_redesign.py` のエンドツーエンド スモークによって検証されます (コンパイルを 2 回、サイトの差分を行い、ファイル デルタはゼロであると予想されます)。

## スケーリングノート

- **グラフ ビュー ノード キャップ** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) は、インタラクティブなフォース レイアウトのページ埋め込みペイロードを制限します。ノードが 1500 を超えると、ミッドレンジのハードウェアではブラウザ側のシミュレーションが遅くなるため、ノード数が上限を超えると、ページは最初に最下位の wiki レイヤー ノードを削除します。エクスポートされた `graph.json` は影響を受けません。常に完全なグラフが含まれています。コード ノードは、キャップが適用される前にフィルターで除外されます。
- **`llms-full.txt` の上限。** 5 MB の安全上限は [`tesserae/site/exports.py`](../../tesserae/site/exports.py) に適用されます。キャップに達すると、ファイルは `[TRUNCATED — see graph.jsonld for the full set]` マーカーで終わります。 JSON-LD コンシューマは完全なセットを期待しているため、`graph.jsonld` には上限がありません。
- **検索インデックス。** Wiki レイヤーの種類のみ。コードグラフ ノードが `search-index.json` に入ることはありません。再設計の目標はドッグフード コーパスの 500 KB 未満であり、現在はそれを大きく下回っています。
- **ページごとのバイト予算 (経験則)。** 各詳細ページ < 60 KB gz HTML、共有 CSS < 30 KB、共有 JS < 25 KB、グラフ ページのシグマ ベンダーのみ (~60 KB)。グラフ ビューは 3D-force-graph + Three.js を 1 回ロードして使用します。他のページはすべてバニラのままです。
- **dogfood でのコンパイル時間。** 最近の開発マシンでは、最大 300 個のマークダウン ファイルが 5 秒以内に抽出されます。サイトのレンダリングではさらに約 2 秒が追加されます。 wiki 層の冪等性は、後続のコンパイルが変更されたパスのみに影響することを意味します。

## フロントエンド インタラクション サーフェス

- **検索パレット** — `cmd+k` / `ctrl+k` / `/`。 Wiki の種類に限定された、`search-index.json` に対するあいまい一致。最近のページは `localStorage` に残りました。
- **テーマの切り替え** — 右上のボタン; `data-theme="dark"` は `localStorage` に保存され、フラッシュを避けるためにペイント前に適用されます。
- **右に貼り付けられた目次** — デスクトップのみ。モバイルでは `<details>` ドロワーに折りたたまれます。ページ本文の `<h2>` / `<h3>` から生成されます。
- **アクティビティ ヒートマップ** — 月と曜日のラベルが付いた 26 週間の SVG。セルは、その日の `digest.md` ソース ページが存在する場合、そのページにリンクします。 (日ごとのタイムライン詳細ページ — `/timeline/<YYYY-MM-DD>.html` — は明示的なフォローアップです。`render_timeline` のインライン通知でフラグが立てられます。⚠ 進行中です。)
- **グラフ表示** — `/graph/`。ホバー ツールチップ、エッジ ラベル、カーソル アンカー ズーム、および 2D フォールバック ビューを備えた 3D フォース レイアウト (3d-force-graph + Three.js)。ノードの色は `ResearchNodeType` から取得されます。
- **モバイル シェル** — ドロワー レール、ボトム ナビゲーション、流体タイプ、タッチセーフ ヒット ターゲット (≥ 44 ピクセル)。

## テスト戦略

- **ユニット** — `tests/test_wiki_store.py`、`tests/test_synthesis.py`、`tests/test_site_components.py`、`tests/test_site_pages.py`、`tests/test_site_exports.py`、`tests/test_relevance.py`。
- **エンジン スパイン** — `tests/test_pipeline.py`、`tests/test_refresh_pipeline.py`、`tests/test_daemon_core.py`、`tests/test_daemon_sources.py`、`tests/test_cli_engine.py`。
- **自己改善メモリ** — `tests/test_memory_sidecar.py`、`tests/test_decay_supersede.py`、`tests/test_supersede_suppression.py`、`tests/test_mcp_supersede_suppression.py`、`tests/test_memory_contradiction_reinforce.py`。
- **検索 + 埋め込み** — `tests/test_hybrid_search.py`、`tests/test_ppr.py`、`tests/test_real_embeddings_phase6.py`。
- **コンテキスト コンパイラ** — `tests/test_context_compiler.py`（形状、引用整合性、決定性、予算、PPR フォールバック）、`tests/test_cli_context.py`、`tests/test_mcp_server_context.py`。
- **増分コンパイル（実験的）** — `tests/test_incremental_compile.py`、`tests/test_incremental_parity.py`、`tests/test_provenance_readiness.py`、`tests/test_sqlite_provenance.py`。
- **冪等** — `tests/test_project_e2e_redesign.py` は 2 回コンパイルされ、`wiki/` と `site/` の差分がゼロであるとアサートされます。
- **リンクの整合性** - `tests/test_frontend.py` は、出力されたすべての HTML を href に対して解析し、すべての内部リンクが生成されたファイルに解決されることをアサートします。 `nodes/codeclass-*.html`は生産しておりません。
- **AI 兄弟** — すべての `path/foo.html` について、テスト スイートは `path/foo.txt` と `path/foo.json` が存在することをアサートします。 JSON は解析され、`{title, kind, body, links}` が含まれます。
- **Playwright なし** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` でのバニラ pytest。

## 関連ドキュメント

- [クイックスタート](quickstart.ja.md) — `project init` から閲覧可能なサイトまでの最小パス。
- [フロントエンド再設計ウォークスルー](frontend-redesign.ja.md) — すべてのルートの注釈付きツアー。
- [機能マップ](feature-map.ja.md) — 出荷されたもの、進行中のもの、ファイル ポインター付き。
- [Self-dogfood デモ](self-dogfood.ja.md) — 独自のリポジトリに対して Tesserae を実行します。
