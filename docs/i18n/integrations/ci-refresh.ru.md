# Плановое CI-обновление с гейтом output-snapshot

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.ko.md">한국어</a> · <a href="ci-refresh.zh.md">中文</a> · <a href="ci-refresh.ja.md">日本語</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.fr.md">Français</a> · <a href="ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

Компиляция Tesserae записывает машиночитаемый сигнал no-op в
`.tesserae/output-snapshot.json` (см. `tesserae/output_snapshot.py`): после
каждой компиляции `changed` равен `true` только тогда, когда байт-идемпотентный
набор артефактов (графовый слой + `wiki/`/`site/`/`markdown_projection/`)
действительно отличается от предыдущего состояния. Плановый workflow может
гейтировать свой PR-шаг этим флагом — по образцу снапшот-гейта OpenWiki в
`examples/openwiki-update.yml`.

Гейт `changed` — это то, что предотвращает бесконечные циклы плановых PR
(урок OpenWiki): без него каждый запуск cron открывает PR независимо от того,
изменилось ли что-нибудь. Следствие: **появление refresh-PR, когда в
репозитории *ничего* не изменилось, — это живой симптом регрессии
байт-идемпотентности** — компиляция переписала проекцию идентичных входных
данных иначе. Заведите баг вместо того, чтобы мержить такой PR.

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
