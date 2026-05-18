# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae グラフビュー" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

[ライブデモ](https://ca1773130n.github.io/Tesserae) · [ドキュメント](docs/) · [MCP セットアップ](docs/i18n/integrations/mcp.ja.md) · [Obsidian エクスポート](docs/i18n/integrations/obsidian.ja.md)

Tesserae はプロジェクトメモリのコンパイラです。Markdown、ソースファイル、必要に応じて PDF/Office 文書/画像が入ったディレクトリを与えると、型付きの知識グラフを抽出し、クエリ可能な wiki を書き出し、ポータブルな成果物を生成します: Markdown プロジェクション、Cognee 向けの bundle、エージェント harness、そして Claude Code、Codex、任意の MCP クライアントに接続できる MCP サーバ。ホスティングサービスではなく、プロジェクトコンテキストのためのビルドステップです。

## 使うべきとき（と使うべきでないとき）

次の場合に向いています:

- 単一プロジェクトのテキスト中心のソース（ドキュメント、コード、調査メモ）に対して、永続的で検査可能な知識グラフが欲しい。
- 自分のファイルに基づいて回答するローカルな MCP サーバが欲しい。
- 自分でグルーコードを書かずに、Cognee にきれいな bundle を流し込んだり、Obsidian に Markdown プロジェクションを置きたい。

次の場合は使わない方がよいでしょう:

- 小さなディレクトリ上でベクトル検索ができれば十分 —— `ripgrep` と embedding ライブラリの方がシンプルです。
- 編集 UI つきのホスティング wiki が欲しい。ここで提供する静的サイトは読み取り専用です。
- 箱から出してすぐ使える高精度のセマンティック embedding が欲しい。デフォルトの RAG-Anything embedding は決定的です（[ステータス](#ステータス) 参照）。
- ターンキーの「何でも質問」エージェントを期待している。これはその土台を作るだけで、最終的にどのエージェントに接続するかはあなた次第です。

## ステータス

進化中の研究/エージェントツールプロジェクトです。既知の制限:

- コンパイル時間はコーパスのサイズにほぼ線形に比例します。大きな Markdown ツリー（数千ファイル）の初回コンパイルは数分かかることがあります。
- RAG-Anything のデフォルト embedding プロバイダは `deterministic` です。再現可能で依存関係はありませんが、セマンティック検索の精度には限界があります。検索品質を高めるには `ollama`（例: `qwen3-embedding:0.6b`）か OpenAI 互換エンドポイントに切り替えてください — [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md) を参照。
- RAG-Anything のビジョンサポート（画像内容の抽出）はまだエンドツーエンドで結線されていません。画像ファイルは構造的にはパースされますが、説明文は生成されません。
- Cognee の runtime cognify は best-effort です。プロバイダ未設定、有料 API キーの未設定、ネットワーク障害などは、ビルドを止めずにログに残してスキップします。
- MCP サーバが公開するツール集合は安定していますが、内部のグラフ schema は今後も追加される可能性があります。

## クイックスタート

Python 3.9 以上が必要です。RAG-Anything を有効化する場合は Python 3.10 以上が必要です。

```bash
pip install tesserae

cd /path/to/my-project
tesserae project setup
tesserae project compile
tesserae project ask "Where is Mermaid rendering implemented?"
tesserae project build-site && tesserae project serve --port 8765
```

セットアップウィザードは一般的なソース（`README.md`、`docs/`、`src/`、`data/`）を検出し、`.tesserae/config.json` を書き出します。LLM 呼び出し系の機能は OAuth ベースの `codex` CLI をデフォルトで使うため、通常経路では API キーは不要です。詳細は [docs/quickstart.md](docs/quickstart.md) と [docs/installation.md](docs/installation.md) を参照してください。

> [!tip]
> **インストール後に `tesserae: command not found` が出る場合?** `pip` がバイナリをシェルが探さない場所に配置しています。**どのプラットフォーム** でも最も確実な解決策は [`pipx`](https://pipx.pypa.io/) です — CLI ツールを独立した venv にインストールし、`PATH` を自動管理します:
>
> ```bash
> # macOS — `brew install pipx`
> # Ubuntu / Debian — `sudo apt install pipx`
> # その他 — `python3 -m pip install --user pipx`
> pipx ensurepath          # ~/.local/bin を PATH に追加します。その後新しいシェルを開いてください
> pipx install tesserae
> ```
>
> **Ubuntu 23.04+** で素の `pip install tesserae` を使うとよく遭遇する問題:
>
> | エラー | 原因 | 解決策 |
> |---|---|---|
> | `error: externally-managed-environment` | PEP 668 — システム Python がロックされている | `pipx` を使う(上記)、もしくは `pip install --user --break-system-packages tesserae`(汚い)、または venv |
> | `pip install --user …` 後の `tesserae: command not found` | `~/.local/bin` が `PATH` に含まれていない | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
> | Ubuntu 20.04 での `ModuleNotFoundError: pydantic` | システムの `python3` が 3.8、tesserae は 3.9 以上が必要 | `sudo apt install python3.11 python3.11-venv` 後に `python3.11 -m pip install --user tesserae` |


## コンパイル後に得られるもの

```text
.tesserae/
  config.json
  graph.json              # 型付きノード/エッジ
  manifest.json           # ソースのフィンガープリント（--changed-only が使用）
  sqlite.db               # クエリ可能なグラフストア
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  markdown_projection/    # 人が読める wiki ページ
  obsidian_vault/         # Obsidian にそのまま入れられる vault
  agent_harness/          # 各エージェント向け設定（Claude/Codex/Gemini/Cursor/...）
  harness_sessions/       # 取り込んだ Claude/Codex セッションメモリ
  cognee_bundle/          # Cognee 取り込み用の JSONL
  site/                   # build-site で作る静的サイト
  external/               # 補助ツールの成果物（UA、RAG-Anything）
```

`project compile` のあと、`ls .tesserae/` で実際に生成されたものを確認できます。

## CLI 概要

日常的に使うコマンドです。フラグの全容は `tesserae <subcommand> --help` で確認してください。

| コマンド | 役割 |
|---|---|
| `tesserae project setup` | 対話型ウィザード。`.tesserae/config.json` を書き出します。`--with-understand-anything`、`--with-raganything`、`--run-cognee` などを受け付けます。 |
| `tesserae project compile` | 設定されたソースを読み、補助ツールのリフレッシュを実行し、`.tesserae/` 配下にすべての成果物を書き出します。差分ビルドには `--changed-only` を使ってください。 |
| `tesserae project build-site` | 静的フロントエンドを `.tesserae/site/` にビルドします。 |
| `tesserae project serve --port 8765` | ローカルで静的サイトを提供します。 |
| `tesserae project refresh-understand-anything` | Tesserae 管理の Understand Anything リフレッシュラッパーを実行します。 |
| `tesserae project refresh-raganything --parser mineru` | RAG-Anything で非コードのソース（PDF、Office、画像）を再パースします。 |
| `tesserae project ask "<question>"` | 設定済みバックエンド（`auto`/`raganything`/`cognee`/`wiki`）に質問します。 |
| `tesserae project mcp-config` | Claude Code、Codex、Hermes に貼り付けられる MCP サーバ設定スニペットを出力します。 |
| `tesserae wiki register <path> --name <alias>` | 共有 registry にプロジェクトを登録します。 |
| `tesserae wiki list` / `tesserae wiki activate <name>` | 登録済みプロジェクトを一覧表示し、アクティブを切り替えます。 |
| `tesserae ask "<question>" [--wiki <name>]` | registry を介して解決するトップレベルの ask コマンドです。 |

## インテグレーション

すべてのインテグレーションはオプトインです。素の Markdown/コードプロジェクトで Tesserae を使うのに必須ではありません。

- **Understand Anything** — 別プロジェクト（[Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything)）で、`.understand-anything/knowledge-graph.json` にコード知識グラフを書き出します。`--with-understand-anything` で有効化。Tesserae が管理リフレッシュラッパーを保存するため、`project compile` がグラフを最新に保ちます。[docs/integrations/understand-anything.md](docs/integrations/understand-anything.md) を参照。
- **RAG-Anything** — マルチモーダル取り込み（[HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything)）で、MinerU/Docling/PaddleOCR を介して PDF、Office 文書、画像を処理します。`--with-raganything` で有効化。ランタイムの質問バックエンド（LightRAG）としても動作します。Python 3.10 以上が必要。[docs/integrations/rag-anything.md](docs/integrations/rag-anything.md) を参照。
- **Cognee** — グラフ+ベクトルのメモリバックエンド。`--run-cognee --install-cognee` で有効化。通常の compile は常に `.tesserae/cognee_bundle/` を書き出し、ランタイムの `cognify` パスは best-effort で、明示的に有効化したときのみ実行されます。

## マルチプロジェクト registry

`~/.tesserae/registry.json` の永続 registry により、トップレベルの `ask` CLI と MCP サーバは呼び出しごとに `--project` を指定しなくてもプロジェクト名をルートに解決できます。

```bash
tesserae wiki register /path/to/my-project --name myproj
tesserae wiki activate myproj
tesserae ask "Where is the parser entry point?"
```

MCP サーバも同じ registry を参照するため、MCP クライアントから登録済みの任意の wiki に対して `list_projects`、`activate_project`、`ask` を呼び出せます。

## MCP

`tesserae project mcp-config` は Claude Code、Codex、その他 MCP 対応クライアントに貼り付けられるサーバエントリを出力します。サーバが公開するツールは `schema`、`graph_summary`、`search_nodes`、`node_context`、`search_facts`、`timeline`、`wiki_page`、`raw_source`、`lint_report`、`ask`、および registry ツール `list_projects` / `register_project` / `activate_project` / `unregister_project` です。特定のプロジェクトが必要なツールは、CLI と同じ registry を介して解決します。

## 認証と LLM プロバイダ

通常経路では API キーは不要です:

- **Codex CLI**（デフォルト）を OAuth で利用します。`--raganything-llm-provider codex` がデフォルトで、Cognee の `codex_cognify` モードは Cognee の LLM クライアントを Codex CLI へパッチします。
- **Claude Code CLI** を OAuth で利用します。RAG-Anything のランタイム問い合わせには `--raganything-llm-provider claude` を指定してください。マルチアカウント構成では `--raganything-claude-config-dir ~/.claude-personal2` を使います（Tesserae が呼び出し前に `CLAUDE_CONFIG_DIR` をエクスポートします）。
- **Embeddings** は既定でインプロセスの決定的プロバイダを使います。Ollama への切り替えは `--cognee-embedding-provider ollama --cognee-ollama-embedding-model qwen3-embedding:0.6b`、あるいは OpenAI 互換エンドポイントへの接続も可能で、どちらもインテグレーションドキュメントに記載があります。

`ANTHROPIC_API_KEY` や `OPENAI_API_KEY` を設定すれば対応経路で使われますが、必須ではありません。

## プロジェクト構成

```text
tesserae/        # パッケージ本体（CLI、コンパイラ、MCP サーバ、各 adapter）
docs/            # 英語ドキュメントと、6 言語向けの docs/i18n/
ontology/        # コンパイラが検証する node/edge schema
prompts/         # 抽出・統合プロンプト
scripts/         # メンテナンス用スクリプト
tests/           # pytest スイート
evals/           # グラフ品質の評価 harness
data/            # セルフドッグフード用の調査メモのサンプル
```

## ローカライズドキュメント

[English](./README.md) ·
[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md)

長文ドキュメントは `docs/i18n/` と `docs/i18n/integrations/` にそれぞれミラーされています。

## ライセンス

MIT。[LICENSE](LICENSE) を参照してください。
