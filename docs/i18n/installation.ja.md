# インストール

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae は PyPI で公開されており、ユーザーが `python3 -m tesserae.cli` を手動で実行しなくて済むよう、シェルコマンドを提供しています。

## PyPI からのインストール（推奨）

```bash
pip install tesserae
```

これだけです。`pip` は 2 つのコンソールスクリプトを `PATH` に登録します:

```bash
tesserae --help
tesserae_mcp --help
```

ドキュメントにおける正規のコマンドは `tesserae` です。`tesserae_mcp` は MCP サーバーを起動します（現在はオンデマンドの `compile_context` ツールを公開しています — Quickstart を参照）。

> **pipx でも問題ありません。** CLI ツールをそれぞれ独立した venv に保ちたい場合:
> ```bash
> pipx install tesserae
> ```

## アップグレード

```bash
pip install --upgrade tesserae
```

## マシン全体のセットアップ（1 回設定すれば全プロジェクトに適用）

プロジェクトごとではなく一度だけ Tesserae を設定し、オプションの依存関係を
1 つのコマンドでインストールします:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

既知のオプション依存関係: **memex**（高速トランスクリプト検索）、**cognee**、
**raganything**、**understand-anything**。プロジェクトごとの `.tesserae/config.json`
はこれらのグローバルデフォルトを引き続き上書きします（解決順序: env → project → global →
built-in）。`tesserae init` もインタラクティブなセットアップ中に memex のインストールを提案します。

## オプションの統合（プロジェクトごと）

デフォルトの wheel は意図的に軽量であり、オプションのメモリバックエンドは
**デフォルトで無効**です。`tesserae init` はプロジェクトごとの唯一のオンボーディングステップです —
そのウィザードが LLM プロバイダと検出されたソースを選択します。より重い
コンパニオン/ランタイム部品は `tesserae setup
--install …`（または `tesserae config deps --install …`）でマシン全体にインストールし、
`.tesserae/config.json` でプロジェクトごとに有効化します:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything --install understand-anything --install cognee

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true
#   memory_backends.cognee.enabled: true        (query via `tesserae query --backend …`)
#   external_tools: understand-anything entry   (auto_refresh: false by default)
```

高度なワークフローのために、手動でのパッケージインストールも引き続き利用できます:

```bash
pip install kuzu graphiti-core
pip install "tesserae[cognee]"
```

- `kuzu` — Kuzu グラフの永続化。
- `tesserae[cognee]` — オプトインの Cognee ランタイム add/cognify ワークフロー（デフォルトで無効。Codex パッチ版の cognify モードは削除されました）。
- Understand Anything — 上流のインストーラ経由でインストールされます（`tesserae setup --install understand-anything`）。ユーザーにシェルコマンドを考案させる代わりに、Tesserae は管理されたリフレッシュラッパーを保存します。
- RAG-Anything — `pip install 'raganything[all]'` 経由でインストールされます（`tesserae setup --install raganything`）。Tesserae はマルチモーダルパーサー実行のための管理されたリフレッシュラッパーを保存します。
- `graphiti-core` — ライブの Graphiti/Neo4j 同期。`export graphiti` と `export graphiti --sync --dry-run` はこれがなくても動作します。

Anthropic を利用する synthesis パスは extras マーカーを使います:

```bash
pip install "tesserae[synthesis-llm]"
```

実際のセマンティック埋め込み（v0.5.0 以降のデフォルト検索レーン）は `semantic` extra の背後で提供されます:

```bash
pip install "tesserae[semantic]"
```

これは `model2vec` を取り込み、軽量でオフライン、torch 不要の静的モデル（約 8 MB の `potion-base-8M`、初回使用時に一度だけ取得）をダウンロードします。これがない場合、hybrid/embedding 検索は非セマンティックなハッシュバケットのスタブにフォールバックし、目立つ警告を発するため、`tesserae ask`、`tesserae context`、または MCP の `compile_context` ツールを使うすべての人に、この extra のインストールを推奨します。

すべてのパーサーがプリインストールされたマルチモーダル RAG-Anything スタックには:

```bash
pip install 'tesserae[raganything-all]'
```

> **システム前提条件:** `.doc/.docx/.ppt/.pptx/.xls/.xlsx` のパースにはホスト上の LibreOffice が必要です。プラットフォームのパッケージマネージャでインストールしてください（例: `brew install --cask libreoffice`、`apt-get install libreoffice`）。LibreOffice がない場合、RAG-Anything は警告を出して Office ドキュメントをスキップします。

## ソースからのインストール（コントリビューター向け）

コードベースをいじりたい場合は、代わりに編集可能なチェックアウトをインストールします:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

便利なインストーラも同梱されています — クローンし、プロジェクトローカルの `.venv` を作成し、`pip install -e .` を実行し、ラッパーを `~/.local/bin` に配置します:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

便利なフラグ（`./scripts/install.sh --help`）:

| オプション | 目的 |
| --- | --- |
| `--dir PATH` | `PATH` のチェックアウトをインストールまたは更新する。 |
| `--branch NAME` | 特定のブランチをインストールする。 |
| `--repo URL` | Git リポジトリの URL を上書きする。フォークやローカルのスモークテストに便利。 |
| `--bin-dir PATH` | コマンドラッパーを `~/.local/bin` 以外の場所に書き込む。 |
| `--no-venv` | `.venv` を作成せず、現在の Python 環境にインストールする。 |
| `--skip-shell-config` | `.zshrc` / `.bashrc` の編集を避ける。 |

`--skip-shell-config` を使った場合は、シェルを再起動するか、次を実行してください:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## インストールの確認

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
