# Tuning-Referenz — Umgebungsvariablen

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Alle Parameter, die Tesserae aus der Umgebung liest, ihre Standardwerte und
wann Sie diese tatsächlich ändern möchten. Hier ist nichts erforderlich: die
Standardwerte werden gewählt, damit ein einfaches `tesserae compile` das
Richtige tut.

Die Projekt- und globale Konfiguration (`.tesserae/config.json`, `~/.tesserae/config.json`)
haben Vorrang vor den LLM-Backend-Einstellungen; die Umgebungsvariablen unten
setzen beide in der Ausführung, in der sie gesetzt sind, außer Kraft.

---

## Extraktion

### `TESSERAE_EXTRACT_TIMEOUT`

**Standard `1800` (Sekunden), pro Versuch.** Begrenzt jeden codex/claude-Extraktionsaufruf,
damit ein steckengebliebener CLI-Kindprozess kein Kompilieren aufhängen kann.

Dies ist passiert: Ein Kompilieren wurde bei 0% CPU für **5 h 43 m** beobachtet,
hinter einem `codex exec`-Kindprozess, der **4 h 6 m** lang untätig war und
`.tesserae/compile.lock` die ganze Zeit hielt. Er hatte bereits 32 Community-Zusammenfassungen
im Speicher aufgebaut und schaffte es nie, sie zu persistieren.

Pro Versuch, nicht pro Dokument — bei Timeout rotiert der Client zum nächsten
`CODEX_HOME` / claude Konfigurationsverzeichnis, daher ist der schlechteste Fall
für ein Dokument `timeout × konfigurierte Profile`.

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # mehr Spielraum für sehr große Dokumente
export TESSERAE_EXTRACT_TIMEOUT=0      # kein Cutoff — bis zum Abschluss ausführen
```

Ein Wert, der gesetzt aber nicht verwendbar ist (`10m`, `600s`, negativ, `inf`),
warnt auf stderr und behält den Standard. Ein Tippfehler darf ein Sicherheitsventil
nicht stillschweigend deaktivieren.

### `TESSERAE_EXTRACT_CONCURRENCY`

**Standard `4`.** Dokumente, die parallel extrahiert werden. Jedes ist ein
blockierender CLI-Kindprozess, der etwa eine Minute dauert, daher macht eine
sequenzielle Schleife die Wanduhr zur buchstäblichen Summe jedes Modell-Roundtrips
— gemessen mit ~2 h 40 m für 161 Dokumente.

Die Obergrenze ist die Rate-Limit-Grenze Ihres Provider-Kontos, nicht Ihrer Maschine,
daher ist der Standard bescheiden. Setzen Sie `1` für streng sequenzielles Verhalten.

Concurrency ändert nie die Ausgabe: Die Arbeitsliste ist in Pfadreihenfolge
behoben und Ergebnisse werden nach Index gesammelt, daher ist eine parallele
Ausführung byte-identisch mit einer sequenziellen.

### `TESSERAE_LLM_CACHE`

**Standard ein.** Inhaltsadressierter Cache von CLI-Provider-Antworten unter
`~/.tesserae/llm_cache`, indiziert nach (Dokument, Art, Leitfaden) sowie Modell
und Reasoning Effort — daher fragt das Wechseln von Modellen erneut ab, anstatt
frühere Modell-Antworten zu servieren. Nur parsierbare Antworten werden gespeichert,
daher kann eine schlechte Generierung nicht dauerhaft werden.

```sh
export TESSERAE_LLM_CACHE=0   # immer erneut fragen
```

### `TESSERAE_LLM_CHUNK_CHARS`

Zeichen pro Chunk, wenn ein Dokument zu groß für einen Aufruf ist. Lassen Sie
ungesetzt, es sei denn, Sie stoßen auf Kontext-Limits.

---

## LLM-Backend

| Variable | Standard | Notizen |
|---|---|---|
| `TESSERAE_LLM_PROVIDER` | `claude` | `codex`, `claude`, `anthropic`, `custom` |
| `TESSERAE_LLM_MODEL` | anbieterspezifisch | Begrenzt durch Anbieter, damit ein claude-ähnliches Modell niemals auf dem codex-Pfad landet |
| `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | Strukturierte Extraktion benötigt nicht das `xhigh`, das Sie für interaktive Arbeit setzen könnten — `xhigh` macht eine Multi-Dokument-Kompilierung viel langsamer |

`tesserae config status` gibt das aufgelöste Backend aus und prüft es auf Verfügbarkeit.

---

## Kompilierungs-Pässe

| Variable | Standard | Was es steuert |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **ein** | GraphRAG-ähnlicher Summary-Pass. Ein LLM-Aufruf pro Cluster ≥ 5 Mitglieder, gecacht nach Mitgliedschafts-Digest. Mit `false`/`0`/`no`/`off` deaktivieren |
| `TESSERAE_ENABLE_LLM_PASSES` | aus | Optionale LLM-Anreicherungs-Pässe über die Extraktion hinaus |
| `TESSERAE_AGENT_DISTILL` | aus | Pro-Agent L1-Expertise-Artefakte (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | aus | Runbook/Gotcha distillierte Gedächtnis-Knoten |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | ein | Verlinkt Session-Insights mit Code-Symbolen |
| `TESSERAE_SUPERSEDE_PASS` | ein | `superseded_by` Kanten zwischen überarbeiteten Ansprüchen |
| `TESSERAE_PROMPT_SIGNATURES` | aus | Zeichnet Prompt-Signaturen für Drift-Erkennung auf |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | Sekunden bis zum Warten auf `.tesserae/compile.lock` vor Aufgabe |

**Zu Community-Summaries:** Der Kompilierungs-Pass deckt eifrig die gröbste Ebene ab;
`graph_map` materialisiert zusätzlich lazy eine Summary beim ersten Abstieg in einen
cold Scope, gecacht pro Ebene. Das Ausschalten des Passes ist eine legitime Cost-Strategie
— Sie zahlen nur für tatsächlich besuchte Zweige — mit einer Warnung: **Föderales Abstieg
materialisiert niemals lazy.** Karten eines Geschwister-Projekts können nur aus seinen
In-Graph-Summaries oder bereits-warmen Caches benannt werden, daher möchte ein Projekt,
das Sie cross-project navigieren, den eifrigen Pass ein.

---

## Abfrage und Synthese

| Variable | Standard | Notizen |
|---|---|---|
| `TESSERAE_QUERY_LLM` | aus | LLM-Planer für `tesserae query` |
| `TESSERAE_QUERY_DRY_RUN` | aus | Plan ohne Modell-Aufruf |
| `TESSERAE_SYNTHESIS_LLM` | aus | Prose-Synthese in `tesserae ask` |
| `TESSERAE_SYNTHESIS_MODEL` | — | Overrides das Synthese-Modell |
| `TESSERAE_SYNTHESIS_WORKERS` | — | Parallele Synthese-Worker |
| `TESSERAE_SYNTHESIS_DRY_RUN` | aus | Skip das Modell, führe Pipeline aus |

---

## Pfade und Infrastruktur

| Variable | Standard | Notizen |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Projekt-Registry-Speicherort |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-Erkennungs-Cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv-Metadaten-Cache |
| `TESSERAE_NO_FEDERATION_CACHE` | aus | Deaktiviert die föderale Graph-LRU |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | aus | Gibt den kombinierten Cross-Project-Graph aus |
| `TESSERAE_FLEET_PIDFILE` | — | Engine-Fleet-Pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Gemeinsames Secret für den Web-Clipper |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | aus | Wendet Schema-Drift-Vorschläge an (`tesserae lab`) |

---

## Wiederherstellung eines degradierten Corpus

Wenn die Extraktion für ein Dokument fehlschlägt, wird es von der deterministischen
Basislinie bedient und **markiert** in `.tesserae/manifest.json`. Ohne die Markierung
wäre es von einer sauberen Extraktion nicht zu unterscheiden, daher würde `--changed-only`
es für immer überspringen und die Degradation wäre permanent, bis sich der Dateiinhalt ändert.

```sh
tesserae compile --changed-only --retry-fallbacks
```

Versucht nur die markierten Dokumente erneut; saubere bleiben übersprungen.

## Inspizieren der Hierarchie

```sh
tesserae graph-map                          # root map
tesserae graph-map --scope <scope_id>       # hinabsteigen
tesserae graph-map --scope '<alias>::'      # ein geschwister registriertes Projekt
```

Jede Karte meldet `size` und `leaf_member_count` aus der Hierarchie-Sidecar,
plus `live_member_count` — wie viele Mitglieder der *aktuelle* Graph tatsächlich
trägt. Ein `0` dort bedeutet der Scope ist tot (Sidecar/Graph-Skew): überspringen
Sie ihn, anstatt hinabzusteigen.
