# フロントエンド再設計 — 注釈付きルートウォークスルー

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
このドキュメントは、再設計された Tesserae 静的サイトで表示されるすべてのルートを案内するガイドです。[`architecture.md`](architecture.ja.md) の高レベルモデルと、[`feature-map.md`](feature-map.ja.md) のステータス表を補完します。

サイトは `tesserae compile` の実行後、`.tesserae/site/` に生成されます。ローカルで確認するには:

```bash
tesserae serve --port 8765
# open http://127.0.0.1:8765/
```

すべてのルートは静的 HTML ファイルであり、AI コンシューマ向けに 2 つの兄弟ファイル（`<page>.txt`、`<page>.json`）を持ちます。サイト全体の AI エクスポート（`llms.txt`、`llms-full.txt`、`graph.jsonld`、`sitemap.xml`、`rss.xml`、`robots.txt`、`ai-readme.md`、`manifest.json`）については、このドキュメントの末尾で説明します。

ステータス凡例: ✅ 出荷済み · ⚠ 進行中。

## すべてのページに共通する規約

すべてのリーフページは同じ構造に従います（設計仕様 §3.3）:

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

サイト全体の chrome:

- **トップバー。** 左にロゴ + プロジェクト名、右に検索トリガー + テーマ切り替えがあります。
- **左レール**（デスクトップ ≥ 1024 px）: 階層ツリー — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About。件数は `manifest.json` から取得します。
- **下部ナビ**（モバイル）: ドロワーレールは折りたたまれ、下部ナビに最もよく使われる kind が表示されます。
- **検索パレット。** `cmd+k` / `ctrl+k` / `/` — `search-index.json` に対し、wiki kind にスコープしたファジーマッチを行います。最近開いたページは `localStorage` に保存されます。
- **テーマ。** 既定はライトです。切り替えると `data-theme="dark"` が `localStorage` に永続化されます。フラッシュを避けるため、描画前に適用されます。

## ホーム

### `/` ✅

> _スクリーンショット: home.png_

ホームページはプロジェクトの現在の脈動を示します。グローバルな `pulse` synthesis（`wiki/syntheses/pulse.md`）によって駆動され、コンパイルのたびに再生成されます。ヒーローは sources、concepts、papers、open questions の統計行で、その後に最新の `pulse` 本文から抽出された 1〜3 枚の「今週の新着」カードが続きます。ヒーローの下には、初訪問のユーザーがナビを読まなくても深掘りできるよう、各 kind のインデックスページへつながる厳選された入口があります。

LLM agent を最初に着地させるのに適したページです。コーパス内で最も信号対雑音比の高い要約を提供します。カードはインデックスに戻るのではなく、リーフページへリンクします。

**注目すべき操作。** 統計行をクリックすると、対応する kind インデックスへスクロールまたは移動します。ヒーロー文言は編集可能です — `wiki/overview.md` を手書きすると、次回コンパイル時にホームページがそれを取り込みます。

**関連ルート。** 活動ログは [Timeline](#timeline)、長文形式は [Syntheses](#syntheses)、空間ビューは [Graph](#graph-view) を参照してください。

## Sources

これらは L1 の生文書です — `.tesserae/config.json` が参照する `data/`、`docs/`、プロジェクトツリー内のファイルです。すべての source は `SourceDocument`（または `Paper` / `Repository`）ノードになり、`WikiLayerProjector` によって wiki ページへ投影されます。

### `/sources/` ✅

> _スクリーンショット: sources-index.png_

コーパスが把握しているすべての生文書の表です。列は、型バッジ（Document / Paper / Repository / Project）、タイトル、言及数、最終更新です。表はゼブラストライプで、ホバーすると 1 行プレビューが表示され、型バッジは検索パレット（`/ kind:papers`）でフィルタできます。

wiki が構築されている文字どおりの証拠を agent が列挙する必要があるときのインデックスです。

**関連ルート。** 論文のみの切り口は [Papers](#papers)、repo のみは [Repos](#repos)、抽出された観点は [Concepts](#concepts) を参照してください。

### `/sources/<slug>.html` ✅

> _スクリーンショット: source-detail.png_

stdlib markdown パイプライン（`tesserae/site/markdown.py`）を通してレンダリングされた単一の source 文書です。本文は元の markdown で、安全なリンク/画像レンダリングが適用されます。本文の下には次があります。

- **Mentions** — この source から抽出されたすべての concept / entity / paper と、edge 型バッジ。
- **Backlinks** — ここへリンクしている他のすべての wiki ページ。
- **Related** — 4 信号ランキング（direct link 3.0 + source overlap 4.0 + Adamic-Adar 1.5 + type affinity 1.0）。
- **Source provenance** — 元ファイルパス + 証拠としての生ファイル先頭 12 行。
- **Activity** — 直近 12 週間の週次言及 sparkline。
- **AI siblings footer** — `<page>.txt` プレーンテキストビュー、`<page>.json` 構造化レコード。

**注目すべき操作。** 本文中の URL と arXiv ID は自動リンクされます。コードブロックはクリックでコピーでき、右レール TOC はスクロールを追跡します。

## Concepts

Concepts は、コーパス全体で繰り返し現れるアイデア、用語、アルゴリズム、アーキテクチャ、方法論です。対象には `Concept`、`TechnicalTerm`、`Algorithm`、`MathematicalConcept`、`MethodologicalConcept`、`ArchitecturePattern`、`TrainingParadigm`、`InferenceStrategy`、`EvaluationProtocol`、`Task`、`Capability`、`ObjectiveFunction` が含まれます。

### `/concepts/` ✅

> _スクリーンショット: concepts-index.png_

facet フィルタ可能なカードグリッドです。各カードには型バッジ、タイトル、1 行定義、言及数、最終更新日が含まれます。ページはタグ chip（Algorithm, Architecture, Training paradigm, …）による型フィルタをサポートし、既定では言及数順に並びます。

「このコーパスは何について語っているのか？」と尋ねるときに行く場所です。

**関連ルート。** [Papers](#papers) — concepts は通常、論文で導入されます。[Topics](#topics) — concepts は topics にクラスタ化されます。

### `/concepts/<slug>.html` ✅

> _スクリーンショット: concept-detail.png_

合成された定義（synthesis がない場合は最も権威ある source の最初の段落）、コーパス全体の全言及リスト、ランキングされた関連近傍、activity sparkline を備えた豊かな concept ページです。

「Mentions in the corpus」セクションは agent にとって要となる部分です — その concept を参照するすべての paper / source / repo を、文脈用の 1 行抜粋とともに列挙します。

**注目すべき操作。** 右レール TOC は本文中の `<h2>` / `<h3>` を追跡します。関連カードグリッドは 4 信号スコアを尊重するため、近傍は単に隣接しているだけでなく関連していると感じられます。

## Entities

Entities は、コーパス内の名前付きで識別可能なものです: `Model`、`Dataset`、`Benchmark`、`Metric`、`Organization`、`Person`。graph nodes から投影され、そのページは散文よりも claims と results を強調します。

### `/entities/` ✅

> _スクリーンショット: entities-index.png_

型 facet を持つ表です。列は型バッジ、名前、要約（frontmatter `description` があればその最初の文、なければ本文の最初の段落）、言及数です。型 chip でフィルタできます。

### `/entities/<slug>.html` ✅

> _スクリーンショット: entity-detail.png_

詳細ページは 3 つのセクションを前面に出します。

- **Claims** — この entity に接する `ContributionClaim`、`PerformanceClaim`、`ComparisonClaim`、`LimitationClaim`、`CausalClaim` edges と、インラインの証拠抜粋。（Claim nodes 自体には独自 URL はなく、ここで bullet として表示されます。）
- **Reported results** — この entity にリンクされたすべての `Result` / `evaluated_on` / `reports_result` を、metric + score + paper provenance とともに列挙します。
- **Mentions in the corpus** — concept ページと同じ形です。

agent が「モデル X について何が分かっているか？」または「benchmark Y はどの datasets で報告されているか？」に答える必要があるときに適したページです。

## Papers

Papers は一級の証拠として扱われる研究文献です。再設計では一般的な source pool から切り出し、論文固有の機能をレンダリングできるよう専用 kind を与えました。

### `/papers/` ✅

> _スクリーンショット: papers-index.png_

年、topic、family chip を備えた facet フィルタ可能なカードグリッドです。各カードは、タイトル、著者（最初の 3 名 + “et al.”）、1 行 abstract 抜粋、年バッジ、言及数を含みます。既定では年の降順で並びます。

### `/papers/<slug>.html` ✅

> _スクリーンショット: paper-detail.png_

論文カードレイアウトです。タイトル、著者、年、abstract 抜粋に続いて、次のセクションがあります。

- **Contributions** — 論文からの `ContributionClaim` edges。
- **Results** — metric + score を持つ `reports_result` edges。
- **Comparisons** — `compares_against` edges。
- **Related concepts** — `introduces` / `extends` / `uses` edges。
- **Open questions** — 論文を通じてリンクされた `OpenQuestion`。

ArXiv リンクは `tesserae/site/markdown.py` によって自動リンクされます。右レール TOC は上記のセクションリストを反映します。

## Repos

Repos はソフトウェアプロジェクトです — `Repository`、`Project`、`CodeProject`。再設計では、class / function ごとの HTML ページは明示的にレンダリングしません。repo ページはプロジェクトの表面を要約し、source tree へリンクします。

### `/repos/` ✅

> _スクリーンショット: repos-index.png_

言語バッジ付きのカードグリッドです。各カードは、名前、1 行 README 抜粋、主要言語、既知であれば star 数、最終更新を含みます。

### `/repos/<slug>.html` ✅

> _スクリーンショット: repo-detail.png_

Repo ページには次が表示されます。

- **README excerpt** — コーパス内にその repo の `README.md` がある場合、先頭約 600 文字。
- **Dependencies** — 他の repos / models / concepts への `depends_on` / `imports` / `uses` 型の out-edges。
- **Implements** — papers からの `implemented_in` edges（つまり「この repo は論文 X を実装している」）。
- **Module overview** — modules / classes / functions の件数。ただし設計上、function ごとのリンクはありません。
- **Related** — 4 信号ランキング。

agent がアプローチ群の中から repo を選ぶ必要があるときに適したページです。

## Topics

Topics は concepts をより広い分野へまとめます: `ResearchField`、`ResearchTopic`、`ProblemArea`、`ApproachFamily`、`Trend`。Topic ページは一部が graph nodes から投影され、一部が synthesis されます。topic の導入文を駆動する field-overview ページについては [Syntheses](#syntheses) を参照してください。

### `/topics/` ✅

> _スクリーンショット: topics-index.png_

型 chip をキーにしたカードグリッドです。各カードは topic 名、親 field、および “X papers · Y concepts · Z repos” の統計を表示します。

### `/topics/<slug>.html` ✅

> _スクリーンショット: topic-detail.png_

Topic ページは、`wiki/syntheses/topic-<slug>.md` に存在する場合 synthesis 段落で始まり、次を列挙します。

- **Papers in this topic** — 表。
- **Approach families** — `ApproachFamily` 型の sub-topics。
- **Concepts in scope** — chip cloud。
- **Open questions** — リンクされた `OpenQuestion` nodes。
- **Related fields** — `belongs_to` / `rising_in` 近傍。

**関連ルート。** 長文の物語は [Syntheses → topic-…](#syntheses)、構成要素は [Concepts](#concepts) を参照してください。

## Syntheses

Syntheses は `SynthesisProjector` が生成する高次ページです。7 種類（pulse, daily_digest, weekly, topic, comparison, field_overview）が、コーパスの時間的・構造的な切り口をカバーします。現在の synthesis 本文は決定的テンプレートです。`TESSERAE_SYNTHESIS_LLM=1` は LLM アップグレード用フック（stub）です。

### `/syntheses/` ✅

> _スクリーンショット: syntheses-index.png_

インデックスはすべての synthesis を kind ごとにグループ化し、各グループ内で `generated_at` の降順に並べます。各行は kind バッジ、タイトル、1 行 lead、generated-at timestamp を含みます。

### `/syntheses/<slug>.html` ✅

> _スクリーンショット: synthesis-detail.png_

Synthesis ページは markdown 本文をそのままレンダリングします。Frontmatter には `synthesis_kind`、`slug`、`sources`、`inputs`、`generated_at`、`generator`、`content_hash` があり、ページは eyebrow に `synthesis_kind` と `generated_at` を表示します。本文の下には次があります。

- **Sources consumed** — `summarizes` edge targets — synthesis が参照した source ごとに 1 つ。
- **Inputs (graph nodes)** — `synthesizes` edge targets — 入力になったすべての node。
- **Related syntheses** — 同じ kind / 重複する inputs。

`pulse` synthesis はホームページです。daily / weekly syntheses は [Timeline](#timeline) レールのアンカーになります。

## Questions

Open questions は `OpenQuestion` nodes としてコーパスから抽出されます — paper、source、synthesis が未解決の問題を明示的に示している箇所です。

### `/questions/` ✅

> _スクリーンショット: questions-index.png_

open question ごとに 1 行のリストビューです。列は question text、それを提起した sources、related concepts、age（初出からの経過）です。既定では新しい順に並びます。

### `/questions/<slug>.html` ✅

> _スクリーンショット: question-detail.png_

単一の open question に焦点を当てたページで、次を含みます。

- 質問文そのもの。
- **Raised in** — 質問が現れる sources / papers / syntheses。
- **Related concepts** — 質問の対象。
- **Adjacent questions** — 同じ source または共有 concepts の質問。

agent が「この領域でまだ未回答のことは何か？」と尋ねられたときに着地するページです。

## Sessions

Sessions はインポートされたローカル AI-harness transcripts で、`.tesserae/harness_sessions/` に正規化された後、検索可能なプロジェクトメモリとしてレンダリングされます。インポートは `tesserae sessions discover --import` または `tesserae sessions import ...` で明示的に行います。通常のサイトビルドは、すでに正規化済みの records だけを消費します。

### `/sessions/` ✅

> _スクリーンショット: sessions-index.png_

Sessions インデックスは、プロジェクトのトップレベル Claude Code と Codex sessions をグループ化します。カード/表には title、harness、model、project path、start/end timestamps、message count、tool count、既知の場合は token counts、files touched、commands、preview text が表示されます。このページは global rail、home Browse cards、kind が `session` の search palette entries からリンクされます。

### `/sessions/<project>/<session>.html` ✅

> _スクリーンショット: session-detail.png_

Session 詳細ページは、生の transcript dump ではなく共有 shell を使用します。レイアウトには hero、stat strip、High-Level Summary、Timeline & size、decisions/files/commands/tools/errors、折りたたまれた subagent tree、turn-by-turn conversation block が含まれます。

Session 固有の左レールは、汎用 file-tree rail を user/assistant turn anchors（`#turn-N`）に置き換えます。User と assistant turns はサイト markdown renderer を通してレンダリングされます。inline code、command/tag markup、paths、filenames、hashtags などの意味的な表面はコンパクトな chips になります。Tool calls は直前の assistant turn の下に折りたたまれ、light / dark themes の両方で暗い code/tool backgrounds を使います。

現在の詳細タイポグラフィは、通常の conversation prose を 8 px、汎用 conversation code fences を 10 px、bash/shell fenced code content を 11 px、tool details/summary を 10 px、tool headers を 8 px、tool payload text を 6 px に保ちます。Selector map と公開時のプライバシーチェックリストは [`session-history.md`](session-history.ja.md) を参照してください。

## Timeline

Timeline ページは活動ログです。コーパスはいつ増えたのか、どの kind の nodes が追加されたのか、日・週単位でどう見えるのかを示します。

### `/timeline/` ✅

> _スクリーンショット: timeline.png_

ページには 3 つの rail があります。

- **Activity heatmap** — 月 + 曜日ラベル付きの 26 週 SVG。セルは node-add-count によって色付けされます。その日の `digest.md` source page が存在する場合、各セルはそこへリンクします。
- **Days** — 直近 60 日の日付行で、各行は `<date> · X activity · Y papers` を表示します。その日付に `digest.md` がある場合、日付はリンクになります。
- **Syntheses rail** — すべての synthesis を新しい順に並べ、kind badge + title + timestamp を表示します。

「最近何が起きているか」という質問のためにブックマークするページです。

### `/timeline/<YYYY-MM-DD>.html` ✅

> _スクリーンショット: timeline-day.png_

日別詳細ページ — その暦日に紐づくすべての paper / repo / concept / synthesis を列挙するもの — が出荷されました。`render_timeline_day`（`tesserae/site/pages.py`）が `StaticSiteBuilder`（`tesserae/site/__init__.py`）を通じて日付ごとに emit され、各ページは `manifest.json` に `timeline_day` ルートとして記録されます。heatmap cells はその日別ページへ直接リンクします（日別ルートが存在しない場合にのみ、その日の `digest.md` source page へフォールバックします）。

## Graph view

### `/graph/` ✅

> _スクリーンショット: graph.png_

インタラクティブな graph view は 3D force layout（3d-force-graph + Three.js、`assets/` に CDN snapshot として vendored）で、2D fallback もあります。Nodes は `ResearchNodeType` によって色分けされます。Edges は hover 時に型をラベルとして表示します。

**注目すべき操作。**

- node を hover → 名前、型、言及数を含む tooltip。
- node を click → その wiki page へ移動。
- edge を hover → 関係（`uses` / `extends` / `compares_against` / …）を示すラベル。
- Mouse wheel → カーソルをアンカーにした zoom（中心ではなくカーソル方向へズーム）。
- Drag → orbit（3D）または pan（2D）。
- 右上で 2D/3D を切り替え。

ページ埋め込み payload は `MAX_GRAPH_NODES = 1500` に制限されています（[`pages.py`](../../tesserae/site/pages.py) 参照）。完全な graph は tooling 用に常に `/graph.json` で利用できます。Code-graph nodes（`CodeClass`、`CodeFunction`、`Dependency`、…）は設計上、visualisation から除外されます。

**関連ルート。** すべての wiki page は、フォーカスされた subgraph view へリンクします。

## About

### `/about.html` ✅

> _スクリーンショット: about.png_

schema（すべての `ResearchNodeType` と edge whitelist、それぞれの 1 行説明）、build info（commit SHA、generator version、generated-at timestamp）、AI exports inventory を説明する静的ページです。

新しい contributor が、どの型が存在し、それぞれ何のためのものかを理解するのに適したページです。

## AI siblings — すべてのページがデータでもある仕組み

Tesserae はすべてのページを 3 つの形式で出荷します。人間向け HTML、プレーンテキストの兄弟ファイル、構造化 JSON の兄弟ファイルです。さらに crawlers と agents 向けのサイト全体エクスポートもあります。

### ページごとの `<page>.txt` ✅

1 ページのプレーンテキストビューです — nav も styling もなく、本文が明示的に使うもの以外の markdown 装飾もありません。agent が HTML scraper を書かずに単一ページを context として取り込みたいときに適しています。

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### ページごとの `<page>.json` ✅

構造化レコードです。

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

tool が markdown parser なしで、link list、frontmatter、kind tag などの型付きアクセスを必要とするときに適しています。

### `/llms.txt` ✅

llmstxt.org の短いインデックスです。各 kind と、その kind ごとの最も関連性の高い entries を 1 ページに列挙します。LLM agent がサイトを発見するときに最初に行うリクエストに適しています。

### `/llms-full.txt` ✅

llmstxt.org の長い形式です。すべての wiki page を連結します。5 MB で上限があり、上限に達した場合は `[TRUNCATED — see graph.jsonld for the full set]` マーカーでファイルが終わります。agent が 1 リクエストと 5 MB context でコーパス全体を取り込む予算を持つときに適しています。

### `/graph.json` ✅

完全な `ResearchGraph` payload です — HTML ページを持たない code-graph nodes も含みます。tool が自身の分析のために完全な graph を必要とするときに適しています（MCP、Cognee、Graphiti consumers はすべてこれを読みます）。

### `/graph.jsonld` ✅

schema.org `Dataset` JSON-LD です。wiki-layer nodes のみ（code nodes なし）。構造化データを理解する crawlers に適しています — Google Knowledge Graph、検索インデクサ、schema.org 対応 aggregators など。

### `/search-index.json` ✅

palette + page-search index です。wiki-layer kinds のみです。サードパーティ検索 UI を統合するときに適しています。schema は `{kind, title, slug, body, score_hints}` entries のリストです。

### `/sitemap.xml` ✅

出力されたすべての route と、frontmatter（`generated_at`、`updated_at`、`published_at`、`date`）から派生した `lastmod` を含みます。検索エンジンに適しています。

### `/rss.xml` ✅

新しい順に並べた直近 30 件の syntheses です。「この wiki を購読する」に適しています — RSS readers、IFTTT、mailing lists。

### `/robots.txt` ✅

寛容です — すべてを crawl + index します。この wiki は agents に読まれることを意図しています。

### `/ai-readme.md` ✅

HTML が得意でない AI agents 向けの機械可読なサイトマップです。上記すべての artifact と目的、各形式がいつ適しているかの 1 行説明を列挙します。

### `/manifest.json` ✅

サイトで出力されたすべてのファイルについて、sha256 + size のレコードです。次に使われます。

- compile-twice idempotence test（`tests/test_project_e2e_redesign.py`）。
- 完全な diff なしで「前回訪問以降、このサイトは変わったか？」を検出したい downstream tooling。
- 何も変わっていないときに pushes を short-circuit する deploy command。

## 適切な形式を選ぶ

| あなたが… | 読むもの |
|---|---|
| 初めて訪れる人間 | `/` の後、[Concepts](#concepts) または [Papers](#papers) へ掘り下げる |
| 「新着」を知りたい人間 | [Timeline](#timeline) と最近の [Syntheses](#syntheses) |
| 構造を見たい人間 | [Graph view](#graph-view) |
| 1 回の query を行う LLM agent | 型付きアクセス用の `<page>.json` |
| 1 回の query を行うが予算制約のある LLM agent | `<page>.txt` |
| すべてを取り込む LLM agent | `/llms-full.txt`（≤ 5 MB）または `/graph.jsonld`（上限なし） |
| カスタム UI を作る tool | `/search-index.json` + `/graph.json` |
| 検索エンジン | `/sitemap.xml` + `/graph.jsonld` |
| 購読者 | `/rss.xml` |
| 変更検出器 | `/manifest.json` |

## 関連ドキュメント

- [Architecture](architecture.ja.md) — 3 層モデル、module map、冪等性の説明。
- [Feature map](feature-map.ja.md) — すべての feature と status、source files、このページへの links。
- [Quickstart](quickstart.ja.md) — `project init` から閲覧可能なサイトまでの最短経路。
- [Self-dogfood demo](self-dogfood.ja.md) — このサイトを含め、Tesserae を自身の repo に対して実行する方法。
