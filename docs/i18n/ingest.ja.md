# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a> · <a href="ingest.de.md">Deutsch</a></p>
<!-- translations:end -->
単一のドキュメントファイルまたは URL をナレッジベースにマージします。

## 使い方

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>` は 1 つ以上のローカルファイルパスまたは `http(s)` URL です。URL は取得され、
markdown に変換され、来歴を示す front-matter（`source_url`、`fetched_at`、`content_sha256`、
そして検出された場合は `arxiv_id`）とともに `data/ingested/<slug>.md` に保存されてからマージされます。
プロジェクト外部のローカルファイルは `data/ingested/` にコピーされ、追跡対象のソースになります
（後で完全コンパイルを行うとまったく同じものが再現されます）。

URL の取り込みにはオプションの追加パッケージが必要です。

    pip install tesserae[ingest-url]

## 動作の仕組み

デフォルトでは `ingest` は新しいソースを増分コンパイルでマージします — コーパス全体を
再抽出することはありません — そして結果は完全コンパイルとバイト単位で同一です（増分パスが
扱えないケースについては、自動の完全再コンパイルフォールバックが正しさを保証します）。
コーパス全体の完全再コンパイルを強制するには `--full` を渡してください。

## フラグ

- `--full` — コーパス全体の完全再コンパイルを強制します。
- `--dry-run` — 取得して、何が取り込まれるかを報告します。グラフは書き込みません。
- `--title` — タイトルの上書き。素の URL に便利です。
- `--source-kind` — ソースの分類を上書きします。

## コンセプトレイヤー（`--extractor`）

Tesserae は LLM wiki なので、`compile` は**デフォルトでコンセプト/クレームレイヤーを
構築**します（`--extractor llm`）: 各ドキュメントを設定済みの LLM プロバイダ —
`llm_provider` に従い **codex / claude / Anthropic API** — を通して読み、コンセプト、
クレーム、ケイパビリティ、技術用語、エビデンスのスパン、そしてそれらを結ぶ型付き
エッジを生成します。これが、グラフが単に *「どのファイルがそれを言ったか」* ではなく
*「これはどんなアイデアで、どう関係しているか」* に答えられるようにするレイヤーです。

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

LLM バックエンドが未設定/未認証の場合、compile は**決定論的（deterministic）**
エクストラクタ（構造のみ — ソース、セクション、明示的リンク）にデグレードし、警告します。
明示的に指定することもできます — 高速でキー不要、バイト安定であり、CI /
再現可能モードです:

    tesserae compile --extractor deterministic

### どのアカウントを消費するか指定する（`llm_claude_config_dirs`）

`claude` プロバイダでは、Tesserae はログイン済みの Claude CLI アカウントを順に切り
替える。レート制限に達したアカウントは次のアカウントへ引き継がれるため、実行の残り
すべてが決定論的抽出に落ちることはない。既定では `~/.claude*` ディレクトリを自動検出
する。

どのアカウントをどの順序で消費するかを厳密に指定するには、`.tesserae/config.json`
（プロジェクト）または `~/.tesserae/config.json`（グローバル）に
`llm_claude_config_dirs` を設定する:

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

このリストが最終的な権威であり、リスト外のアカウントは一切試されない。さらにこの設定
は**周囲の `CLAUDE_CONFIG_DIR` よりも優先される**。この変数は Claude Code セッション
が生成するすべてのプロセスに継承されるため、放置するとコンパイル全体がそのセッション
1つのクォータに縛られる。何も設定されていなければ、`CLAUDE_CONFIG_DIR` が最初に試す
アカウントとして使われる。

設定されたすべてのアカウントが使用上限に達すると、コンパイルは文書ごとに問い直す代わ
りに残りの実行で LLM 呼び出しを止め、それらの文書を `fallback: true` と記録して通知
する。上限がリセットされた後、全体を再コンパイルせずに回復するには:

    tesserae compile --changed-only --retry-fallbacks


**コスト意識型（`selective-llm`）** — マッチするドキュメントだけを LLM に通し、
残りは決定論的に処理します:

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

同じフラグは `tesserae extract <paths>`（スタンドアロン）と
`tesserae compile <paths>`（アドホックなパス取り込み）でも機能します。

**チューニング:**

- `--llm-provider codex|claude|anthropic` — プロバイダを上書き（デフォルト:
  config の `llm_provider`）。
- `--llm-model` — エクストラクタで使うモデル（デフォルト: プロバイダのデフォルト）。
- `--llm-include <glob>` — `selective-llm` で、どのファイルを LLM に通すか
  （複数指定は繰り返し。パターンは絶対パスの任意の位置にマッチします。例:
  `"*docs/superpowers*"`）。
- `--llm-limit N` — LLM に到達するファイル数の上限（残りは決定論的のまま）。

**デフォルトのタイムアウトはありません。** 大きな設計ドキュメントは大量の JSON を生成し、
数分かかることがあります。抽出は黙って打ち切られるのではなく完了まで実行されます
（タイムアウトはオプトインのみです）。

**実際のコーパスに対して堅牢。** ノイズの多い、あるいは遅いドキュメント 1 つがコンパイル
全体を中断させることは決してありません: あるドキュメントでの LLM の失敗（認証、エラー、
パース不能な生成）は*その*ドキュメントについて決定論的ベースラインへフォールバックし、
制御された語彙の外にあるエッジやノードの型は破棄され、コンテンツをキーとするキャッシュに
より、変更されていないドキュメントの再コンパイルは以前の抽出を再利用します。

> `claude-cli` / `selective-claude` というエクストラクタ名（および `--claude-*`
> フラグ）は `llm` / `selective-llm`（および `--llm-*`）の非推奨エイリアスです。
> まだ動作しますが、非推奨の注意が表示されます。

## コンパイルスコープの管理（`sources`）

`tesserae compile`（引数なし）はプロジェクトの `sources` リストにあるディレクトリを
コンパイルします。そのリスト — **local または global** — は `sources` サブコマンドで
管理します:

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

プロジェクト内のパスはプロジェクト相対で保存されます（ポータブル）。外部のものは
絶対パスで保存されます。どちらもコンパイル時に解決されるため、global なソースも
local なソースと同じようにコンパイルされます。（追加は解決後の場所で重複排除されるため、
同じディレクトリの絶対パス形式と `../` 相対形式が二重にカウントされることはありません。）

## 関連コマンド

- `tesserae compile`（引数なし）は追跡対象のコーパス全体を再抽出します。
- `tesserae ingest <x>` は 1 つのソースを増分的に追加します。
- `tesserae code ingest` は Python ソースからコードグラフを生成します（別のコマンドです）。
