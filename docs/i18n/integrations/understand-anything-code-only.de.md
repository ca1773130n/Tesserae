# Understand-Anything: code-graph-only Modus

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.es.md">Español</a> · <a href="understand-anything-code-only.fr.md">Français</a></p>
<!-- translations:end -->

Dies ist ein Folgedokument zu [understand-anything.md](../../integrations/understand-anything.md). Das Basisdokument erklärt, wie man [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) als Companion installiert und aktiviert, der einen Code-Graphen in `.understand-anything/knowledge-graph.json` erzeugt. **Dieses Dokument erklärt, wie man UA dazu bringt, AUSSCHLIESSLICH einen Code-Graphen beizusteuern und niemals die Concept-Schicht des Research-Graphen von Tesserae mit Abschnittsüberschriften aus den eigenen Docs zu verschmutzen.**

Wer schon einmal nach dem Aktivieren von UA den typisierten Graphen geöffnet hat und in der Concept-Schicht Dinge wie `'Quickstart'`, `'2) Paste it into your MCP client'` oder dieselbe Überschrift in sieben Sprachen vorgefunden hat, kennt das Problem, das dieses Dokument behebt.

## Warum das passiert

Zwei Schichten desselben Fehlers verstärken sich gegenseitig:

1. **UA durchläuft standardmäßig die Docs.** Out of the box durchsucht UAs Source-Loader jede lesbare Datei unterhalb des Projekt-Roots — einschließlich `docs/`, `docs/i18n/`, READMEs in allen Sprachen usw. Für jede gefundene Markdown-Überschrift legt es in `.understand-anything/knowledge-graph.json` einen Node mit dem Überschriftentext als Entity-Namen an.
2. **Tesserae merged den gesamten UA-Graphen nativ.** Wenn `external_tools` UA mit `sync_mode: "native_graph"` listet, liest `ProjectWiki._merge_configured_understand_anything_graph()` das Artefakt und importiert jeden UA-Node als `Concept` in den Research-Graphen. UAs Intent „dies ist ein Code-Symbol“ wird zu „dies ist ein Research-Concept“ verflacht — und die aus Doc-Überschriften erzeugten Nodes reisen mit.

Nettoeffekt: Jede übersetzte Überschrift erscheint als doppeltes Concept (`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`) und erzeugt slug-Kollisionen, die der Projector als `setup-2.md`, `setup-3.md`, …, `setup-7.md` umbenennt.

> [!warning] Man erkennt es, wenn man es sieht
> Symptomcheck auf einem Projekt, in dem das passiert ist:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> Wenn die Top-Quelle `.understand-anything/knowledge-graph.json` mit Hunderten von Concept-Nodes ist, wird jede vorhandene übersetzte Überschrift als separates Concept importiert.

## Fix in drei Schritten

### Schritt 1 — Verhindere, dass die Tesserae-Seite UA als Concepts importiert

Bearbeite `.tesserae/config.json` und setze sowohl `enabled: false` als auch `sync_mode: "disabled"` am UA-Tool-Eintrag. Beide Flags werden vom Merge-Code-Pfad geprüft (doppelte Absicherung):

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← war true
      "sync_mode": "disabled",     // ← war "native_graph"
      "auto_refresh": false,       // optional: UA bei jedem Compile nicht mehr aktualisieren
      // ...der Rest des Eintrags bleibt unverändert
    }
  ]
}
```

`enabled: false` lässt `_merge_configured_understand_anything_graph()` das Tool komplett überspringen. `sync_mode: "disabled"` ist eine zweite Absicherung für den Fall, dass ein künftiger Bug das `enabled`-Flag ignoriert.

### Schritt 2 — Lösche die veralteten Artefakte, damit nichts zurückbleibt

Wenn zuvor ein Compile mit aktiviertem UA gelaufen ist, liegen die verschmutzten Artefakte noch auf der Platte:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae regeneriert `.tesserae/external/understand-anything.md` nur, wenn das Tool aktiviert ist — das Löschen ist also sicher, sobald Schritt 1 umgesetzt ist.

### Schritt 3 — Erneut kompilieren + Obsidian-Vault aufräumen

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

Der Compile überspringt das UA-Merge und hält den Research-Graphen frei von UA-stämmigen Concepts. Der Prune-Schritt löscht alle verwaisten Seiten im Obsidian-Vault, die auf vom Merge erzeugte node_ids zeigten.

## Verifikation

Nach dem erneuten Compile sollte das oben gezeigte Audit-Skript null (oder nahezu null) Concept-Nodes aus `.understand-anything/knowledge-graph.json` melden. Ein zweiter nützlicher Check:

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

Sollte `0 Concept slug collision(s), 0 duplicate page(s)` ausgeben, wenn der Fix gegriffen hat.

## Wenn man die Code-Graph-Navigation wirklich wieder will

UAs Code-Graph ist tatsächlich nützlich — call-/import-Edges, Klassenhierarchien usw. — solange er nicht in Doc-Überschriften-Rauschen ertrinkt. Für ein sauberes Wieder-Aktivieren:

1. **Beschränke UA selbst auf Code, nicht auf Docs.** UA akzeptiert include/exclude-Pattern; konfiguriere es so, dass es nur `src/`, `lib/`, `tesserae/` usw. durchläuft und `docs/`, `README*.md` sowie `docs/i18n/` explizit ausschließt. Der genaue Config-Schalter ist in den UA-eigenen Docs unter [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything) dokumentiert.
2. **Reaktiviere in `.tesserae/config.json`**: setze `enabled` zurück auf `true`, `sync_mode` zurück auf `"native_graph"`, `auto_refresh` zurück auf `true`.
3. **Compile erneut ausführen** und das Audit erneut laufen lassen. Ein sauberer UA-Lauf sollte Concepts produzieren, die auf echte Code-Symbole abbilden (Funktionsnamen, Klassennamen, Module) statt auf englischsprachige Abschnittsüberschriften.

Die Asymmetrie ärgert — Deaktivieren ist ein Config-Flip, sauberes Reaktivieren erfordert Verständnis von UAs Source-Scoping —, aber sie zieht die richtige Grenze. UAs Aufgabe sind Code-Graphen, Tesseraes Aufgabe sind Research-Graphen, und die Naht zwischen beiden sollte Doc-Überschriften niemals von einer Seite zur anderen durchlassen.

## Wo das hineinpasst

| Schicht | Anliegen | Konfiguriert über |
|---|---|---|
| UA-eigener Walker | Welche Dateien UA überhaupt liest | UA-eigene Config (außerhalb des Scopes von Tesserae) |
| `auto_refresh` am UA-Tool | Ob `tesserae compile` UA erneut ausführt | `.tesserae/config.json`-external_tools-Eintrag |
| `enabled` am UA-Tool | Ob Tesserae UA überhaupt berücksichtigt | `.tesserae/config.json`-external_tools-Eintrag |
| `sync_mode` am UA-Tool | Ob UA-Nodes in den Research-Graphen gemerged werden | `.tesserae/config.json`-external_tools-Eintrag |

Die Schalter `enabled` + `sync_mode` sind die Naht zwischen den beiden Projekten. Der Walker und `auto_refresh` sind interne Belange von UA.
