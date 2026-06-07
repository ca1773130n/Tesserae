# Understand-Anything-Companion-Workflow

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything.md">English</a> · <a href="understand-anything.ko.md">한국어</a> · <a href="understand-anything.zh.md">中文</a> · <a href="understand-anything.ja.md">日本語</a> · <a href="understand-anything.ru.md">Русский</a> · <a href="understand-anything.es.md">Español</a> · <a href="understand-anything.fr.md">Français</a></p>
<!-- translations:end -->

[Understand Anything](https://github.com/Lum1104/Understand-Anything) und Tesserae sind komplementäre Projekte.

- Understand Anything ist stark darin, einen Codebase-Knowledge-Graph und ein interaktives Dashboard zu erzeugen.
- Tesserae konzentriert sich auf langlebige Agent-Memory: Docs, Markdown-/Wiki-Kompilation, statisches Publishing, Session-History und agent-orientierte Exports.

Tesserae sollte Understand Anything weder vendoren noch absorbieren. Behandle es als unabhängigen Companion, der nützliche Graph-Artefakte produziert.

## Warum beides nutzen?

Understand Anything kann Folgendes schreiben:

```text
.understand-anything/knowledge-graph.json
```

Dieser Graph erfasst Code-Struktur wie Dateien, Funktionen, Klassen, Module, Konzepte, Abhängigkeiten, Layer und Touren.

Tesserae kann dieses Artefakt dann neben dem restlichen Projektgedächtnis aufbewahren:

- Quell-Docs und Markdown-Seiten;
- Repository-Dateien;
- Research-Notizen;
- lokale Claude Code- / Codex-Session-History;
- generierte statische Wiki-Seiten;
- 2D-/3D-Graph-Website-Sichten;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json` und Per-Page-Agent-Geschwister.

## Aktueller reibungsarmer Workflow

Empfohlen ist der Setup-Wizard:

```bash
tesserae init
```

Wähle im Schritt „Companion-Tools“ Understand Anything aus. Tesserae installiert/aktualisiert die Companion-Skills auf Wunsch und schreibt einen verwalteten Refresh-Befehl in `.tesserae/config.json`. Künftige `tesserae compile`-Aufrufe führen diesen Wrapper automatisch aus, wenn der UA-Graph fehlt oder veraltet ist.

Für nicht-interaktive Automatisierung:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
tesserae compile
```

Der hinterlegte Befehl gehört Tesserae — nichts, was sich der Nutzer ausdenken muss:

```bash
tesserae integrations refresh understand-anything --platform codex
```

Während des Compiles geht Tesserae so vor:

1. prüft, ob `.understand-anything/knowledge-graph.json` existiert und mit dem aktuellen git-commit übereinstimmt, sofern Metadaten verfügbar sind;
2. führt die konfigurierte Agent-Plattform (`codex`, `opencode` oder `claude`) nur dann aus, wenn der Graph fehlt/veraltet ist oder ein Refresh erzwungen wurde;
3. verifiziert, dass der Graph geschrieben wurde;
4. materialisiert `.tesserae/external/understand-anything.md`;
5. setzt die normale Memory-Kompilation fort.

Du kannst vor einem Compile alle konfigurierten externen Refresh-Befehle erzwingen:

```bash
tesserae compile --refresh-integrations
```

Cognee zusätzlich nötig? Füge die Runtime-Memory-Flags im selben Setup-Befehl hinzu:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Manuelles Äquivalent

Der verwaltete Setup-Pfad ist vorzuziehen. Wenn du UA absichtlich außerhalb von Tesserae nutzen willst, starte Understand Anything zuerst in deiner Agent-Umgebung:

```bash
/understand
```

Führe danach den Setup-Wizard aus und **aktiviere Understand Anything bei der
Abfrage**, damit Tesserae die Quelle der Markdown-Projektion festhält. Direkte
JSON-Dateien werden als rohe Companion-Artefakte gehalten, nicht als
handgepflegte Source-Pfade.

```bash
tesserae init
# Understand Anything aktivieren, wenn der Wizard danach fragt
tesserae compile
tesserae export site
```

Für nicht-interaktive Automatisierung führe `tesserae init --yes` aus
(Integrationen AUS), aktiviere Understand Anything in `.tesserae/config.json`
(der Wizard schreibt die Integration unter den Schlüssel `external_tools`) und
führe dann vor dem Kompilieren `tesserae integrations refresh
understand-anything` aus.

Wenn du zusätzlich lokale Agent-Session-Memory möchtest:

```bash
tesserae sessions discover --import
tesserae export site
```

## Native Graph-Synchronisation

Tesserae behält die Markdown-Projektion jetzt für die Lesbarkeit bei und importiert den UA-Graphen zusätzlich nativ während des Compiles, sofern das konfigurierte Tool `sync_mode: native_graph` verwendet.

Der native Adapter liest `.understand-anything/knowledge-graph.json`, mappt UA-Nodes/Edges in die kontrollierte Ontologie von Tesserae und schreibt ein Sync-Manifest:

```text
.tesserae/external/understand-anything-sync.json
```

Aktuelles Mapping:

| Understand Anything | Tesserae-Ziel |
|---|---|
| `project` | Repository-/Projekt-Metadaten |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | kanonische `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unbekannte Edge-Typen | `shares_concept_with` mit `ua_edge_type`-Metadaten |

Die Synchronisation von Concepts erfolgt kanonisiert statt blind dupliziert. Wenn UA `Mermaid Rendering` ausgibt und Tesserae bereits `Mermaid rendering` kennt, behält der Compile einen Concept-Node und ergänzt UA-Provenance unter `metadata.external_refs`.

Tesserae bleibt der Memory-Compiler; UA bleibt ein eigenständiger Companion-Graph-Generator.

## Kollaborationsprinzip

Tesserae nicht als Ersatz für Understand Anything framen.

Eine bessere Rahmung:

- Understand Anything hilft einem Entwickler, eine Codebase jetzt zu verstehen.
- Tesserae hilft Agenten, Projektwissen über die Zeit zu erinnern, zu durchsuchen, zu zitieren, zu aktualisieren und zu publizieren.
