# 日次セッションチャンク — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.ko.md">한국어</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a> · <a href="session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
ウィンドウ指定のセッションクエリ — `tesserae summary`、`tesserae decisions`、および
`ask` プランナーのアクティビティアクション — は、以前は呼び出しのたびにウィンドウ内の
すべての Claude Code / Codex トランスクリプトを再パースしていました。日次チャンクストアは
正規化された各ターンを **1 回だけ**永続化し、KST の日ラベルでバケット化するため、
完全にカバーされた過去の日は生の再スキャンではなく SQLite から提供されます。実際の
数千セッション規模のコーパスで計測したところ、これによりウィンドウ指定のサマリは
**約 20 倍高速**になります。

ストアは 1 つの SQLite ファイル `.tesserae/session_chunks.db`（WAL、
操作ごとの短命な接続）です: 日でインデックスされた `turns` テーブル、どの
`(day, harness)` ペアが完全かを記録する `day_coverage` テーブル、そして
スキーマバージョンを持つ `meta` テーブルで構成されます。

## 書き込むもの

1. **ライブ — エンジンの tailer。** `tesserae engine` の実行中、セッション tailer は
   ターンを tail しながらポーリングごとにストアへ追記し、影響を受けた日の
   カバレッジを upsert します（`source: "tailer"`）。書き込みパスは
   追記専用で、再配送されたターンに対して冪等であり、デーモンループに例外を
   送出することは決してありません。意図的に **SessionEnd フックのライターは存在しません** —
   バックグラウンド化された SessionEnd ライターは積み上がっていくためです（記録された障害モード）。
2. **バックフィル。** 2 つのエントリポイントが既存のトランスクリプトを走査して履歴を
   埋めます（`source: "backfill"`）:
   - `tesserae refresh` は sessions-import ステップの一部として自動的に
     バックフィルを実行するため、アップグレード後の最初の refresh で追加の操作なしに
     ストアが埋まります。
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` は明示的に実行します。
     `--since` はどこまで過去を走査するかを制限します（デフォルト: 全
     履歴）。

   バックフィルは `.tesserae/session_chunks.lock` に対して skip-if-held セマンティクスの
   **ノンブロッキング** flock を取得します — 並行するバックフィル（またはすでにロックを
   保持しているエンジン）がある場合、2 番目の呼び出し側はキューに並ぶのではなく
   きれいにスキップします。バックフィルの upsert は
   `(session_path, ts, role, hash(text))` をキーとするため、tailer の行とバックフィルの行が
   互いに重複することはありません。増分バックフィルにおける 1 日分のオーバーラップは、
   ある日のカバレッジが最初に確定した後に到着したターンを修復します。

## 読み取るもの

高速パスは単一のスキャンチョークポイント
（`activity_summary.iter_project_transcripts` / `scan_messages`）にあるため、下流の
すべてがそれを透過的に継承します:

- `tesserae summary`（組み込みの decisions 収集を含む）
- `tesserae decisions`
- `tesserae ask` — プランナーの `activity_summary` / `decisions` アクション
- MCP の `activity_summary` と `query_decisions`
- ライブセッションビュー

## カバレッジルール: 今日は常に生スキャン

ウィンドウがチャンクから提供されるのは、以下の**すべて**が成り立つ場合のみです:

1. KST に正確に整列した単一の日であること;
2. その日が**厳密に今日より前**であること — 今日はまだ書き込み中なので、
   常に生のトランスクリプトスキャンが使われます;
3. その日に対して要求された**すべての** harness について `day_coverage` 行が存在すること。

それ以外の場合は、そのウィンドウについて生スキャンにフォールバックします。

## 生スキャンフォールバックの保証

チャンクストアはアクセラレータであり、真実のソースでは決してありません:

- DB エラー、ファイルの欠落/破損、`schema_version` の不一致はいずれも、チャンクパスから
  **何も**返しません — 呼び出し側の生トランスクリプトスキャンが従来どおりそのまま
  進行します。スキーマの不一致はストアを破棄して空に再構築します。カバレッジも
  一緒に消えるため、フォールバックは正しいままです。
- カバレッジのない日（たとえばエンジンが動いておらず、バックフィルも行われていない場合）は
  黙って遅いパスを取ります。正しくはありますが、高速化は失われます — `tesserae doctor` は
  直近ウィンドウのカバレッジの欠落を報告し、`tesserae sessions chunk-backfill` を
  案内します（[doctor.md](doctor.ja.md) を参照）。
- **パリティ不変条件:** 完全にカバーされた日について、チャンクから提供されるターンは
  生スキャンが生成したであろうものと等しくなります（同じタイムスタンプ、ロール、名前、テキスト、
  セッションキー、harness）。

## 運用上の注意

- `tesserae engine` を動かし続けていれば過去の日はライブでカバーされ続けます。そうでなければ、
  ときどき `tesserae refresh`（または明示的な `chunk-backfill`）を実行することで欠落が
  埋まります。
- ストアはプロジェクトごとに存在し、`.tesserae/` 配下にあり、いつでも安全に削除できます —
  次のバックフィルが再構築し、その間、読み取り側は生スキャンにフォールバックします。
