# Obsidian 双方向同期 — 提案設計

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.fr.md">Français</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **ステータス: 出荷済み (Tier 1, v0.5.0)。** 以下で説明するオーバーレイリーダー、ユーザーノート追記ゾーン、ウォッチモード、孤立ページの削除は、`tesserae vault sync` の背後で実際に動作します。本ページは設計の根拠とユーザーガイドを兼ねます。マルチヴォルト連合 (Tier 3) は依然としてスコープ外です。

以前の [Obsidian エクスポート](obsidian.ja.md)は厳密に一方向でした: `.tesserae/graph.json` 内の型付きグラフがヴォルトへ射影され、`project compile` が射影されたファイルを上書きします。`obsidian-sync` は逆方向を追加します — Obsidian で説明を編集すると、再コンパイル後もそれが残ります。

このドキュメントは、データモデルを破綻させずにそれがどう動作するかを明文化します。

## 戦略的な方針転換、率直に

現在の README はライブ編集を否認しています:

> Tesserae はライブ編集ではなくソースからのコンパイルを選びます。UI でノートを編集したい場合は Logseq または Obsidian を使ってください。

双方向同期は、フィールドの一部について**この契約を変更します**。これは意図的に行う価値があります。目標は「Obsidian をエディタにする」ではなく — 「ユーザーの Obsidian での編集が再コンパイル時に黙って消されないようにする」ことです。

## 核心アイデア: マージではなくオーバーレイ

同じ node のダイバージした 2 つのコピーをマージしようとする代わりに、ヴォルトを射影に対する**差分レイヤー**として扱います:

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` は `.tesserae/` 配下に置かれ、手書きされるものではなく**計算される**ものです。コンパイルのたびに Tesserae はヴォルトを巡回し、射影された各ページを前回の射影が書き出した内容と比較し、ユーザーによる変更をすべてオーバーレイエントリとして記録します。最終的なグラフは `base_graph` にオーバーレイを適用したものです。次の射影はその結果をディスクに書き戻します。

ラウンドトリップは安定しています。ソース側に変更のない同じヴォルトを再コンパイルしても差分は生じません。

## フィールドごとの所有権

node の各フィールドには所有者があります。所有権はソースとヴォルトが食い違ったときに何が起こるかを決めます。

| フィールド | ソース所有 | ヴォルト上書き可 | 備考 |
|---|---|---|---|
| `id`, `type` | yes | no | スキーマ管理対象。extractor が所有 |
| `name` | initial | yes | 正規名はユーザーの方が extractor よりよく知っていることが多い |
| `aliases` | initial | yes | ヴォルトからは追記のみ。ヴォルト側のエントリは常に保持される |
| `description` | initial | **yes** | Obsidian での最も一般的な編集対象 |
| `source_path` | yes | no | 由来情報。編集で消すことはできない |
| `metadata`（宣言済みキー） | initial | yes | 例: `arxiv_id`, `github_repo` — ユーザーが訂正可能 |
| `metadata.user.*` | n/a | yes | ユーザー専用キーの予約名前空間。extractor は書き込まない |
| Outgoing edges（型付き） | yes | no | edges はオントロジーに属し、ヴォルトには属さない |
| ユーザーが書いた新しい wikilink | n/a | yes | `edge_type=user_link` として浮上させ、グラフに書き込む |
| `<!-- user-notes -->` 本文ブロック | never written | always preserved | 射影器が決して触らない追記専用ゾーン |

## 競合ケースとデフォルト

| ケース | デフォルト | 理由 |
|---|---|---|
| ヴォルトの `description` が再抽出されたソースの `description` と異なる | **ヴォルトの勝ち**、`.tesserae/lint-report.md` の「diverged fields」配下に記録 | ユーザー編集を尊重する: ユーザーが明確に編集を意図していた。監査証跡で後から見直せる。 |
| ソースファイルが削除されたが射影ページがヴォルトに残っている | node をグラフから除去し、`.tesserae/orphans.md` に列挙 | 存在についてはソースが正典。孤児ログにより復元するか受け入れるかを判断できる |
| ユーザーが存在しない slug への wikilink を書いた | トゥームストーン node（type `Stub`）を作成し、lint レポートに浮上させる | ユーザーの意図を捨てず、整理のためにフラグを立てる |
| ユーザーがスキーマの知らない frontmatter キーを追加した | `metadata.user.<key>` として保存し、決して上書きしない | 型付きグラフを汚さずに前方互換性を確保 |
| 異なるマシンの 2 つのヴォルトが同じ node を編集し、両方が Obsidian Sync で同期されている | **v1 の範囲外。** ファイルシステムレベルで最後の書き手が勝つ。 | 真のマルチヴォルト連合は Tier 3。実際のユースケースが現れるまで延期 |

## ユーザーノート追記ゾーン

射影された各ページには、射影器が決して触らないフェンス付きゾーンが用意されます:

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

実用的な効果は 2 つ:
1. ユーザーは任意のページに注釈（例:「自分のノートの第 4 章を参照」）を、リビルドで失うことなく付けられる。
2. プルパスはユーザーノートブロックを wikilink について走査し、それらをオントロジー型 `user_link` の edges として浮上させ、形式的な edge type を汚さずにグラフ到達性を与える。

## リモート転送 — 明示的な非ゴール

Tesserae は同期サーバー、認証レイヤー、競合解消デーモン、ホスト型ヴォルトを構築**しません**。ここでの「双方向」は「コンパイルがヴォルトから読む」という意味であり — コンパイルを実行するマシンへヴォルトをどう届けるかはユーザーの問題で、すでに存在するツールで解決されます:

| スタック | コスト | 備考 |
|---|---|---|
| Obsidian Sync | 有料、$4-8/月 | E2E 暗号化、公式、極めてシンプル |
| iCloud / Dropbox / OneDrive | OS バンドル | 機能はするが競合 UX は厳しい |
| Syncthing | 無料、セルフホスト | 一人クロスデバイスに最適 |
| Git（ヴォルトをコミット） | 無料 | 技術者には競合 UX が最良 |
| LiveSync（CouchDB プラグイン） | 無料、サーバー要 | リアルタイムのマルチデバイス |

5 つすべてがオーバーレイモデルと互換です。Tesserae はヴォルトをミューテーションのストリームではなく、ディスク上のファイルとして見るためです。

## CLI 表面

`tesserae vault sync` はヴォルトの編集を型付きグラフに適用し、再射影します:

```bash
# オーバーレイを一度適用: ユーザー編集をプルし、ヴォルトへ再射影。
tesserae vault sync

# まず何が変わるかを検査。.tesserae/diverged-fields.md を書き出し、
# 適用も再射影もしない。
tesserae vault sync --dry-run

# この呼び出しで特定のヴォルトを指定（解決順:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/）。
tesserae vault sync --vault ~/Documents/tesserae-vault

# そのヴォルトパスを以後のコマンドの既定値にする。
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# 長時間ウォッチ: ヴォルトが変わるたびにオーバーレイを再適用。
# Ctrl-C で停止。--interval でポーリング間隔を調整（既定 1.5 秒）。
tesserae vault sync --watch --interval 1.5

# ソースノードがもう存在しない射影ページを削除（射影器は
# 上書きのみで決して削除しない）。ユーザーノートを持つページは
# --force-prune-with-notes も渡さない限り保持される。
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

`/tesserae:obsidian-sync` スラッシュコマンドがこれをラップし、`tesserae refresh`
（および `/tesserae:refresh` マクロ）は import → compile → sync チェーンの最終ステップとして
オーバーレイを実行します。

## 提供状況

| Tier | スコープ | ステータス |
|---|---|---|
| **1a** | オーバーレイリーダー: ヴォルトを巡回し `vault_overrides.json` を構築し、同期時に適用。ダイバージェンスは `.tesserae/diverged-fields.md` に記録。 | 出荷済み |
| **1b** | ユーザーノート追記ゾーン: 射影器は `<!-- user-notes:start --> ... <!-- user-notes:end -->` ブロックを決して触らない。 | 出荷済み |
| **2** | ウォッチモード: 長時間動作する `obsidian-sync --watch` がヴォルトの変更に応じてポーリングループでオーバーレイを再実行。 | 出荷済み |
| **3** | マルチヴォルト連合: グラフがヴォルトごとの由来情報を保持し、同期されたヴォルト間の同時編集をサポート。 | 実際のユースケースが現れるまで延期 |

## 非ゴール（明示的に）

- 同期サーバー / 認証 / ホスト型バックエンド。
- Obsidian 内でのリアルタイム共同編集（必要なら LiveSync を使ってください）。
- すべてのフィールドをラウンドトリップさせるために extractor を書き直すこと — オーバーレイテーブル外のものについては、ソース markdown が引き続き正典である。
- 静的 HTML サイトの同期（`build-site` は引き続き射影専用）。

## 確定した決定事項

これらは設計時の未解決事項でした。出荷済みの Tier 1–2 実装は次のように決着させました:

1. **lint レポートの形。** ダイバージしたフィールドは `lint-report.md` のセクションではなく、専用ファイル `.tesserae/diverged-fields.md`（`--dry-run` 時および適用のたびに書き出し）として出力され、git で差分を取れます。
2. **トゥームストーン node の型。** `Stub` を実スキーマ型として追加するか、それとも `OpenQuestion` に `_kind: stub` 識別子を付けて流用するか。提案: 実型、名前は `Stub`、公開インデックスから非表示。
3. **コンパイル時プルのデフォルト。** デフォルト ON か OFF か。提案: 設定されたパスにヴォルトが存在する場合は ON。初回起動時のみ確認プロンプトを出し、ユーザーが意図的にオプトインできるようにする。
4. **差分のための「前回の射影」とは何か。** スナップショットを `.tesserae/vault_snapshot.json` に保存するか、それともコンパイルのたびに即時に再射影するか。提案: スナップショット方式で、各コンパイルの終わりに書き出す。安価で、extractor の非決定性がオーバーレイに漏れることを避けられる。
5. **多言語ヴォルト射影。** 現在の射影は単一言語（ソース）です。オーバーレイはロケール対応にすべきか（例: 韓国語ヴォルトでの `description` 編集は韓国語射影のみに適用）。提案: v1 の範囲外。ヴォルトはプロジェクトの主言語に合わせた単一言語とする。

## これを `obsidian.md` でどう露出するか

ユーザー向けガイドは「ヴォルトを読んでクエリできる」に焦点を絞り続け、1 行サマリ「Obsidian でフィールドを編集すれば再コンパイル後も残ります。完全なモデルは [obsidian-sync.md](obsidian-sync.ja.md) を参照。」とともに往復の話のためにここへリンクします。
