# 安装

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki 已发布到 PyPI，并提供 shell 命令，因此用户无需手动运行 `python3 -m llm_wiki.cli`。

## 从 PyPI 安装（推荐）

```bash
pip install llm-research-wiki
```

就这样。`pip` 会在你的 `PATH` 中注册三个控制台脚本：

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

文档中的规范命令是 `llm_wiki`；`llm-wiki`（带短横线）是别名。`llm_wiki_mcp` 用于启动 MCP 服务器。

> **也可以使用 pipx。** 如果你希望把 CLI 工具保存在各自隔离的 venv 中：
> ```bash
> pipx install llm-research-wiki
> ```

## 升级

```bash
pip install --upgrade llm-research-wiki
```

## 可选集成

默认 wheel 有意保持轻量。只有在你明确要求时，设置向导才会安装较重的配套/运行时组件：

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

高级工作流仍可手动安装包：

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — Kuzu 图持久化。
- `cognee` — 运行时 Cognee add/cognify 工作流；设置会保存 `{python} -m pip install cognee`，如果缺少 Cognee 会重试一次。
- Understand Anything — 选择 `--install-understand-anything` 时通过上游安装器安装；LLM-Wiki 会保存一个托管的刷新 wrapper，而不是要求用户自己发明 shell 命令。
- `graphiti-core` — 实时 Graphiti/Neo4j 同步。没有它时，`export-graphiti` 和 `sync-graphiti --dry-run` 仍可工作。

Anthropic 支持的合成路径使用 extras 标记：

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## 从源码安装（贡献者）

如果你想修改代码库，请改用可编辑 checkout 安装：

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

仓库还附带一个便捷安装器：它会 clone、创建项目本地 `.venv`、运行 `pip install -e .`，并把 wrapper 放到 `~/.local/bin`：

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

有用的标志（`./scripts/install.sh --help`）：

| 选项 | 用途 |
| --- | --- |
| `--dir PATH` | 在 `PATH` 安装或更新 checkout。 |
| `--branch NAME` | 安装指定分支。 |
| `--repo URL` | 覆盖 Git 仓库 URL。对 fork 或本地 smoke test 很有用。 |
| `--bin-dir PATH` | 将命令 wrapper 写到 `~/.local/bin` 之外的位置。 |
| `--no-venv` | 不创建 `.venv`，安装到当前 Python 环境。 |
| `--skip-shell-config` | 避免编辑 `.zshrc` / `.bashrc`。 |

如果使用了 `--skip-shell-config`，请重启 shell 或运行：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 验证安装

```bash
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
