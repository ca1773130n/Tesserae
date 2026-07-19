# Geschichtete Agentenerinnerung — Wissensgraphen pro Agent

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Niemand erinnert sich an alles — und kein Kontextfenster eines Agenten passt alles.
Tesseraes Antwort ist eine **geschichtete Wissensdatenbank**: Jeder Agent entwickelt seine eigene
Erinnerung aus seinen eigenen Sitzungen, diese Erinnerung wird periodisch **destilliert**
(organisiert, komprimiert, poliert, verfeinert — und sicher vergessen), und Manager sehen
nur die destillierte Schicht ihrer Berichte. Der Manager des Managers sieht eine weitere Rollup.
Wie in einer echten Organisation benötigt kein einzelner Leser das vollständige Archiv.

Alles Folgende ist optional und additiv: Projekte, die nie `tesserae distill` ausführen,
verhalten sich genau wie zuvor.

## Die Schichten

- **L0 — Projektgraph** (`.tesserae/graph.json`). Unverändert, bleibt
  byte-idempotent. Der strukturelle Pass der Kompilierung generiert jetzt einen `Agent`-Knoten
  pro beobachteten Agenten plus `performed_by`-Kanten aus jeder Sitzung — Rohattribution,
  LLM-Kosten null.
- **L1 — ein Artefakt pro Agent** (`.tesserae/agents/<key>/distilled.graph.json`).
  Geschrieben von `tesserae distill`. Eine gewöhnliche Graphdatei begrenzt auf **eine 48k-Leseoperation**,
  sodass jeder Agent seine gesamte destillierte Erinnerung in einem einzigen Aufruf laden kann.
- **L2 — Manager-Rollups.** Bei der Destillierung eines Agenten mit Berichten werden die L1en der
  Berichte aufgerollt: Deduplizierung nach Abstammung, Gruppierung nach gemeinsamen Rohevidenzen
  und beste Notiz **wörtlich** beibehalten — die LLM-Neuzusammenfassungstiefe ist auf 1 begrenzt,
  daher ist eine Zusammenfassung nie eine Umformulierung einer Zusammenfassung. Derselbe Pass
  rekursiert zu beliebiger Organisationstiefe.

## Agent-Identität

Agenten werden als `harness:account:role` kodiert — Rollenebene, sodass ein `reviewer`-Unteragent
und ein `planner`-Unteragent auch auf einem Computer unterschiedliche Fachkenntnisse entwickeln.
Rollen stammen aus Unteragenten-Deskriptoren in Transkripten, dann aus deklarativen
Registrierungsabgleichregeln, dann Rückfall auf `default`.

```bash
tesserae agents init         # Sitzungen scannen, .tesserae/agents/registry.json vorschlagen
tesserae agents list         # beobachtete Schlüssel, Etiketten, Eltern, Sitzungszähler
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migriert Artefaktverzeichnis + Registrierung atomar
```

Konfigurationslos funktionieren: Jeder beobachtete Agent meldet sich implizit bei `org:root` an,
und `agent="org"` bietet eine flache Teamübersicht ohne Registrierung.

## Destillation

```bash
tesserae distill                      # alle Agenten, Blätter zuerst, Manager zuletzt
tesserae distill --agent <key>        # ein Agent
tesserae distill --dry-run            # LLM-Aufrufe schätzen, nichts schreiben
tesserae distill --max-llm-calls 50   # hartes Budget; begrenzte Durchläufe konvergieren bei Neuläufen
tesserae distill --retry-fallbacks    # Cluster, die zurückgefallen sind, erneut versuchen
tesserae distill --full               # Wasserzeichen ignorieren, komplett neu destillieren
```

Der Pass gruppiert Erkenntnisse eines Agenten, fasst jeden Cluster zusammen (Zitate auf der Whitelist
und auf Treue überprüft) und generiert destillierte Notizen, deren Identität ein **Abstammungsschlüssel** ist —
der Hash der zugrunde liegenden Raw-L0-Evidenz, nie LLM-Formulierung. Caching ist aggressiv und geteilt:
Unveränderte Eingaben werden per Wasserzeichen übersprungen, wachsende Cluster falten sich inkrementell,
Provider-Ausfälle werden unterbrochen und erzeugen deterministische strukturelle Fallbacks (gekennzeichnet,
wiederholbar, niemals als Erfolg zwischengespeichert).

Destillation ist **optional**: Setzen Sie `TESSERAE_AGENT_DISTILL=1` (oder
`{"agent_distill": {"enabled": true}}` in `config.json`). Wenn aktiviert, destilliert auch `tesserae refresh`
automatisch — aber nur Agenten unter *Speicherdruck* (ihre nicht destillierten Erkenntnisse passen nicht
mehr in die Hälfte einer Kontextleseoperation), MemGPT-Style-Konsolidierungstrigger.

## Vergessen — nie Löschen

- **Absorbieren**: Eine verfallende, niedrig vertrauenswürdige Erkenntnis, die durch ein destilliertes LLM-Qualitätsprodukt
  abgedeckt wird, wird darin aufgenommen (`absorbed_refs`) und in Standardlesevorgängen unterdrückt — bleibt
  aber über `include_superseded` und `drill_down` erreichbar.
- **Herabstufung**: Alles andere fällt im schlimmsten Fall vom kompletten Text auf eine Titel+Referenz-Zeile
  in der Indexnotiz des Agenten. Das Alter allein macht Wissen nie unsichtbar.
- **Buch**: Jede Beförderung/Herabstufung/Absorption wird an das Vergessensregister angehängt und von
  `tesserae lint` angezeigt (`AGENT_FORGET_LEDGER`), zusammen mit einer nicht destillierten Rückstandsmetrik
  pro Agent (`AGENT_UNDISTILLED_BACKLOG`).

## Als Agent lesen — `agent=`-Argument

Jedes Graph-Read-MCP-Tool akzeptiert `agent=`:

- **Worker-Schlüssel** → eigene Roherfahrung ∪ eigene destillierte Notizen, Destillat bevorzugt
  (absorbierte Rohe werden beim Laden automatisch durch abgeleitete Überlagerung unterdrückt —
  nichts wird jemals zurück in `graph.json` geschrieben).
- **Manager-Schlüssel** → eine Vereinigung von nur L1-Artefakten von Berichten. Rohe Erkenntnisse
  lecken niemals nach oben.
- **`org`** → alle destillierten Artefakte, keine Konfiguration.

Unterstützungswerkzeuge: `agent_view_explain` (Mitglieder + `distilled_through` Veraltungs-Wasserzeichen —
wie alt die Expertise jedes Berichts ist) und `drill_down` (löse `member_refs` von eine destillierten
Notiz zurück zu Rohevidenz L0 mit Status lebendig/geändert/absorbiert/verschwunden — jeder Aufruf wird
geprüft). `compile_context --multi-pool` reserviert Budgetplätze für destillierte Notizen und Fachwissensprofile
und kennzeichnet veraltete oder Fallback-Qualitätswissen in der Ausgabe.

## Die Wachstumschleife

- **Pro-Agent-Geschirr**: Der Agenten-Modus `write_harness` gibt ein Geschirr-Verzeichnis pro Agent aus,
  dessen MCP-Konfiguration die aufgelöste Ansicht dieses Agenten erreicht, plus eine einmal gesäte `purpose.md`-Missionsseite,
  die aus seinem Fachwissenprofil generiert wird.
- **Pro-Agent-Anleitung**: Lenken Sie die Destillation eines Agenten durch `.tesserae/extraction-guidance-<key>.md`,
  geschichtet über das Projekt-Niveau `.tesserae/distill-guidance.md`. Die Bearbeitung eines Agent-Streams
  destilliert nur diesen Agenten.
- **Semantische Brücken** (optional): Verlinken Sie *verwandte* Destillate zwischen Agenten mit
  `shares_concept_with`-Kanten in Manager-/Organisationsansichten — Kanten, nie Zusammenführungen.
- **Themenkarten**: `agent_topics` wickelt den Destillat-Satz eines Agenten in ein deterministisches
  `topics.md` ein — das Inhaltsverzeichnis des Agenten.
- **Unteragenten-Beförderung**: Typisierte Unteragenten-Läufe generieren Erkenntnisse unter dem
  eigenen Schlüssel des Unteragenten, sodass delegierte Arbeit sich in dem Fachwissen des Delegaten ansammelt.

## Determinismus-Garantien

Der Projektgraph bleibt byte-idempotent; destillierte Artefakte sind deterministisch gegeben
(Graph-Bytes, Registrierung, Cache-Verzeichnis, vorheriges Artefakt, Optionen). Die Zeit ist immer
die **Corpus-Uhr** — der neueste Moment in den Sitzungen selbst, rekursiv für Manager das neueste
Kind-Wasserzeichen — niemals die Wanduhr. Node-Identität hängt nicht von LLM-Prosa ab. Eine Lint-Sonde
lehnt Zeitstempel-/Zähler-förmige Metadaten auf Agent-Level-Knoten ab, da diese genaue Zustandsklasse
zuvor die Byte-Idempotenz unterbrochen hat.

Vollständige Designbegründung: `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
