# Harness セッション履歴

<!-- translations:start -->
<p align="center"><a href="../session-history.md">English</a> · <a href="session-history.ko.md">한국어</a> · <a href="session-history.zh.md">中文</a> · <a href="session-history.ja.md">日本語</a> · <a href="session-history.ru.md">Русский</a> · <a href="session-history.es.md">Español</a> · <a href="session-history.fr.md">Français</a> · <a href="session-history.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae はローカルの AI エージェントトランスクリプトをインポートし、静的サイトの `sessions/` セクション配下にプロジェクトメモリとしてレンダリングできます。

この機能は意図的に `export harness` とは分離されています:

- `export harness` は、Claude Code、Codex、Gemini、Cursor、Kiro、OpenCode などのツールに向けたアウトバウンドのコンテキストです。
- `sessions ...` はインバウンドの履歴です: 現在のプロジェクトの過去の Claude Code/Codex セッションを正規化し、`.tesserae/harness_sessions/` 配下に保存し、`export site` がセッションのインデックス/詳細ページを公開できるようにします。

## 2 つの取り込み経路: バッチインポートとライブモニタリング

セッションの取り込みはもはやバッチだけではありません。同じ正規化ストアへの
2 つの経路があります:

- **バッチインポート** — `sessions discover/import` はオンデマンドで
  トランスクリプトのルートをスキャンし、ワンショットで書き込みます。このページでは以下でそのフローを説明します。
- **ライブモニタリング** — スーパーバイザーデーモン（`tesserae engine`）は
  `SessionTailer` を実行し、*このプロジェクト自身の*
  Claude Code および Codex トランスクリプトを監視して、新しいターンが到着するたびに取り込みます。各
  tick は永続化されたファイルごとのバイトオフセットにシークし、新しいバイトのみを読み取り、
  完全なターンを SQLite の `HarnessSessionsDB`
  （`.tesserae/sqlite.db`）に保存して**から**、デバウンスされた再コンパイルをエンキューするため、
  コンパイルは常に一貫した状態を読み取ります。tailer はプロジェクト自身の
  セッションにスコープされ（Claude は `projects/<slug>/*.jsonl`。Codex は cwd でフィルタ）、
  再起動後は保存されたオフセットから、ターンを再生することなく再開します。

ライブループの実行:

```bash
tesserae engine        # watch sources, coalesce bursts, auto-recompile
tesserae engine --once # single drain cycle then exit (deterministic)
```

`tesserae refresh` は同じ ingest → compile → project パイプラインを
1 回、インプロセスで実行し、長寿命のウォッチャーは起動しません
（`--no-sessions` を渡すと harness セッションの discovery スキャンをスキップします）。

## プライバシーモデル

どちらの取り込み経路も明示的です: ライブ tailer は `tesserae engine` を
維持している間だけ動作し、バッチの discovery は
`--import` を指定したときだけ書き込みます。通常の `tesserae compile` や `tesserae export site` は、
すでに正規化されたセッションを `.tesserae/harness_sessions/` から、ライブレコードを
`.tesserae/sqlite.db` から読み取りますが、勝手にプライベートな
harness トランスクリプトディレクトリを不意にスクレイピングすることはありません。

インポートされたセッションレコードはローカルのプロジェクト成果物です。公開サイトを発行する前に、特にトランスクリプトにシークレット、プライベートなパス、顧客データ、未リリースのコードが含まれる可能性がある場合は、内容を確認してください。

## ローカルセッションの発見とインポート

プロジェクトルートから:

```bash
tesserae sessions discover --import
```

discovery は、現在のプロジェクト作業ディレクトリに属するローカルの Claude Code および Codex トランスクリプトルートをスキャンします。特定の設定ディレクトリをスキャンするには `--root` を使い、discovery を制限するには `--harness` を繰り返し指定します:

```bash
tesserae sessions discover \
  --root ~/.claude \
  --root ~/.codex \
  --harness claude-code \
  --harness codex \
  --import
```

`--import` なしの場合、discovery は正規化されたセッションレコードを書き込まずに、見つかったものを表示します。

## 正規化済み JSON の直接インポート

別のツールがすでに正規化された `HarnessSession` JSON を生成している場合は、1 つのファイル、またはファイルのリストをインポートします:

```bash
tesserae sessions import path/to/session.json path/to/more-sessions.json
```

各入力には、1 つのセッションオブジェクトまたはセッションオブジェクトのリストを含めることができます。

## インポート済みセッションの一覧

```bash
tesserae sessions list
```

セッションは以下に保存されます:

```text
.tesserae/harness_sessions/
  manifest.json
  <harness>/
    <session>.json
    <session>.md
```

ライブモニタリングされたセッションは、加えて SQLite の
`HarnessSessionsDB`（`.tesserae/sqlite.db`）でも追跡され、tailer が再開に使う
ファイルごとの読み取りオフセットもそこに永続化されます。`tesserae sessions list` は
統合されたビューを報告します。

## 静的セッションページのビルド

セッションをインポートしたら、サイトを再ビルドします:

```bash
tesserae export site
```

サイトは以下を出力します:

```text
.tesserae/site/sessions/index.html
.tesserae/site/sessions/<project>/<session>.html
```

生成されたサイトは、グローバルレール、ホームの Browse カード、検索エントリ、そして各セッション詳細ページのパンくずリストから Sessions にリンクします。

## 高速トランスクリプト検索（memex）

サイトを `tesserae serve` すると、**sessions ダッシュボード**に、インデックス化された
すべての Claude/Codex トランスクリプトを対象とする全文検索ボックスが追加されます。これは
[`nicosuave/memex`](https://github.com/nicosuave/memex)（BM25）が支えています。結果には
`project · role · date · score` とマッチしたスニペットが表示されます。

```bash
cargo install --git https://github.com/nicosuave/memex --locked   # or: tesserae config deps --install memex
memex index                                                        # build the index once
tesserae serve                                                     # search box appears on /sessions
```

これは**オプションであり、グレースフル**です: `memex` バイナリ（またはインデックス）がない場合、
ボックスには明確で実行可能なメッセージが表示され、ダッシュボードの残りの部分は影響を受けません。
検索エンドポイント（`GET /api/transcript-search`）は same-origin/loopback の呼び出し元に
制限されているため、訪問した Web ページがローカルの履歴を探ることはできません。

## セッション詳細ページのレイアウト

セッション詳細ページは、スタンドアロンのトランスクリプトダンプではなく、共有の静的サイトシェルを使用します。以下が含まれます:

- ヒーローと統計ストリップ;
- 高レベルなサマリ;
- タイムラインとサイズのメタデータ;
- 存在する場合は decisions、files、commands、tools、errors;
- 折りたたまれたサブエージェントツリー;
- ターンごとの user/assistant 会話;
- 直前の assistant ターンの下に付けられた、折りたたまれた tool-use ブロック;
- `#turn-N` アンカーにリンクする左側の会話レール。

会話の markdown はサイトの markdown レンダラーを通してレンダリングされます。インラインコード、明示的なコマンド/タグのマークアップ、パス、ファイル名、ハッシュタグといったセマンティックな表層はコンパクトなチップとして装飾されます。単に大文字で始まる名詞が自動的にチップ化されることはありません。

現在のトランスクリプトタイポグラフィ:

| 表層 | セレクタ | サイズ |
|---|---|---|
| 会話 markdown の本文 | `.session-turn-text`, prose children | `8px` |
| 一般的な会話のコードフェンス | `.session-turn-text pre` | `10px` |
| Bash/シェルのフェンス付きコード内容 | `.session-code-block code.language-bash`, `.language-sh`, `.language-shell`, `.language-zsh` | `11px` |
| ツールの details/summary | `.session-tool-details`, `.session-tool-details > summary` | `10px` |
| tool-use ヘッダー | `.session-tool-use-header` | `8px` |
| ツールペイロードのテキスト | `.session-tool-use-text` | `6px` |

## セッションの公開チェックリスト

セッションを含む公開サイトをデプロイする前に:

1. `tesserae sessions list` を実行し、件数が想定どおりであることを確認する。
2. `.tesserae/harness_sessions/` に機密性の高い内容がないか点検する。
3. `tesserae export site` で再ビルドする。
4. `sessions/index.html` と少なくとも 1 つのセッション詳細ページをローカルで開く。
5. ツールブロックがデフォルトで折りたたまれていること、生のツールペイロードが公開して差し支えないことを確認する。
6. ソースツリーがコミットされたら `tesserae export site --deploy` でデプロイする。
