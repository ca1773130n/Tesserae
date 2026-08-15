# アーキテクチャ

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae は**コンテキストエンジン**です。プロジェクトから自己改善型のナレッジベースを再構築し、それをすぐに使えるコンテキストとしてエージェントに渡します。3 本の柱の上で動作します: (1) **セッション監視** — ライブのエージェント/作業セッションを観察し、発見をその場で捕捉する; (2) **自律的・能動的なナレッジ取り込み** — パイプライン + スーパーバイザーのループが知識を継続的に取り込み再抽出し、指示を待たずにベースを改善し続ける; (3) **オンデマンドのドキュメント/コンテキスト** — 同じベースからコンパイルされる、ユーザー要求の成果物。型付きグラフ、markdown ヴォルト、静的サイトはナレッジベースの*射影*であり、エンジンはそれらを新鮮に保ち、エージェントに供給するループです。

その下層で、Tesserae はソース素材のディレクトリを制御された型付きナレッジグラフに変換し、そのグラフを永続的な markdown wiki レイヤーを通して、静的で AI フレンドリーなウェブサイトに射影します。2026 年 4 月の再設計では、射影側を Karpathy の 3 層モデルを中心に再編成しました: 生の証拠は生のまま残り、型付きグラフがオントロジーを統治し、markdown wiki レイヤーがグラフとあらゆるレンダリング出力の間に位置します。静的サイトはグラフの直接ダンプではなく、その wiki レイヤーの*レンダラー*であり、[`tesserae/research_graph.py`](../../tesserae/research_graph.py) 内の制御されたオントロジーをスキーマとします。**v0.5.0** マイルストーン（2026 年 6 月）は、3 本の柱すべてを駆動するエンジンスパインを追加しました — 下記の*エンジンスパイン*と*オンデマンドコンテキストコンパイラ*を参照してください。

## Karpathy の 3 層モデル

LLM フレンドリーなナレッジベースに関する Andrej Karpathy のフレーミングは、それぞれ固有の永続性保証を持つ 3 つのレイヤーを区別します:

| レイヤー | 関心事 | リポジトリ内の場所 | 所有者 |
|---|---|---|---|
| L1 — 生ソース | ユーザーが執筆または収集したそのままのバイト列。追記専用。 | `data/`、`docs/`、`.tesserae/config.json` で参照されるプロジェクトツリー | ユーザー |
| L2 — Wiki | YAML frontmatter を持つ型付き markdown ページ（sources、concepts、entities、papers、repos、topics、syntheses、questions）。冪等: コンパイルごとに再生成されるが、コンテンツハッシュが変わったときにのみ書き直される。 | `.tesserae/wiki/` | `WikiPageStore`、`WikiLayerProjector`、`SynthesisProjector` |
| L3 — レンダリング済み | 静的 HTML サイト、AI シブリングのエクスポート、検索インデックス、サイトマップ、JSON-LD。コンパイルごとに消去・再書き込みされるが、再実行間でバイト安定。 | `.tesserae/site/` | `StaticSiteBuilder`（`tesserae/site/`） |

スキーマは独立した軸として 3 層すべてにまたがります: `graph.json` の `ResearchGraph` は L2 ページがリンクする制御されたオントロジーであり、[`tesserae/research_graph.py`](../../tesserae/research_graph.py) の `ResearchNodeType` / エッジのホワイトリストが、そもそもどんな型が存在するかについての真実のソースです。

再設計は L2 を明示的に追加しました。2026 年 4 月以前は、静的サイトは `graph.json` から直接射影されており、wiki レイヤーは Obsidian ヴォルトのエクスポート内にのみ存在していました。これを分離したことで得られたもの:

- 人間が編集できる単一のサーフェス（`.tesserae/wiki/` を Obsidian や任意の markdown エディタで開く）。
- 冪等な再ビルド: ソースコンテンツが変わらない限り、`project compile` の再実行はファイル差分ゼロを生みます。
- 進化のログ: 統合（synthesis）ページは時間とともに蓄積し、プロジェクトが自らを語れるようにします。

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

すべてのステップが増分的です。グラフ抽出器は `manifest.json` のコンテンツハッシュを使って未変更のソースファイルをスキップします。`WikiPageStore.write_page` は、ボディのハッシュがディスク上の既存の内容と一致する場合に `False` を返し（書き込みをスキップし）ます。`StaticSiteBuilder` は `.tesserae/site/` を消去して書き直しますが、その出力は決定的です — 下記の「冪等性の話」を参照してください。

## コンテキストコンパイラのデータフロー

オンデマンドコンテキストコンパイラ（[`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)）は柱 3 の看板パスです。クエリおよび/または明示的なシードノード ID を与えると、`compile_context` はグラフから直接、引用付きに仕立てられた markdown バンドルを構築してメモリ上で返します — `.tesserae/` 配下には何も書き込みません。

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  2b. 手続き的予約（与えられるのではなく獲得される）
        PROCEDURAL_POOL_ORDER の順にプールごと 1 枠: Runbook, Gotcha, Event,
        DistilledNote, ExpertiseProfile。枠は、その型で最上位かつプロデューサーの
        provenance を持つノードに与えられる — 型名だけでは足りない
     │
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

既定値: `depth=2`、`budget=32000`。決定的なアセンブリ（ステップ 1–4）が契約であり、LLM 合成は純粋に加算的です。同じパイプラインが `project context` CLI コマンド、`compile_context` MCP ツール、トピックスコープのエクスポートスライス（`slice_export_context_for_topic`、トピックスコープの `llms.txt`）を支えています。

**手続き的な枠が provenance によって獲得される理由。** 五つの手続き的型は、
エージェントが何をしたか、何をできるようになったか、何が得意かを名指します — が、
文書抽出にもそれらを生成することが許されているため、投稿募集を読んだ LLM が
「CVPR 2026」という型付き `Event` を正当に作ります。予約は*加算的*です: 近傍の
どこにあってもノードを予算ウォークの先頭へ引き上げます。したがって型だけで予約
すると、学会の締切がその枠を実際に獲得したセッションの発見を追い出しかねません。
両者を分けるのが `has_producer_provenance` であり、予約は枠に対する主張であって
その証明ではありません — `delivered` は予算ウォークのあとで確定するので、呼び出し
側は「手続き的記憶が予約された」と「手続き的記憶が届いた」を区別できます。
`PROCEDURAL_POOLS` lint コードがその差を報告します。


## モジュールマップ

### Wiki + 統合（L2）

| モジュール | 責務 |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | `WikiPage` データクラス、ファイルシステム I/O のための `WikiPageStore`。標準ライブラリのみの YAML サブセット frontmatter パーサー。ボディハッシュによる冪等性。 |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: wiki レイヤー型の各 `ResearchGraph` ノードを、適切な `kind/` フォルダ内の markdown ページにマッピングします。 |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: pulse、daily_digest、weekly、topic、comparison、field_overview のための決定的テンプレート。`Synthesis` ノードと `synthesizes` / `summarizes` エッジをグラフに追加し返します。 |

### グラフ + オントロジー

| モジュール | 責務 |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `ResearchNodeType` 列挙型（`SYNTHESIS` を含む）、エッジ型のホワイトリスト（`synthesizes`、`summarizes` を含む）、バリデーション。 |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | エイリアスの正規化 + 近似重複のレビューキュー。 |
| [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | `.tesserae/merge-ledger.json`: コンパイルが折りたたむすべての重複に対する敗者→生き残り墓碑銘。先のコンパイル由来のノード id が、単なる not-found ではなく解決される。**派生状態、歴史ではない** — パブリッシュは現在のものと和集合し、その敗者がちょうど発行グラフに存在しない間だけレコードを保ち、その外に出た連鎖が存在するノードに着地する場合のみです。敗者が復活するなら削除される。グラフ自身が見落とした後にのみ読まれ、それが生きた id を決してリダイレクトできないことを保証する。 |
| [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | `.tesserae/candidate-same-as.json`: 候補マージペアごとの保留中/確認済み/却下済み。ソート済みノード id ペアをキーとし、その他は何もない — スコア、理由、バックエンドは意図的にキーの外にあり、まさに評決が生き残らねばならないのと同じ変動です。**マージ台帳の対極、蓄積されるもの**: マージは派生状態、人間の評決はパイプラインの唯一のもので機械は再導出できず、ここは何も枝刈りされない。二つは同じコードを共有せず。 |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | 開発スライス用の決定的 Python AST 抽出器。 |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Claude CLI/OAuth の選択的抽出器。 |

### サイトレンダラー（L3）

| モジュール | 責務 |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: サイトを消去 + 再ビルドし、すべてのルートを走査し、エクスポート + AI シブリング + マニフェストを出力します。 |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | ルートごとに 1 つのレンダラー（ホーム、インデックス、詳細ページ、タイムライン、グラフ、about）。`SiteContext` が事前計算済みインデックスを運ぶため、レンダラーは純粋なままです。 |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | HTML プリミティブ: `breadcrumbs`、`card`、`badge`、`node_table`、`edge_list`、`sparkline_svg`、`heatmap_svg`、`toc`、`page_shell`、`ai_siblings_footer`。 |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | デザイントークン — CSS 変数、ライト + ダークテーマ、レイアウト、タイポグラフィ。すべてのコンポーネントがここでスタイルされます。 |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | クライアント JS バンドル: 検索パレット、テーマトグル、sigma + 3D-force グラフビュー。 |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | 標準ライブラリのみの markdown レンダラー（リンク、オートリンク、コード、強調、見出し）。外部依存なし。 |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | すべての `Related` セクションが使う 4 シグナルの関連度スコアリング（直接リンク、ソースの重なり、Adamic-Adar、型の親和性）。 |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | `search-index.json` のビルダー。wiki レイヤーの kind のみ。 |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | インポートされたハーネス履歴のセッション インデックス/詳細レンダラー: プロジェクトメモリのサマリセクション、会話ターンのレール、markdown トランスクリプトのレンダリング、折りたたまれたツール使用ブロック。 |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、ページ単位の `.txt`/`.json` シブリング。 |

### パイプラインオーケストレーション

| モジュール | 責務 |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: 抽出 → グラフ → メモリパス → wiki レイヤー → サイトを駆動。`ProjectPaths`（`config`、`graph`、`manifest`、`wiki`、`site` など）を所有。来歴（provenance）駆動の増分コンパイルが適格かを事前に判断します（`incremental_compile` でゲート、既定 OFF）。 |
| [`tesserae/cli.py`](../../tesserae/cli.py) | フラット動詞の CLI ディスパッチ（レガシーの `project`/`wiki` サブコマンド群を削除した後で約 2,732 行）。動詞 — `init`、`compile`、`ingest`、`context`、`ask`、`query`、`doctor`、`summary`、`decisions`、`refresh`、`serve`、`engine`、`export`、`vault`、`code`、`lab`、`setup`、`config`、`projects`、`sources`、`federation`、`integrations` — は [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) にメタデータとして宣言され、手動登録ではなくそのツリーから配線されます。 |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: ワークツリー経由で `.tesserae/site/` を `gh-pages` ブランチにプッシュし、オプションで `gh` 経由で Pages を有効化します。 |

### エンジンスパイン（v0.5.0 — 柱 1 & 2）

エンジンスパインは、セッション監視と自律的な再取り込みを駆動するインプロセスのループです。同じ `Pipeline.run()` が、CLI・スーパーバイザーデーモン・（後には）MCP サーバーのすべてが呼び出す単一のリフレッシュパスです。

| モジュール | 責務 |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: 逐次的なステップランナー。散文で書かれていたリフレッシュチェーン（取り込み → コンパイル → 射影/公開）を、print して exit する代わりに構造化された `List[StepResult]` を返すインポート可能なオブジェクトとして成文化し、結果の見せ方を各呼び出し側が決められるようにします。`run()` はステップごとに `Exception` を捕捉し（`KeyboardInterrupt`/`SystemExit` は通し）、最初の失敗で停止します。 |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: 単一所有者の asyncio スーパーバイザー。ソースディレクトリ、Obsidian ヴォルト、ハーネスセッションのディレクトリを監視し、`TriggerEvent` のバーストを cancel-and-reschedule のデバウンスで正確に 1 回の `Pipeline.run()` に合流させます。既存の `watch.py` / `vault_watch.py` ウォッチャーを再利用し（書き直しません）、pidfile を書き込み、実行中の例外にも耐えます。`engine`（`--interval`、`--debounce`、`--once`）として公開されます。 |
| [`tesserae/watch.py`](../../tesserae/watch.py)、[`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | スタンドアロンの `export site --watch` コマンドとデーモンのソース/ヴォルトレーンの両方で再利用されるポーリングウォッチャー。 |

### 自己改善メモリ（v0.5.0 — 柱 2）

フェーズ 5 は永続的な自己改善を起動しました。ノードごとの可変状態は（`.tesserae/sqlite.db` 内の）`node_memory` SQLite サイドカーに存在し、不変の `node_provenance.first_seen_at` 初出スタンプ（フェーズ 4 のサイドカー）とは分離されています。コンパイルはグラフに対して一連の決定的パスを駆動します。

| モジュール | 責務 |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + `node_memory` テーブル上のストア非依存アクセサ（`read_memory`、`write_memory`、`bump_access`） — `decay_score`、`last_accessed_at`、`confidence`、`superseded`。生の SQL を埋め込む呼び出し箇所はありません。 |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: セッションの発見をランク付けするために使われる、エビングハウス様の鮮度スコア（最新 + 最もアクセスされたものが先頭）。 |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass`（**既定で ON**）: 古い近似重複のインサイトを新しいものに置き換えられたとマークし、`supersedes` エッジを追加する決定的な判定。 |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: セッションのインサイトを、それが論じるコードシンボルに `discusses` エッジで結びつけます。 |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py)、[`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 同じサイドカー上のアクセス強化と矛盾検出のヘルパー。 |

再帰性の確信度は出力において数値です: 時間的射影は各事実の `confidence` を `NodeMemoryRow.confidence`（SQLite ではテキスト、`temporal.py` 経由で表面化）からスタンプし、保存された値が存在しない場合にのみ `infer_confidence` にフォールバックします。

### 検索（v0.5.0 — 柱 2 & 3）

| モジュール | 責務 |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: 3 つのレーン — Okapi BM25（k1=1.5、b=0.75）、ケースフォールドされた字句/FTS スタイルの部分文字列、プラガブルな埋め込みレーン — を相互ランク融合（RRF、k=60）で融合するローカルファーストのハイブリッドリトリーバー。完全に決定的。 |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: マルチホップのシード拡張のための、グラフ上の HippoRAG-2 スタイル（arXiv:2502.14802）Personalized PageRank — 1 ホップ近傍だけでなく、シードから数ホップ先のよく接続されたノードを表面化します。 |
| 埋め込みバックエンド（フェーズ 6、トラック B） | ハイブリッド埋め込みレーンの既定バックエンドは、追加依存を必要としない決定的なハッシュバケット疑似埋め込みです。`sentence-transformers`（`all-MiniLM-L6-v2`）が優先され、オプション依存がインストールされている場合に遅延ロードされます。`embedding_status` MCP ツールがどのバックエンドがアクティブかを報告します。 |
| [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | SQLite サイドカーの `node_vectors` テーブルと、3 つの `.embed(` サイトすべてがルーティングするアクセサ。`(backend_name, backend_dim, sha256(embedded_text))` でキーになる — アイデンティティはここでノード id ではなく埋め込まれた**テキスト**なため、フルリコンパイル後、プロジェクト移動後、または正規化による id 書き直し後に変わらないノードがヒットし、再記述されたノードはミスして再埋め込みされる。二つのモデルのベクトルは決して出会わない: それらのスペースは比較不可能であり、静かな混在は失敗ではなく余弦距離を腐らせるだろう。 |
| [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py) | ビューレジストリ: `semantic` / `temporal` / `causal` / `entity`、各々 `ALLOWED_EDGE_TYPES` の名前付きサブセット、`weights_for()` で解決し、ビュー外のすべての型に明示的なゼロ重みを付ける。二つの分割判定が負荷を担う: `summarizes` + `evidenced_by`（すべてのエッジの約 50% — 抽象化と来歴）は**どの**ビューにも属さない、そうでなければセマンティックビューがグラフ全体になる; 因果ビューは `CAUSAL_EDGE_TYPES` より広く、`{recovers}` だけではライブエッジのないビューになるだろう。 |
| [`tesserae/blocking.py`](../../tesserae/blocking.py) | 両方の両側通行パス（正規化のレビュービルダーと `memory.supersede`）に対する単一ブロッキングレイヤー。キャップは**ソート済み id** で截断するため、キャップ実行は id がどのような順序で到着したかに依存せず、絞られたコンパイルは再現可能。呼び出し元はスコアラーより粗いブロッカーが真の一致を静かに削除するため、独自のトークナイザーを供給。各パスはキャップに到達したことを報告しており、静かにより短いキューを返さない。 |

### オンデマンドコンテキストコンパイラ（v0.5.0 — 柱 3 の看板）

| モジュール | 責務 |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: 柱 3 の看板機能。クエリ/シード集合のために、仕立てられた**引用付き**コンテキストバンドルをグラフから直接コンパイルします — 下記の*コンテキストコンパイラのデータフロー*を参照。メモリ上の `ContextBundle`（`ContextCitation` 付き）を返し、ディスクには何も書き込みません。`project context` CLI コマンドと `compile_context` MCP ツールとして公開されます。 |

### 永続化ポート + グラフストア

| モジュール | 責務 |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `GraphStore` プロトコル: `upsert_node`/`upsert_edge`、`get_node`、`iterate_nodes`、`query_subgraph`、`find_canonical`、およびフェーズ 4 の削除サーフェス — `delete_node` と `delete_nodes_by_source`（指定されたソースパスを取り除いた後に来歴集合が空になるノードを削除するため、ファイル横断の概念は生き残ります）。 |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: スタンドアロンのバッキングストア。`node_provenance` と `node_memory` のサイドカーテーブルを所有します。 |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | ストア URL（`sqlite:///…`、`hypepaper-postgres://…`）を適切な `GraphStore` に解決し、MCP サーバーが実行時に任意のバッキングストアを指せるようにします。 |

### 外部アダプター（今回は変更なし）

| モジュール | 責務 |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Obsidian ヴォルト射影（グラフの色付け、Dataview ダッシュボード、生アセット）。 |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode のハーネスエクスポート。 |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | インバウンドの Claude Code/Codex セッションの発見、正規化、`.tesserae/harness_sessions/` 配下への保存、および秘匿化された markdown サマリ。 |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | 時間的事実の JSONL + オプションのライブ Graphiti 同期。 |
| [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | Kuzu データベースへの一方向エクスポート（`tesserae export kuzu`）。ストアではありません — [Kuzu エクスポート](#kuzu-エクスポート)を参照。 |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | MCP stdio サーバー。検索/グラフ: `schema`、`graph_summary`、`search_nodes`、`node_context`（`use_ppr` 付き）、`search_facts`、`timeline`、`graph_ppr`、`wiki_page`、`raw_source`、`lint_report`、`doctor_report`。コンテキストエンジン（v0.5.0）: `compile_context`（オンデマンドコンテキストコンパイラ）、`embedding_status`、`fresh_insights`（減衰ランクのセッション発見）、`list_communities`、`find_session_findings`、`find_code_symbol_mentions`。さらに `ask`、マルチプロジェクトレジストリのツール（`list_projects`、`register_project`、`unregister_project`、`list_sessions`）、および `tesserae_setup_plan` / `tesserae_setup_apply`。 |

## プロジェクトワークスペースのレイアウト

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/`（L2）

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

各ファイルは手で編集できます。ボディのハッシュが射影器の書こうとする内容と異なる限り、次のコンパイルはユーザーの編集を尊重します。（ボディだけの編集は勝ちます; frontmatter の編集は再生成されるため次のコンパイルで負けます。）Obsidian ユーザーは `.tesserae/wiki/` を直接開けます。既存の `obsidian_vault/` アダプターは別の射影であり、代替ではありません。

### `.tesserae/site/`（L3）

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

## 憲章

コミュニティ検出はドメイン語彙を**提案**し、憲章
（[`tesserae/charter.py`](../../tesserae/charter.py)）は明示的な再編のあいだ、
それを**所有**します。この分担があるのは、検出が決定的ではあっても安定では
ないからです: 同じ入力は 1,649 のコミュニティすべてを正確に再現しますが、
15 ノードの文書ひとつでメンバーの約 29% がコミュニティ間を移動し、大きな
コミュニティの Jaccard は 0.39〜0.60 まで落ちます。したがってコミュニティ所属を
キーにするものはすべて、取り込みのたびにほぼ全面的なキャッシュミスを被ります —
そしてこのコーパスは毎日取り込みます。

そこで憲章が制度を固定します: セクションを検出し、商グラフ（セクションごとに
1 ノード、セクション間の L0 エッジごとに 1 本の `part_of` エッジ）へ畳み、
**規模ではなくサブコミュニティによって**部門 → 部 → チームへ分割します。各
ドメインのアンカーは、主題を名づけられる型のなかで次数最大のメンバーです
（`SourceDocument`、`TechnicalTerm`、`EvidenceSpan`、`Session`、`Event`、`Agent`
は降格されます。節見出しや引用、文字起こしの冒頭行、アカウント識別子は、誰かが
エージェントを固定できる名前ではないからです）。二つのドメインが同じものを
共有しないよう貪欲に選ばれ、人が目にするスラッグはそのアンカーから一度だけ作られて固定
されます。再編をまたぐときは `succeed` がアンカー一致でスラッグを引き継ぐので、
その下のメンバーが入れ替わっても安定した名前は生き残ります。すべてのノードは
ちょうど一つのドメインに収まります: `intake_members` が、検出なら黙って失って
いたはずの捨てられた単独ノードとエッジ的に孤立したセクションを受け止めます。

`tesserae domains status [--json]` がツリーを表示します。**ステータス:**
すべての `compile` が、階層サイドカーを組み立てるのと同じ正規化済みグラフから
憲章を `.tesserae/charter/charter.json` へ導出します。何も再編しなかった再
コンパイルは書き込みを見送るので、ファイルはバイト単位で変わりません —
`reorg_seq` が数えるのは再編であってコンパイルではないからです。リサーチ層が
一読分に収まるプロジェクトは閾値未満で、憲章をまったく持ちません。そこでは
コマンドは従来どおり "no charter yet" と報告して 0 で終了します。これが正直な
答えです。

## 意図的に除外されているもの

再設計は明示的な線を引きました: コードクラスとコード関数のノードは `graph.json` に留まります（そのため MCP と Graphiti のコンシューマーからは引き続き見えます）が、HTML ページを持つことはなく、`search-index.json` に現れることもなく、ナビゲーションに現れることもありません。これがユーザー向けの契約です — この wiki はドキュメントファーストのナレッジベースであり、関数ブラウザではありません。

具体的には、`StaticSiteBuilder` は、型が L2 wiki kind マップ（`tesserae/wiki_projector.py::_KIND_FOR_TYPE`）にないノードをすべてスキップします:

- L2 + L3 から除外: `CodeClass`、`CodeFunction`、`CodeModule`、`Dependency`、`EvidenceSpan`、`SourceFile`、すべての `Claim` 系（`Claim`、`ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim`）。
- それらが引き続き現れるサーフェス: 関連する wiki ページ上のインラインの箇条書き、バッジ、隣接数、証拠の抜粋として、および下流ツール向けの `graph.json` 内。

コードレベルのブラウジングが必要なら、LSP / コールグラフツールをソースツリーに直接向けてください — それは「このプロジェクトが知っていることの wiki」とは別の問題です。

## OKF v0.2 エクスポート / インポート

[`tesserae/okf.py`](../../tesserae/okf.py) はグラフを [Google **OKF v0.2**](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) バンドルへ投影します — YAML frontmatter を持つマークダウンファイルのディレクトリツリーで、必須キーは空でない `type` ただ一つです。`tesserae export okf` は **v0.2 を書き**、`tesserae export okf --import DIR` は **v0.1 と v0.2 を読みます**。バンドルは `graph.json` の純粋な投影です: 壁時計も `os.stat()` も環境も使わないので、同じグラフを二度エクスポートすればバイト単位で同一になります。

Tesserae が何を出力し、それぞれの値が正直にどこから来るのか:

| Frontmatter | §  | 由来 |
|---|---|---|
| `type` | §4.1 | ノード型、または `metadata.okf_type` に保存された外部型 |
| `title` | §4.1 | `node.name` — v0.1 は仕様にない `name` を書いていました。下の破壊的変更を参照 |
| `description` | §4.1 | ノード説明の最初の一文、上限付き |
| `resource` | §4.1 | `arxiv_id` → `https://arxiv.org/abs/<id>`、なければ `repo_url` / `github_repo` |
| `generated: {by, at}` | §5.2 | `by` は `agent_key` → `<key>/tesserae-agent-write`、なければ `extractor` → `process:tesserae-<extractor>`、なければ `process:tesserae-compile`。`at` は [`temporal.py`](../../tesserae/temporal.py) の共通ソースタイムスタンプ・ラダーから |
| `sources[]` | §5.1 | プロジェクトルート相対にした `source_path`、加えて `author`（単独の `authored_by`）、`last_modified`（`frontmatter_date` / `analysis_date`）、`usage_count`（重複を除いた `discussed_in` セッション数） |
| `usage_window` | §5.1 | 上で数えたセッションの `started_at` / `ended_at` の最小・最大 |
| `status: deprecated`, `stale_after` | §5.4, §5.5 | `supersedes` エッジに指されたノード。`stale_after` は置き換える側のノードの日付で、それが置き換えられる側自身の日付より前になる場合は省略されます |
| `x_tesserae` | 拡張 | 実際のノード id、エイリアス、`source_path`、メタデータ、型付きエッジ — 無損失の往復チャネル |

`index.md` は §8 に従い（frontmatter はちょうど `okf_version: '0.2'` で、§12 がそれを許す唯一の場所です）、`log.md` は §9 に従います（frontmatter なし、`## YYYY-MM-DD` のグループ、新しい順）。Tesserae のプロジェクトグラフ（5197 ノード / 15284 エッジ）では 5195 ファイルが生成され、5193 の概念すべてが `generated` を、3934 が `sources` を、1264 が `usage_window` を、1749 が `description` を、822 が `resource` を、25 が `status`/`stale_after` を持ちます。

**意図的に出力しないもの。** `verified` キー（§5.2）はなく、したがって `unverified` より上の信頼ティア（§5.3）もありません: コンパイル済みグラフの中に、行為者とタイムスタンプを伴う検証*イベント*であるものは一つもないからです。`verify_claim` と再グラウンディングはグラフに対するクエリ時の関数であり、`lint --verify-claims` は LLM の審判で、[`verify.py`](../../tesserae/verify.py) 自身がそれは証拠ではないと述べています。エッジの provenance クラスはグラフが*トリプル*をどれだけ強く是認するかを表しますが、OKF の信頼ファミリーは*概念*ごとの確認です。片方をもう片方に写像すれば、誰も確認していない内容に機械確認済みのティアを与えることになるので、`generated.by` が `human:` で始まることは決してありません — テストがそれを固定しています。同様に Attested Computation ファミリー（§10）もありません: Tesserae には認可された計算も、実行器も、レシートも、アテスター ABI もなく、§10.5 は消費者にアテステーションで*ゲートせよ*と告げているため、空の足場は履行できない契約を宣伝することになります。正直な出所がないために欠けているものもあります: `tags`（ノード単位のタグ項目がありません — `aliases` は別名であって分類ではありません）、主張ごとの `[^id]` 脚注、`status: draft`（`metadata.confidence` は抽出の確信度であってレビュー状態ではありません）、そして保存された信頼度スコアの類（§5.1 が記録するのはシグナルであって判定ではありません）。`last_modified` はグラフ内の文書日付から来るのであって、**決して**ファイルの mtime からではありません — もっともらしい `os.stat()` の近道こそ、ここで以前にバイト冪等性を壊した環境リークそのものです。

**読み取り。** §11 に従い、インポーターは何も拒否しません: 未知の `type` 値、未知の frontmatter キー、欠けたオプションファミリー、壊れた相互リンク、`index.md` の欠如、いずれも許容されます。スキップされるのは空でない `type` を持たないファイルだけです。Tesserae 自身のバンドルは `x_tesserae` を通じて無損失で往復します。外部バンドルは `type` を対応するノード種別または `Concept` へ、本文リンクを `references` エッジへ写し、認識できない frontmatter キーはすべて `metadata.okf` に入れ（§4.1 の往復 SHOULD）、裸の `verified` は 1 要素のリストに正規化されます（§11 MUST）。v0.1 フォールバック（§13.1）: 旧来の `timestamp` は `metadata["updated_at"]` に着地し（タイムスタンプ・ラダーが既に読む段）、旧来の本文の `# Citations` リストは `metadata["okf"]["sources"]` となり、散文として飲み込まれる代わりに説明から取り除かれます。再エクスポート時、保存されたバケットは Tesserae が導出したものの*上に*マージされるため、他人のバンドルを再エクスポートしても彼らの provenance や信頼の主張をこちらのもので上書きすることはありません。`--import` は信頼ティアのヒストグラムを表示するので、混在バンドルは黙って通り過ぎずに可視化されます。ティアは読み取り時に `okf_trust_tier` が推論し、保存されません。

**Tesserae の v0.1 出力からの破壊的変更。** `name:` は `title:` になります（`name` はどちらのバージョンでも OKF のキーであったことはありません。リーダーは `title` の後ろで今も受け付けます）。`index.md` と `log.md` は `type:` / `name:` の frontmatter を失うので（§8, §9）、それらを型付き概念として扱っていた消費者は幽霊エントリ二つを失います — それが狙いです。関連して、この二つはバンドルのルートだけでなく階層の*どの*レベルでも予約されるようになりました（§3.1）。すべての概念ファイルのバイトが変わるため、最初の v0.2 エクスポートはバンドル全体を書き直します。

**既知の限界。** `usage_count` が数えるのは、その文書に触れたトランスクリプトを持つ重複除去済みのエージェント / 作業セッション数であって、人間のページ閲覧数ではありません — §5.1 もこのシグナルが粗いと警告しています。人気ではなく生存性として読んでください。ライフサイクル・ファミリーは `supersedes` エッジに指されたノードにのみ発火し（ここでは 5197 中 25）、本当の網羅には `TemporalFactProjector` がクエリ時に導く時間的妥当区間が要りますが、それを 15k エッジに対してエクスポーター内部で走らせるのはスコープ外として却下されました。`generated.by` は §7 の `<producer>/<version>` ではなく `process:tesserae-<extractor>` を意図的に使います: バージョンを帯びた行為者は、意味が何も変わらないのにリリースごとに約 5200 の概念ファイル全部を書き換えさせるからです。パス値を取る OKF フィールド（`resource`, `sources[].resource`）が絶対パスを運ぶことは決してありません — プロジェクトルート相対にできないものは生のまま出さずに省略します。§6.2 のもとでは消費者がそれをバンドル相対として読んでしまうからです — ただし絶対パスは `x_tesserae.source_path`（ノードの実体としての識別子で、外部の消費者は無視します）の中と、たまたまパスを引用しているノード本文の中には依然として現れ得ます。

## Kuzu エクスポート

[`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) は `graph.json` を埋め込み [Kuzu](https://kuzudb.com) データベースへ投影し、別のツールがグラフ上で Cypher を実行できるようにします。`tesserae export kuzu` が書き出し、`--graph PATH` はプロジェクトのコンパイル済みグラフではなく、抽出しただけの素のグラフをエクスポートします。これは **一方向のエクスポート**であり、[OKF](#okf-v02-エクスポート--インポート) と [Graphiti](../../tesserae/graphiti_adapter.py) と並ぶ三つ目です。`write_graph(replace=True)` はデータベースを削除して作り直すので、出力は渡されたグラフの純粋な関数になります。

**Kuzu は意図的にストアではなく、この区別が構造を支えています。** かつて `KuzuResearchGraphStore` が本物の SQLite ストアの隣、[`tesserae/persistence.py`](../../tesserae/persistence.py) に置かれ、依存関係が dev-only と宣言された `extract --kuzu-output` フラグからのみ到達できました — 半分だけ配線された second backend であり、それが「Tesserae はグラフデータベースを採用すべきか?」を未解決の問いのように見せていました。未解決ではなく、その理由は法的なものではなくアーキテクチャ上のものです（Kuzu は MIT ライセンスで、埋め込み型で、サーバーを必要としません）:

- **二つ目の権威あるストアは、同じ事実について `graph.json` と食い違いうる**のに、調停者がいません。`graph.json` が真実の源であり、それに矛盾しうるものはすべてバグの表面です。
- **バイト冪等性が純粋関数からデータベースの書き込み順序へ移ってしまいます。** `tests/test_byte_idempotence_phase5.py` が固定している性質 — 二度のコンパイルがバイト単位で同一の `graph.json` を生む — は、コンパイルが入力に対するキー整列済みの純粋関数だから成り立ちます。比較した graph-memory システムのどれもそれを試みてすらおらず、書き込みをエンジン経由にすることは、それを失う方法そのものです。

エクスポートにはどちらの反論も当てはまりません: データベースは派生出力であり、グラフから消去され書き直され、どのコンパイル経路もクエリ経路も読み戻しません。`read_graph` が存在するのは `okf.read_okf_bundle` と同じ理由 — 読み戻せないエクスポートは検証できないエクスポートだから — であって、エンジンの何かが Kuzu からロードするからではありません。`tests/test_kuzu_adapter.py` は `tesserae.persistence` が Kuzu シンボルを一切公開しないことを保証するので、復活したストアはレビューではなくテストスイートで落ちます。

同じ判断が Neo4j を基盤として除外します: [`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md`](../superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md) を参照。そこでは能力（永続化されたベクトルインデックス、ソフトマージの墓標、トランザクション時刻の時計）をエンジンではなくファイルと SQLite のサイドカーとして採り入れています。

## 冪等性の話

再設計は、**未変更の入力に対する連続 2 回の `project compile` 実行でバイト単位に同一な出力**を目指しています。その構成要素:

1. **ソース抽出**は `manifest.json` のコンテンツハッシュを使います。未変更のファイルはスキップされるため、グラフは安定したままです。
2. **wiki レイヤーの書き込み**はボディレベルで冪等です。`WikiPageStore.write_page` は既存ファイルを読み、frontmatter を剥がし、ボディを sha256 し、新しいボディが同じハッシュになる場合は — 新しい frontmatter の `generated_at` タイムスタンプが違っていても — ショートサーキットします。これが再ビルド時の git 差分を小さく保つ鍵となるトリックです。
3. **統合（synthesis）出力**は frontmatter に `content_hash: sha256-…` を持ちます。ボディハッシュは `generated_at` 抜きで計算されるため、同じグラフに対する繰り返しのコンパイルは同じハッシュを生み、`Synthesis` ノードはグラフメタデータに同じ `content_hash` を持ちます。
4. **サイトレンダリング**は `write_site` の最初に `site/` を消去し、その後決定的に書き込みます: ルートはソートされ、辞書は `sort_keys=True` でダンプされ、`manifest.json` は `sorted(rglob("*"))` で走査されます。2 回の実行はマニフェストを含めてバイト単位に同一なファイルを生みます。
5. **ノードの日付はソース由来です。** ノードの `first_seen_at` は、そのソースが取り込まれたパスから決まり、コンパイル時の壁時計からは決まりません。時計を読めば再実行のたびに差分が出るため、これを素朴に実装すると 1 番が壊れます。同じ規則が `Event` パスをバイト冪等に保ちます: 生成されるあらゆる id・本文・日付が内容から導かれ、481 セッションのコーパスで検証済みです。

これは `tests/test_site_pages.py` と、`tests/test_project_e2e_redesign.py` のエンドツーエンドスモーク（2 回コンパイルし、サイトを diff し、ファイル差分ゼロを期待する）で検証されています。

## スケーリングノート

- **グラフビューのノード上限。** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) が、インタラクティブなフォースレイアウトのページ埋め込みペイロードを制限します。約 1500 ノードを超えるとブラウザ側のシミュレーションはミッドレンジのハードウェアで重くなるため、数が上限を超えるとページは次数の最も低い wiki レイヤーノードから先に落とします。エクスポートされる `graph.json` は影響を受けません — 常に完全なグラフを含みます。コードノードは上限適用の前にフィルタリングされます。
- **`llms-full.txt` の上限。** [`tesserae/site/exports.py`](../../tesserae/site/exports.py) で 5 MB の安全上限が適用されます。上限に達した場合、ファイルは `[TRUNCATED — see graph.jsonld for the full set]` マーカーで終わります。`graph.jsonld` は上限なしです。JSON-LD のコンシューマーは完全な集合を期待するからです。
- **検索インデックス。** wiki レイヤーの kind のみ。コードグラフのノードは決して `search-index.json` に入りません。再設計の目標はドッグフードコーパスで 500 KB 未満であり、現在は余裕でその範囲内です。
- **ページごとのバイト予算（経験則）。** 各詳細ページは gz HTML で 60 KB 未満、共有 CSS は 30 KB 未満、共有 JS は 25 KB 未満、sigma ベンダーはグラフページのみ（約 60 KB）。グラフビューは 3D-force-graph + Three.js を一度だけロードします。他のすべてのページはバニラのままです。
- **ドッグフードでのコンパイル時間。** 最近の開発マシンで約 300 の markdown ファイルが 5 秒未満で抽出され、サイトレンダリングがさらに約 2 秒を追加します。wiki レイヤーの冪等性により、以降のコンパイルは変更されたパスにのみ触れます。

## フロントエンドのインタラクションサーフェス

- **検索パレット** — `cmd+k` / `ctrl+k` / `/`。`search-index.json` 上のあいまい一致、wiki kind にスコープ。最近のページは `localStorage` に永続化。
- **テーマトグル** — 右上のボタン。`data-theme="dark"` は `localStorage` に保存され、フラッシュを避けるためにペイント前に適用されます。
- **スティッキーな右側 TOC** — デスクトップのみ。モバイルでは `<details>` ドロワーに折りたたまれます。ページボディの `<h2>` / `<h3>` から生成されます。
- **アクティビティヒートマップ** — 月と曜日のラベル付きの 26 週 SVG。セルは、存在する場合にその日の `digest.md` ソースページにリンクします。（日ごとのタイムライン詳細ページ — `/timeline/<YYYY-MM-DD>.html` — は明示的なフォローアップです。`render_timeline` 内のインライン通知がそれを示します。⚠ 進行中。）
- **グラフビュー** — `/graph/`。ホバーツールチップ、エッジラベル、カーソル基点のズーム、2D フォールバックビューを備えた 3D フォースレイアウト（3d-force-graph + Three.js）。ノードの色は `ResearchNodeType` に由来します。
- **モバイルシェル** — ドロワーレール、ボトムナビ、流動的なタイポグラフィ、タッチセーフなヒットターゲット（44 px 以上）。

## テスト戦略

- **ユニット** — `tests/test_wiki_store.py`、`tests/test_synthesis.py`、`tests/test_site_components.py`、`tests/test_site_pages.py`、`tests/test_site_exports.py`、`tests/test_relevance.py`。
- **エンジンスパイン** — `tests/test_pipeline.py`、`tests/test_refresh_pipeline.py`、`tests/test_daemon_core.py`、`tests/test_daemon_sources.py`、`tests/test_cli_engine.py`。
- **自己改善メモリ** — `tests/test_memory_sidecar.py`、`tests/test_decay_supersede.py`、`tests/test_supersede_suppression.py`、`tests/test_mcp_supersede_suppression.py`、`tests/test_memory_contradiction_reinforce.py`。
- **検索 + 埋め込み** — `tests/test_hybrid_search.py`、`tests/test_ppr.py`、`tests/test_real_embeddings_phase6.py`。
- **コンテキストコンパイラ** — `tests/test_context_compiler.py`（形状、引用の整合性、決定性、予算、PPR フォールバック）、`tests/test_cli_context.py`、`tests/test_mcp_server_context.py`。
- **増分コンパイル（実験的）** — `tests/test_incremental_compile.py`、`tests/test_incremental_parity.py`、`tests/test_provenance_readiness.py`、`tests/test_sqlite_provenance.py`。
- **冪等性** — `tests/test_project_e2e_redesign.py` は 2 回コンパイルし、`wiki/` と `site/` に差分ゼロであることをアサートします。
- **リンクの整合性** — `tests/test_frontend.py` は出力されたすべての HTML の href をパースし、すべての内部リンクが生成済みファイルに解決されることをアサートします。`nodes/codeclass-*.html` は生成されません。
- **AI シブリング** — すべての `path/foo.html` について、テストスイートは `path/foo.txt` と `path/foo.json` の存在をアサートします。JSON はパースでき、`{title, kind, body, links}` を含みます。
- **Playwright なし** — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` の下でのバニラ pytest。

## 関連ドキュメント

- [クイックスタート](quickstart.ja.md) — `project init` から閲覧可能なサイトまでの最短パス。
- [フロントエンド再設計のウォークスルー](frontend-redesign.ja.md) — すべてのルートの注釈付きツアー。
- [機能マップ](feature-map.ja.md) — 何が出荷済みで何が進行中か、ファイルポインタ付き。
- [セルフドッグフードのデモ](self-dogfood.ja.md) — Tesserae を自身のリポジトリに対して実行する。
