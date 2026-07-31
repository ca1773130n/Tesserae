# 发布检查清单

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
在公开展示 Tesserae 之前使用此检查清单。

## 仓库卫生

- [ ] README 说明项目是什么以及它解决什么问题。
- [ ] 安装命令可在全新的 shell 中运行。
- [ ] Quickstart 使用 `tesserae`，而不是 `python3 -m`。
- [ ] 架构文档解释原始证据 → 图谱 → 投影。
- [ ] 功能图列出已实现的功能，不夸大未来工作。
- [ ] 会话历史文档解释显式导入、隐私审查、生成的 routes 和 transcript typography。
- [ ] Self-dogfood 演示已经运行并记录。
- [ ] 生成的产物可复现，并且要么被忽略，要么有意发布。

## 验证

```bash
.venv/bin/pytest tests/ -x          # 任何失败都要中止 — 绝不发布红色构建
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # 按需上下文编译器
```

### 演示构建冒烟测试（手动 — CI 不覆盖）

每次发布前手动运行。它过去与每次推送到 `main` 时运行的 `build-demo` CI 作业一致；
该工作流已被移除，因此这条编译路径现在只在这里被检查。`tests.yml` 只运行单元测试
套件，并不端到端地执行 `init` → `compile` → `export site`。

它使用确定性提取器（无 LLM 调用，无需 API key）将 Tesserae 针对其自身源码树编译，
并构建站点：

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## 发布流程

由 `release` skill（`.claude/skills/release/SKILL.md`）驱动，它是这套流程的权威
版本 —— 两者若有出入，以 skill 为准，该改的是这份清单。

- [ ] 在 `main` 上，工作树干净，执行 `git pull --ff-only origin main`。
- [ ] 测试通过（`uv run pytest tests/ -x`，约 9 分钟）。演示构建冒烟测试是手动的，
      CI 已不再覆盖 —— 见上文。
- [ ] 提升**全部三个**版本文件：`pyproject.toml`、`.claude-plugin/plugin.json`、
      `npm/package.json`。三者之间以及与标签都必须一致；npm 包装器固定
      `tesserae==<npm 版本>`。
- [ ] 撰写发布说明及全部 7 种翻译；`uv run pytest tests/test_docs_i18n.py -q`
      必须为绿。
- [ ] 执行 `uv lock` 并暂存 `uv.lock` —— 它把 `tesserae` 固定到自身版本，而 CI 运行
      `uv sync --locked`，锁文件过期就会失败。
- [ ] 提交 `release: vX.Y.Z`，附上取自 `git log v<prev>..HEAD` 的一段变更日志。
- [ ] **开 PR —— `main` 受保护，直接推送会被拒绝**（`GH006`；`enforce_admins`
      已开启，需要三项检查）。三条腿全绿后再合并。绝不要给红色构建打标签。
- [ ] 给合并后的提交打标签（`git tag -a vX.Y.Z -m "vX.Y.Z"`）并推送标签。这是不可
      回头的一步：标签推送会触发 npm 的 OIDC 工作流，而已发布的 npm 版本永远无法
      重复使用。
- [ ] 发布 GitHub release。
- [ ] **PyPI 发布 —— 必须，而非可选。** 从标签的干净 worktree 构建并上传，然后用
      `--no-cache-dir` 验证全新 venv 安装（pip 会缓存索引，把已经上线的版本报成
      找不到）。
- [ ] **npm 发布 —— 必须。** 推送标签时通过 OIDC 自动执行；观察该 run，并用
      `npx -y @jokerized/tesserae@X.Y.Z status` 做冒烟测试。绝不要手动发布 ——
      没有 token，而且手动发布会跳过 provenance 证明。

### GitHub Pages

**已经没有任何工作流部署该站点。** `build-demo` 工作流曾在每次推送到 `main` 时部署，
但它已被移除。它最后一次部署的站点仍在提供服务，README 也仍将其作为在线演示链接 ——
因此该页面现在是冻结在最后一次 `build-demo` 运行时的快照，而不是 `main` 的当前视图。

重新发布需要手动执行 `tesserae export site` 并上传，或者新建工作流。无论哪种方式，
都要有意识地决定：一个悄悄与代码脱节的演示链接，比没有演示链接更糟。

## Self-dogfood

集成选项（RAG-Anything）现在是**交互式向导提示**，
而不是 CLI 标志。运行向导并回答它们：

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# 当向导提示时：
#   - 启用 RAG-Anything，安装：是，解析器：mineru，之后运行：是
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

如需完全非交互式运行，请使用 `tesserae init --yes`（所有集成关闭），然后在
`.tesserae/config.json` 中启用每个集成——向导会将它们写入 `memory_backends`
和 `external_tools`（RAG-Anything）键下——并在编译
前对每个集成运行 `tesserae integrations refresh <name>`。确切的配置键请参阅集成
文档。

## 演示要点

- Tesserae 不是通用的名词短语图谱。它使用受控 ontology。
- 研究和开发代码共享基础设施，但保持不同的 schema。
- Markdown 和 HTML 是投影，而不是权威事实存储。
- 默认路径是本地的，并且不需要 API key，易于使用。
- 智能体 harness 和 MCP 让编码智能体可以使用该图谱。
- 导入的 harness 会话页面把之前的 Claude Code/Codex 工作转化为可搜索的项目记忆，同时保持 transcript 发现为显式操作。
