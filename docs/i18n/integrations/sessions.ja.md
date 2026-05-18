# セッショングラフ

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.fr.md">Français</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae のセッショングラフは、プロジェクトに関する Claude Code / Codex の会話を型付き知識グラフのファーストクラスノードに変換し、登場したドキュメントにリンクし直します。コンパイル後、`tesserae project ask "3D Gaussian Splatting について何を決めたか?"` と尋ねると、それらを生成したセッションへの出所付きで具体的な Insight / Decision / Question / TODO / Hypothesis / Takeaway ノードが返されます。

## 仕組み

セッションごとに 2 つのパスがあります:

1. **構造的**(常時実行、LLM なし)。`tesserae sessions discover --import` が `.tesserae/harness_sessions/` に書き込んだ正規化された `HarnessSession` レコードを読みます。各セッションについて `Session` エンベロープノードを発行し、エージェントが開いたすべてのドキュメントから `discussed_in` エッジを発行し、既存の `decisions` フィールドを `SessionDecision` ノードに変換します。
2. **LLM**(オプトイン、`ANTHROPIC_API_KEY` 設定時に実行)。正規化された会話ターン(`metadata["turns"]` フィールド — 生のトランスクリプトファイルではない)を JSON のみの発見スキーマで Claude に送信します。6 種類の発見を返し、それぞれ特定のターンと現在のグラフ内の特定のドキュメントノード ID を引用します。content_hash + project_root_hash でキャッシュされるため、変更されていないセッションは次のコンパイルで呼び出しをスキップします。

## セットアップ

```bash
# このプロジェクトのセッションを `.tesserae/harness_sessions/` にインポートします。cwd でフィルタリングするため、このプロジェクト内で実行されたセッションのみがインポートされます。
tesserae sessions discover --import

# コンパイル。構造的パスは無料で実行されます。`claude` CLI にサインインしていれば LLM パスが自動実行されます — API キー不要。
tesserae project compile
```

セッションなしでコンパイルするには(例: ハーネス履歴のないサーバーで):

```bash
tesserae project compile --no-sessions
```

構造的のみを強制するには(キーが設定されていても LLM 呼び出しをスキップ):

```bash
tesserae project compile --sessions-llm=false
```

## 設定

`.tesserae/config.json` は `sessions` ブロックを受け入れます:

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

CLI フラグは設定を上書きします。`llm_enabled = "auto"`(デフォルト)は `claude` CLI にサインインしているか `ANTHROPIC_API_KEY` が設定されているときに LLM パスを実行します。どちらもない場合は構造的パスのみが実行されます(エラーなし、アウトバウンド呼び出しなし)。

## クエリ

既存の検索/wiki ツールに加えて、2 つの MCP ツールが追加されます:

* `list_sessions(since?, limit?)` — アクティブプロジェクトの Session エンベロープ(id、started_at、title、発見数)。
* `find_session_findings(node_id, kinds?)` — `discussed_in` または `references` 経由で `node_id` にリンクされたすべてのセッション派生発見。オプションで insight / decision / question / todo / hypothesis / takeaway にフィルタリング。

CLI から:

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## プライバシー

* `claude` CLI 未サインイン かつ `ANTHROPIC_API_KEY` 未設定(または `--sessions-llm=false`)では、アウトバウンドネットワーク呼び出しはゼロです。構造的パスのみが実行されます。
* LLM パスが実行されると、まだキャッシュされていないセッションの**完全な正規化された会話ターン**が送信されます。トランスクリプトファイル自体はディスクに残り、LLM の JSON 出力のみがグラフとセッションごとのキャッシュに永続化されます。
* キャッシュファイルは `.tesserae/session_findings/<session_id>.findings.json` に `content_hash` と `project_root_hash` の両方とともに存在します。プロジェクト間でコピーされたキャッシュファイルは読み取り時に拒否されます — プロジェクト間の再生はありません。
* セッションは読み込み後 `session_matches_project` でフィルタリングされるため、`cwd` が兄弟プロジェクトのトランスクリプトはこのプロジェクトのグラフにノードを生成しません。

## Vault レイアウト

発見は Obsidian vault の下で発見ごとに 1 ページずつセッションごとにグループ化されてレンダリングされます:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

発見ページの `<!-- user-notes:start -->` … `<!-- user-notes:end -->` ブロック内のユーザーノートは、他のすべての vault ページと同じ契約で再コンパイル後も保持されます。

## トラブルシューティング

* **コンパイル後に Session ノードが表示されません。**先に `tesserae sessions discover --import` を実行しましたか?コンパイルパスは `.tesserae/harness_sessions/` のみを消費し、`~/.claude/projects/` を自動的にスキャンしません(数千の履歴セッションがあるマシンではそのスキャンに数分かかることがあります)。
* **LLM コストの懸念。**キャッシュにより、各セッションは content-hash ごとに最大 1 回 LLM に送信されます。長いセッションは `max_turns_per_chunk`(デフォルト 30)で 5 ターンの重複でチャンクされます。総コストを制限するには、`max_turns_per_chunk` を下げる、`include_doc_id_context` を下げる、または `--sessions-llm=false` を設定します。
* **発見が存在しないノード ID を引用します。**オーケストレーターは引用されたすべての参照をライブドキュメントグラフに対して検証し、不明なものを静かにドロップします。ログに警告が表示される場合、LLM が引用を幻覚しました — 生き残った参照は依然として信頼できます。

## 仕様

完全な設計は [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md) にあります。実装計画は [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md) です。
