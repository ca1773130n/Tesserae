# Obsidian — das kompilierte Wiki als echten Vault öffnen

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.fr.md">Français</a></p>
<!-- translations:end -->

Der Obsidian-Export von Tesserae verwandelt deinen kompilierten typisierten Graphen in einen echten, opinionierten [Obsidian](https://obsidian.md)-Vault. Kein bloßes Markdown-Verzeichnis — ein Vault mit `.obsidian/`-Konfiguration, typbewussten [Callouts](https://help.obsidian.md/Editing+and+formatting/Callouts), [Dataview](https://blacksmithgu.github.io/obsidian-dataview/)-abfragbarem Frontmatter, einem Vault-Dashboard und einem Index aller `wiki://`-Querverweise zwischen Vaults.

## Voraussetzungen

Kompiliere zuerst das Projekt:

```bash
cd /path/to/your-project
tesserae project setup
tesserae project compile
```

Der Compile erzeugt `.tesserae/graph.json` (die Quelle der Wahrheit) und eine schlichte Markdown-Projektion unter `.tesserae/markdown_projection/`. Der Obsidian-Export setzt auf dieser Projektion auf, ergänzt aber auf jeder Seite Obsidian-eigene Anreicherungen.

## 1) Den Vault exportieren

```bash
tesserae project export-obsidian --vault ~/Documents/tesserae-vault
```

Das Verzeichnis wird angelegt, falls es nicht existiert. Erneutes Ausführen überschreibt es idempotent — die Markdown-Projektion ist deterministisch bei gleichem Graphen.

Was auf der Platte landet:

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Das Verzeichnis in Obsidian öffnen

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian erkennt `.obsidian/`, identifiziert das Ganze als echten Vault und lädt ihn. Die Liste der Community-Plugins enthält Dataview; Obsidian wird dich bitten, es zu aktivieren (empfohlen — ohne Dataview werden die dataview-Blöcke nur als Code-Fences gerendert).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Rundgang durch den Vault

### Einstiegspunkte

- `README.md` — was dieser Vault ist und wie man ihn aktualisiert
- `index.md` — jeder Node nach Sektion (papers, concepts, claims) mit Wikilinks
- `_meta/dashboard.md` — Dataview-Übersicht: jüngste Seiten, Papers, Concepts/Claims

### Anreicherungen pro Seite

Jede Node-Seite bringt jetzt Folgendes mit:

**Typbewusste Callouts.** Ein semantisches Callout am Seitenanfang macht den Node-Typ auf einen Blick sichtbar:

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Mapping (Auszüge): `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Dataview-abfragbare Edges.** Das Frontmatter führt die typisierten Edges jetzt als verschachtelte Maps:

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

Damit kannst du Queries schreiben wie:

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**Cross-Vault-Bridges.** Jede `wiki://<alias>/<kind>/<slug>`-URI, die in der Beschreibung oder den Metadaten eines Nodes erwähnt wird, taucht sowohl als Frontmatter-Feld auf:

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

als auch als Body-Sektion `Cross-vault references`. Der vault-weite Index `_bridges.md` aggregiert jede ausgehende Referenz gruppiert nach Ziel-Alias, sodass du Querverweise zwischen Vaults aus einer einzigen Seite heraus auditieren kannst.

**Related-(Dataview-)Block.** Jede Seite endet mit einer Query, die zurückverlinkende Seiten anzeigt und automatisch befüllt wird:

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### Vault-Dashboard

`_meta/dashboard.md` bringt Dataview-Blöcke für die nützlichsten Aggregat-Sichten mit: zuletzt aktualisierte Seiten, alle Papers mit Metadaten-Spalten, alle Concepts und Claims nach Typ sortiert. Bearbeite es frei — es ist ein Startpunkt, kein festgeschriebener Vertrag.

### Vault-Graph-Ansicht

Obsidians eingebaute Graph-Ansicht (`Ctrl/Cmd+G`) funktioniert bereits mit den Wikilinks aus den Sektionen `## Outgoing` / `## Incoming`. Die mitgelieferte `.obsidian/graph.json` färbt `papers/`-, `concepts/`- und `claims/`-Pfade zur Orientierung ein. Du kannst darüber Dataview-gefilterte Sichten legen, um feinere Slices zu bekommen.

## Cross-Vault-Workflows

Registriere mehrere Tesserae-Vaults, damit `wiki://`-URIs sich vault-übergreifend auflösen lassen:

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Exportiere jeden Vault nach der Registrierung erneut. `_bridges.md` zeigt in jedem Export nun auflösbare Referenzen zwischen Vaults, gruppiert nach Alias.

Obsidian selbst folgt `wiki://`-URIs nicht nativ — sie werden als Inline-Text gerendert — aber `_bridges.md` plus die Sektion `Cross-vault references` pro Seite liefern dir einen manuellen Index, bis ein dediziertes Obsidian-Plugin verfügbar ist.

## Refresh-Workflow

Um neue Quellen oder Fixes aus deinen Quelldateien einzubauen:

```bash
# Bearbeite die Quelldateien in den Quellverzeichnissen des Projekts, dann:
tesserae project compile
```

`compile` reprojiziert den Vault jetzt automatisch — du musst keinen separaten Export-Schritt mehr ausführen. (`tesserae project export-obsidian --vault <Pfad>` existiert weiterhin für eine einmalige Reprojektion ohne vollständige Neukompilierung.) Obsidian lädt geänderte Dateien auf der Platte hot neu.

Wenn du im Vault eigene Markdown-Notizen ergänzt hast, die nicht aus dem Graphen projiziert sind (z. B. persönliche Annotationen), bleiben sie erhalten — der Projektor überschreibt nur Dateien, die er selbst besitzt: unter `papers/`, `concepts/`, `claims/` sowie `index.md`, `_bridges.md`, `_meta/dashboard.md` und `README.md`. Handgeschriebene Seiten (ohne Frontmatter-Schlüssel `node_id:`) und der dedizierte Nutzernotiz-Block (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) auf jeder projizierten Seite bleiben über Neukompilierungen hinweg erhalten.

### Änderungen in Obsidian fließen zurück (bidirektionale Synchronisation)

Ab v0.5.0 ist der Vault **kein Einweg-Export mehr**. Er ist jetzt eine *bidirektionale Projektion*: Der typisierte Graph bleibt die Quelle der Wahrheit, aber `project compile` liest deine Obsidian-Änderungen nun aus dem Vault zurück und überlagert sie **vor** der erneuten Projektion auf den Graphen. Bearbeite in Obsidian den `title`, die `aliases`, das Beschreibungs-Callout oder einen beliebigen Nicht-System-Frontmatter-Skalar eines Knotens, kompiliere neu, und die Änderung bleibt erhalten — und propagiert zur statischen Site, zu MCP und zu allen anderen Projektionen.

```bash
tesserae project compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Was die Überlagerung erntet (die *Vault-gewinnt*-Felder):

- `title` → `name` des Knotens
- `aliases` → Aliase des Knotens
- das Beschreibungs-Callout im Text (oder der erste Absatz) → `description` des Knotens
- jeder nicht reservierte Frontmatter-Skalar → `metadata.<key>` (die reservierten/System-Schlüssel `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` werden nie als Nutzer-Overrides behandelt)

Jeder Überlagerungslauf schreibt einen `.tesserae/diverged-fields.md`-Bericht (`## Field overrides — N across M node(s)`), damit du genau prüfen kannst, was zurückgeholt wurde. Wikilinks, die du innerhalb des Nutzernotiz-Blocks hinzufügst, werden zu `user_link`-Kanten. Übergib `tesserae project compile --no-vault-pull`, um die Überlagerung für einen Lauf zu umgehen — nützlich zur Wiederherstellung oder wenn du absichtlich möchtest, dass das Quell-Markdown gewinnt.

Die erste Kompilierung nach dem Aktivieren dieser Funktion erhält einen „Freifahrtschein": Solange es noch keine `vault_snapshot.json`-Baseline gibt, wird nichts geerntet; der am Ende geschriebene Snapshot wird zur Baseline für das Diff der nächsten Kompilierung.

Für einen dedizierten Live-Workflow wendet `tesserae project obsidian-sync` die Überlagerung erneut an und reprojiziert ohne vollständige Neukompilierung:

```bash
# Vorschau, was eine Kompilierung zurückholen würde, ohne den Graphen zu verändern.
tesserae project obsidian-sync --dry-run

# Den Vault beobachten und Änderungen live hin- und zurückführen (Ctrl-C zum Stoppen).
tesserae project obsidian-sync --watch

# Nach dem Umbenennen/Löschen von Knoten verwaiste projizierte Seiten löschen.
tesserae project obsidian-sync --prune-orphans
```

Die vollständige Feld-für-Feld-Eigentümermatrix und die Designbegründung findest du in [obsidian-sync.md](obsidian-sync.md).

## Wann das vs. die statische Site einsetzen

Die kompilierte HTML-Site (`tesserae project build-site` → `.tesserae/site/`) ist ein Einweg-Export, schreibgeschützt und zum Teilen gedacht — push sie auf GitHub Pages, S3 oder einen beliebigen statischen Host. Der Obsidian-Vault dient dem **lokalen Lesen, Abfragen und Bearbeiten** mit Dataview und Obsidians Graph-Ansicht: Er ist die einzige Projektion, deren Änderungen in den Graphen zurückfließen (siehe Abschnitt zur bidirektionalen Synchronisation oben). Beide projizieren aus demselben Graphen, sodass sie nie auseinanderdriften — und Korrekturen, die du in Obsidian vornimmst, propagieren bei der nächsten Kompilierung zur Site.
