# Obsidian — コンパイル済み wiki を本物のヴォルトとして開く

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae の Obsidian エクスポートは、コンパイル済みの型付きグラフを、意見のある本物の [Obsidian](https://obsidian.md) ヴォルトに変換します。単なる markdown のディレクトリではなく、`.obsidian/` 設定、型を意識した [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts)、[Dataview](https://blacksmithgu.github.io/obsidian-dataview/) でクエリ可能な frontmatter、ヴォルトダッシュボード、そしてヴォルト横断の `wiki://` 参照インデックスを備えたヴォルトです。

## 前提条件

まずプロジェクトをコンパイルしてください:

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

コンパイルすると `.tesserae/graph.json`（信頼できる情報源）と、プレーンな markdown 射影が `.tesserae/markdown_projection/` に生成されます。Obsidian エクスポートはこの射影の上に構築されますが、各ページに Obsidian ネイティブのエンリッチメントを重ねます。

## 1) ヴォルトをエクスポートする

```bash
tesserae vault export --output ~/Documents/tesserae-vault
```

ディレクトリが存在しない場合は作成されます。再実行すると冪等に上書きされます — 同じグラフが与えられれば markdown 射影は決定論的です。

ディスク上に何が配置されるか:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) ディレクトリを Obsidian で開く

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`。

Obsidian は `.obsidian/` を検出して本物のヴォルトとして認識し、読み込みます。コミュニティプラグイン一覧には Dataview が含まれているため、Obsidian は有効化を促します（推奨 — 有効でないと dataview ブロックはコードフェンスとしてレンダリングされます）。

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`。

## 3) ヴォルトを見て回る

### エントリーポイント

- `README.md` — このヴォルトが何であるか、どう更新するか
- `index.md` — セクション（papers、concepts、claims）ごとに wikilink 付きで並んだ全 node
- `_meta/dashboard.md` — dataview の概観: 最近のページ、papers、concepts/claims

### ページごとのエンリッチメント

各 node ページには次が同梱されます:

**型を意識した callouts。** 各ページ先頭のセマンティック callout により、node の type が一目で分かります:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

主なマッピング: `Paper → quote`、`Repository → info`、`Contribution → success`、`Performance → info`、`Limitation → warning`、`Causal → important`、`OpenQuestion → question`、`Evidence → example`。

**Dataview でクエリ可能な edges。** frontmatter には型付き edges がネストしたマップとして格納されます:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

次のようなクエリを書けます:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**ヴォルト横断ブリッジ。** node の説明やメタデータで言及された `wiki://<alias>/<kind>/<slug>` URI は、frontmatter フィールドとしても:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

`Cross-vault references` 本文セクションとしても露出します。ヴォルトレベルの `_bridges.md` インデックスは、すべての外向き参照を destination alias でグルーピングして集約するため、1 ページからヴォルト横断リンクを監査できます。

**Related (dataview) ブロック。** 各ページの末尾には、リンクバックしているページを自動で表示するクエリが配置されます:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### ヴォルトダッシュボード

`_meta/dashboard.md` は最も役立つ集計ビュー（最近更新されたページ、メタデータ列付きの全 paper、type 順にソートされた全 concept と claim）の dataview ブロックを同梱します。自由に編集してください — これは出発点であり、固定された契約ではありません。

### ヴォルトのグラフビュー

Obsidian 組み込みのグラフビュー（`Ctrl/Cmd+G`）は、`## Outgoing` / `## Incoming` セクションに出力された wikilink に対してすでに機能します。同梱の `.obsidian/graph.json` は方向感のために `papers/`、`concepts/`、`claims/` のパスをカラーコード付きにします。より豊かな切り口のために dataview でフィルタしたビューを上に重ねることもできます。

## ヴォルト横断のワークフロー

複数の Tesserae ヴォルトを登録して `wiki://` URI がヴォルト間で解決されるようにします:

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

登録後、各ヴォルトを再エクスポートしてください。各エクスポートの `_bridges.md` には alias でグルーピングされた、ヴォルト間で解決可能な参照が表示されます。

Obsidian 自体は `wiki://` URI をネイティブにたどりません — インラインテキストとしてレンダリングされます — が、`_bridges.md` とページごとの `Cross-vault references` セクションが、専用の Obsidian プラグインが登場するまでの手動インデックスを提供します。

## 更新ワークフロー

ソースファイルから新しいソースや修正を取り込むには:

```bash
# プロジェクトのソースディレクトリ配下のソースファイルを編集してから:
tesserae compile
```

`compile` がヴォルトを自動的に再射影します — 別途エクスポート手順を実行する必要はもうありません。（フルの再コンパイルなしで一度きりの再射影だけを行いたい場合は `tesserae vault export --output <パス>` も引き続き使えます。）Obsidian はディスク上で変更されたファイルをホットリロードします。

ヴォルト内にグラフから射影されない markdown メモ（例: 個人的な注釈）を追加してあれば、それらは残ります — プロジェクターは自身が所有するファイル（`papers/`、`concepts/`、`claims/` 配下、および `index.md`、`_bridges.md`、`_meta/dashboard.md`、`README.md`）のみを上書きします。手書きのページ（`node_id:` フロントマターのないファイル）と、各射影ページにある専用のユーザーノートブロック（`<!-- user-notes:start -->` … `<!-- user-notes:end -->`）は再コンパイルをまたいで保持されます。

### Obsidian での編集はグラフへ反映されます（双方向同期）

v0.5.0 以降、ヴォルトはもはや**一方向のエクスポートではありません**。いまや*双方向の射影*です: 型付きグラフが依然として真実の源ですが、`project compile` は再射影する**前に**、Obsidian での編集をヴォルトから読み戻してグラフにオーバーレイします。Obsidian でノードの `title`、`aliases`、説明コールアウト、あるいは任意の非システム・フロントマター・スカラーを編集して再コンパイルすると、その変更は残り — 静的サイト、MCP、その他すべての射影へ伝播します。

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

オーバーレイが取り込む対象（*ヴォルト優先*のフィールド）:

- `title` → ノードの `name`
- `aliases` → ノードのエイリアス
- 本文の説明コールアウト（またはその最初の段落）→ ノードの `description`
- 予約されていないすべてのフロントマター・スカラー → `metadata.<key>`（予約／システムキー `node_id`、`title`、`type`、`aliases`、`source_path`、`edges_out`、`edges_in`、`cross_vault` がユーザーオーバーライドとして扱われることはありません）

オーバーレイの実行ごとに `.tesserae/diverged-fields.md` レポート（`## Field overrides — N across M node(s)`）が書き出されるため、何が反映されたかを正確に監査できます。ユーザーノートブロック内に追加した wikilink は `user_link` エッジになります。1 回の実行でオーバーレイをバイパスするには `tesserae compile` (with `compile_options.no_vault_pull = true` in `.tesserae/config.json`) を渡します — 復旧時や、意図的にソース markdown を優先させたいときに便利です。

この機能を有効化した後の初回コンパイルは「フリーパス」になります: まだ `vault_snapshot.json` のベースラインが存在しないため何も取り込まれず、最後に書き出されるスナップショットが次回コンパイルの diff のベースラインになります。

専用のライブワークフローとして、`tesserae vault sync` はフルの再コンパイルなしにオーバーレイを再適用して再射影します:

```bash
# グラフを変更せずに、コンパイルが何を読み戻すかをプレビューします。
tesserae vault sync --dry-run

# ヴォルトを監視し、編集をリアルタイムでラウンドトリップします（Ctrl-C で停止）。
tesserae vault sync --watch

# ノードのリネーム／削除後、孤立した射影ページを削除します。
tesserae vault sync --prune-orphans
```

フィールドごとの所有権マトリクスと設計の根拠の全体は [obsidian-sync.md](obsidian-sync.ja.md) を参照してください。

## 静的サイトとの使い分け

コンパイル済みの HTML サイト（`tesserae export site` → `.tesserae/site/`）は共有のための一方向・読み取り専用のエクスポートです — GitHub Pages、S3、任意の静的ホストへプッシュしてください。Obsidian ヴォルトは Dataview と Obsidian のグラフビューを使って**ローカルで読み、クエリし、編集する**ためのものです: 編集がグラフへ反映される唯一の射影です（上記の双方向同期セクションを参照）。両者は同じグラフから射影されるため、ドリフトすることはなく — Obsidian で加えた修正は次回のコンパイルでサイトへ伝播します。
