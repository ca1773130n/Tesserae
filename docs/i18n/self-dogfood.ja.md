# Self-dogfood デモ

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
このプロジェクトは自分自身をインデックスできます。self-dogfood フローは、Tesserae をインストールし、自身のリポジトリ内で設定し、自身の docs/source/tests/scripts を取り込み、任意で Understand Anything と Cognee を更新し、グラフ成果物をコンパイルし、静的 Web フロントエンドをビルドできることを証明します。

さらに自己改善ループも動かします。各コンパイルは可変なメモリ状態 — `decay_score`、`access_count`、`confidence`、`superseded` フラグ — を `.tesserae/sqlite.db` 内部の **`node_memory` サイドカー** テーブルへ再導出します。これらのスカラーはサイドカーに*のみ*存在し、`graph.json` には決して入りません。そのため再 dogfood コンパイルではグラフは byte-identical のまま、サイドカーが decay と recurrence を追跡します。`>= 3` 個の別個のセッションにまたがって再発する insight は `(0, 1]` の数値 confidence で強化され（3 セッション → `0.5`、4 → `0.75`、5+ → `1.0`、上限あり）、サイドカーに書き込まれ、MCP `fresh_insights` ツールが公開します。このツールはデフォルトで、より新しい near-duplicate に置き換えられた（superseded）finding を隠します。

## コマンド

リポジトリルートから:

```bash
# shell コマンドがインストールされていることを確認します。
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# （任意）デフォルトのセマンティック埋め込みバックエンドをインストールします。
pip install 'tesserae[semantic]'

# このリポジトリを Tesserae プロジェクトとして設定します。
tesserae project setup \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# 設定済みソースをコンパイルします。
tesserae project compile

# 静的フロントエンドを明示的に再ビルドします。
tesserae project build-site

# ローカルで配信します。
tesserae project serve --port 8765
```

開く:

```text
http://127.0.0.1:8765/
```

## 生成されるワークスペース

self-demo は生成された成果物を次の場所に書き込みます:

```text
.tesserae/
```

主要な成果物:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # 型付きグラフ + node_memory サイドカー + ライブ HarnessSessionsDB
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

生成されたワークスペースは、デフォルトではコミットしないことを意図しています。上記のコマンドでリポジトリソースから再現できます。

## 最新の検証済み実行

Tesserae リポジトリ自身から `2026-04-27 11:11:23 KST` に検証済みです。

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae project setup --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts --with-understand-anything --install-understand-anything --understand-anything-platform codex --run-cognee --install-cognee
ingest command:  tesserae project ingest README.md docs --changed-only
compile command: tesserae project compile
site command:    tesserae project build-site
serve command:   tesserae project serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

最終成果物数:

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

上位ノードタイプ:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

ブラウザ検証:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## これが示すこと

- 公開インストール経路が動作します。
- `tesserae` shell コマンドが動作します。
- リポジトリはプロジェクトローカルの `.tesserae` ワークスペースを接続できます。
- 研究/ドキュメント markdown と開発コードのグラフノードが共存できます。
- Markdown、Obsidian、frontend、Graphiti、Cognee、SQLite、report、agent-harness の投影が 1 つのグラフパイプラインから生成されます。
- 静的 HTML フロントエンドは、JavaScript ビルド手順なしでプロジェクトグラフを閲覧できます。
- 自己改善ループが動作し永続化されます。decay、access count、recurrence confidence、supersede フラグが `graph.json` を乱さずに `node_memory` サイドカーへ書き込まれます。
- `tesserae[semantic]` がインストールされていると、ハイブリッド検索は実際のセマンティックバックエンドを解決します（デフォルト `auto` 順: model2vec → sentence-transformers → hash-bucket スタブ）。未インストールの場合、埋め込み検索は非セマンティックな hash-bucket スタブに格下げされ、目立つ警告を発します。
