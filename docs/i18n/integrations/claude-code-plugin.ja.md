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
* **`tesserae_mcp` サーバーの自動登録** — エージェントが手動設定編集なしに `ask`、`search_nodes`、`list_sessions`、`find_session_findings` などを `mcp__plugin_tesserae_tesserae__<tool>` として呼び出せます。
* **`using-tesserae` スキル** — 型付きグラフ、過去のセッション想起、wiki/vault コンテンツ、tesserae ワークフローについて質問したときに自動ロードされます。どの MCP ツールを使うか vs どのスラッシュコマンドを提案するかをエージェントに教えます。
* **4 つのフック** — `SessionStart` はグラフサマリを出力;`SessionEnd` は今回の会話の洞察が次のセッションのグラフノードになるよう import+compile をバックグラウンド実行;`PostToolUse`(オプトイン)は docs/ 編集時に増分再コンパイル;`PreToolUse` は大規模グラフのコンパイルを確認ダイアログでゲート。

完全な詳細、コマンド/フックの完全な表、プロジェクトごとのオプトアウト手順はプラグイン自身の [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) にあります。

## なぜプラグインと MCP サーバーの両方?

役割が異なります:

- **MCP ツール** = エージェントが会話中に呼び出す読み取り専用のグラフクエリ。常時オン、低摩擦。
- **スラッシュコマンド** = 明示的に呼び出すワークフローアクション(compile、refresh、obsidian-sync)。レバレッジが高いがあなたの判断であるべき。

MCP サーバーだけを単独で使うこともできます(`tesserae project mcp-config` 経由で手動 `claude_desktop_config.json` 編集)。プラグインは単にそれをスラッシュコマンド、スキル、フックとパッケージ化し、インストールを 1 ステップにします。

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
