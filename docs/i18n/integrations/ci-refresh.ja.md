# output-snapshot ゲートを使ったスケジュール CI リフレッシュ

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.ko.md">한국어</a> · <a href="ci-refresh.zh.md">中文</a> · <a href="ci-refresh.ru.md">Русский</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.fr.md">Français</a> · <a href="ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae のコンパイルは、機械可読な no-op シグナルを
`.tesserae/output-snapshot.json` に書き込みます（`tesserae/output_snapshot.py`
参照）。毎回のコンパイル後、バイト冪等なアーティファクト集合（グラフ層 +
`wiki/`/`site/`/`markdown_projection/`）が直前の状態から実際に変化したときに
のみ `changed` が `true` になります。スケジュール実行のワークフローはこの
フラグで PR ステップをゲートできます — OpenWiki の
`examples/openwiki-update.yml` スナップショットゲートをそのまま踏襲した形です。

`changed` ゲートこそが、終わりのないスケジュール PR ループを防ぐ仕組みです
（OpenWiki の教訓）。これがなければ、何も変わっていなくても cron 実行のたびに
PR が開かれます。系: **リポジトリで*何も*変わっていないのにリフレッシュ PR が
現れたら、それはバイト冪等性リグレッションの生きた症状です** — コンパイルが
同一入力のプロジェクションを異なる形で書き直したということです。マージせずに
バグとして起票してください。

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
