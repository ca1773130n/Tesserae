# MCP — Tesserae を Claude Code、Codex、Cursor に接続する

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.zh.md">中文</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.fr.md">Français</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae は [Model Context Protocol](https://modelcontextprotocol.io) の stdio サーバーを同梱しており、コンパイル済みの型付きグラフを任意の MCP 対応クライアント（Claude Code、Codex CLI、Cursor、Claude Desktop など）に公開します。サーバーは MCP の 3 つの完全な面 — **tools**、**resources**、**prompts** — を提供するため、クライアントはオンデマンドでグラフを問い合わせることも、正規化された URI から低コストでコンテキストを供給することもできます。

## 前提条件

サーバーは `.tesserae/graph.json` を読み込むため、最初に一度コンパイルしておく必要があります:

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

ソースが変わったらいつでも再コンパイルしてください。サーバーは再起動なしで次回の tool 呼び出し時に新しいグラフを読み込みます。

## 1) クライアント設定を生成する

```bash
tesserae projects mcp-config
```

おおよそ次のような JSON スニペットを出力します:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

正確なパスは現在のプロジェクトから補完されます。サーバーエントリー名を `tesserae` 以外にしたい場合は `--name <alias>` を渡してください。

## 2) MCP クライアントに貼り付ける

| クライアント | 設定ファイルの場所 |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json`（または `~/.config/claude-code/mcp-servers.json`） |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → JSON を貼り付け |
| Hermes | `~/.hermes/config.toml`（`mcp-config --format hermes` が出力する TOML 相当ブロックを使用） |

編集後はクライアントを再起動してください。次のセッションで接続され、Tesserae のサーフェスが検出されます。

## 3) クライアントから見えるもの

### Tools — モデルから呼び出される

各 tool はオプションの `graph_path` または `project`（レジストリのエイリアス）を受け取るため、1 つのサーバーが呼び出しごとに任意の登録済み vault を解決できます。省略時はアクティブなプロジェクトにフォールバックします。

**グラフのクエリと検索**

| Tool | 用途 |
|---|---|
| `graph_map` | **ここから始めてください。** グラフ階層の予算付きマップ — Descent の入口です。スコープなしで呼ぶとルートのカード集合（件数、上位ハブ、最も粗いコミュニティごとに 1 枚）、`scope='<カードの scope_id>'` はデンドログラムを 1 段降り、`org:root` はエージェント組織ツリーをたどります。検索語を当てずにエージェントの位置を定めます |
| `schema` | 管理された node、edge、wiki-kind の語彙 |
| `graph_summary` | アクティブなプロジェクトの node + edge 数と種別分布 |
| `search_nodes` | 公開グラフ node を `query`、`type`/`types`、`kind`、`limit`、ハイブリッド `mode`/`weights` でフィルタ。`include_superseded` で廃止済み node も表示 |
| `node_context` | ある node とその接続 edge、隣接 node。`use_ppr` は 1-ホップ走査ではなくパーソナライズド PageRank で隣接をランキングし、`include_superseded`・`limit` で結果を制限 |
| `embedding_status` | ハイブリッド検索を駆動するアクティブな埋め込みバックエンドを報告 |
| `search_facts` | グラフから射影された時系列ファクト（Graphiti スタイル）。ランキングはファクトの内容（主語・述語・目的語・エビデンス）のみで行い、シリアライズしたファクト全体は見ないため、ID やメタデータの断片は一致しません。`dated`（`any`、`dated`、`undated`）は利用可能な `valid_from` を持つかどうかで絞り込みます。`current_only` で現行ファクトのみ、`as_of` は過去時点での回答。両者の併用は拒否されます（異なる時計を表すため）。`undated_included` は返された行のうち日付を持たない件数を報告します |
| `timeline` | パース済みの `valid_from` で順序付けられたファクトの縦断的ビュー。日付のないファクトは日付のあるファクトの後ろにまとめられ、混在させずに `undated_events` として件数が返ります。`dated`（`any`、`dated`、`undated`）は利用可能な `valid_from` を持つかどうかで絞り込みます。`as_of` は過去時点での回答（有効期間に対する時点指定であり、範囲の下限ではありません）。`undated_included` は返された行のうち日付を持たない件数を報告します。日付のないファクトは `as_of` でも残るため、この件数だけが薄い回答と完全な回答を見分ける手がかりになります |
| `graph_ppr` | 1 つ以上の `seed_node_id` をシードとするパーソナライズド PageRank で最も関連性の高い top-K node を返す。`alpha`、`directed`、`edge_type_weights` を調整可能 |
| `wiki_page` | ある node のコンパイル済み markdown ページ本文と、それが参照する内部リンク |
| `raw_source` | 元のソース markdown（16 KB を上限としてキャップ） |
| `verify_claim` | トリプルを 1 つだけグラフに対して検証します — 完全一致の照会で、LLM もあいまい一致もランキング結果もありません。`{verdict, reason, triple, citation, provenance, advisory}` を返し、`verdict` は `SUPPORTED`（エッジが存在し、**その証拠が文書の逐語スパン**である）、`PRESENT_UNEVIDENCED`、または拒否です。手元に散文しかないときは `search_nodes` → `verify_claim` とつなげてください |
| `doctor_run` | ヘルスチェックを実行し、レポートを JSON（`findings`, `exit_code` 0/1/2）で返します。**常に読み取り専用** — 修正が MCP 経由で走ることはありません。修復は CLI の `tesserae doctor --fix` を使ってください |
| `doctor_report` | `.tesserae/doctor-report.md` の内容（64 KB を上限としてキャップ）。`tesserae doctor` を実行するまでは空です |
| `lint_report` | 直近のコンパイル時 lint 結果（64 KB を上限としてキャップ） |

**オンデマンドコンテキストコンパイラ**（Phase 7）

| Tool | 用途 |
|---|---|
| `compile_context` | `query` または明示的な `seeds` に対して、調整された**引用付き**コンテキスト文書をコンパイル。深さ制限付きサブグラフ（`depth`、1–10、既定 2）を走査し、PPR でランキングして文字 `budget`（既定 32000、`0` で無制限）を埋める。既定は決定論的で、`synthesize: true` で LLM が書く叙述型 "topic" スライスを生成。`body`、`citations`、`selected_node_ids`、`char_budget_used` を返す。`view` は名前付きエッジパーティション（`semantic`、`temporal`、`causal`、`entity`）へのウォークを制限します；名前の配列を渡すと各ビューごとに 1 つのウォークを実行して融合させます（加重 RRF）。ビューを要求すると — 名前が 1 つでも複数でも — 各引用はそれに到達したビューを `via_views` で携えます |
| `get_handle` | 以前に `handle` として返された大きなペイロード（例: `preview` 付きの `compile_context`）をスライス（`offset`, `limit`）で取得します — すべてをコンテキストに流し込むのではなく、必要な分だけ後から取り寄せます |
| `list_communities` | 後コンパイルパスが生成した `COMMUNITY_SUMMARY` node をメンバー数順に列挙（`min_size`、`limit`）。`node_context` で `summarizes` edge をたどってメンバーへ回帰 |
| `fresh_insights` | エビングハウス式の減衰スコア（新しく・最もアクセスされた順）でランキングされたセッション発見。廃止された近似重複は除外。任意で `kind`、`limit`、`include_superseded` |

**セッションメモリ**（[sessions.md](sessions.ja.md) 参照）

| Tool | 用途 |
|---|---|
| `list_sessions` | アクティブなプロジェクトのセッションエンベロープ（id、started_at、title、files_touched、発見数）。`since`、`limit` |
| `find_session_findings` | `discussed_in` / `references` を介して `node_id` にリンクされた全セッション発見。`kinds`（insight / decision / question / todo / hypothesis / takeaway）でフィルタ可能 |
| `find_code_symbol_mentions` | セッション発見を、それが言及する `CodeFunction`/`CodeClass`/`CodeMethod` シンボルへ展開（オプトインの insight↔symbol リンクパスが生成する `discusses` edge を使用）。コードレイヤーはオプトインです。`codegraph` の `external_tools` エントリがなければ、何も返しません |
| `activity_summary` | 登録済みプロジェクト横断の日次 / 週次ダイジェスト — セッション、発見、git コミット、PR、取り込んだ文書。それぞれ**自身の**タイムスタンプで窓を切り、セッションの `started_at` は決して使いません。決定的なマークダウンを描画し、無効化しない限り LLM による語りを先頭に付けます |
| `query_decisions` | 期間内の登録済みプロジェクト横断の決定: Claude Code の `AskUserQuestion` から決定的に解析した明示的な**人間**の選択（質問と選ばれた選択肢）に加え、会話から掘り出したエージェントの決定 |

**エージェントメモリと書き戻し**（[agent-memory.ja.md](../agent-memory.ja.md) を参照）

| ツール | 用途 |
|---|---|
| `agent_view_explain` | エージェントスコープのビューを*読み込まずに*説明します: 解決モード（worker / manager / org）、メンバーエージェント、各 L1 アーティファクトのパスとノード数、そして `distilled_through` の鮮度ウォーターマーク |
| `drill_down` | 蒸留物の `member_ref` を元の L0 ノードへ解決します — 蒸留された可視性を越える、マネージャーの明示的で監査記録の残るエスカレーションです。状態は `alive` / `changed` / `absorbed` / `gone` を返し、呼び出しはすべてサイドカーに記録されます |
| `read_audit` | 誰がこのグラフを読んだか。記録された読み取りイベント (`tool`、`actor`、`node_ids`、`at`、`tesserae_version`) を新しい順に返し、アクター別の集計も添えます。これにより、不使用による忘却を駆動するアクセス回数を読み手に帰属させられます。**オプトイン** — サーバープロセスに `TESSERAE_READ_AUDIT=1` を設定しない限り何も記録されません。常時オンの監査はすべての読み取りを書き込みに変えてしまうからです。フラグをオフにしても記録済みの行は読めます。`enabled` は現在の設定を報告します。`actor` / `tool` / `node_id` で絞り込めます |
| `graph_write` | 型付きノードとエッジをグラフへ直接書き込みます — マークダウンも抽出パスもありません。追記専用のオーバーレイに積まれ、コンパイルのプロデューサーとして再生されるため、**再コンパイルを生き延びます**。厳格です: 未知の型、証拠のないエッジ、このペイロードにも既存ノード id にも該当しないエンドポイントはいずれも拒否されます。**単に誤っているものを撤回する**には、代替を捏造せずに `retracts` エッジを誤ったノードへ **id で** 向けます — 対象は既定の読み取り (`search_nodes`、`fresh_insights`、`node_context`、`compile_context`) すべてから抑制されますが、`include_superseded: true` では依然到達でき、何も削除されません |

**Q&A とレジストリ**

| Tool | 用途 |
|---|---|
| `ask` | 設定されたメモリバックエンド（raganything、cognee、またはコンパイル済み wiki）経由の自然言語 Q&A。`backend`、`top_k`。`scope`/`scope_aliases` で複数 vault へのファンアウト。多アカウントルーティング用の `claude_config_dir` |
| `query` | LLM を使わない生の検索 — `tesserae query` と同じです。`backend='wiki'`（既定）はコンパイル済み Wiki に対する決定的な BM25 / セマンティック検索で、抜粋付きのランク結果を返します。`backend='raganything'` は、プロジェクトが有効化していればオプションのマルチモーダル RAG インデックスに問い合わせます。統合された出典付きの回答が欲しいときは `ask` を使ってください |
| `ingest` | 生の Web / テキストコンテンツ（例: ブラウザのクリップ）を、解決されたプロジェクトの知識グラフへ取り込みます |
| `list_projects` | 登録済みプロジェクトの一覧 |
| `register_project` | レジストリにプロジェクトを追加 |
| `unregister_project` | レジストリからプロジェクトを削除（特権的な「アクティブ」プロジェクトは存在しません） |

**ガイド付きセットアップ**

| Tool | 用途 |
|---|---|
| `tesserae_setup_plan` | 環境を検出してセットアップ計画を JSON で提案。読み取り専用 — `.tesserae/` には一切触れない |
| `tesserae_setup_apply` | （編集された可能性のある）計画を適用：`.tesserae/config.json` を書き込み、ゲートされたインストール/実行アクションを行う。`confirm_install_actions` / `confirm_run_actions` でゲート |

### Resources — モデルのコンテキストへ自動的に読み込まれる

クライアントが tool turn を消費せずに resource picker から取り込める URI:

- `tesserae://graph/schema` — `schema` tool と同じペイロードを静的コンテキストとして提供
- `tesserae://graph/summary` — アクティブなプロジェクトのサマリー
- `tesserae://lint-report` — 直近の lint レポートを markdown として提供

加えて、クライアントがオンデマンドで構築できる URI テンプレート:

- `tesserae://wiki/{kind}/{slug}` — コンパイル済み wiki ページ本文
- `tesserae://raw/{source_path}` — 任意の生ソース markdown

### Prompts — ワンクリックのリサーチテンプレート

これらはクライアントのスラッシュメニュー（例: Claude Code の `/` パレット）に表示されます:

| Prompt | 引数 | 動作 |
|---|---|---|
| `summarize-paper` | `slug`（必須） | `node_context` + `wiki_page` + 任意の `raw_source` を呼び出し、貢献、手法のスケッチ、主要な結果、限界、関連 node を含む構造化サマリーを返す |
| `find-related-work` | `topic`（必須）、`limit` | `search_nodes` + `node_context` を連鎖させて、関連度の根拠付きで上位 K 件の関連項目を返す |
| `compare-approaches` | `a`、`b`（両方必須） | 両者に対して `node_context` を取得し、性能主張については `search_facts` を取得; 統合付きの並列比較を返す |
| `gap-analysis` | `topic`（任意） | 未解決の論点、欠落しているベンチマーク、根拠の薄い主張を浮かび上がらせる |
| `triage-open-questions` | _なし_ | すべての `OpenQuestion` node を列挙し、トピックでグルーピングし、優先順位を提案する |

各 prompt は単一のユーザーメッセージにレンダリングされ、モデルに対してどの Tesserae tool をどう連鎖させるかを正確に伝えるため、モデルが毎回サーフェスを再発見する必要はありません。

## マルチプロジェクト: 1 つのサーバーに複数のヴォルトを登録する

`~/.tesserae/registry.json` の永続レジストリにより、同じ MCP サーバーが任意の登録済みプロジェクトを名前で解決できます:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

これ以降、`project` または `graph_path` を受け取るすべての tool は、フルパスを必要とせず `project: "research"` をレジストリで解決します。サーバーは登録済みの `graph_path` がまだ存在するかも検証し、再コンパイルが必要な場合は明確なエラーを返します。

### 登録済みすべてのヴォルトへのファンアウト

`ask` tool は `scope: "all-registered"` を受け取り、登録済みのすべてのプロジェクトに並列でクエリして集約結果を返します:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

`scope_aliases: ["research", "notes"]` で対象を絞り込めます。

## マルチアカウントの Claude CLI

`ask` tool が Claude CLI 経由でルーティングされ、複数アカウント（例: `~/.claude` と `~/.claude-personal2`）を使っている場合は、呼び出しごとに `claude_config_dir` を渡してください:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

サーバーはその呼び出しの間だけ `CLAUDE_CONFIG_DIR` をエクスポートし、終了後に元の値を復元します。呼び出し間でリークしません。

## 動作確認

MCP クライアントを再起動した後、接続を確認します:

- Claude Code: `/mcp` に `tesserae` が tool 数とともに表示されるはずです。
- Cursor: チャットバーの MCP アイコンに `tesserae: connected` と tool/resource/prompt の件数が表示されるはずです。
- Codex / Hermes: 任意の tool（例: `schema`）を名前で呼び出してレスポンスを確認してください。

何も現れない場合、`--graph` が既存の `.tesserae/graph.json` を指しているか再確認してください — サーバーは起動時および各 tool 呼び出し時にこれを検証するようになり、サイレントな 500 ではなく明確なエラーメッセージが表示されます。

## どこに位置づけられるか

MCP サーバーは型付きグラフへの**読み取りインタフェース**です。**書き込み経路**（ソースの取り込み、再コンパイル、RAG-Anything のような連携ツールの更新）には CLI を直接使ってください。両者は疎結合です: CLI が `.tesserae/` を更新し、MCP サーバーは次の tool 呼び出しでそこにあるものを読み取ります。
