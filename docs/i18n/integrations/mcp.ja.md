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
| `schema` | 管理された node、edge、wiki-kind の語彙 |
| `graph_summary` | アクティブなプロジェクトの node + edge 数と種別分布 |
| `search_nodes` | 公開グラフ node を `query`、`type`/`types`、`kind`、`limit`、ハイブリッド `mode`/`weights` でフィルタ。`include_superseded` で廃止済み node も表示 |
| `node_context` | ある node とその接続 edge、隣接 node。`use_ppr` は 1-ホップ走査ではなくパーソナライズド PageRank で隣接をランキングし、`include_superseded`・`limit` で結果を制限 |
| `embedding_status` | ハイブリッド検索を駆動するアクティブな埋め込みバックエンドを報告 |
| `search_facts` | グラフから射影された時系列ファクト（Graphiti スタイル）。`current_only` で現行ファクトのみ |
| `timeline` | `valid_from` で順序付けられたファクトの縦断的ビュー |
| `graph_ppr` | 1 つ以上の `seed_node_id` をシードとするパーソナライズド PageRank で最も関連性の高い top-K node を返す。`alpha`、`directed`、`edge_type_weights` を調整可能 |
| `wiki_page` | ある node のコンパイル済み markdown ページ本文と、それが参照する内部リンク |
| `raw_source` | 元のソース markdown（16 KB を上限としてキャップ） |
| `lint_report` | 直近のコンパイル時 lint 結果（64 KB を上限としてキャップ） |

**オンデマンドコンテキストコンパイラ**（Phase 7）

| Tool | 用途 |
|---|---|
| `compile_context` | `query` または明示的な `seeds` に対して、調整された**引用付き**コンテキスト文書をコンパイル。深さ制限付きサブグラフ（`depth`、1–10、既定 2）を走査し、PPR でランキングして文字 `budget`（既定 32000、`0` で無制限）を埋める。既定は決定論的で、`synthesize: true` で LLM が書く叙述型 "topic" スライスを生成。`body`、`citations`、`selected_node_ids`、`char_budget_used` を返す |
| `list_communities` | 後コンパイルパスが生成した `COMMUNITY_SUMMARY` node をメンバー数順に列挙（`min_size`、`limit`）。`node_context` で `summarizes` edge をたどってメンバーへ回帰 |
| `fresh_insights` | エビングハウス式の減衰スコア（新しく・最もアクセスされた順）でランキングされたセッション発見。廃止された近似重複は除外。任意で `kind`、`limit`、`include_superseded` |

**セッションメモリ**（[sessions.md](sessions.md) 参照）

| Tool | 用途 |
|---|---|
| `list_sessions` | アクティブなプロジェクトのセッションエンベロープ（id、started_at、title、files_touched、発見数）。`since`、`limit` |
| `find_session_findings` | `discussed_in` / `references` を介して `node_id` にリンクされた全セッション発見。`kinds`（insight / decision / question / todo / hypothesis / takeaway）でフィルタ可能 |

**Q&A とレジストリ**

| Tool | 用途 |
|---|---|
| `ask` | 設定されたメモリバックエンド（raganything、cognee、またはコンパイル済み wiki）経由の自然言語 Q&A。`backend`、`top_k`。`scope`/`scope_aliases` で複数 vault へのファンアウト。多アカウントルーティング用の `claude_config_dir` |
| `list_projects` / `register_project` / `activate_project` / `unregister_project` | マルチプロジェクトレジストリの制御 |

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
