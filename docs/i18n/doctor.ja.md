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

カテゴリ別にグループ化された 20 のチェック:

| チェック | カテゴリ | 検証内容 | `--fix` の動作 |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` が存在し、Tesserae ワークスペースの体裁であること | レポートのみ（`tesserae init` を提案） |
| `graph_parse` | core | `graph.json` がパースでき、期待される形をしていること | レポートのみ（`tesserae compile` を提案） |
| `config_valid` | core | `.tesserae/config.json` がパースでき、init テンプレートに対して妥当であること | レポートのみ |
| `vault_configured` | core | 設定された vault パスが解決できること | **SAFE**: 解決された vault ディレクトリがプロジェクト内にある場合に作成 |
| `registry_consistent` | registry | `~/.tesserae/registry.json` のエントリが実在するプロジェクトルートを指していること | **SAFE**: ルートが消滅したエントリを削除し、レガシーの `active` キーを除去。グラフの欠落はレポートのみ |
| `graph_staleness` | freshness | 最後のコンパイルで記録された `git_head` 以降の git 差分 | レポートのみ（`tesserae refresh` を提案 — コンパイルは重い処理のため） |
| `site_search_index` | freshness | 静的サイト / `search-index.json` が `graph.json` より新しいこと | **SAFE**: サイトを再ビルド |
| `backend_artifacts` | freshness | RAG-Anything / Understand-Anything の成果物が最新であること | レポートのみ（これらのリフレッシュは LLM / ネットワーク負荷が大きいため） |
| `session_chunks` | freshness | [日次セッションチャンク](session-chunks.ja.md)のカバレッジに直近ウィンドウの欠落がないこと | レポートのみ（`tesserae sessions chunk-backfill` を提案） |
| `wiki_lint` | graph | グラフ ⇄ wiki のドリフト + 自明に修正可能な lint 検出項目 | **SAFE**: lint の自明な修正（`fix_trivial`）を適用 |
| `compile_lock` | processes | 稼働中のコンパイルロックが保持されているか、またどの pid によってか | レポートのみ — doctor は**稼働中のロックを決して kill も削除もしません** |
| `daemon_pid` | processes | `daemon.pid` が生きているエンジンプロセスを指していること | **SAFE**: 所有プロセスが死んでいる場合に pidfile を削除 |
| `llm_login` | environment | 設定された LLM バックエンドが実際に使用可能であること（claude/codex CLI にログイン済み、または API キーが存在） | レポートのみ（`claude /login` / `codex login` を提案） |
| `optional_deps` | environment | オプション依存関係（memex、cognee、raganything、understand-anything）の状態 | レポートのみ（インストールはネットワークを要するため） |
| `embedding_backend` | environment | 実際のセマンティック埋め込みバックエンドが利用可能であること | レポートのみ（`pip install tesserae[semantic]` を提案） |
| `environment` | environment | 環境検出の全体サマリ | レポートのみのセクション |
| `build_history` | hygiene | `.build-history` のサイズと形状 | **SAFE**: トリムする。ただし最新の `git_head` エントリは常に保持（staleness チェックがそれに依存するため） |
| `idempotence` | hygiene | 出力スナップショットの `idempotence_suspect` トリップワイヤ | レポートのみ（これはバグのシグナルであり、自動修復すべきものではないため） |
| `orphan_worktrees` | hygiene | 古くなった `git worktree` の登録 | **SAFE**: `git worktree prune`。ディレクトリの削除はレポートのみ |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` の肥大化 | **SAFE**: 10 MB を超えるログをローテーション/切り詰め |

クラッシュしたチェックはエラーの検出項目として報告されます — doctor 自体が例外を送出することはありません。

## `--fix` ポリシー

- `--fix` は上記で SAFE と記されたチェック**のみ**を実行し、その後に再検出を行うため、
  レポートは修復後の状態を反映します。
- すべての修復は冪等です: `doctor --fix` を 2 回実行しても、2 回目の実行は
  クリーンなままです。
- doctor は**プロセスを決して kill せず、稼働中のコンパイルロックを決して削除しません** —
  保持されているロックは所有 pid とともに報告され、そのまま残されます。
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
