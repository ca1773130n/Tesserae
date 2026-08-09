# Geschichtete Agentenmemoria — Wissensgraphen pro Agent

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a></p>
<!-- translations:end -->

Niemand erinnert sich an alles — und kein Kontextfenster eines Agenten kann alles enthalten.
Die Antwort von Tesserae ist eine **geschichtete Wissensdatenbank**: Jeder Agent züchtet sein eigenes Gedächtnis aus seinen eigenen Sitzungen, dieses Gedächtnis wird regelmäßig **destilliert** (organisiert, komprimiert, poliert, verfeinert — und sicher vergessen), und Manager sehen nur die destillierte Schicht ihrer Berichte. Der Manager des Managers sieht einen weiteren Rollup. Wie in einer echten Organisation braucht kein einzelner Leser jemals die gesamte Archive.

Alles Nachfolgende ist optional und ergänzend: Projekte, die `tesserae distill` nie ausführen, verhalten sich genau wie zuvor.

## Die Schichten

- **L0 — der Projektgraph** (`.tesserae/graph.json`). Unverändert. Bleibt
  Byte-Idempotent. Der strukturelle Durchgang der Kompilierung prägt jetzt einen `Agent`-Knoten
  pro beobachtetem Agenten plus `performed_by` Kanten aus jeder Sitzung — rohe
  Zuordnung, Null-LLM-Kosten.
- **L1 — ein Artefakt pro Agent** (`.tesserae/agents/<key>/distilled.graph.json`).
  Geschrieben von `tesserae distill`. Eine gewöhnliche Graphdatei, die auf **einen 48-KB-Lesevorgang** begrenzt ist,
  so dass jeder Agent seinen gesamten destillierten Speicher in einem einzigen Aufruf laden kann.
- **L2' — Manager-Rollups.** Beim Destillieren eines Agenten mit Berichten werden die
  L1 der Berichte zusammengefasst: Deduplizierung nach Abstammung, Gruppierung nach gemeinsamen Rohdaten und
  Beibehaltung der besten Notiz **wörtlich** — LLM-Neuzusammenfassungstiefe wird auf 1 begrenzt, daher ist eine Zusammenfassung nie eine Umformulierung einer Zusammenfassung. Derselbe Durchgang rekursiert zu jeder Organisationstiefe.

## Agenten-Identität

Agenten werden mit `harness:account:role` codiert — Rollenstufe, daher entwickelt ein `reviewer`-Subagent und ein `planner`-Subagent *unterschiedliche* Fachkompetenz auch auf einer Maschine. Rollen kommen von Subagenten-Deskriptoren in Transkriptionen, dann von deklarativen Registrierungsabgleichsregeln, dann Fallback auf `default`.

```bash
tesserae agents init         # Sitzungen scannen, INFER die Org, .tesserae/agents/registry.json schreiben
tesserae agents tree         # das Organigramm, mit Sitzungszählern + Destillationsveralterung
tesserae agents list         # beobachtete Schlüssel, Beschriftungen, Eltern, Sitzungszählungen
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migriert Artefaktverzeichnis + Registrierung atomar
```

`init` leitet die Hierarchie aus dem Rollensignal ab. Eine Subagenten-Rolle(`claude-code:me:reviewer`) wird dem Hauptagenten zugeordnet, der sie erzeugt hat (`claude-code:me:default`), daher gibt ein Befehl Sie eine funktionierende mehrschichtige Organisation — `set-parent` ist nicht erforderlich. Übergeben Sie `--flat`, um das alte Diagramm "alle unter der Wurzel" zu erzwingen. `set-parent` ist nur für tiefere, manuell konzipierte Hierarchien. Nullkonfiguration funktioniert immer noch: Ohne Registrierung meldet sich jeder Agent bei `org:root` an, und `agent="org"` ist die Übersicht des flachen Teams.

## Destillation

```bash
tesserae distill                      # jeder Agent, Blätter zuerst, Manager zuletzt
tesserae distill --agent <key>        # ein Agent
tesserae distill --dry-run            # LLM-Aufrufe schätzen, nichts schreiben
tesserae distill --max-llm-calls 50   # hartes Budget; Begrenzte Läufe konvergieren über Neuläufe
tesserae distill --retry-fallbacks    # Cluster erneut versuchen, die zurückgefallen sind
tesserae distill --full               # Wasserzeichen ignorieren, von vorne umstellen
```

Der Durchgang clustert die Ergebnisse des Agenten, fasst jeden Cluster zusammen (Zitierungszulassungsliste und Getreuheitsprüfung) und prägt destillierte Notizen, deren Identität ein **Abstammungsschlüssel** — der Hash der zugrunde liegenden rohen L0-Evidenz, niemals die LLM-Formulierung. Caching ist aggressiv und geteilt: Unveränderte Eingaben werden mit Wasserzeichen übersprungen, wachsende Cluster falten sich schrittweise ein, Anbieterausfälle werden unterbrochen und erzeugen deterministische strukturelle Fallbacks (gekennzeichnet, wiederholbar, niemals als Erfolg zwischengespeichert).

Destillation ist **optional**: Stellen Sie `TESSERAE_AGENT_DISTILL=1` ein (oder `{"agent_distill": {"enabled": true}}` in `config.json`). Wenn aktiviert, destilliert `tesserae refresh` auch automatisch — aber nur Agenten unter **Speicherdruck** (ihre nicht destillierten Ergebnisse passen nicht mehr auf eine halbe Kontextlesart), MemGPT-Stil-Konsolidierungsauslöser.

## Automatische Konsolidierung (Schlafzyklus)

Sie müssen sich nicht an die Destillation erinnern. Wie ein Gehirn, das Erinnerungen im Ruhezustand konsolidiert, konsolidiert der ständig aktivierte `tesserae engine`-Daemon sich selbst, wenn ein Projekt **untätig** ist (wenige Minuten ohne Bearbeitungen oder Sitzungen), plus eine periodische Obergrenze, damit ein ständig beschäftigtes Projekt immer noch konsolidiert. Jeder Durchlauf führt drei Vorgänge durch: **komprimiert und vergisst** (der Destillationsdurchgang unten), lässt nicht abgerufenes Wissen **durch Nichtgebrauch verblassen** (LRU-Zerfall oben) und **entdeckt neue Verbindungen** zwischen dem, was überlebt. Der Destillationsschritt verpackt genau den oben beschriebenen `maybe_distill_on_refresh`-Auslöser — dasselbe Opt-In-Gate, Pro-Agent-Wasserzeichen und Speicherdruckprüfungen — daher ist der Zyklus ein No-Op, es sei denn, `TESSERAE_AGENT_DISTILL` ist gesetzt, läuft unter dem Kompilierungs-Gate und beeinträchtigt nicht die deterministischen Artefakte.

Vollständiges Verhalten, CLI-Flags(`--consolidate-idle` / `--consolidate-every` / `--consolidate-check`) und Flottennotizen:
[docs/engine-consolidation.md](engine-consolidation.de.md).

## Vergessen — keine Löschung

- **Aufnahme**: ein entschwundenes, niedriges Vertrauen befindlich, das von einem llm-Qualitäts-Destillat abgedeckt wird, wird in es gefaltet (`absorbed_refs`) und bei Standardlesevorgängen unterdrückt — bleibt aber über `include_superseded` und `drill_down` erreichbar.
- **Herabstufung**: Alles andere fällt schlimmstenfalls aus dem vollständigen Textkörper in eine Titelzeile+Referenzzeile in der Indexnote des Agenten. Das Alter allein macht Wissen nie unsichtbar.
- **Durch Nichtgebrauch (LRU)**：Der Verfall wird durch *Abruf-Neuheit* angetrieben, nicht nur durch Erstellungsalter. Lesen Sie Oberflächen-Datensatzzugriff — `last_accessed_at` / `access_count` — in ein `node_memory` Sidecar (niemals in `graph.json`). Destillation führt diesen Live-Zugriff in seine Arbeitsansicht **vor** der Berechnung des Verfalls ein, damit ein Befund, der niemals abgerufen wird, verfällt und zur Aufnahme oder Herabstufung berechtigt wird, während ein kürzlich gelesener unabhängig vom Alter beibehalten wird. Ein leeres Sidecar reproduziert genau das alte Verhalten nur nach Alter.
- **Ledger**: Jede Beförderung/Herabstufung/Absorption wird an ein Vergessenheitsbuch angehängt und durch `tesserae lint` angezeigt (`AGENT_FORGET_LEDGER`), zusammen mit einer nicht destillierten Backlog-Metrik pro Agent (`AGENT_UNDISTILLED_BACKLOG`).

## Entdeckte Verbindungen

Über die Komprimierung und das Vergessen hinaus **entdeckt die Konsolidierung auch neue Verbindungen** zwischen destillierten Notizen——zwischen Agenten innerhalb des Projekts, nicht nur innerhalb eines Agenten. Es bettet Notizen ein und verknüpft nahe Paare als `shares_concept_with` Kanten (mit einem `federation_semantic` Marker). Die Entdeckung ist **durch Einbettung gated** — sie läuft nur, wenn ein echtes Einbettungs-Backend konfiguriert ist und überspringt den Hash-Stub — daher produziert sie nie falsche Links. Kanten werden in eine kumulative **Sidecar-Überlagerung** unter `.tesserae` geschrieben, *niemals* in `graph.json`, und werden zum Zeitpunkt der Abfrage/PPR/Verbund-Lesart im Speicher zusammengeführt (genau wie die Bereichsansicht-Überlagerung). Jeder Konsolidierungszyklus dedupliziert gegen und erweitert das, was frühere Zyklen gefunden haben. Siehe
[docs/engine-consolidation.md](engine-consolidation.de.md) für den Schlafzyklus-Betrieb, der ihn ausführt.

## Lesen einer Bereichsansicht

Aus der **CLI** begrenzt `--agent KEY` `query`, `ask` und `context`:

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # Arbeiteransicht
tesserae ask "what does my team know about deploys?" --agent org      # gesamtes Team
tesserae agents show claude-code:me:manager    # Modus, Mitglieder, Veralterung
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

In **MCP** akzeptiert jedes Graphlesewerkzeug dasselbe `agent=`. In beiden Fällen wird der Schlüssel zu einem der folgenden aufgelöst:

- **Arbeiterschlüssel** → eigene rohe Erfahrung ∪ eigene destillierte Notizen, Destillat bevorzugt (absorbierten Rohe werden automatisch durch eine bei Ladezeit abgeleitete Überlagerung unterdrückt — niemals in `graph.json` zurückgeschrieben).
- **Manager-Schlüssel** → eine Vereinigung nur von L1-Artefakten der Berichte. Rohe Erkenntnisse lecken niemals nach oben.
- **`org`** → alle destillierten Artefakte, Nullkonfiguration.

Hilfswerkzeuge: `agents show` / `agent_view_explain`(Mitglieder + `distilled_through` Veralterungswasserzeichen — wie alt die Fachkompetenz jedes Berichts ist) und `agents drill` / `drill_down`(Auflösen von `member_refs` destillierter Notizen zurück zu rohen L0-Evidenzen mit Status Lebendig/Geändert/Absorbiert/Weg — jeder Aufruf wird überprüft). `compile_context --multi-pool` reserviert Budgetplätze für destillierte Notizen und Fachkompetenzprofile und kennzeichnet veraltetes oder Fallback-Qualitätswissen in der Ausgabe. Einen Platz kann nur ein Knoten belegen, den ein Produzent tatsächlich erzeugt hat — die Destillationsdurchläufe, der session-event-Durchlauf oder das eigene `graph_write` eines Agenten —, sodass ein Pool, dessen Typ nur durch Dokumentextraktion gefüllt ist, leer bleibt; sowohl die CLI als auch `knobs.pool_reservations` nennen die Pools, die nichts zurückgegeben haben.

## Die Wachstumsschleife

- **Pro-Agent-Anbindung**: Der `write_harness`-Agentenmodus gibt ein Anbindungsverzeichnis pro Agent aus, dessen MCP-Konfiguration die gelöste Ansicht dieses Agenten erreicht, plus eine einmalig gesäte `purpose.md`-Missionsseite, die aus seinem Fachkompetenzprofil generiert wird.
- **Pro-Agent-Leitfaden**: Lenken Sie die Destillation eines Agenten über `.tesserae/extraction-guidance-<key>.md`, geschichtet über die Projektebene `.tesserae/distill-guidance.md`. Das Bearbeiten des Streams eines Agenten destilliert nur diesen Agenten neu.
- **Semantische Brücken** (optional): Verknüpfen Sie *verwandte* Destillate zwischen Agenten mit `shares_concept_with` Kanten in Manager-/Organisationsansichten — Kanten, nicht Fusionen.
- **Themenkarten**: `agent_topics` walzt den Destillatssatz eines Agenten in deterministische `topics.md` — das Inhaltsverzeichnis des Agenten.
- **Subagenten-Promotion**: Typisierte Subagenten-Läufe prägen Befunde unter dem eigenen Schlüssel des Subagenten, daher werden delegierte Arbeiten in der Fachkompetenz des Vertreters akkumuliert.

## Determinismus-Garantien

Der Projektgraph bleibt Byte-Idempotent; destillierte Artefakte sind
deterministisch bei (Graphbytes, Registrierung, Cache-Verzeichnis, vorheriges Artefakt,
Optionen). Die Zeit ist immer die **Corpus-Uhr** — der neueste Moment in
den Sitzungen selbst, rekursiv der neueste untergeordnete Wasserzeichen für Manager —
nie Wanduhr. Die Knotenidentität hängt nicht von LLM-Prosa ab. Eine Lint-Sonde
lehnt Metadaten in Form von Zeitstempel/Zähler auf Agenten-Ebene-Knoten ab, da
diese genaue Zustandsklasse die Byte-Idempotenz zuvor unterbrochen hat.

Vollständige Designrationale: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
