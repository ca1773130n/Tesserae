# `tesserae ingest`

<!-- translations:start -->
<p align="center"><a href="../ingest.md">English</a> · <a href="ingest.ko.md">한국어</a> · <a href="ingest.zh.md">中文</a> · <a href="ingest.ja.md">日本語</a> · <a href="ingest.ru.md">Русский</a> · <a href="ingest.es.md">Español</a> · <a href="ingest.fr.md">Français</a></p>
<!-- translations:end -->
Merged eine einzelne Dokumentdatei oder URL in die Wissensbasis.

## Verwendung

    tesserae ingest <input>...  [--title T] [--source-kind K] [--full] [--dry-run]

`<input>` ist ein oder mehrere lokale Dateipfade oder `http(s)`-URLs. URLs werden geholt, zu
Markdown konvertiert und unter `data/ingested/<slug>.md` mit Provenance-Frontmatter persistiert
(`source_url`, `fetched_at`, `content_sha256` und `arxiv_id`, wenn erkannt), dann gemerged.
Lokale Dateien von außerhalb des Projekts werden nach `data/ingested/` kopiert, damit sie zu
getrackten Quellen werden (ein späterer Full-Compile reproduziert sie identisch).

URL-Ingest erfordert das optionale Extra:

    pip install tesserae[ingest-url]

## Wie es funktioniert

Standardmäßig merged `ingest` die neue Quelle über einen inkrementellen Compile — es re-extrahiert
nicht den ganzen Korpus — und das Ergebnis ist byte-identisch zu einem Full-Compile (ein automatischer
Full-Recompile-Fallback garantiert Korrektheit für jeden Fall, den der inkrementelle Pfad nicht abdecken kann).
Übergib `--full`, um einen Full-Recompile des ganzen Korpus zu erzwingen.

## Flags

- `--full` — erzwingt einen Full-Recompile des ganzen Korpus.
- `--dry-run` — holt und berichtet, was ingestet würde; schreibt keinen Graph.
- `--title` — Titel-Override, nützlich für nackte URLs.
- `--source-kind` — überschreibt die Quellen-Klassifikation.

## Die Konzept-Schicht (`--extractor`)

Tesserae ist ein LLM-Wiki, daher baut `compile` die **Konzept-/Claim-Schicht
standardmäßig** (`--extractor llm`): Es liest jedes Dokument durch deinen konfigurierten LLM-Provider
— **codex / claude / Anthropic API**, gemäß `llm_provider` — und prägt
Konzepte, Claims, Capabilities, Fachbegriffe, Evidence-Spans und die typisierten
Kanten dazwischen. Das ist die Schicht, die den Graph *"welche Idee ist
das, und wie hängt sie zusammen"* beantworten lässt, nicht bloß *"welche Datei hat es gesagt"*.

    tesserae compile                        # LLM concept layer, configured provider
    tesserae compile --llm-provider codex   # force a provider for this run

Wenn kein LLM-Backend konfiguriert/authentifiziert ist, degradiert compile zum **deterministischen**
Extraktor (nur strukturell — Quellen, Abschnitte, explizite Links) und warnt. Du kannst
ihn auch explizit anfordern — er ist schnell, key-frei und byte-stabil, der CI- /
Reproduzierbarkeits-Modus:

    tesserae compile --extractor deterministic

### Auswählen, welche Konten verbraucht werden (`llm_claude_config_dirs`)

Mit dem Provider `claude` rotiert Tesserae über deine angemeldeten Claude-CLI-Konten:
Ein Konto, das sein Limit erreicht, übergibt an das nächste, statt den Rest des Laufs
an die deterministische Extraktion zu verlieren. Standardmäßig werden alle
`~/.claude*`-Verzeichnisse automatisch erkannt.

Der Provider **codex** funktioniert genauso: Er rotiert über authentifizierte
`~/.codex*`-Homes (ein Verzeichnis zählt nur mit `auth.json`) und wird über
`llm_codex_homes` konfiguriert. Jeder Provider hat einen eigenen Schlüssel, weil jeder
sein eigenes Kontenlayout auf der Platte hat — Claude-CLI-Konfigurationsverzeichnisse und
Codex-Homes sind nicht austauschbar:

| Provider | Konfigurationsschlüssel | was er auflistet |
|---|---|---|
| `claude` | `llm_claude_config_dirs` | Claude-CLI-Konfigurationsverzeichnisse (`~/.claude*`) |
| `codex`  | `llm_codex_homes`        | Codex-Homes (`~/.codex*`) |

Um genau festzulegen, welche Konten verbraucht werden dürfen und in welcher
Reihenfolge, setze `llm_claude_config_dirs` in `.tesserae/config.json` (Projekt) oder
`~/.tesserae/config.json` (global):

```json
{
  "llm_claude_config_dirs": [
    "/Users/you/.claude-work",
    "/Users/you/.claude-personal"
  ]
}
```

Diese Liste ist maßgeblich — außerhalb davon wird nichts versucht. Sie **schlägt auch
die ambiente Variable `CLAUDE_CONFIG_DIR`**, die jeder aus einer Claude-Code-Sitzung
gestartete Prozess erbt und die andernfalls die gesamte Kompilierung an das Kontingent
genau dieser einen Sitzung binden würde. Ohne Konfiguration bleibt
`CLAUDE_CONFIG_DIR` das zuerst versuchte Konto.

Melden alle konfigurierten Konten ihr Nutzungslimit, stellt die Kompilierung für den
Rest des Laufs LLM-Aufrufe ein, statt pro Dokument erneut nachzufragen, markiert diese
Dokumente mit `fallback: true` und sagt es dir. Nach dem Zurücksetzen des Limits ohne
vollständige Neukompilierung nachholen:

    tesserae compile --changed-only --retry-fallbacks


**Kostenbewusst (`selective-llm`)** — route nur passende Dokumente durch das LLM, den
Rest deterministisch:

    tesserae compile --extractor selective-llm \
      --llm-include "docs/**/*.md" --llm-limit 20

Dieselben Flags funktionieren auf `tesserae extract <paths>` (standalone) und
`tesserae compile <paths>` (Ad-hoc-Pfad-Ingest).

**Tuning:**

- `--llm-provider codex|claude|anthropic` — überschreibt den Provider (Default:
  `llm_provider` in der Config).
- `--llm-model` — Modell für den Extraktor (Default: der Default des Providers).
- `--llm-include <glob>` — für `selective-llm`, welche Dateien durch das LLM gehen
  (wiederholbar für mehrere; Patterns matchen überall im absoluten Pfad, z. B.
  `"*docs/superpowers*"`).
- `--llm-limit N` — deckelt, wie viele Dateien das LLM erreichen (der Rest bleibt deterministisch).

**Kein Default-Timeout.** Ein großes Design-Dokument erzeugt viel JSON und kann
Minuten dauern; die Extraktion läuft bis zum Abschluss, statt still abgeschnitten zu werden (ein
Timeout ist ausschließlich Opt-in).

**Robust auf realen Korpora.** Ein einzelnes verrauschtes oder langsames Dokument bricht nie den ganzen
Compile ab: Ein LLM-Fehler bei einem Dokument (Auth, Fehler, eine unparsbare Generierung) fällt
für *dieses* Dokument auf die deterministische Baseline zurück, ein Kanten- oder Knotentyp außerhalb des
kontrollierten Vokabulars wird verworfen, und content-keyed Caching bedeutet, dass ein Re-Compile
unveränderter Dokumente die vorherige Extraktion wiederverwendet.

> Die Extraktor-Namen `claude-cli` / `selective-claude` (und die `--claude-*`-Flags)
> sind deprecated Aliase für `llm` / `selective-llm` (und `--llm-*`); sie
> funktionieren noch, geben aber einen Deprecation-Hinweis aus.

## Den Compile-Scope verwalten (`sources`)

`tesserae compile` (ohne Argumente) kompiliert die Verzeichnisse in der `sources`-Liste
des Projekts. Verwalte diese Liste — **lokal oder global** — mit den `sources`-Unterbefehlen:

    tesserae sources add docs                 # local: inside the project (stored project-relative)
    tesserae sources add /data/shared-notes   # global: an absolute path outside the project
    tesserae sources add ../sibling-project   # global: a relative path that escapes the root
    tesserae sources list                     # shows each source tagged local/global, flags missing
    tesserae sources remove docs

Ein Pfad innerhalb des Projekts wird projekt-relativ gespeichert (portabel); alles außerhalb
wird absolut gespeichert. Beide werden zur Compile-Zeit aufgelöst, sodass eine globale Quelle genauso
kompiliert wie eine lokale. (Adds dedupen nach aufgelöster Location, sodass die absolute und die
`../`-relative Form desselben Verzeichnisses nie doppelt zählen.)

## Verwandte Befehle

- `tesserae compile` (ohne Argumente) re-extrahiert den ganzen getrackten Korpus.
- `tesserae ingest <x>` fügt eine Quelle inkrementell hinzu.
- `tesserae code ingest` prägt einen Code-Graph aus Python-Quellen (ein anderer Befehl).
