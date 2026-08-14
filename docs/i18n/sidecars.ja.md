# `.tesserae/` — 中身は何で、消すと何を失うのか

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
成熟したプロジェクトでは `.tesserae/` の下に六十ほどのエントリが溜まりますが、
ディレクトリの一覧を見ただけでは、どれがコンパイルでただ再構築されるのか、どれが
再構築に LLM の一回分のコストを要するのか、そしてどれが何をもってしても復元できない
成果物なのかは分かりません。`compile.lock` やゼロバイトの孤児 tmp ファイルは、人間の
判定を抱えた `candidate-same-as.json` とまったく同じ顔をしています。

このページがその答えであり、結果（何を失うか）の順に並べてあります。分類そのものは
`tesserae/sidecars.py` にあります — ファイル一つにつきレジストリ項目一つで、所有者、
種別、そして削除によって失われるものを記録しています。真実の出所はレジストリで、
このページはその可読な射影です。実際の状態は `tesserae doctor` が出力します。

各エントリは互いに独立した二つのフィールドを持ちます:

| 種別 | バイトの出どころ |
|---|---|
| `derived` | コンパイルがソースから再発行する |
| `accumulated` | 時間とともに積み上がる。どのコンパイルも再導出できない |
| `cache` | もう一度尋ねられる問いに対する、保存済みの答え |
| `scratch` | プロセスの帳簿: ロック、pid ファイル、tmp の残骸 |

種別が語るのはバイトの出どころだけで、削除して安全かどうかは**語りません**。
`safe_to_delete` は別のフィールドであり、両者は無視できない頻度で食い違います。
答えがモデル由来の `cache` は削除して安全ではありませんし、`derived` のファイルが
人間の承認を抱えていることもあります。以下の節はその二つ目のフィールドの順に
並べてあります。実際に知りたいのはそちらだからです。

## 気兼ねなく消してよいもの — コンパイルが作り直します

以下はどれを消しても、次のコンパイルがモデルを一度も呼ばずにバイト単位で元に戻します:

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` がこの一覧にあるのは意図的です。コンパイル済みのグラフはソースと、
下に挙げる累積型サイドカーの純粋関数です — だからこそ守るべきは*そちら*であり、
「`.tesserae/` を消して再コンパイルすればいい」という反射は、いちばん目立つ
ファイルが使い捨てであるにもかかわらず間違っています。

## モデル一回分のコストがかかり、`graph.json` のバイトが変わります

これらは LLM が返した答えの保存版です。作り直すには一回分のコストがかかり、モデルは
同じ言い回しを二度返さないので、その下流にあるものもバイトごと変わります。

| エントリ | 種別 | 作り直しのコスト |
|---|---|---|
| `session_findings` | `cache` | 最も鋭い事例です。これらの findings はグラフの**ノード**になるため、キャッシュを捨てると非決定的な抽出器が再実行され、次の `graph.json` のバイトが変わります — このリポジトリが四度踏んだバイト冪等性の破壊です |
| `community_summaries` | `cache` | メンバーハッシュをキーとする、LLM が書いたコミュニティ要約 |
| `distill_cache` | `cache` | エージェント蒸留の結果 |
| `distillation_cache` | `cache` | 蒸留の結果 |
| `extraction_guidance_cache` | `cache` | フィードバッククラスタごとに LLM が言語化した箇条書き一つ |
| `schema_drift_cache` | `cache` | ホスト型ごとの LLM サブタイプ提案 |
| `supersede_cache` | `cache` | LLM による supersede の裁定 |
| `schema-drift-proposals.json` | `derived` | バイトは派生でも中身は派生不能です。同じレコードが人間の `approved` ゲートと編集可能な `proposed_type` を併せ持つため、作り直すと一回分のコストがかかり、**そのうえ**承認まで捨てられます |

## 復元不能 — 何をもっても作り直せません

ここにあるものはどのコンパイルも再導出しません。一つ消すことは遅延ではなく、
データの喪失です。

| エントリ | 種別 | 失うもの |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | 人間による same-as の判定。これを見つけられなかったコンパイルは失敗しません — 人間がすでに答えた問いを黙って問い直し、却下された組がそのまま戻ってきます |
| `sqlite.db` | `accumulated` | 混在。下記参照 |
| `agent-writes.jsonl` | `accumulated` | エージェントが書いたオーバーレイ。毎コンパイルで五番目のプロデューサーとして再生されます。消せばエージェントの書き込みがすべて消えます |
| `vault_snapshot.json` | `accumulated` | `vault_pull` が差分の基準にするベースライン。編集の途中で消すと、次のコンパイルはユーザーの編集と自分自身の以前の射影を区別できません — vault の上書き機構そのものです |
| `obsidian_vault` | `accumulated` | 双方向かつユーザー所有です。ここでの編集はグラフへ引き戻されるため、単純に描き直せる射影ではありません |
| `config.json` | `accumulated` | `obsidian.vault_path` を含むプロジェクト設定 — ユーザー入力であり、再生成されません |
| `charter` | `accumulated` | プロジェクトの charter は抽出物ではなく、書かれたものです |
| `agents` | `accumulated` | エージェントごとの `registry.json` と、手書きの `purpose.md` |
| `discovered_links.json` | `accumulated` | 関連オーバーレイは複数回の実行にわたってスコア付きリンクを積み上げます。一回の実行では再構成できません |
| `extraction-feedback.jsonl` | `accumulated` | vault オーバーレイと review-apply の過程で集めた人間の訂正 |
| `extraction-guidance.md` | `accumulated` | 手で編集されたガイダンス。evolve のパスがここへマージします |
| `harness_sessions` | `accumulated` | 取り込んだセッションの状態 |
| `harness_sessions.db` | `accumulated` | 取り込んだエージェントセッション。上流のトランスクリプトはローテートして消えるため、再インポートでは復元できません |
| `session_chunks.db` | `accumulated` | デーモンの tailer がライブで書き込む正規化済みターン。元のトランスクリプトは残り続けません |
| `manifest.json` | `accumulated` | ソースごとの取り込み状態。これがないと次のバッチがすべてを取り込み直し、すでに読んだソースに対して抽出を再実行します |
| `.build-history.jsonl` | `accumulated` | ビルドごとに一行、コンパイル時点の `git_head` を記録します。消すとグラフの鮮度が恒久的に不明になります |

### `sqlite.db` は混在で、最も価値ある表を基準に分類されています

中のグラフミラーは派生であり `node_vectors` は捨ててよいベクトルキャッシュです —
しかし同じファイルが `node_memory`（減衰、アクセス回数、強化された確信度）、
`fact_observed`（トランザクション時間 — 前にしか進まない本物の壁時計）、`read_audit`
を抱えており、どれも復元できません。ベクトルキャッシュを回収するためにファイルごと
消すと、すべての事実の「いつ知ったか」が今にリセットされます。容量の回収は
データベースの削除ではなく、vacuum を行う `tesserae doctor --fix` で行ってください。

## ロック、pid ファイル、残骸

| エントリ | 種別 | 消す前に |
|---|---|---|
| `compile.lock` | `scratch` | コンパイルのミューテックス。**いかなる**自動経路も削除しません — 記録された失敗は SessionEnd のコンパイル滞留であり、doctor の `compile_lock` チェックが報告のみなのも同じ理由です |
| `.recompile.lock.d` | `scratch` | mkdir ベースのフックミューテックス。保持中のものを消すと二つの再コンパイルが競合します |
| `session_chunks.lock` | `scratch` | バックフィルの「保持中ならスキップ」flock。保持中のものを消すと二つのバックフィルが同じ日を書きます |
| `daemon*.pid` | `scratch` | エンジンの pid ファイル。`daemon.<host>.pid` の形でホスト単位です。doctor は記録された所有者が**このマシンで**死んでいると確認した後にのみ削除します |
| `graph.json.bak-*` | `scratch` | Tesserae のどのコード経路もこれを書きません。復旧作業中に人が手で作ったコピーなので、報告するだけで決して削除しません |
| `*.tmp*` | `scratch` | tmp+replace 書き込みの孤児となった片割れで、名前は `<target>.tmp.<pid>.<hex>` です。所有 pid が消えた後にのみ削除できます: 生きている書き手は rename の途中だからです |
| `.*-hook.log*` | `scratch` | シェルフックの診断ログ。肥大したものは doctor がローテートします |

## `~/.tesserae/` — マシン全体、同名で別物

ユーザースコープのディレクトリはプロジェクト側と同じ名前ですが、意味が違います。
`config.json` は両方に存在し、プロジェクトではプロジェクト設定、こちらではマシン上の
すべてのプロジェクトに効く LLM 設定です。

| エントリ | 種別 | 失うもの |
|---|---|---|
| `registry.json` | `accumulated` | プロジェクトレジストリ。消すとこのマシンのすべてのプロジェクトが登録解除されます |
| `config.json` | `accumulated` | マシン全体の LLM 設定。ユーザー入力です |
| `host_id` | `accumulated` | このマシンの識別子。作り直すと、共有ストレージ上のホスト単位の pid ファイルとセッション記録がすべて他所のものに見えます |
| `harness_sessions` | `accumulated` | マシン全体のセッション取り込み状態 |
| `llm_cache` | `cache` | キャッシュされた LLM 応答。作り直すとモデルを呼び、同じものは再現されません |
| `federation` | `cache` | プロジェクト横断のリンクとベクトルのキャッシュ — 消して安全です |
| `wiki` | `derived` | マシンスコープの serve 用スクラッチ — 消して安全です |
| `engine.pid` | `scratch` | フリートの pid ファイル。かつて六日前に死んだ pid を抱えたままだったことがあり、pidlock が信用せず検証するのはそのためです |
| `engine.pid.lock` | `scratch` | フリート pid ファイルのミューテックス。保持中のものを消すと二つのフリートが起動します |
| `*.bak*` | `scratch` | `registry.json` と `config.json` の移行前コピー。どのコード経路も書かないので、誰かが残したくて存在しています |

## 実際の分類を見る

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

`sidecars` チェックは実際の `.tesserae/` をレジストリと突き合わせ、三つの集団を
別々に報告します: 孤児となった tmp の片割れ、手作りの `graph.json.bak-*` コピー、
そしてどのレジストリ項目も所有を主張しないエントリです。`--fix` が消すのは最初の
一つだけで、しかも書き手の pid が死に、ファイルが 24 時間より古い場合に限られます —
生きている書き手は `write_text` と `replace` の間にいて、複数のホストが一つの
`.tesserae/` をマウントし得る以上、`os.kill(pid, 0)` はローカルのプロセステーブルに
ついてしか答えないからです。

**未分類のエントリは報告されるだけで、決して触られません。** レジストリが所有を
主張しないエントリは、Tesserae のバグというよりは誰か他人のファイル — あなたのメモ、
別のツールのキャッシュ — である可能性の方が高いので、見つけたときの答えは削除では
なく、名前を挙げることです。登録を忘れた新しい Tesserae サイドカーが姿を現す経路
でもあります。

Tesserae に一括の `reset` 動詞はありません。この分類はそうしたコマンドを可能にする
前提ですが、分類を書き下ろすのと同じ変更でそれに対する破壊的コマンドを出荷するのは
順序が逆です。
