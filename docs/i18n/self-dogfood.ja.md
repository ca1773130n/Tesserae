# セルフドッグフードデモ

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
このプロジェクトは自分自身をインデックスできます。セルフドッグフードのフローは、Tesserae をインストールし、自身のリポジトリ内にセットアップし、自身の docs/ソース/テスト/スクリプトを取り込み、必要に応じて Understand Anything と Cognee をリフレッシュし、グラフ成果物をコンパイルし、静的 Web フロントエンドをビルドできることを証明します。

同じフローはマルチモーダルのスモークテストも兼ねています。RAG-Anything がインストールされ（`tesserae setup --install raganything`）、`.tesserae/config.json` で有効化されている場合（`memory_backends.raganything.enabled: true`）、ドッグフードのコンパイルは RAG-Anything を Tesserae 自身の `docs/` markdown と、`docs/assets/` およびプロジェクトレベルの `assets/` の画像に向けます。これにより、テキストファーストのソースローダーがスキップするスクリーンショットや図を含む、実在するプロジェクト所有の非コードコーパスに対してマルチモーダルパイプラインを検証できます — 別個のフィクスチャセットを発明することなく。

これは自己改善ループの動作確認にもなります。各コンパイルは可変のメモリ状態 —
`decay_score`、`access_count`、`confidence`、そして `superseded` フラグ — を
`.tesserae/sqlite.db` 内の **`node_memory` サイドカー**テーブルに再導出します。
これらのスカラーはサイドカー*のみ*に存在し、`graph.json` には決して入らないため、
新規のドッグフードコンパイルはグラフについてバイト単位で同一のまま、
サイドカーが減衰と再出現を追跡します。`>= 3` の異なるセッションにわたって再出現する
インサイトは `(0, 1]` の数値的な confidence で強化され
（3 セッション → `0.5`、4 → `0.75`、5+ → `1.0`、上限あり）、サイドカーに書き込まれ、
MCP の `fresh_insights` ツールで表面化されます。このツールはデフォルトで、より新しい
近似重複によって置き換えられた（superseded）検出項目を隠します。

## コマンド

リポジトリルートから:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything --install understand-anything --install cognee
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

開く:

```text
http://127.0.0.1:8765/
```

## 生成されるワークスペース

セルフデモは生成された成果物を以下に書き込みます:

```text
.tesserae/
```

主要な成果物:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

生成されたワークスペースは意図的にデフォルトではコミットされません。上記のコマンドでリポジトリのソースから再現できます。

## 最新の検証済み実行

Tesserae リポジトリ自身から `2026-04-27 11:11:23 KST` に検証済み。

統合のオプトイン（Understand Anything、cognee）は現在、CLI フラグではなく
**インタラクティブなウィザードのプロンプト**です。以下の非インタラクティブな同等手順は、
`tesserae init --yes`（統合は OFF）を実行し、`.tesserae/config.json` で統合を有効化し
（ウィザードは `memory_backends` と `external_tools` キーの下に書き込みます —
正確なキーは各統合のドキュメントを参照）、コンパイル前にそれぞれをリフレッシュします。

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable Understand Anything + cognee in .tesserae/config.json and run:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

最終的な成果物の数:

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

上位のノードタイプ:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

ブラウザでの検証:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## これが実証すること

- 公開されたインストール経路が機能する。
- `tesserae` シェルコマンドが機能する。
- リポジトリにプロジェクトローカルの `.tesserae` ワークスペースをアタッチできる。
- 研究/ドキュメントの markdown と開発コードのグラフノードが共存できる。
- Markdown、Obsidian、フロントエンド、Graphiti、Cognee、SQLite、レポート、agent-harness の各プロジェクションが 1 つのグラフパイプラインから生成される。
- 静的 HTML フロントエンドが JavaScript のビルドステップなしでプロジェクトグラフを閲覧できる。
- 自己改善ループが動作し永続化される: 減衰、アクセスカウント、再出現の confidence、supersede フラグが `graph.json` を乱すことなく `node_memory` サイドカーに書き込まれる。
- `tesserae[semantic]` がインストールされている場合、hybrid 検索は実際のセマンティックバックエンドを解決する（デフォルトの `auto` 順序: model2vec → sentence-transformers → hash-bucket スタブ）。ない場合、埋め込み検索は非セマンティックな hash-bucket スタブにデグレードし、目立つ警告を発する。
