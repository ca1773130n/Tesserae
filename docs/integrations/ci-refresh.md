# Scheduled CI refresh with the output-snapshot gate

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/ci-refresh.ko.md">한국어</a> · <a href="../i18n/integrations/ci-refresh.zh.md">中文</a> · <a href="../i18n/integrations/ci-refresh.ja.md">日本語</a> · <a href="../i18n/integrations/ci-refresh.ru.md">Русский</a> · <a href="../i18n/integrations/ci-refresh.es.md">Español</a> · <a href="../i18n/integrations/ci-refresh.fr.md">Français</a> · <a href="../i18n/integrations/ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae's compile writes a machine-readable no-op signal to
`.tesserae/output-snapshot.json` (see `tesserae/output_snapshot.py`): after
every compile, `changed` is `true` only when the byte-idempotent artifact set
(graph layer + `wiki/`/`site/`/`markdown_projection/`) actually differs from
the previous state. A scheduled workflow can gate its PR step on that flag —
mirroring OpenWiki's `examples/openwiki-update.yml` snapshot gate.

The `changed` gate is what prevents endless scheduled-PR loops (the OpenWiki
lesson): without it, every cron run opens a PR whether or not anything moved.
Corollary: **a refresh PR appearing when *nothing* changed in the repo is the
live symptom of a byte-idempotence regression** — the compile rewrote a
projection of identical inputs differently. File it as a bug instead of
merging it.

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
