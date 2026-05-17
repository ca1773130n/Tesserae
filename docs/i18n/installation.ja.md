# インストール

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki は PyPI で公開されており、ユーザーが `python3 -m llm_wiki.cli` を手動で実行しなくて済むように shell コマンドを提供します。

## PyPI からインストール（推奨）

```bash
pip install llm-research-wiki
```

これだけです。`pip` は `PATH` に 3 つのコンソールスクリプトを登録します。

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

ドキュメントでの正式なコマンドは `llm_wiki` です。`llm-wiki`（ダッシュ付き）はエイリアスです。`llm_wiki_mcp` は MCP サーバーを起動します。

> **pipx でも問題ありません。** CLI ツールをそれぞれ独立した venv に置きたい場合:
> ```bash
> pipx install llm-research-wiki
> ```

## アップグレード

```bash
pip install --upgrade llm-research-wiki
```

## 任意の統合

デフォルトの wheel は意図的に軽量です。セットアップウィザードは、要求された場合にのみ重い companion/runtime 部品をインストールできます。

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

高度なワークフローでは、手動でのパッケージインストールも引き続き利用できます。

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — Kuzu グラフ永続化。
- `cognee` — ランタイム Cognee add/cognify ワークフロー。セットアップは `{python} -m pip install cognee` を保存し、Cognee が見つからない場合は一度だけ再試行します。
- Understand Anything — `--install-understand-anything` が選択された場合、upstream インストーラーでインストールされます。LLM-Wiki はユーザーに shell コマンドを作らせる代わりに、管理された refresh wrapper を保存します。
- `graphiti-core` — ライブ Graphiti/Neo4j 同期。`export-graphiti` と `sync-graphiti --dry-run` はこれなしでも動作します。

Anthropic ベースの合成パスは extras マーカーを使います。

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## ソースからインストール（コントリビューター向け）

コードベースを編集したい場合は、editable checkout としてインストールしてください。

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

便利なインストーラーも同梱されています。clone し、プロジェクトローカルの `.venv` を作成し、`pip install -e .` を実行して、wrapper を `~/.local/bin` に配置します。

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

便利なフラグ（`./scripts/install.sh --help`）:

| オプション | 目的 |
| --- | --- |
| `--dir PATH` | `PATH` の checkout をインストールまたは更新します。 |
| `--branch NAME` | 特定のブランチをインストールします。 |
| `--repo URL` | Git リポジトリ URL を上書きします。fork やローカル smoke test に便利です。 |
| `--bin-dir PATH` | コマンド wrapper を `~/.local/bin` 以外の場所に書き込みます。 |
| `--no-venv` | `.venv` を作成せず、現在の Python 環境にインストールします。 |
| `--skip-shell-config` | `.zshrc` / `.bashrc` の編集を避けます。 |

`--skip-shell-config` を使用した場合は、shell を再起動するか次を実行してください。

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## インストールの確認

```bash
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
