# `tesserae doctor` — プロジェクトのヘルスチェック

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` は Tesserae ワークスペースを端から端まで検査し — 初期化、
グラフの整合性、レジストリの一貫性、鮮度、ロック、LLM ログイン、ディスクの
衛生状態 — チェックリストを出力します。**デフォルトでは読み取り専用**です。`--fix`
は再実行しても安全な修復のみを適用し、稼働中の状態を破壊することは決してありません。

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## チェック内容

カテゴリ別にグループ化されたチェック:

| チェック | カテゴリ | 検証内容 | `--fix` の動作 |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` が存在し、Tesserae ワークスペースの体裁であること | レポートのみ（`tesserae init` を提案） |
| `graph_parse` | core | `graph.json` がパースでき、期待される形をしていること | レポートのみ（`tesserae compile` を提案） |
| `config_valid` | core | `.tesserae/config.json` がパースでき、init テンプレートに対して妥当であること | レポートのみ |
| `vault_configured` | core | 設定された vault パスが解決できること | **SAFE**: 解決された vault ディレクトリがプロジェクト内にある場合に作成 |
| `registry_consistent` | registry | `~/.tesserae/registry.json` のエントリが実在するプロジェクトルートを指していること | **SAFE**: ルートが消滅したエントリを削除し、レガシーの `active` キーを除去。グラフの欠落はレポートのみ |
| `graph_staleness` | freshness | 最後のコンパイルで記録された `git_head` 以降の git 差分 | レポートのみ（`tesserae refresh` を提案 — コンパイルは重い処理のため） |
| `site_search_index` | freshness | 静的サイト / `search-index.json` が `graph.json` より新しいこと | **SAFE**: サイトを再ビルド |
| `backend_artifacts` | freshness | RAG-Anything の成果物が最新であること | レポートのみ（これらのリフレッシュは LLM / ネットワーク負荷が大きいため） |
| `session_chunks` | freshness | [日次セッションチャンク](session-chunks.ja.md)のカバレッジに直近ウィンドウの欠落がないこと | レポートのみ（`tesserae sessions chunk-backfill` を提案） |
| `wiki_lint` | graph | グラフ ⇄ wiki のドリフト + 自明に修正可能な lint 検出項目 | **SAFE**: lint の自明な修正（`fix_trivial`）を適用 |
| `compile_lock` | processes | 稼働中のコンパイルロックが保持されているか、またどの pid **とホスト**によってか | レポートのみ — doctor は**稼働中のロックを決して kill も削除もしません** |
| `filesystem_locking` | processes | `.tesserae/` がネットワークファイルシステム上にあり、`flock(2)` が黙って no-op になりうる場所かどうか | レポートのみ（ホスト間での強制は証明できません — 後述） |
| `daemon_pid` | processes | `daemon.<host>.pid` が生きているエンジンプロセスを指していること | **SAFE**: 所有プロセスが死んでいる場合に**このホストの** pidfile を削除。他マシンのものは報告するだけで、決して触りません |
| `llm_login` | environment | プロジェクトが実際に使うことになる設定ディレクトリが存在するかどうか | レポートのみ — **認証情報の検証は行いません**（後述） |
| `optional_deps` | environment | オプション依存関係（memex、raganything）の状態 | レポートのみ（インストールはネットワークを要するため） |
| `embedding_backend` | environment | 実際のセマンティック埋め込みバックエンドが利用可能であること | レポートのみ（`pip install tesserae[semantic]` を提案） |
| `environment` | environment | 環境検出の全体サマリ | レポートのみのセクション |
| `build_history` | hygiene | `.build-history` のサイズと形状 | **SAFE**: トリムする。ただし最新の `git_head` エントリは常に保持（staleness チェックがそれに依存するため） |
| `idempotence` | hygiene | 出力スナップショットの `idempotence_suspect` トリップワイヤ | レポートのみ（これはバグのシグナルであり、自動修復すべきものではないため） |
| `orphan_worktrees` | hygiene | 古くなった `git worktree` の登録 | **SAFE**: `git worktree prune`。ディレクトリの削除はレポートのみ |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` の肥大化 | **SAFE**: 10 MB を超えるログをローテーション/切り詰め |

クラッシュしたチェックはエラーの検出項目として報告されます — doctor 自体が例外を送出することはありません。

## `llm_login` が伝えること、伝えないこと

このチェックが報告するのは、設定ディレクトリが存在するということです。その中の CLI が有効な
トークンを保持していることは**報告しません**。そして検出項目のテキスト自身がそう明言します。

この区別は些末なこだわりではありません。かつてこのチェックは `~/.claude/history.jsonl` のような
ファイルを根拠に `credentialed LLM CLI: claude, codex` と報告していました — それらが証明するのは
CLI が*使われた*ことであって、*いま*認証できることではありません。同じ秒のうちに連続して実行した
ところ、`tesserae compile` が `Claude CLI not logged in (tried 1 config dir)` を出力する一方で、
doctor はグリーンのチェックを出力しました。いま直面している失敗と矛盾する診断は、診断がないより
悪いのです。

認証情報を検証するとは、`tesserae doctor` を実行するたびに実際の LLM 呼び出しを 1 回消費すると
いうことであり、このチェックが自らの判断で負うコストではありません。そのため、実際に確認した
ことだけを述べます。決定的な答えが必要なら `tesserae compile` を使ってください。

チェックの対象は、プロジェクトが実際に試行することになるディレクトリに絞られており、
`ProjectWiki._build_json_client` が使うのと同じ経路で解決されます — そしてプロジェクトの
プロバイダーが `codex` の場合、claude の設定ディレクトリについては何も述べません。

## 共有ディスクと `flock(2)`

Tesserae における並行性の保証は — なかでもコンパイルロックは — すべて、`.tesserae/` を保持する
ファイルシステムが `flock(2)` を強制することの上に成り立っています。NFS や SMB ではそれは設定
次第です：動作するロックデーモンがなければ `flock` は黙って no-op に退化しうるため、2 つのホストが
それぞれ排他ロックを保持していると信じたまま、同じプロジェクトを同時にコンパイルすることになります。

`filesystem_locking` が報告するのは、単一のホストが判断できる範囲のことです：プロジェクトを支えて
いるファイルシステムの種類、それがネットワークファイルシステムかどうか、そして `flock` の獲得が
そもそも成功するかどうか。ネットワークファイルシステム上であれば警告します。

このチェックはホスト間での強制を証明することは**できません**し、できるとも主張しません。あるホストが
ロックを取得できたことは、2 番目のホストが同じロックの取得を拒まれるかどうかについて何も語りません。
共有ストレージに対して複数のマシンから Tesserae を動かすなら、コンパイルロックに頼る前に、実機で
直接テストしてください。

## `--fix` ポリシー

- `--fix` は上記で SAFE と記されたチェック**のみ**を実行し、その後に再検出を行うため、
  レポートは修復後の状態を反映します。
- すべての修復は冪等です: `doctor --fix` を 2 回実行しても、2 回目の実行は
  クリーンなままです。
- doctor は**プロセスを決して kill せず、稼働中のコンパイルロックを決して削除しません** —
  保持されているロックは所有 pid とホストとともに報告され、そのまま残されます。
- doctor は**他マシンの pidfile に決して触れません。** 共有ストレージでは、ローカルのプロセス
  テーブルは別のホストが書き込んだ pid について何も語らないため、`daemon.<other-host>.pid` は
  無条件に報告されてスキップされます — 生存確認のために読まれることすらありません。削除の対象に
  なりうるのは、このホスト自身の pidfile だけです。
- 重い処理やネットワークを要する操作（再コンパイル、依存関係のインストール、バックエンドの
  リフレッシュ）が `--fix` に組み込まれることはありません。doctor は代わりに実行すべき
  コマンドを表示します。

## 終了コード

`tesserae lint` と同じ規約です:

| 終了コード | 意味 |
|---|---|
| `0` | 健全 — OK を超える検出項目なし |
| `1` | 警告あり |
| `2` | エラーあり |

## レポート成果物

毎回の実行で、両方の形式のレポートがワークスペースに書き込まれます:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` を指定すると、markdown チェックリストの代わりに JSON レポートを stdout に
追加出力します。`--all` はレジストリ内のすべてのプロジェクトを走査し（`--project` は
無視）、プロジェクトごとに報告します。

## MCP: `doctor_report`

MCP サーバーは同じレポートを `doctor_report` ツールとして公開しており
（`lint_report` を踏襲し、返却コンテンツのバイト上限も含む）、エージェントは
シェルに出ることなく会話の途中でワークスペースの健全性を確認できます。プロジェクト
ルートが必要です — `graph_path`/`project` を渡すか、デフォルトグラフを設定してください。
