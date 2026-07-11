# 使用 output-snapshot 门控的定时 CI 刷新

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.ko.md">한국어</a> · <a href="ci-refresh.ja.md">日本語</a> · <a href="ci-refresh.ru.md">Русский</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.fr.md">Français</a> · <a href="ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae 的编译会把一个机器可读的 no-op 信号写入
`.tesserae/output-snapshot.json`（见 `tesserae/output_snapshot.py`）：每次编译
之后，只有当字节幂等的工件集合（图层 + `wiki/`/`site/`/`markdown_projection/`）
与上一个状态确实存在差异时，`changed` 才为 `true`。定时工作流可以用这个标志来
门控其 PR 步骤 —— 与 OpenWiki 的 `examples/openwiki-update.yml` 快照门控如出一辙。

`changed` 门控正是防止定时 PR 无限循环的机制（OpenWiki 的教训）：没有它，无论
是否有任何变动，每次 cron 运行都会开一个 PR。推论：**当仓库中*没有任何*变化时
却出现了刷新 PR，这就是字节幂等性回归的实时症状** —— 编译对相同的输入以不同的
方式重写了投影。请把它作为 bug 提交，而不是合并它。

```yaml
name: Tesserae refresh

on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch: {}

permissions:
  contents: write
  pull-requests: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: true

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Tesserae
        run: pip install tesserae

      - name: Compile the knowledge base
        run: tesserae compile --project .

      - name: Read the output-snapshot gate
        id: gate
        run: |
          if [ "$(jq -r .changed .tesserae/output-snapshot.json)" != "true" ]; then
            echo "no-op compile — skipping PR"
            echo "changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Open refresh PR
        if: steps.gate.outputs.changed == 'true'
        uses: peter-evans/create-pull-request@v7
        with:
          add-paths: .tesserae/wiki
          branch: tesserae/refresh
          commit-message: "docs: refresh tesserae knowledge base"
          title: "docs: refresh tesserae knowledge base"
```
