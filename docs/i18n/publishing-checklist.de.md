# Publishing-Checkliste

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a></p>
<!-- translations:end -->
Nutze diese Checkliste, bevor du Tesserae öffentlich präsentierst.

## Repository-Hygiene

- [ ] Die README erklärt, was das Projekt ist und welches Problem es löst.
- [ ] Der Install-Befehl funktioniert aus einer frischen Shell.
- [ ] Der Quickstart nutzt `tesserae`, nicht `python3 -m`.
- [ ] Die Architektur-Docs erklären Rohbelege → Graph → Projektionen.
- [ ] Die Feature-Map listet implementierte Features, ohne zukünftige Arbeit zu überverkaufen.
- [ ] Die Session-History-Docs erklären expliziten Import, Privacy-Review, generierte Routen und Transcript-Typografie.
- [ ] Die Self-Dogfood-Demo wurde ausgeführt und dokumentiert.
- [ ] Generierte Artefakte sind reproduzierbar und entweder ignoriert oder absichtlich veröffentlicht.
- [ ] RAG-Anything-Index aktualisiert (falls aktiviert).

## Verifikation

```bash
.venv/bin/pytest tests/ -x          # Bei jedem Fehler ABBRECHEN — niemals einen roten Build ausliefern
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # On-Demand-Kontext-Compiler
```

### Demo-Build-Smoke (manuell — nichts in der CI deckt das ab)

Vor jedem Release von Hand ausführen. Früher spiegelte das einen `build-demo`-CI-Job,
der bei jedem Push auf `main` lief; dieser Workflow wurde entfernt, daher wird dieser
Kompilierpfad nur noch hier geprüft. `tests.yml` führt die Unit-Suite aus und deckt
`init` → `compile` → `export site` nicht durchgängig ab.

Es kompiliert Tesserae gegen seinen eigenen Quellbaum mit dem deterministischen
Extraktor (keine LLM-Aufrufe, keine API-Keys) und baut die Site:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Release-Flow

Gesteuert vom `release`-Skill (`.claude/skills/release/SKILL.md`), das die
maßgebliche Fassung dieses Ablaufs ist — weichen beide voneinander ab, gilt das
Skill, und diese Liste ist das, was korrigiert gehört.

- [ ] Auf `main`, Arbeitsbaum sauber, `git pull --ff-only origin main`.
- [ ] Tests grün (`uv run pytest tests/ -x`, ~9 Min.). Der Demo-Build-Smoke ist
      manuell und nicht mehr von der CI abgedeckt — siehe oben.
- [ ] **Alle drei** Versionsdateien anheben: `pyproject.toml`,
      `.claude-plugin/plugin.json`, `npm/package.json`. Sie müssen untereinander
      und mit dem Tag übereinstimmen; der npm-Wrapper pinnt
      `tesserae==<npm-Version>`.
- [ ] Release Note plus alle 7 Übersetzungen schreiben; `uv run pytest
      tests/test_docs_i18n.py -q` muss grün sein.
- [ ] `uv lock` ausführen und `uv.lock` stagen — es pinnt `tesserae` auf die
      eigene Version, und die CI läuft mit `uv sync --locked`, das bei veralteter
      Lockdatei fehlschlägt.
- [ ] `release: vX.Y.Z` committen, mit einem Changelog-Absatz aus
      `git log v<prev>..HEAD`.
- [ ] **PR öffnen — `main` ist geschützt und weist direkte Pushes ab** (`GH006`;
      `enforce_admins` ist an, drei Checks erforderlich). Erst mergen, wenn alle
      drei Läufe grün sind. Niemals einen roten Build taggen.
- [ ] Den gemergten Commit taggen (`git tag -a vX.Y.Z -m "vX.Y.Z"`) und den Tag
      pushen. Das ist der Punkt ohne Wiederkehr: der Tag-Push startet den
      npm-OIDC-Workflow, und eine veröffentlichte npm-Version ist für immer
      verbraucht.
- [ ] GitHub-Release veröffentlichen.
- [ ] **PyPI-Publish — PFLICHT, nicht optional.** Aus einem sauberen Worktree des
      Tags bauen, hochladen, dann eine Frisch-Venv-Installation mit
      `--no-cache-dir` verifizieren (pip cached den Index und meldet eine bereits
      live geschaltete Version als fehlend).
- [ ] **npm-Publish — PFLICHT.** Läuft beim Tag-Push automatisch per OIDC; den
      Run beobachten und mit `npx -y @jokerized/tesserae@X.Y.Z status` smoken.
      Niemals von Hand publishen — es gibt kein Token, und ein manueller Publish
      überspringt die Provenance-Attestierung.

### GitHub Pages

**Kein Workflow deployt die Site mehr.** Der `build-demo`-Workflow tat das bei jedem
Push auf `main`; er wurde entfernt. Die zuletzt von ihm deployte Site wird weiterhin
ausgeliefert, und die README verlinkt sie weiterhin als Live-Demo — diese Seite ist
also jetzt ein auf dem letzten `build-demo`-Lauf eingefrorener Schnappschuss und kein
aktueller Blick auf `main`.

Erneut veröffentlichen heißt: manuelles `tesserae export site` plus Upload, oder ein
neuer Workflow. Wie auch immer — bewusst entscheiden: ein Demo-Link, der still vom Code
abdriftet, ist schlimmer als gar kein Demo-Link.

## Self-Dogfood

Integrations-Opt-ins (RAG-Anything) sind jetzt
**interaktive Wizard-Abfragen**, keine CLI-Flags. Führe den Wizard aus und
beantworte sie:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# wenn der Wizard fragt:
#   - RAG-Anything aktivieren, installieren: ja, Parser: mineru, danach ausführen: ja
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Für einen vollständig nicht-interaktiven Lauf nutze `tesserae init --yes` (alle
Integrationen AUS), aktiviere dann jede Integration in `.tesserae/config.json`
— der Wizard schreibt sie unter den Schlüsseln `memory_backends` und
`external_tools` (RAG-Anything) — und führe `tesserae
integrations refresh <name>` für jede vor dem Kompilieren aus. Die genauen
Konfigurations-Schlüssel stehen in den Integrationsdokumenten.

## Demo-Talking-Points

- Tesserae ist kein generischer Noun-Phrase-Graph. Er nutzt eine kontrollierte Ontologie.
- Research- und Development-Code teilen sich die Infrastruktur, behalten aber distinkte Schemas.
- Markdown und HTML sind Projektionen, keine maßgeblichen Wahrheits-Stores.
- Der Default-Pfad ist lokal und no-API-Key-freundlich.
- Agent-Harnesses und MCP machen den Graph für Coding-Agenten nutzbar.
- Importierte Harness-Session-Seiten verwandeln frühere Claude-Code-/Codex-Arbeit in durchsuchbares Projektgedächtnis, während die Transcript-Discovery explizit bleibt.
