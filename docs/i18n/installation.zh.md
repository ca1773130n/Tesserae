# 安装

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae 已发布到 PyPI，并暴露 shell 命令，用户无需手动运行 `python3 -m tesserae.cli`。

## 从 PyPI 安装（推荐）

```bash
pip install tesserae
```

就这样。`pip` 会在你的 `PATH` 上注册两个控制台脚本：

```bash
tesserae --help
tesserae_mcp --help
```

文档中的规范命令是 `tesserae`。`tesserae_mcp` 启动 MCP 服务器（它现在暴露按需的 `compile_context` 工具——见快速入门）。

> **pipx 也可以。** 如果你更喜欢把 CLI 工具放在各自隔离的 venv 中：
> ```bash
> pipx install tesserae
> ```

> **uv 也可以，但 extra 必须写在引号里。** `uv tool install` 没有 `--extra` 参数
>（基于 uv 0.10 验证），所以 `uv tool install tesserae --extra semantic` 会以
> `unexpected argument '--extra'` 退出，extra 根本没装上：
> ```bash
> uv tool install "tesserae[semantic]"        # extra 写在引号里
> uv tool install --with model2vec tesserae   # 等价写法
> ```
> 写进脚本时请检查退出码。`semantic` 被静默跳过，正是 hybrid retrieval 和 `associate`
> 退回到非语义 stub 的原因。

## 升级

```bash
pip install --upgrade tesserae
```

## 机器级设置（设置一次，所有项目生效）

一次性配置 Tesserae 而不是每个项目单独配置，并用一条命令安装可选依赖：

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

已知的可选依赖：**memex**（快速转录搜索）和 **raganything**。每个项目的 `.tesserae/config.json` 仍会覆盖这些全局默认值（解析顺序：env → 项目 → 全局 → 内置）。在交互式设置期间，`tesserae init` 也会提议安装 memex。

## 可选集成（按项目）

默认的 wheel 刻意保持轻量，可选内存后端**默认关闭**。`tesserae init` 是唯一的按项目入门步骤——它的向导选择 LLM 提供方和检测到的来源；较重的伴生/运行时组件通过 `tesserae setup --install …`（或 `tesserae config deps --install …`）在机器级安装，并在每个项目的 `.tesserae/config.json` 中启用：

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true   (query via `tesserae query --backend raganything`)
```

高级工作流仍可手动安装软件包：

```bash
pip install kuzu graphiti-core
```

- `kuzu` —— Kuzu 图持久化。
- RAG-Anything —— 通过 `pip install 'raganything[all]'` 安装（`tesserae setup --install raganything`）；Tesserae 为多模态解析器运行存储一个受管的刷新包装器。
- `graphiti-core` —— 实时 Graphiti/Neo4j 同步。`export graphiti` 和 `export graphiti --sync --dry-run` 在没有它的情况下也能工作。

由 Anthropic 支持的合成路径使用 extras 标记：

```bash
pip install "tesserae[synthesis-llm]"
```

真正的语义嵌入（自 v0.5.0 起为默认检索通道）位于 `semantic` extra 之后：

```bash
pip install "tesserae[semantic]"
```

它会引入 `model2vec`，并下载一个轻量、离线、无 torch 的静态模型（约 8 MB 的 `potion-base-8M`，首次使用时下载一次）。没有它，混合/嵌入检索会回退到非语义的哈希桶存根并发出显眼的警告，因此推荐所有使用 `tesserae ask`、`tesserae context` 或 MCP `compile_context` 工具的用户安装此 extra。

如需预装所有解析器的多模态 RAG-Anything 全家桶：

```bash
pip install 'tesserae[raganything-all]'
```

> **系统先决条件：**解析 `.doc/.docx/.ppt/.pptx/.xls/.xlsx` 需要主机上安装 LibreOffice。请通过你平台的包管理器安装（例如 `brew install --cask libreoffice`、`apt-get install libreoffice`）；当 LibreOffice 缺失时，RAG-Anything 会跳过 Office 文档并发出警告。

## 从源码安装（面向贡献者）

如果你想改动代码库，请改为安装可编辑的检出：

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

还捆绑了一个便捷安装器——它会克隆仓库、创建项目本地的 `.venv`、运行 `pip install -e .`，并把包装器放进 `~/.local/bin`：

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

有用的标志（`./scripts/install.sh --help`）：

| 选项 | 用途 |
| --- | --- |
| `--dir PATH` | 在 `PATH` 处安装或更新检出。 |
| `--branch NAME` | 安装特定分支。 |
| `--repo URL` | 覆盖 Git 仓库 URL。适用于 fork 或本地冒烟测试。 |
| `--bin-dir PATH` | 把命令包装器写到 `~/.local/bin` 以外的位置。 |
| `--no-venv` | 安装到当前 Python 环境，而不是创建 `.venv`。 |
| `--skip-shell-config` | 避免编辑 `.zshrc` / `.bashrc`。 |

如果使用了 `--skip-shell-config`，要么重启 shell，要么运行：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 验证安装

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
