# 機能マップ

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
このドキュメントは、Tesserae に現在実装されている機能を、ステータス、ソースファイル、そしてどこで文書化されているかとともにまとめたものです。

Tesserae は 3 つの柱で動く**コンテキストエンジン**です: (1) セッションモニタリング、(2) 自律的でプロアクティブなナレッジの取り込み、(3) オンデマンドのドキュメント/コンテキスト。型付きグラフ、vault、静的サイトはナレッジベースのプロジェクションです。以下の機能は、それぞれがどの柱に貢献するかでグループ化されています。**v0.5.0** マイルストーン（2026 年 6 月）でエンジンのスパインと、柱 3 の目玉機能であるオンデマンドコンテキストコンパイラが出荷されました。

ステータス凡例: ✅ 出荷済み · ⚠ 進行中 / 部分的。

> **読む順序。** 以下の節はマイルストーンで、新しいものが先です。v0.12.0 から
> v0.28.7 のあいだのバージョンはここでは再掲しません — リリースごとの詳細は
> 権威ある変更履歴である [`docs/release-notes/`](../release-notes/) にあります。
> このマップが扱うのはシステムの形であって、すべてのコミットではありません。

## エージェントメモリ、時間的深さ & 検索ビュー — v0.31.0 以降（2026 年 8 月）

Neo4j のエージェント-メモリ設計を読み、Tesserae 自身の制約を生き残るパーツを取った
サイクルです: 第二の時間軸、名付けられたエッジ分割、アイデンティティ墓碑銘、そして
機械が再導出できない評決のための永続的な住処。データベース自身は外に留まりました —
`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md` で何が取られ、
何を要したか、なぜかを参照してください。

| 機能 | ステータス | ソース | 備考 |
|---|---|---|---|
| トランザクション時間（`observed_as_of`） | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | 第二の時計: `as_of` が「そのとき何が真だったか」をソース自身のタイムスタンプから回答; `observed_as_of` が「そのときまでに何を学んだか」をコンパイルごとにスタンプされた `fact_observed` テーブルから回答。二つは合成します。`sqlite.db` 内にのみ存在 — `graph.json` 内の壁時計は同じソースを明日は別のバイトにコンパイルさせるだろう。以前は `as_of` が「バイテンポラル」を標榜しながら軸は一本だけだった。 |
| ファクトが内容として検索される; `dated` が述語 | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `search_facts` は subject / predicate / object / evidence の上でランク付けし、シリアライズされたファクトそのものは決して見ません。したがって id やメタデータ断片はもう一致しません。`dated`（`any`/`dated`/`undated`）は、呼び出し側が `undated_included` から推し量るしかなかったものを、日付の有無というフィルタに変えます。 |
| `resolved_by` が区間を閉じる | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | 矛盾パスが敗者を仲裁しますが、時間的射影はそれを無視し、仲裁された敗者は `current: true` 読み続けました。**敗者側から**閉じ — `resolved_by` は source→winner を実行、無効化述語の対極 — プラス Graphiti の重複ガード: 敗者より先か同時に観測された勝者は敗者がいつ真をやめたかは言えない。 |
| タイムラインが自身のマッチをカウント | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | `timeline` は**完全な**マッチセットを日付ソートしてからページング、そして `total_events` がすべてのマッチをカウント。以前はランク選定された 100 行スライスをソートし、その固定を母集団カバレッジとして報告 — だからタイムライン向けの最古のイベントが最もドロップされやすかった。 |
| ビューレジストリ + 複数ビュー融合 | ✅ | [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py), [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | 一つのメモリ、四つの直交グラフとして走行可能 — `semantic` / `temporal` / `causal` / `entity`, 各々エッジ語彙の名付けられたサブセット。新しいランキングアルゴリズムではない: ビューはビュー外のすべてのエッジ型にゼロウェイトを付与するよう解決され、近傍ウォークは同じセットでフィルタするので、ビュー専用ノードは決して取り入れられません。複数ビューは重み付き RRF で融合、各引用は `via_views` を報告。 |
| 永続的なベクトルキャッシュ | ✅ | [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | すべての埋め込みコールサイトは毎実行で全コーパスを再埋め込み。`node_vectors` テーブルがいまはすべての三つをバッキング、`(backend, dim, sha256(embedded_text))` でキー — ノード id ではなく、ノード id は変わらないとヒット、全リコンパイルまたはムーブ後、再記述はミスして再埋め込み、二つのモデルのベクトルは出会わない。`embedding_status` が `vectors_cached` プラスプロセス全体ヒット/ミス/エラーを報告。 |
| レーンごとの検索プロファイリング | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `explain: true` on `search_nodes` / `compile_context` はレーンごとのウェイト、コーパス、埋め込み呼び出し、キャッシュヒット/ミス、ウォール時間を返す、プラス各勝者を供給したレーン。オプトイン、Neo4j の `PROFILE` のように、計測はコストするから — そしてそれは決してランキングを動かせない、すべての数字は融合が既に生成したテーブルから読まれるから。 |
| マージ台帳 — 死んだ id がサバイバーに解決 | ✅ | [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | すべてのコンパイルが三つの方法で重複を折りたたみ、以前は各回答を捨てたため、最後のコンパイルのノード id を持つエージェントは単なる not-found を得た。`merge-ledger.json` は敗者→サバイバー墓碑銘、グラフミス後のみ参照（生きた id は決してリダイレクトされない）; `node_context` は `status: merged` に `merged_from` / `merged_into` で報告。派生状態、歴史ではない: 敗者が復活するなら落ちる。 |
| 取消（`retracts`） | ✅ | [`tesserae/research_graph.py`](../../tesserae/research_graph.py), [`tesserae/graph_filters.py`](../../tesserae/graph_filters.py) | エージェントは「これは誤り」と言え、置き換えを発明せずに: `retracts` エッジがノードに**id で**指さすと、発見から脱落し（`search_nodes`, `fresh_insights`）、コンテキスト選択から（`compile_context`）、そして `node_context` のすべての隣人から。`node_context` の完全一致ルックアップ（id または名前による）でもなおそのノード自身を返す、`retracted` フラグ付き — ノードを名指しすることは発見ではない。`include_superseded: true` はそれを発見サーフェスに復す; 何も削除されない。 |
| 候補 same-as 評決台帳 | ✅ | [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | 「これらは異なる」と答えたレビュアーは永遠に同じ質問を尋ねられた — `apply_decisions` は `keep_separate` を消費し何もしなかった。`.tesserae/candidate-same-as.json` は評決をソート済みノード id ペアでキー、ほかは何もなく、書き直された説明、新しいソース、別の埋め込みバックエンドはすべてそれを置き去り。蓄積され、決して枝刈りされない: 評決はここで機械が再導出できない唯一。`PENDING_REVIEW` として表面化。 |
| 両側通行パス用一ブロッキングレイヤー | ✅ | [`tesserae/blocking.py`](../../tesserae/blocking.py) | 正規化はインライン逆索引を持つ; `supersede` は検索グループのすべてのペアをバウンドなしで比較。いまは両方が一レイヤーを共有、二つのプロパティを試す: キャップは**ソート済み id** で截断、キャップ実行は到着順序に依存しない、そし呼び出し元はスコアラーより粗いブロッカーは真のマッチを静かに削除するので自分のトークナイザーを供給。各パスはキャップに到達したことを報告し、静かにより短いキューを返さない。 |
| アーティファクト証拠ノード がサイトに達する | ✅ | [`tesserae/raganything_adapter.py`](../../tesserae/raganything_adapter.py), [`tesserae/site/raw_view.py`](../../tesserae/site/raw_view.py) | 図表、テーブル、式は一等市民 `Artifact` ノードになり、各 id はアーティファクトの kind とコンテンツハッシュのみから生成され、ドキュメント、パス、キャプション、ページはない。図表はさらに生ページとコンテンツアドレス指定バイト `raw-assets/` 配下（テーブルと式はアセット持たない — その内容*は*記述）、そしてプロジェクト内に資産が解決された図表について `drill_down` は `asset_path` / `asset_sha256` / `asset_site_path` を引き渡す。オーナーごとのファクト — kind, page, caption, ordinal — は `part_of` エッジに乗る、そのノードは設計でドキュメント不可知であり 2 つのドキュメント図書が一枚を印字したら 2 番目のページを失うだろう。証拠は**グラフキャンバスオフ**: 全アサーション層は永遠に除外。[rag-anything](integrations/rag-anything.ja.md) を参照。 |
| プランナーがグラフを歩き、書き込みを提案 | ✅ | [`tesserae/ask_planner.py`](../../tesserae/ask_planner.py) | カタログは 7 つの射影プリミティブを持ち、グラフを歩く方法なし; `compile_context` はそれを結び、ビューユニオンをレジストリから補間。プランナーは `proposed_write` も返しうる — ノードとエッジは*質問*が主張したことのみを根拠 — **提案、決して実行ではない**: 来歴は常にヌル、だから `graph_write` はエージェントキー・外部アンカーを持つ呼び出し元が供給するまで拒否。 |
| 読み取り監査 — グラフを読んだのは誰 | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py), [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | アクセスカウントは不使用による忘却を駆動、だが何もが*誰を*引き起こしたかを記録しません。`TESSERAE_READ_AUDIT=1` はアクセスカウントがバンプされるところで `{tool, actor, node_ids, at, tesserae_version}` を記録します — 1 行が、その呼び出しが数えたすべてのノードを名指しします。ただし `fresh_insights` はノードごとにバンプするので 1 ノードにつき 1 行を書き、何も表面化しない呼び出しは 1 行も書きません — `read_audit` からアクター単位の集計付きで読み戻せます。**既定でオフ**、そしてゲートはストアを開く前に置かれます — テーブルを作ること自体が書き込みだからです。[agent memory](agent-memory.ja.md#忘却--削除されない) を参照。 |
| `tesserae schema-drift` がファーストクラス動詞 | ✅ | [`tesserae/schema_drift.py`](../../tesserae/schema_drift.py) | サブタイプ提案は `lab` のみを通して到達可能。提案は `.tesserae/schema-drift-proposals.json` に生き、ノードメタデータではない — そのメタデータキーは増分コンパイルを生き残り、全コンパイルで消えるだろう、バイト冪等ブラインドスポット、このリポは 4 回ヒット。`SUGGESTED_SUBTYPE` として表面化; **昇格は人的編集のまま** `ResearchNodeType` へ、その後 `"approved": true` と `TESSERAE_SCHEMA_DRIFT_APPLY=1`。 |
| ポータブルコンパイル + エージェント-書き込みロック | ✅ | [`tesserae/locking.py`](../../tesserae/locking.py) | ロックは `if fcntl is None: yield` — Windows ではそれはロックなし、そしてエージェント-書き込みオーバーレイは 2 つの非同期 append がアネックス行を破く唯一のパス。いまは存在する場所では `flock(2)`, 存在しない場所では `msvcrt.locking`（1 バイト範囲にピン、msvcrt ロックはファイル位置から）。二つのプリミティブもないプラットフォームは警告する（プロセスごと 1 回のみ、黙ってではなく）。スキップ再生行はいまリント検出（`AGENT_WRITE_SKIPPED`）、stderr 警告のみではない。 |
| サイドカーレジストリ | ✅ | [`tesserae/sidecars.py`](../../tesserae/sidecars.py) | すべての `.tesserae/` エントリがその所有者、種類（`derived` / `accumulated` / `cache` / `scratch`）、削除コストを宣言 — そして `safe_to_delete` は別フィールド、`cache` がモデルから来た回答はドロップ安全ではない、`derived` ファイルは人的承認を実施しうるから。Doctor の `sidecars` チェックは実ディレクトリを読み込む。[sidecars](sidecars.ja.md) を参照。 |
| Kuzu は export、決してストアではない | ✅ | [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | 一方向の支配: `tesserae export kuzu` は `graph.kuzu` を書き込み、コンパイルまたはランタイムパスは何も読み戻さない — `read_graph` はエクスポートが由来するグラフに対して検証できるためだけに保持される。[architecture § Kuzu export](architecture.ja.md#kuzu-エクスポート) を参照。 |

## 認知メモリとスコープ — v0.29.0 → v0.31.0（2026 年 8 月）

グラフに、何が書かれたかだけでなく*何が起きたか*を知らせたサイクルです: 結果が
取り込みを生き延び、そこから因果エッジが一本導かれ、これまで黙っていた劣化が
声を上げるようになりました。

| 機能 | 状態 | ソース | 備考 |
|---|---|---|---|
| コードレイヤーのオプトイン化 | ✅ | `cli.py`, [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | `compile` は既定でコードシンボルを取り込まなくなりました。大きなリポジトリでは他のすべてを数で圧倒し、検索を押しのけていたためです。`tesserae code ingest` で CodeGraph を意図的に接続できます。[ingest](ingest.ja.md) を参照。 |
| 隠れていた検索面の開放 | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | バイテンポラルおよびビュー選択のパラメータは実装もテストも済んでいたのに、MCP からは届きませんでした。`search_facts` は `current_only` に加えて `as_of`（過去の時点での回答）を受け取ります — 異なる時計なので**同時指定は拒否**され、`undated_included` が返された行のうち日付を持たないものの数を報告します。 |
| 劣化を声に出す | ✅ | [`tesserae/lint.py`](../../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../../tesserae/ingest/orchestrator.py) | 三つの沈黙した失敗を明示化しました: 何も生まなかったバイナリ取り込み、日付のない区間カバレッジ（`INTERVAL_COVERAGE`）、捨てられた非テキストコンテンツ。沈黙が成功と読まれていましたが、もう読まれません。 |
| ソース由来の `first_seen_at` | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py), [`tesserae/session_graph.py`](../../tesserae/session_graph.py) | ノードの日付は、コンパイル時の壁時計ではなく、そのソースが取り込まれたパスから決まります — 再実行でも同じ日付になり、バイト冪等性が保たれます。 |
| 手続き的検索プール | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | `context` は手続き的記憶 — 何を実行し、どうなったか — のための枠を予約しますが、それは既定で与えられるものではなく**来歴によって獲得**されます。枠を正直に埋められないときは `PROCEDURAL_POOLS` lint が報告します。 |
| ツール結果はターン | ✅ | [`tesserae/session_event.py`](../../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | 終了コードとエラーフラグが取り込みを生き延び、`Event` ノードに刻まれます。グラフは失敗したコマンドと単に実行されただけのコマンドを区別できます。ホームディレクトリは入口で伏せられます。 |
| `recovers` エッジ | ✅ | [`tesserae/session_recovery.py`](../../tesserae/session_recovery.py) | 唯一の因果エッジ:「あれが失敗したあとにこれが成功した」を、ツール・プログラム系統・作業ディレクトリ・オペランドが一致する一つのセッション内の二つの**観測された**結果から導きます。`CAUSAL_EDGE_TYPES` は意図的に要素一つです。[セッション履歴](session-history.ja.md) を参照。 |
| 憲章によるドメイン構造 | ⚠ | [`tesserae/charter.py`](../../tesserae/charter.py), `cli.py` | コミュニティ検出はドメイン語彙を*提案*し、憲章が明示的な再編のあいだそれを*所有*します。検出は決定的でも安定ではないからです（15 ノードの文書ひとつでメンバーの約 29% が動きます）。`tesserae domains status` がそれを読みます。**まだ `compile` は生成しません** — それまでこのコマンドは "no charter yet" と報告します。 |
| 共有ディスク上のマルチホスト | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID` が*誰がそのレコードを書いたか*で prune と上書きの範囲を定め、一つの共有ディスクを使う N 台のサーバーが互いのセッション履歴を消し合うのをやめさせます。[セッション履歴](session-history.ja.md) を参照。 |

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
| マシン全体のセットアップ + 依存関係 | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` はグローバルな LLM デフォルトを書き込み、オプション依存関係（memex、raganything）をインストールします。`tesserae config deps` は一覧/インストール。`tesserae init` は memex を提案します。プロジェクトごとの config は引き続き優先されます。 |

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
