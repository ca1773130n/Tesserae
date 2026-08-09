# Bidirektionale Obsidian-Synchronisation — vorgeschlagener Entwurf

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.fr.md">Français</a></p>
<!-- translations:end -->

> **Status: Ausgeliefert (Tier 1, v0.5.0).** Der unten beschriebene Overlay-Reader, die User-Notes-Append-Zonen, der Watch-Modus und das Aufräumen verwaister Seiten sind hinter `tesserae vault sync` live. Diese Seite dient zugleich als Design-Begründung und als Nutzerhandbuch. Multi-Vault-Föderation (Tier 3) bleibt außerhalb des Umfangs.

Der [Obsidian-Export](obsidian.de.md) war früher strikt einseitig: Der typisierte Graph in `.tesserae/graph.json` projiziert in den Vault, und `project compile` überschreibt projizierte Dateien. `obsidian-sync` ergänzt die Gegenrichtung — bearbeite eine Beschreibung in Obsidian, und sie überlebt das Recompile.

Dieses Dokument legt dar, wie das funktioniert, ohne das Datenmodell inkohärent zu machen.

## Strategische Verschiebung, klar benannt

Die aktuelle README weist Live-Editing explizit zurück:

> Tesserae entscheidet sich für Compile-from-Source statt Live-Editing. Wer Notizen in einer UI bearbeiten will, nimmt Logseq oder Obsidian.

Bidirektionale Synchronisation **ändert diesen Vertrag** für eine Teilmenge der Felder. Das verdient bewusste Auseinandersetzung. Das Ziel ist nicht „Obsidian wird zum Editor" — sondern „die Obsidian-Edits des Nutzers werden beim Recompile nicht stillschweigend zerstört".

## Die Grundidee: Overlays statt Merges

Statt zu versuchen, zwei auseinanderdriftende Kopien desselben Nodes zu mergen, wird der Vault als **Diff-Layer** über der Projektion behandelt:

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` liegt in `.tesserae/` und wird **berechnet**, nicht von Hand verfasst. Bei jedem Compile durchläuft Tesserae den Vault, vergleicht jede projizierte Seite mit dem, was die vorherige Projektion geschrieben hat, und erfasst jede vom Nutzer eingebrachte Änderung als Overlay-Eintrag. Der finale Graph ist `base_graph` mit angewandten Overlays. Die nächste Projektion schreibt das Ergebnis zurück auf die Platte.

Round-Trip-stabil. Ein erneutes Compile desselben Vaults ohne quellseitige Änderungen erzeugt keine Diffs.

## Eigentümerschaft pro Feld

Jedes Feld eines Nodes hat einen Eigentümer. Die Eigentümerschaft entscheidet, was passiert, wenn Quelle und Vault nicht übereinstimmen.

| Feld | Quelle besitzt | Vault darf überschreiben | Hinweise |
|---|---|---|---|
| `id`, `type` | ja | nein | Schemagesteuert; Extractor-eigen |
| `name` | initial | ja | Nutzer kennt den kanonischen Namen oft besser als der Extractor |
| `aliases` | initial | ja | Append-only aus dem Vault; Vault-Einträge bleiben stets erhalten |
| `description` | initial | **ja** | Die häufigste Obsidian-Bearbeitung |
| `source_path` | ja | nein | Provenienz; lässt sich nicht wegbearbeiten |
| `metadata` (deklarierte Schlüssel) | initial | ja | Z. B. `arxiv_id`, `github_repo` — Nutzer kann korrigieren |
| `metadata.user.*` | n/a | ja | Reservierter Namespace für nutzereigene Schlüssel; Extractor schreibt nie |
| Ausgehende Edges (typisiert) | ja | nein | Edges leben in der Ontologie, nicht im Vault |
| Neue Wikilinks, die der Nutzer tippt | n/a | ja | Werden als `edge_type=user_link` exponiert, in den Graphen geschrieben |
| `<!-- user-notes -->` Body-Block | wird nie geschrieben | wird stets erhalten | Append-only-Zone, die der Projector niemals anfasst |

## Konfliktfälle und Defaults

| Fall | Default | Begründung |
|---|---|---|
| Vault-`description` weicht von der erneut extrahierten Quell-`description` ab | **Vault gewinnt**, Eintrag in `.tesserae/lint-report.md` unter „diverged fields" | Nutzer-Edits respektieren: Der Nutzer wollte die Änderung eindeutig. Der Audit-Trail erlaubt späteres Review. |
| Quelldatei gelöscht, projizierte Seite noch im Vault | Node aus dem Graphen entfernen, in `.tesserae/orphans.md` listen | Die Quelle ist autoritativ für die Existenz; das Orphan-Log lässt dich entscheiden, ob wiederhergestellt oder akzeptiert wird |
| Nutzer hat einen Wikilink auf einen nicht existierenden Slug geschrieben | Tombstone-Node anlegen (Typ `Stub`), im Lint-Report exponieren | Die Nutzer-Intention nicht verwerfen; zum Cleanup flaggen |
| Nutzer hat einen Frontmatter-Schlüssel hinzugefügt, den das Schema nicht kennt | Als `metadata.user.<key>` erhalten, nie überschreiben | Vorwärtskompatibel, ohne den typisierten Graphen zu verschmutzen |
| Zwei Vaults auf verschiedenen Maschinen bearbeiten denselben Node, beide via Obsidian Sync synchronisiert | **Außerhalb des Scopes für v1.** Last-Writer-Wins auf Dateisystemebene. | Echte Multi-Vault-Föderation ist Tier 3; aufschieben, bis ein realer Anwendungsfall vorliegt |

## User-Notes-Append-Zone

Jede projizierte Seite bekommt eine eingezäunte Zone, die der Projector niemals anfasst:

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

Zwei praktische Effekte:
1. Nutzer können jede Seite annotieren (z. B. „siehe Kapitel 4 meiner Notizen"), ohne sie beim Rebuild zu verlieren.
2. Der Pull-Durchlauf scannt den User-Notes-Block nach Wikilinks und exponiert sie als ontologisch typisierte `user_link`-Edges, was ihnen Graph-Erreichbarkeit verschafft, ohne die formalen Edge-Typen zu verschmutzen.

## Remote-Transport — explizites Nicht-Ziel

Tesserae baut **keinen** Sync-Server, keinen Auth-Layer, keinen Conflict-Resolution-Daemon und keinen gehosteten Vault. „Bidirektional" bedeutet hier „Compile liest aus dem Vault" — wie der Vault auf die Compile-Maschine kommt, ist Sache des Nutzers, gelöst mit Tools, die es längst gibt:

| Stack | Kosten | Hinweise |
|---|---|---|
| Obsidian Sync | Kostenpflichtig, $4-8/Monat | E2E-verschlüsselt, offiziell, denkbar einfach |
| iCloud / Dropbox / OneDrive | Im OS gebündelt | Funktioniert, aber die Konflikt-UX ist unfreundlich |
| Syncthing | Kostenlos, selbst gehostet | Beste Wahl für Single-User-Cross-Device |
| Git (Vault eingecheckt) | Kostenlos | Konflikt-UX ist für technische Nutzer am besten |
| LiveSync (CouchDB-Plugin) | Kostenlos, erfordert Server | Multi-Device in Echtzeit |

Alle fünf sind mit dem Overlay-Modell kompatibel, weil Tesserae den Vault als Dateien-auf-Platte sieht, nicht als Strom von Mutationen.

## CLI-Oberfläche

`tesserae vault sync` wendet Vault-Edits auf den typisierten Graphen an und projiziert neu:

```bash
# Overlay einmal anwenden: Nutzer-Edits einlesen, in den Vault neu projizieren.
tesserae vault sync

# Zuerst prüfen, was sich ändern würde. Schreibt .tesserae/diverged-fields.md und
# wendet NICHT an und projiziert nicht neu.
tesserae vault sync --dry-run

# Für diesen Aufruf einen bestimmten Vault angeben (Auflösungsreihenfolge:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae vault sync --vault ~/Documents/tesserae-vault

# Diesen Vault-Pfad zum Standard für künftige Befehle machen.
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# Langlaufender Watch: Overlay bei jeder Vault-Änderung erneut anwenden.
# Ctrl-C zum Stoppen; --interval steuert die Abtastrate (Standard 1,5 s).
tesserae vault sync --watch --interval 1.5

# Projizierte Seiten löschen, deren Quell-Node nicht mehr existiert (der Projector
# überschreibt nur, löscht nie). Seiten mit User-Notes bleiben erhalten,
# sofern du nicht zusätzlich --force-prune-with-notes übergibst.
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

Der Slash-Befehl `/tesserae:obsidian-sync` umhüllt dies, und `tesserae refresh`
(plus das `/tesserae:refresh`-Makro) führt das Overlay als letzten Schritt seiner
import → compile → sync-Kette aus.

## Lieferstatus

| Tier | Scope | Status |
|---|---|---|
| **1a** | Overlay-Reader: Vault durchlaufen, `vault_overrides.json` bauen, beim Sync anwenden. Divergenzen landen in `.tesserae/diverged-fields.md`. | Ausgeliefert |
| **1b** | User-Notes-Append-Zonen: Projector fasst `<!-- user-notes:start --> ... <!-- user-notes:end -->`-Blöcke nie an. | Ausgeliefert |
| **2** | Watch-Modus: dauerhaft laufendes `obsidian-sync --watch` führt das Overlay in einer Poll-Schleife aus, während sich der Vault ändert. | Ausgeliefert |
| **3** | Multi-Vault-Föderation: Graph speichert Provenienz pro Vault, unterstützt parallele Edits über synchronisierte Vaults hinweg. | Aufgeschoben bis zum realen Anwendungsfall |

## Nicht-Ziele (explizit)

- Ein Sync-Server / Auth / gehostetes Backend.
- Kollaboratives Echtzeit-Editing innerhalb von Obsidian (dafür LiveSync nehmen, falls nötig).
- Den Extractor umschreiben, sodass jedes Feld Round-Trip-fähig ist — die Quell-Markdown bleibt kanonisch für alles außerhalb der Override-Tabelle.
- Sync der statischen HTML-Site (`build-site` bleibt rein projektionsbasiert).

## Getroffene Entscheidungen

Dies waren die offenen Fragen zur Designzeit; die ausgelieferte Tier-1–2-Implementierung hat sie wie folgt geklärt:

1. **Form des Lint-Reports.** Divergierte Felder tauchen als dedizierte Datei `.tesserae/diverged-fields.md` auf (geschrieben von `--dry-run` und bei jeder Anwendung), damit sie in Git diffbar bleibt, statt als Abschnitt von `lint-report.md`.
2. **Tombstone-Node-Typ.** `Stub` als echten Schematyp hinzufügen oder auf `OpenQuestion` mit einem Diskriminator `_kind: stub` aufsatteln? Vorschlag: echter Typ namens `Stub`, aus öffentlichen Indizes versteckt.
3. **Pull-on-Compile-Default.** Standardmäßig AN oder AUS? Vorschlag: AN, wenn am konfigurierten Pfad ein Vault existiert, mit einer einmaligen Bestätigungsabfrage beim ersten Auslösen, damit Nutzer bewusst zustimmen.
4. **Was zählt für den Diff als „die vorherige Projektion"?** Ein in `.tesserae/vault_snapshot.json` abgelegter Snapshot oder bei jedem Compile spontanes Re-Projizieren? Vorschlag: Snapshot, am Ende jedes Compiles geschrieben. Günstiger und vermeidet, dass Extractor-Nichtdeterminismus ins Overlay leakt.
5. **Mehrsprachige Vault-Projektion.** Heutige Projektion ist einsprachig (die Quelle). Sollen Overlays locale-aware sein (z. B. greift ein Edit an `description` in einem koreanischen Vault-Overlay nur auf die koreanische Projektion)? Vorschlag: außerhalb des Scopes für v1; der Vault ist einsprachig und folgt der primären Sprache des Projekts.

## Wie sich das in `obsidian.md` zeigt

Der nutzergerichtete Guide bleibt fokussiert auf „du kannst den Vault lesen und abfragen" und verlinkt dann für die Round-Trip-Geschichte hierher, mit einer einzeiligen Zusammenfassung: „Felder in Obsidian bearbeiten, sie überleben das Recompile. Siehe [obsidian-sync.md](obsidian-sync.de.md) für das vollständige Modell."
