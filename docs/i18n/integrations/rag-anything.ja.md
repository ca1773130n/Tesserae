# RAG-Anything マルチモーダルコンパニオン

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) は（LightRAG 上に構築された）マルチモーダル RAG フレームワークで、PDF・Office ドキュメント・画像・数式を MinerU/Docling/PaddleOCR 経由で解析します。Tesserae はこれを、マルチモーダル取り込みパイプライン（UA スタイルのネイティブグラフ射影）として、またオプションのランタイムメモリバックエンドとして、両面で統合しています。

## なぜ両方を使うのか？

- Tesserae — 長寿命のエージェントメモリ、wiki コンパイル、グラフ射影。
- RAG-Anything — マルチモーダル取り込み + LightRAG ランタイム検索。

両者は補完関係にあります。RAG-Anything は Tesserae のテキスト優先ソースローダーが提供しない PDF/Office/画像の理解をもたらし、Tesserae はセッションをまたいで生き続ける、クエリ可能な長寿命メモリを維持します。

## 現在の低摩擦ワークフロー

推奨パスはセットアップウィザードです:

```bash
tesserae init
```

RAG-Anything は現在、CLI フラグの集合ではなく**対話的なウィザードプロンプト**になっています。ウィザードが起動したら、統合に関するプロンプトに答えてください:

- プロンプトが出たら RAG-Anything を有効化する;
- 尋ねられたらインストールする（`raganything` + `docling` がインストールされます）;
- パーサーとして `mineru` を選ぶ;
- 提案されたらインストール後のリフレッシュ実行を有効にする。

その後コンパイルします:

```bash
tesserae compile
```

非対話の自動化（CI）では、既定値（オプションの統合はすべて OFF）でウィザードを実行し、その後 `.tesserae/config.json` で RAG-Anything を有効化して — ウィザードは統合設定を `external_tools` / `memory_backends` キーの下に書き込みます（このドキュメントが以下で参照するキーを参照）— 管理されたリフレッシュを実行します:

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

セットアップウィザードは `raganything` と `docling` を一緒にインストールします。MinerU はオプトインのままです: 取り込みたい PDF や画像がある場合にのみ `pip install 'mineru[core]'` でインストールしてください。

Tesserae は、ユーザーにコマンドを自作させる代わりに、管理されたリフレッシュコマンドを保存します:

```bash
tesserae integrations refresh raganything --parser mineru
```

コンパイル中、Tesserae は次を行います:

1. `.tesserae/external/raganything/manifest.json` が存在し、（保存された `meta.json#gitCommitHash` を介して）現在の git コミットと一致するかを確認する;
2. 欠落している/古い場合、または `--refresh-external-tools` が渡された場合に、管理されたリフレッシュラッパーを実行する;
3. 非コードソース（PDF、Office ドキュメント、画像、markdown）を発見し、設定されたパーサーで解析する;
4. `manifest.json` + `meta.json` を書き込む;
5. 通常のメモリコンパイルを続行する。

コンパイル前に、設定済みのすべての外部リフレッシュコマンドを強制実行できます:

```bash
tesserae compile --refresh-integrations
```

## 手動での同等手順

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## コンパイル時 vs ランタイム

Tesserae は統合を明確に分割しています:

- **コンパイル時解析**（`refresh-raganything` と `compile`）: パーサーを直接実行します — `.md/.txt/.rst` はネイティブ読み込み、それ以外はすべて `docling.DocumentConverter`。ここでは RAG-Anything の完全なパイプラインは呼び出され*ません*。したがってコンパイルの成功に LLM/埋め込み/ビジョンのキーは不要です。
- **ランタイムクエリ**（`project ask`）: `raganything_query.py` がプロジェクトに設定された LLM/埋め込み/ビジョン関数で `RAGAnything` をインスタンス化し、LightRAG のストアに対して `aquery` を実行します。このパスには API キーが必要です。

この分割により、`compile` は高速・決定的・キー不要になり、LLM トークンを消費するのは検索時の操作だけになります。

## ネイティブグラフ同期

設定されたツールが `sync_mode: native_graph` を使う場合、Tesserae はコンパイル中に解析済みマニフェストをネイティブにインポートします。

ネイティブアダプタは `.tesserae/external/raganything/manifest.json` を読み込み、解析された各ドキュメントをマルチモーダルブロックメタデータ付き `SourceDocument` ノードへ射影し、さらに（解決可能な内容を持つ各図表/表/数式ごとに）一級市民の `Artifact` 証拠ノード（content-hash id、`part_of` そのドキュメント、`evidenced_by` でターゲット可能）を生成し、同期マニフェストを書き込みます:

```text
.tesserae/external/raganything-sync.json
```

現在のマッピング:

| RAG-Anything | Tesserae 側 |
|---|---|
| `documents[*]` | `SourceDocument` ノード、`metadata.parser="raganything"`、`metadata.content_hash` = ソース sha256 |
| `content_list[type=text]` | `SourceDocument.description` に畳み込み; 概念は既存の抽出器経由 |
| `content_list[type=image]` | `Artifact` ノード（id はアセット **バイト列** sha256 から、キャプションを説明として）+ `SourceDocument.metadata.multimodal_blocks[]`（`img_path`、`caption`、`content_hash` 結合キー）; 解決不可なアセットはノード生成をスキップ（同期マニフェストの `skipped_blocks` に記録） |
| `content_list[type=table]` | `Artifact` ノード（id は `table_body` sha256 から、ボディを説明として）+ `multimodal_blocks[]`（`table_body`、`caption`、`content_hash`） |
| `content_list[type=equation]` | `Artifact` ノード（id は `latex` sha256 から、LaTeX を説明として）+ `multimodal_blocks[]` と `metadata.equations[]`（LaTeX 保持） |

### オーナーごとのファクトは `part_of` エッジに乗る

`Artifact` のid はその kind とコンテンツハッシュのみから生成され、ドキュメント、パス、キャプション、ページは関与しません — なので意図的に**ドキュメント不可知**です: 同じ図が 2 つの論文に印字されるなら、1 つのノード + オーナーごと 1 つの `part_of` エッジ。しかし `kind`、`page`、`caption`、そして 1-ベースのオーナーごとの `ordinal` は*（アーティファクト、ドキュメント）*ペアについてのファクト — ノードだけで保たれると、共有アーティファクトはマージ最初のドキュメントを維持し、後のオーナーの `page` をすべて黙ってロスト。エッジに乗ります。その構築によるオーナーごと。ノードは後方互換のため自身のコピーを保ちます; これは追加で移動ではない。同じバイトが一つのドキュメント内に 2 度現れるなら、より古い位置が確定的に勝ちます。

`evidence` はそのエッジ上にあり目的的にヌル: このコードベースのすべての `edge.evidence` は主張をライセンスした逐語スパンであり、キャプションは何も主張しない。

### バイトに到達する

**図**の `Artifact` はイメージのバイトが存在することを主張 — ノード存在するのはそれらがハッシュされたから — だからサイトは配信。`tesserae export site` は `metadata['asset_path']` をそれ自身の右として読み込み、その図に生ページ、サイトマップ項、そしてそのバイト `raw-assets/` 配下**コンテンツアドレス指定**ファイル名、グラフが既に宣言したダイジェストから導出、決して再-hash。バイトの純粋な関数である名前は何が `asset_site_path` 以下ファクトではなく予測に。

テーブルと式 `asset_path` をもたない — そのコンテンツ*は*ノードの記述 — そして木外アセットがインポートでキーを落とします。どちらも正しくサーバ不能ではなくエラーではない。

MCP 上、`raw_source` はバイトを返さない; `drill_down` がアドレスを報告 — `asset_path`（ディスク上）、`asset_sha256`、そして `asset_site_path`（実行中の `tesserae serve` から取ってくる）。不正に宣言されたハッシュは `asset_site_path` をドロップし、1 つを発明しない。

### アーティファクトはグラフキャンバスから外れたままである

`Artifact` は `EvidenceSpan` そしてすべての Claim 変型と同じアサーション層でバケットされ、そして全アサーション層はインタラクティブグラフビュー — 意図的・永遠に、ペンディングではない — から除外。対象にいての証拠*ではなく*ピアであり、そして 2 つのメカニカル理由は同じを言います: 証拠がそれを支持（洪水が既に `SourceDocument` を `show_sources` 背後に追いやった）かつ `Artifact` の唯一のエッジは `part_of` から `SourceDocument` へ、既定で非表示 — それだけを認めれば到達不可能な孤児のドットを引く。証拠を読む `drill_down` そして生アセットページを通じて、そこは時に取ってこれる。

来歴（provenance）は各ノードに保持されます:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

注: インタラクティブなグラフビューは、概念とエンティティに焦点を当てるため、既定で `sources` グループのノードを非表示にします — 射影された raganything の SourceDocument は `graph.json` に残ります（MCP、検索、ページ単位の wiki ビューからは引き続き見えます）。単にキャンバスを埋め尽くさないだけです。密なビューを復元するには `.tesserae/config.json` で `graph_view.show_sources = true` を設定してください。

## ランタイムメモリバックエンド

`memory_backends.raganything`（`default_raganything_backend_config` が生成する既定値）は唯一のオプションのメモリバックエンドです。RAG-Anything はオプトインです（既定 `enabled: false`）。セットアップフラグ `--with-raganything` で有効になります。

### LLM プロバイダ（API キー不要）

RAG-Anything のランタイムバックエンドは、クエリに答えるための LLM を必要とします。Tesserae は既存の OAuth ベースの CLI 統合を既定とします — API キーは不要です:

| プロバイダ | 認証方法 | セットアップフラグ |
|---|---|---|
| `codex`（既定） | `codex` CLI OAuth（`codex login` で一度ログイン済み） | `--raganything-llm-provider codex` |
| `claude` | `claude -p` CLI; マルチアカウント構成では `CLAUDE_CONFIG_DIR` を尊重 | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

マルチアカウントの Claude 構成（例: `~/.claude-personal1`、`~/.claude-personal2`）では、セットアップ時に `--raganything-claude-config-dir <path>` を渡してください。ランタイムバックエンドは各呼び出しの前に `CLAUDE_CONFIG_DIR=<path>` をエクスポートするため、既定の `~/.claude` に触れることなく選択したアカウントの認証が使われます。

### 埋め込み

| プロバイダ | 使いどころ |
|---|---|
| `deterministic`（既定） | 外部依存なし。ハッシュベース; 意味的品質は低いものの、LightRAG がインデックスを構築するには十分。統合が機能することを証明するための良いベースライン。 |
| `ollama` | 埋め込みモデル（例: `nomic-embed-text`）を載せてローカルで動く Ollama。`--raganything-embedding ollama` を渡します。バックエンドの既定は `http://localhost:11434`。 |

OpenAI 埋め込みの直接サポートは v1 ではこれらのフラグに配線されていません — OpenAI キーを持つユーザーは `OPENAI_API_KEY` を設定し、`.tesserae/config.json` で `memory_backends.raganything.embedding.provider` を直接オーバーライドできます（RAGAnything は LightRAG の既定値経由で環境変数を拾います）。

### CLI からの呼び出し

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything` は `tesserae.raganything_query.query` を直接呼び出します。`memory_backends.raganything` 内の相対 `working_dir` は、呼び出し前にプロジェクトルートに対して解決されます。

### トップレベルの `ask`（マルチプロジェクトレジストリを使用）

各プロジェクトに `cd` することなく、登録された複数の Tesserae プロジェクトに対して質問したいワークフローでは、トップレベルの `tesserae ask` コマンドが、MCP サーバーと共有される永続レジストリ経由でプロジェクトを解決します:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

ディスパッチロジック — `--project > --name > ルーター` — はトップレベルの ask ハンドラに実装されており、回答フォーマットは `tesserae.query.ask_project` を通じて MCP の `ask` ツールと共有されます（メモリバックエンドには `tesserae query --backend …` を通じてのみ到達できます）。レジストリはファイルベース（既定は `~/.tesserae/registry.json`）なので、セッションをまたいで永続し、MCP サーバーのプロジェクトリストと同期し続けます。

#### 複数ヴォルト横断クエリ（`--scope all-registered`）

Bet B2 — 複数の登録済みプロジェクト（研究ヴォルト、仕事ヴォルト、サイドプロジェクトヴォルト）を持っていて、同じ質問をそのすべてに投げたい場合は、`--scope all-registered` を使います:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

ハンドラは登録済みプロジェクトをアルファベット順に反復し、それぞれに対して `ask_project` を呼び出し、プロジェクトごとのエンベロープを集約します。単一プロジェクトの失敗 — 設定の欠落、RAG-Anything が未有効化 — はそのエイリアスのスロットに `{"error": "..."}` として記録され、残りのファンアウトを中断させることはありません。同じ `scope` 引数は MCP の `ask` ツールでも受け付けられるため、MCP 駆動のコーディングエージェントは追加の配管なしで同じファンアウトを得られます。

### マルチプロジェクトレジストリ（`tesserae projects`）

| コマンド | 目的 |
| --- | --- |
| `tesserae projects list [--json]` | 登録済みプロジェクトを表示（すべて対等 — 「アクティブ」なものは存在しない）。 |
| `tesserae projects register <path> [--name <alias>]` | プロジェクトをレジストリに追加; エイリアスの既定はサニタイズされたディレクトリ名。 |
| `tesserae projects unregister <name>` | レジストリからエントリを削除。 |

これらのコマンドは `tesserae.mcp_server.ProjectRegistry` を直接操作します — MCP のラウンドトリップなし — なので、MCP サーバーを起動せずにスクリプト化できます。

### MCP からの呼び出し

stdio MCP サーバーは、同じバックエンドセレクタを備えた `ask` ツールを公開します:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

ディスパッチ順（`raganything` → コンパイル済み wiki 検索）と `working_dir` の解決は CLI ハンドラを正確にミラーするため、コーディングエージェントと人間のオペレーターは同じ回答に収束します。

## システム前提条件

- **Python 3.10+** が RAG-Anything に必要です（上流の `raganything` パッケージ ≥1.3.0 は推移的に `mineru[core]` に依存しており、これは Python 3.10+ です）。それより古い Python では、Tesserae は壊れたプレースホルダーを黙ってインストールするのではなく、明確な警告とともに統合を無効化します。
- **LibreOffice** — `.doc/.docx/.ppt/.pptx/.xls/.xlsx` の解析用。プラットフォームのパッケージマネージャで別途インストールしてください。LibreOffice が無い場合、RAG-Anything は警告を出して Office ドキュメントをスキップします。
- **MinerU のモデル重み**は初回解析時にダウンロードされてキャッシュされます（数 GB）。以降の実行はキャッシュを再利用します。
- **OpenAI 互換の LLM/埋め込み/ビジョンのキー** — ランタイムメモリバックエンド用（`OPENAI_API_KEY`、`OPENAI_BASE_URL`）。パーサーのみのモードにキーは不要です。

## パーサールーティング

Tesserae はファイル拡張子ごとにソースを適切なパーサーへ自動ルーティングします:

| 拡張子 | パーサー | 理由 |
|---|---|---|
| `.md`、`.markdown`、`.txt`、`.rst` | `docling` | 軽量; MinerU のモデルダウンロード不要。 |
| `.doc`、`.docx`、`.ppt`、`.pptx`、`.xls`、`.xlsx` | `docling` | 上流によれば Office 構造の保持がより良い。 |
| `.pdf`、`.png`、`.jpg`、`.jpeg`、`.gif`、`.bmp`、`.tiff`、`.webp` | 設定された既定（`--raganything-parser`、既定 `mineru`） | OCR + テーブル抽出。 |

管理された `tesserae integrations refresh raganything` ラッパーは `--parser`（PDF/画像用の設定済み既定）、`--parse-method {auto,ocr,txt}`、`--root`（繰り返し可、サブツリーに限定）、`--force`、`--full` を公開します。バケットごとのテキスト/Office ルーティングは固定です（どちらも既定は `docling`）。テキストまたは Office のパーサーを明示的にオーバーライドするには、基盤のモジュールを直接呼び出してください — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — これはその 2 つの追加フラグを公開します。設定された既定は引き続き PDF と画像に適用されます。

解析ループの実行前に、Tesserae は必要な各パーサーの Python パッケージがインポート可能か（`importlib.import_module(...)`）を調べ、欠落しているすべてのパーサーとそのインストールコマンドを列挙した単一の集約エラーで素早く中断します。上流の `RAGAnything.check_parser_installation()` は意図的に使いません。それはインスタンスに設定されたパーサーしか検査せず、プリフライト段階に合わないモデル重みの準備状況チェックまで畳み込んでしまうからです。

Tesserae はまた、`RAGAnything` の構築時パーサーを `--raganything-parser` から直接ではなく、実際のルーティング分布（最も多く選ばれたパーサーが勝つ）から選択します。これにより、`RAGAnything.__init__` がモデル重みのまだディスクに無い重いパーサー（例: `mineru`）を初期化しようとして、呼び出しごとの `parser=` オーバーライドが効く前に実行全体を壊してしまう失敗モードを回避します。`--raganything-parser` フラグは引き続き、テキストでも Office でもないソース（PDF、画像）の既定を制御します。

### パーサーパッケージ

コンパイル時の解析パスは、ネイティブテキスト以外のすべてのソースに対して `docling.DocumentConverter` を直接使用します。一度インストールすればカバーされます:

| パーサー | インストールコマンド |
|---|---|
| `docling`（ネイティブテキスト以外すべてのコンパイル時既定） | `--with-raganything --install-raganything` の実行時に同梱（または単体で `pip install docling`） |
| `paddleocr`（オプションの OCR 代替） | `pip install 'raganything[paddleocr]>=1.3.0'` と `pip install paddlepaddle`（プラットフォーム固有の wheel） |

> 注: `mineru` は現在、**コンパイル時には呼び出されません**。コンパイルパスは RAG-Anything の完全なパイプライン（LLM/埋め込み/ビジョンの callable を要求する）をバイパスし、テキスト以外のすべてのソースを docling へ直接ルーティングします。MinerU のサポートは、外部で生成された `content_list.json` を取り込む将来の直接インポートパスのために確保されています。

設定されたパーサーが欠落している場合、`refresh-raganything` は素早く中断します — ファイルごとの連鎖的な失敗ではなく、欠落しているすべてのパーサーを正しいインストールコマンド付きの単一エラーで列挙します。

### ページ単位の ask ウィジェット

すべての詳細ページ（概念、論文、リポジトリ、統合、エンティティ、トピック、質問、ソース）には、インラインの「このページについて質問する」ウィジェットが含まれます。これはローカルの `tesserae serve` インスタンス上の `/api/ask` に POST し、それが `tesserae.query.ask_project` を呼び出して回答をインラインで描画します。CLI（`tesserae ask` は既定で LLM）とは異なり、`/api/ask` はウィジェットのレイテンシのために既定で**非 LLM 検索**です。プラン済み/合成された回答にオプトインするには、ペイロードで `{"llm": true}` を送信してください。ウィジェットは、現在のページのノード名を自然言語のコンテキストヒントとしてユーザーの質問の前に付加します（例: `` About `<NodeName>`: <question> ``）。将来の PR で `ask_project` 自体に本物のサブグラフスコーピングを配線できます。

ウィジェットはロード時に `/api/ask/health` でバックエンドの可用性を検出します。wiki が静的に配信されている場合（GitHub Pages、`file://`、S3、任意のプレーンな静的ホスト）、ウィジェットはローカルでの対話的利用のために `tesserae serve` を案内する 1 行のメモに折りたたまれます。リクエストが失敗することも、ページ描画をブロックすることもありません — ウィジェットは遅延 JS アイランドで、より重いグラフバンドルとは分離されています。

これをマルチプロジェクトレジストリ（`tesserae projects register`）と組み合わせれば、登録済みの任意のプロジェクトの wiki に、その詳細ページのどこからでも質問できます。

## 協業の原則

Tesserae はメモリコンパイラであり続けます。RAG-Anything は独立したコンパニオン — マルチモーダルパーサー + LightRAG 検索エンジン — であり続けます。
