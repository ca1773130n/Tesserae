# Rafraîchissement CI planifié avec la porte output-snapshot

<!-- translations:start -->
<p align="center"><a href="../../integrations/ci-refresh.md">English</a> · <a href="ci-refresh.ko.md">한국어</a> · <a href="ci-refresh.zh.md">中文</a> · <a href="ci-refresh.ja.md">日本語</a> · <a href="ci-refresh.ru.md">Русский</a> · <a href="ci-refresh.es.md">Español</a> · <a href="ci-refresh.de.md">Deutsch</a></p>
<!-- translations:end -->

La compilation de Tesserae écrit un signal de no-op lisible par machine dans
`.tesserae/output-snapshot.json` (voir `tesserae/output_snapshot.py`) : après
chaque compilation, `changed` ne vaut `true` que lorsque l'ensemble
d'artefacts byte-idempotent (couche graphe + `wiki/`/`site/`/
`markdown_projection/`) diffère réellement de l'état précédent. Un workflow
planifié peut conditionner son étape de PR à ce drapeau — à l'image de la
porte de snapshot d'OpenWiki dans `examples/openwiki-update.yml`.

La porte `changed` est ce qui empêche les boucles infinies de PR planifiées
(la leçon d'OpenWiki) : sans elle, chaque exécution cron ouvre une PR, que
quelque chose ait bougé ou non. Corollaire : **une PR de rafraîchissement qui
apparaît alors que *rien* n'a changé dans le dépôt est le symptôme en direct
d'une régression de byte-idempotence** — la compilation a réécrit différemment
une projection d'entrées identiques. Déclarez un bug au lieu de la fusionner.

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
