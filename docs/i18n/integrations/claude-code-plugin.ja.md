# Claude Code プラグイン

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.fr.md">Français</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae は [Claude Code](https://docs.claude.com/en/docs/claude-code) プラグインを提供しており、TUI セッション内から Tesserae ワークフロー全体を実行できます — スラッシュコマンド、自動登録される MCP サーバー、エージェントを案内するスキル、エージェント↔プロジェクトメモリのループを閉じる 4 つのフック。プラグインはリポジトリ内の `plugin/` にあります。

## インストール

```bash
# 前提:`tesserae` がインストール済み(`pip install tesserae` または `pipx install tesserae`)。pipx でインストールする場合、`~/.
/plugin install /path/to/Tesserae/
```

前提:`tesserae` がインストール済み(`pip install tesserae` または `pipx install tesserae`)。pipx でインストールする場合、`~/.local/bin` が Claude Code が起動時に継承する PATH に含まれていることを確認してください。

## 含まれるもの

* **9 つのスラッシュコマンド** — CLI への 1:1 ラッパー 7 つ(`/tesserae:compile`、`/tesserae:ask`、`/tesserae:sessions-import`、`/tesserae:build-site`、`/tesserae:serve`、`/tesserae:obsidian-sync`、`/tesserae:setup`)+ 2 つのワークフローマクロ(`/tesserae:refresh` は import + compile + obsidian-sync を連鎖、`/tesserae:status` はグラフ数と最終コンパイルを表示)。
* **`tesserae` サーバーの自動登録** — エージェントが手動設定編集なしに全ツール群を `mcp__plugin_tesserae_tesserae__<tool>` として利用できます:グラフクエリ(`search_nodes`、`node_context`、`graph_ppr`、`search_facts`)、オンデマンドの `compile_context` / `list_communities` / `fresh_insights` コンパイラ、セッションメモリ(`ask`、`list_sessions`、`find_session_findings`、`find_code_symbol_mentions`)、ガイド付きセットアップ(`tesserae_setup_plan` / `tesserae_setup_apply`)。完全な一覧は [mcp.ja.md](mcp.ja.md) を参照。
* **`using-tesserae` スキル** — 型付きグラフ、過去のセッション想起、wiki/vault コンテンツ、tesserae ワークフローについて質問したときに自動ロードされます。どの MCP ツールを使うか vs どのスラッシュコマンドを提案するかをエージェントに教えます。
* **5 つのフック** — `SessionStart` はグラフサマリを出力;`SessionEnd` は今回の会話の洞察が次のセッションのグラフノードになるよう import+compile をバックグラウンド実行;2 つの `PostToolUse` フックが `Edit`/`Write`/`MultiEdit` で発火 — 一方は docs/ 編集時のオプトイン増分再コンパイル、もう一方はコードグラフ同期をデバウンス(約 30 秒);`PreToolUse`(`Bash` 対象)は大規模グラフのコンパイルを確認ダイアログでゲート。

> **セッション終了時の compile は日和見的であり、保証されません。** フックは `setsid`
> があればそれでバックグラウンドジョブを切り離し、なければ `nohup` にフォールバック
> します。macOS に `setsid` はなく、`nohup` は `SIGHUP` を無視するだけ — ジョブは
> セッションのプロセスグループに残ります — なので、セッション終了時にグループを刈り
> 取るハーネスは依然として compile を途中で kill できます。そのとき残る状態は「無傷」
> ではなく「復旧可能」です。`graph.json` はアトミックな rename で書かれるので半端な
> ファイルにはなりませんが、生成物である `wiki/` と `site/` の投影はアーティファクト
> 書き込みの冒頭で消され、SQLite ストアは `graph.json` の後に書かれるため、その区間で
> kill されるとこれらは失われるか 1 回分古いままになります。ただし黙ってそうなること
> はありません — `.tesserae/manifest.json` はアーティファクトが着地した後にのみ文書へ
> `graphed` を刻むので、次の `compile --changed-only` は no-op を拒否し、`graph.json is
> not known to cover every tracked document` と告げてコーパス全体を再抽出し、そこで投影
> も作り直されます。
> そのコーパス全体の再抽出は再購入ではなく再ウォークです。codex と claude CLI プロバイダーからのレスポンスは `~/.tesserae/llm_cache` 下にキャッシュされており、
> 実際に送信されたプロンプトのダイジェストでアドレス指定され、そのため、キルされたランが既に完了したすべてのドキュメントはディスクから無料で再生され、
> 修復は到達しなかったドキュメントのためだけに支払います。キルの代償は実行の経過時間であり、抽出ではありません。
> その代償を打ち消すものは 2 つです：キャッシュディレクトリを削除すること、および直接 API プロバイダーを使用することです。
> 直接 API プロバイダーは SDK の短命なプロンプトキャッシング のみを備えており、キルから生き残るものは何もありません。
> どちらの場合でも、修復はプロバイダーからコーパス全体を全価格で再購入します。
> 長い compile が起動元セッションより長生きする前提のワークフローは
> 組まないでください — フォアグラウンドで実行するか、`tesserae engine` を使います。
>
> どちらの方法でも監視できます。ターミナルが接続されていないコンパイル（デタッチ、リダイレクト、またはCI下）は、
> `tesserae.compile`チャネルのstderrに1ドキュメントあたり1行をログ出力し、位置、パス、およびそのドキュメントがキャッシュから来たのか、
> それともモデル呼び出しがコストかを示します。`--quiet`はそれをオフにします。

完全な詳細、コマンド/フックの完全な表、プロジェクトごとのオプトアウト手順はプラグイン自身の [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) にあります。

## なぜプラグインと MCP サーバーの両方?

役割が異なります:

- **MCP ツール** = エージェントが会話中に呼び出す読み取り専用のグラフクエリ。常時オン、低摩擦。
- **スラッシュコマンド** = 明示的に呼び出すワークフローアクション(compile、refresh、obsidian-sync)。レバレッジが高いがあなたの判断であるべき。

MCP サーバーだけを単独で使うこともできます(`tesserae projects mcp-config` 経由で手動 `claude_desktop_config.json` 編集)。プラグインは単にそれをスラッシュコマンド、スキル、フックとパッケージ化し、インストールを 1 ステップにします。

## インストール確認

```
/plugin list
/mcp
/tesserae:status
```

## アンインストール

```
/plugin uninstall tesserae
```

可逆。どのプロジェクトの `.tesserae/` ディレクトリにも触れません。

## 関連項目

- [実装計画](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [設計仕様](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [セッション統合](sessions.ja.md) — プラグインのフックがループを閉じるセッショングラフ機能
