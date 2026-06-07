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
tesserae project setup --help
tesserae project compile --help
tesserae project context --help     # On-Demand-Kontext-Compiler
```

### Demo-Build-Smoke (deckungsgleich mit dem `build-demo`-CI-Job)

Sowohl der Release-Flow als auch die CI kompilieren Tesserae gegen seinen eigenen
Quellbaum mit dem deterministischen Extraktor (keine LLM-Aufrufe, keine API-Keys) und bauen die Site:

```bash
.venv/bin/python -m tesserae project setup --yes --no-color --source . \
  --no-cognee --skip-raganything --skip-install-cognee \
  --skip-install-raganything --skip-install-understand-anything
.venv/bin/python -m tesserae project compile
.venv/bin/python -m tesserae project build-site
```

## Release-Flow

Gesteuert durch den `release`-Skill (`.claude/skills/release/SKILL.md`). Der neueste Tag ist `v0.5.0`.

- [ ] Auf `main`, Arbeitsbaum sauber, `git pull --ff-only origin main` ausführen.
- [ ] Tests + Demo-Build-Smoke (oben) bestehen.
- [ ] `version = "X.Y.Z"` in `pyproject.toml` anheben (`package.json` spiegeln, falls vorhanden); `release: vX.Y.Z` mit einem einparagraphigen Changelog aus `git log v<prev>..HEAD` committen.
- [ ] Mit `git tag -a vX.Y.Z -m "vX.Y.Z"` taggen; erst den Commit, dann den Tag pushen.
- [ ] Auf grüne CI warten (`gh run watch <run-id>`) — kein GitHub-Release auf einem roten Build.
- [ ] GitHub-Release veröffentlichen. PyPI-Veröffentlichung ist optional (wenn bereit).

### GitHub Pages

Der `build-demo`-Workflow (Push auf `main`) lädt die kompilierte Dogfood-Site immer als
inspizierbares Workflow-Artefakt hoch und deployt sie **zusätzlich** nach GitHub Pages, wenn
Pages aktiviert ist. Die Pages-Schritte sind `continue-on-error`: das Standard-`GITHUB_TOKEN`
kann keine Pages-Site *erstellen*, daher braucht der allererste Deploy ein einmaliges manuelles
Umschalten unter **Settings → Pages → Source: GitHub Actions**. Bis dieser Schalter an ist,
bleibt der Build grün und das Artefakt wird trotzdem erzeugt.

## Self-Dogfood

```bash
tesserae project setup \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
tesserae project compile
tesserae project sessions list
tesserae project build-site
tesserae project serve --port 8765
```

## Demo-Talking-Points

- Tesserae ist kein generischer Noun-Phrase-Graph. Er nutzt eine kontrollierte Ontologie.
- Research- und Development-Code teilen sich die Infrastruktur, behalten aber distinkte Schemas.
- Markdown und HTML sind Projektionen, keine maßgeblichen Wahrheits-Stores.
- Der Default-Pfad ist lokal und no-API-Key-freundlich.
- Agent-Harnesses und MCP machen den Graph für Coding-Agenten nutzbar.
- Importierte Harness-Session-Seiten verwandeln frühere Claude-Code-/Codex-Arbeit in durchsuchbares Projektgedächtnis, während die Transcript-Discovery explizit bleibt.
