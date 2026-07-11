# Geplanter CI-Refresh mit dem output-snapshot-Gate

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.ko.md">한국어</a> · <a href="ci-refresh.zh.md">中文</a> · <a href="ci-refresh.ja.md">日本語</a> · <a href="ci-refresh.ru.md">Русский</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.fr.md">Français</a></p>
<!-- translations:end -->

Tesseraes Compile schreibt ein maschinenlesbares No-op-Signal nach
`.tesserae/output-snapshot.json` (siehe `tesserae/output_snapshot.py`): Nach
jedem Compile ist `changed` nur dann `true`, wenn sich die byte-idempotente
Artefaktmenge (Graph-Schicht + `wiki/`/`site/`/`markdown_projection/`)
tatsächlich vom vorherigen Zustand unterscheidet. Ein geplanter Workflow kann
seinen PR-Schritt an dieses Flag koppeln — analog zum Snapshot-Gate von
OpenWiki in `examples/openwiki-update.yml`.

Das `changed`-Gate ist es, was endlose Schleifen geplanter PRs verhindert
(die OpenWiki-Lektion): Ohne das Gate öffnet jeder Cron-Lauf einen PR, egal
ob sich etwas bewegt hat oder nicht. Korollar: **Ein Refresh-PR, der
erscheint, obwohl sich im Repository *nichts* geändert hat, ist das
Live-Symptom einer Byte-Idempotenz-Regression** — der Compile hat eine
Projektion identischer Eingaben anders neu geschrieben. Legen Sie dafür einen
Bug an, statt den PR zu mergen.

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
