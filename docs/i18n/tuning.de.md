# Tuning-Referenz — Umgebungsvariablen

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Alle Parameter, die Tesserae aus der Umgebung liest, ihre Standardwerte und
wann Sie diese tatsächlich ändern möchten. Hier ist nichts erforderlich: die
Standardwerte werden gewählt, damit ein einfaches `tesserae compile` das
Richtige tut.

Die LLM-Backend-Einstellungen befinden sich auch in `.tesserae/config.json` und
`~/.tesserae/config.json`; die Umgebungsvariablen unten setzen beide in der
Ausführung, in der sie gesetzt sind, außer Kraft, und [LLM-Backend](#llm-backend)
gibt die vollständige Reihenfolge einmal an.

---

## Hooks die Geld kosten

Das Claude Code Plugin liefert Hooks, die eine Kompilierung im Hintergrund ausführen können. Alles, das Geld kostet, ist **standardmäßig deaktiviert**:

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # opt in to automatic recompiles
```

Gated: `posttooluse-edit.sh` (wird bei jedem Edit/Write ausgelöst) und `session-end.sh`.
Nicht gated, weil sie nichts kosten: `session-start.sh` führt deterministisches `code sync` aus,
und `pretooluse-compile.sh` fängt nur einen `tesserae compile` Befehl ab, den du selbst eingegeben hast.

Dieser Standard existiert, weil die Alternative gemessen wurde. Eine Wissensbasis in
`~/.tesserae` lässt `$HOME` wie einen Projektstamm aussehen, und der Hook-Resolver
wanderte *aufwärts* vom Arbeitsverzeichnis zum ersten `.tesserae/`, das er fand — so
dass jede Sitzung außerhalb eines registrierten Projekts zu `$HOME` auflöste und die
gesamte Home-Verzeichnis kompilierte: 15k Dateien, ein 795 MB Graph, **~10 Stunden LLM-Ausgaben**,
von einem detached Prozess, der die Sitzung überlebte, die ihn startete.

`resolve_project_root()` weigert sich jetzt `$HOME` durch beide Pfade, und gibt
eine leere Antwort zurück anstatt auf das Arbeitsverzeichnis zurückzufallen, daher
no-op Aufrufer anstatt zu raten. Ein Hook, der Model-Arbeit im Hintergrund ausführt,
sollte absichtlich aktiviert werden, nicht nach der Rechnung deaktiviert werden.

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

**Standard `4`, oder `1`, wenn der LLM-Endpunkt auf dieser Maschine liegt.** Dokumente, die parallel extrahiert werden. Jedes ist ein
blockierender CLI-Kindprozess, der etwa eine Minute dauert, daher macht eine
sequenzielle Schleife die Wanduhr zur buchstäblichen Summe jedes Modell-Roundtrips
— gemessen mit ~2 h 40 m für 161 Dokumente.

Die Obergrenze ist die Rate-Limit-Grenze Ihres Provider-Kontos, nicht Ihrer Maschine,
daher ist der Standard bescheiden. Setzen Sie `1` für streng sequenzielles Verhalten.

Ein lokaler Modellserver ist die Ausnahme. Ollama, llama.cpp und LM Studio bedienen eine Anfrage
nach der anderen, daher stellen vier Worker hinter jedem Aufruf drei Anfragen in die Warteschlange,
und eine wartende Anfrage, die der Server verwirft, blockiert ihren Worker für das gesamte
`TESSERAE_EXTRACT_TIMEOUT` — was genau wie ein Speicherproblem aussieht. Wenn die aufgelöste
`llm_base_url` auf `localhost`, `127.0.0.1` oder `::1` zeigt und diese Variable nicht gesetzt ist,
extrahiert der Lauf ein Dokument nach dem anderen und sagt das auf stderr. Ein Loopback-Proxy, der
an eine Cloud-API weiterleitet (LiteLLM, vLLM mit Batching), verträgt mehr: Setzen Sie die Variable
explizit, dann gewinnt sie immer.

Concurrency ändert nie die Ausgabe: Die Arbeitsliste ist in Pfadreihenfolge
behoben und Ergebnisse werden nach Index gesammelt, daher ist eine parallele
Ausführung byte-identisch mit einer sequenziellen.

### `TESSERAE_LLM_CACHE`

**Standard ein.** Inhaltsadressierter Cache von CLI-Provider-Antworten unter
`~/.tesserae/llm_cache`, indiziert nach einem Digest des tatsächlich gesendeten Prompts
sowie dem Modell und dem Reasoning Effort — daher fragt eine andere Frage erneut ab, und
das Wechseln von Modellen fragt erneut ab, anstatt frühere Modell-Antworten auszuliefern.
Nur parsierbare Antworten werden gespeichert, daher kann eine schlechte Generierung nicht
dauerhaft werden.

Ältere Einträge sind absichtlich unerreichbar: Der Schlüssel war früher ein
von der aufrufenden Stufe bereitgestelltes Label, anstatt eines Digests des Prompts,
sodass voneinander unabhängige Fragen einen Eintrag teilen konnten. Sie werden nicht
migriert — das Verzeichnis kann gefahrlos gelöscht werden, und eine Kompilierung wird
es wieder füllen.

```sh
export TESSERAE_LLM_CACHE=0   # immer erneut fragen
```

### `TESSERAE_LLM_CHUNK_CHARS`

Zeichen pro Chunk, wenn ein Dokument zu groß für einen Aufruf ist. Lassen Sie
ungesetzt, es sei denn, Sie stoßen auf Kontext-Limits.

---

## LLM-Backend

Welches Backend antwortet, über welchen Draht, mit welchem Authentifizierungsmittel.
Jeder Schlüssel unten wird auf die gleiche Weise und nur auf diese Weise aufgelöst:

**`TESSERAE_*` Umgebungsvariable → Projekt `.tesserae/config.json` → `~/.tesserae/config.json` → eingebauter Standard.**

| Config-Schlüssel | Umgebungsvariable | Standard | Notizen |
|---|---|---|---|
| `llm_provider` | `TESSERAE_LLM_PROVIDER` | `claude` | Einer von `claude`, `codex`, `anthropic`, `openai`, `custom`. Alles andere wird namentlich abgelehnt — ein Tippfehler wurde früher stillschweigend als `claude` behandelt, also lief eine Config, die `openrouter` sagte, gegen Anthropic und meldete einen Fehler über ein Modell, das Sie nie gewählt hatten |
| `llm_api_style` | `TESSERAE_LLM_API_STYLE` | `openai` wenn `llm_provider` `openai` ist, sonst `anthropic` | Das Draht-Protokoll, das eine andere Frage als das Backend ist. `anthropic` postet zu `{base_url}/v1/messages` durch das Anthropic SDK; `openai` postet zu `{base_url}/chat/completions` |
| `llm_model` | `TESSERAE_LLM_MODEL` | `sonnet` (claude CLI), `gpt-5.6-luna` (codex CLI), `claude-sonnet-4-6` (anthropic wire), `gpt-4o-mini` (openai wire) | Begrenzt durch Anbieter auf den zwei CLI-Backends, damit ein claude-ähnliches Modell niemals auf dem codex-Pfad landet. Ein konfigurierter Endpunkt-Provider behält sein Modell bei, selbst wenn Provider und Modell in verschiedenen Config-Layern gesetzt wurden |
| `llm_base_url` | `TESSERAE_LLM_BASE_URL`, dann `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` (anthropic wire), `https://api.openai.com/v1` (openai wire) | Der Endpunkt, auf das trimmed, was jeder Draht anhängt — siehe [benutzerdefinierte Endpunkte](#benutzerdefinierte-endpunkte) |
| `llm_api_key` | `TESSERAE_LLM_API_KEY`, dann `ANTHROPIC_API_KEY` | — | Das API-Key-Authentifizierungsmittel: `X-Api-Key` auf dem anthropic wire, `Authorization: Bearer` auf dem openai wire |
| `llm_auth_token` | `TESSERAE_LLM_AUTH_TOKEN`, dann `ANTHROPIC_AUTH_TOKEN` | — | Das Bearer-Authentifizierungsmittel, `Authorization: Bearer` auf beiden Drähten. Setzen Sie diesen **oder** `llm_api_key`: Auf dem anthropic wire wird das Token an das SDK als `auth_token=` übergeben und es wird kein API-Schlüssel gesetzt, daher kollidieren die beiden niemals |
| `llm_allow_fallback` | `TESSERAE_LLM_ALLOW_FALLBACK` | aus | Lässt einen konfigurierten Endpunkt-Provider auf ein anderes Backend ausweichen, anstatt zu fehlschlagen — siehe [ein Endpunkt-Provider ist ein Vertrag](#ein-endpunkt-provider-ist-ein-vertrag). Jeder nicht-leere Wert der Umgebungsvariable schaltet sie ein |
| `llm_claude_config_dirs` | `TESSERAE_CLAUDE_CONFIG_DIRS` | der Standard der CLI selbst | Claude-Konfigurationsverzeichnisse in Rotationsreihenfolge, durch `os.pathsep` in der Umgebungsvariable getrennt — der Umgebungskanal für ein wiederholtes `--claude-config-dir`. Nur eine *konfigurierte* Liste ist maßgeblich; das umgebende `CLAUDE_CONFIG_DIR` bewusst nicht, denn ein Anheften daran lässt die Mehrkonten-Rotation auf ein Konto zusammenfallen |
| `llm_codex_homes` | `TESSERAE_CODEX_HOMES` | der Standard der CLI selbst | Codex-Homes, gleiche Form und gleicher Grund wie oben. Der ältere Singular `llm_codex_home` funktioniert immer noch und bedeutet eine Eintrag-Home-Liste |
| `llm_codex_reasoning_effort` | `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | Strukturierte Extraktion benötigt nicht das `xhigh`, das Sie für interaktive Arbeit setzen könnten — `xhigh` macht eine Multi-Dokument-Kompilierung viel langsamer |

Die `ANTHROPIC_*`-Namen funktionieren immer noch, eine Stufe unter den Tesserae-eigenen:
Sie sind ambient — jede Claude Code-Sitzung exportiert sie — daher müssen sie nicht einen
Wert überraken, den Sie speziell für Tesserae gesetzt haben, aber sie schlagen immer noch
beide Config-Dateien.

`tesserae config llm` schreibt die maschinenweite Datei; für ein Projekt, setzen Sie
die gleichen `llm_*`-Schlüssel in sein `.tesserae/config.json`. Eine Authentifizierung,
die in einer Datei geschrieben ist, wird in **Klartext** gespeichert, daher bevorzugen
Sie `TESSERAE_LLM_API_KEY` / `TESSERAE_LLM_AUTH_TOKEN` für diese zwei.

### Benutzerdefinierte Endpunkte

`llm_provider` sagt, welches Backend; `llm_api_style` sagt, welchen HTTP-Dialekt
man damit spricht. Sie getrennt zu halten, ist das, was einen nicht-Anthropic-Endpunkt
überhaupt erreichbar macht: `custom` bedeutete früher das Anthropic-Protokoll, daher
hatte ein OpenAI-kompatibler Server nirgends, wo er konfiguriert werden konnte.
Wenn nicht gesetzt, wird `llm_api_style` immer noch zu `anthropic` für `custom` aufgelöst
— ein Endpunkt, der vor dieser Änderung konfiguriert wurde, verhält sich genau wie zuvor.

**Ein OpenAI-kompatibler Endpunkt** — vLLM, LiteLLM, OpenRouter, Together, Ollama,
LM Studio:

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=openai
export TESSERAE_LLM_BASE_URL=http://localhost:8000/v1
export TESSERAE_LLM_MODEL=qwen2.5-coder-32b-instruct
export TESSERAE_LLM_AUTH_TOKEN=sk-...   # oder TESSERAE_LLM_API_KEY — hier der gleiche Header
tesserae config status
```

Die Anfrage ist `POST {base_url}/chat/completions`. Dieser Draht ist `stdlib` `urllib`,
daher braucht er keine zusätzliche Installation, und ein Schlüsselloses lokales Server
braucht überhaupt keine Authentifizierung — lassen Sie beide nicht gesetzt und es
wird immer noch gebaut.

**Ein Anthropic-kompatibler Endpunkt** — ein Gateway, das die Messages API spricht:

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=anthropic
export TESSERAE_LLM_BASE_URL=https://gateway.internal.example   # kein /v1
export TESSERAE_LLM_MODEL=claude-sonnet-4-6
export TESSERAE_LLM_API_KEY=...         # X-Api-Key; TESSERAE_LLM_AUTH_TOKEN für ein Bearer-Gateway
tesserae config status
```

Die Anfrage ist `POST {base_url}/v1/messages` durch das Anthropic SDK, das
`llm_provider: custom` auf diesem Draht und `llm_provider: anthropic` beide benötigen:

```bash
pip install "tesserae[synthesis-llm]"
```

**Das `/v1` ist nicht dekorativ.** Das SDK hängt selbst `/v1/messages` an, daher
produzierten die `https://host/v1`, die jedes Gateway-README zeigt, `/v1/v1/messages`
— einen 404, der sich wie ein falsches Modell liest. Ein nachgehendes `/v1` wird jetzt
auf dem anthropic wire entfernt und auf dem openai wire gewährleistet. Nur dieses eine
nachgehende Segment wird jemals angefasst, und es wird trimmed egal was davorkommt
— ein Proxy, der echte `/anthropic/v1` dient, verliert auch dieses `/v1` — daher ist
das Umschreiben bei INFO protokolliert anstatt stillschweigend gemacht, und die Log-Zeile
ist, wo Sie die tatsächlich verwendete URL finden.

### Ein Endpunkt-Provider ist ein Vertrag

`anthropic`, `openai` und `custom` tragen einen Endpunkt, den Sie gewählt haben — eine
URL, einen Modellnamen, eine Authentifizierung. Wenn einer davon konfiguriert ist, wird
er allein gebaut, und ein Fehler bringt `LLMProviderConfigError` auf, das den Provider,
den Draht, die Basis-URL, das Modell und welche Art von Authentifizierung aufgelöst wurde,
nennt.

Es war früher eine Vorliebe stattdessen: ein benutzerdefinerter Endpunkt, der nicht gebaut
werden konnte, fiel zurück auf die Claude CLI, die dann mit `--model sonnet` gegen Ihre
eigene Basis-URL herausgebracht wurde und einen nicht unterstützten Modellnamen meldete,
den Sie nie konfiguriert hatten, mit nichts, das die echte Ursache nennt. Setzen Sie
`llm_allow_fallback: true`, um diese Verkettung zurückzubekommen.

Die zwei OAuth-CLI-Provider verketten sich immer noch — untereinander und zum API-Client
dahinter. `claude` und `codex` nehmen keine Basis-URL und ihre Modelle werden pro
Provider begrenzt, daher kann keiner von ihnen einen Endpunkt, den Sie gewählt haben,
auf ein Backend tragen, das Sie nicht genannt haben, das ist das Einzige, das der
Vertrag existiert, um es zu verhindern.

### Sehen Sie, was wirklich wirksam ist

```bash
tesserae config status                 # aufgelöster Backend + eine Live-Sonde
tesserae config status --project .     # wie dieses Projekt's config.json es sieht
tesserae config status --no-ping       # die Sonde überspringen, geben Sie nichts aus
```

Es druckt den Provider, den Draht, das Modell, die Basis-URL und die *Art* der
Authentifizierung, die aufgelöst wurde — `api_key`, `auth_token` oder keine, niemals
das Geheimnis — jede mit dem Layer markiert, der sie gewann, dann die Klasse und
Identität des Clients, der antwortete. Dieser Client wird aus dem gleichen Einstellungen-
Dict gebaut, das ein echter Lauf nutzt, und die Sonde wird nie gecacht, daher bedeutet
eine bestehende Zeile, dass der Backend gerade eben antwortete, anstatt irgendwann
in der Vergangenheit.

Wenn ein Aufruf fehlschlägt, wird das Fehlschlag klassifiziert anstatt abgeflacht:
`401` und `403` werden als Auth gemeldet, `404` — und ein `400`, das das Modell nennt
— als der Endpunkt, jede benennend den Endpunkt, der sie produzierten. Davor war eine
falsch konfigurierte URL nicht zu unterscheiden davon, dass man überhaupt kein LLM
installiert hatte.

---

## Kompilierungs-Pässe

| Variable | Standard | Was es steuert |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **ein** | GraphRAG-ähnlicher Summary-Pass. Ein LLM-Aufruf pro Cluster ≥ 5 Mitglieder, gecacht nach Mitgliedschafts-Digest. Mit `false`/`0`/`no`/`off` deaktivieren |
| `TESSERAE_ENABLE_LLM_PASSES` | aus | Optionale LLM-Anreicherungs-Pässe über die Extraktion hinaus |
| `TESSERAE_AGENT_DISTILL` | aus | Pro-Agent L1-Expertise-Artefakte (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | aus | Runbook/Gotcha distillierte Gedächtnis-Knoten |
| `TESSERAE_SESSION_EVENT_PASS` | **ein** | `Event`-Knoten pro Zug aus Sitzungstranskripten. Ohne LLM und bytegenau deterministisch, aber ein Knoten je bedeutsamem Zug — bei langem Korpus umfangreich. `false`/`0`/`no`/`off` deaktiviert |
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
| `TESSERAE_VERIFY_BAND` | an | Lässt das Modell die unsicheren Prüfmarkierungen von `ask` im gemessenen Band 0.30–0.70 neu entscheiden; `lo-hi` überschreibt es. `off` behält nur die kostenlosen Markierungen, die weder Token noch Netzwerk kosten |
| `TESSERAE_EMBEDDING_PREFER` | auto | Encoder der dichten Spur: `model2vec` (mitgeliefert, statisch, ohne torch), `st` (ein trainiertes sentence-transformers-Modell), `openai`, `hash`. Ungesetzt nimmt die Leiter den ersten installierten |
| `TESSERAE_ST_MODEL` | `BAAI/bge-base-en-v1.5` | Das sentence-transformers-Modell, das `st` lädt; jeder Hugging-Face-Name |

### `TESSERAE_VERIFY_BAND`

Jede `ask`-Antwort trägt Prüfmarkierungen je Satz, die nichts kosten. Sie sind
ungenauer, als ein Modell zu fragen — 0.870 gegen 0.926 auf 755 zurückgehaltenen
Sätzen — und fast der gesamte Unterschied sind Fehlalarme bei treuen Umschreibungen,
die mit ihrer Quelle wenig Wortschatz teilen.

Beide irren bei verschiedenen Sätzen, also holt Bezahlen des Modells nur dort, wo die
kostenlose Prüfung unsicher ist, die Genauigkeit für einen Bruchteil zurück. Die
Abdeckung 0.30–0.70 abzugeben ergab 0.932 bei 42% der Aufrufe: nicht unterscheidbar
davon, nach jedem Satz zu fragen (McNemar p=0.52), für 42% der Ausgaben.

```bash
export TESSERAE_VERIFY_BAND=on          # das gemessene Band 0.30-0.70
export TESSERAE_VERIFY_BAND=0.40-0.60   # enger: 22% der Aufrufe, 0.914
```

In `ask` standardmäßig an: ein Modell-Client ist bereits zur Hand, Token sind für die
Antwort bereits ausgegeben, also kosten die genauen Markierungen wenig obendrauf. Keine
modellfreie Variante der Prüfung schließt die Lücke allein — Stemming, Zeichen-n-Gramme,
Seltenheitsgewichtung und eine lokale Einbettung wurden je gemessen, und keine schlug die
schlichte Abdeckung —, darum ist die Voreinstellung die Kaskade und nicht eine
schlauere kostenlose Prüfung. Die Bibliotheksfunktion `check_against_evidence` bleibt
unberührt und kostet weiterhin nichts. Der Umschlag meldet `adjudicated`: `null`, wenn
die Kaskade nicht lief, sonst eine Anzahl. Ein Modell, das nicht antworten kann, lässt
das kostenlose Urteil stehen — ein fehlgeschlagener Aufruf kann einen markierten Satz
niemals sauber machen.

### `TESSERAE_EMBEDDING_PREFER`

Die dichte Spur von `hybrid_search` bettet mit dem ein, was
`active_embedding_backend` zuerst findet: das mitgelieferte statische Modell
`model2vec` (8 MB, ohne torch, offline), dann sentence-transformers, dann ein
Hash-Platzhalter. Das statische Modell hält `pip install tesserae` klein, und
auf einem kleinen Korpus kostet es nichts Messbares. Auf einem großen ist es
der Engpass: auf 148 Papieren lag der Recall über verschiedene Dokumente bei
0.754 @10 / 0.914 @50 mit dem mitgelieferten Modell und bei 0.791 / 0.962 mit
`BAAI/bge-base-en-v1.5` in derselben Fusion — die dichte Spur allein stieg von
0.473 auf 0.680 @10. Ein schlichter Vektorspeicher über denselben Abschnitten
erreicht 0.784 / 0.942 mit nomic-embed-text und 0.775 / 0.944 mit demselben
bge-base; der Vorsprung des Graphen liegt auf 57 Fragen im Rauschen (gepaarter
Vorzeichentest p=1.0 bei 10, 0.51 bei 50). Der trainierte Encoder ist das, was
den Graphen mit ihm gleichzieht statt hinter ihn.

```bash
uv pip install sentence-transformers          # torch, ~2 GB with the model
export TESSERAE_EMBEDDING_PREFER=st
export TESSERAE_ST_MODEL=BAAI/bge-base-en-v1.5   # the default; any Hugging Face name
```

`auto` wählt weiterhin zuerst das statische Modell, eine Installation, die die
Variable nie setzt, verhält sich also genau wie zuvor. Die Präferenz wird
einmal gelesen, wenn das Backend zum ersten Mal aufgelöst wird; ein Wert, der
kein Backend benennt, wird gemeldet und ignoriert, statt still auf den
Hash-Platzhalter durchzufallen. Ein trainierter Encoder bettet ohne
Vektor-Cache bei jeder Anfrage jeden Knoten neu ein — `compile_context` und der
MCP-Server übergeben bereits den `VectorCache` des Projekts, dessen Schlüssel
das Backend ist, sodass ein Modellwechsel nie veraltete Vektoren liefert.

---

## Pfade und Infrastruktur

| Variable | Standard | Notizen |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Projekt-Registry-Speicherort. Wird von **jedem** Befehl beachtet — bis 0.28.7 las nur der Fleet-Modus der Engine sie, ein Setzen an anderer Stelle blieb also still wirkungslos und die Befehle benutzten weiter die echte Registry |
| `TESSERAE_HOST_ID` | einmalig nach `~/.tesserae/host_id` generiert | Die Identität dieser Maschine. Siehe [mehrere Maschinen fahren](#mehrere-maschinen-gegen-ein-projekt-fahren) |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-Erkennungs-Cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv-Metadaten-Cache |
| `TESSERAE_NO_FEDERATION_CACHE` | aus | Deaktiviert die föderale Graph-LRU |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | aus | Gibt den kombinierten Cross-Project-Graph aus |
| `TESSERAE_FLEET_PIDFILE` | — | Engine-Fleet-Pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Gemeinsames Secret für den Web-Clipper |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | aus | Wendet die **genehmigten** Einträge in `.tesserae/schema-drift-proposals.json` zur Kompilierungszeit an (deterministisch, kein LLM). Schreiben Sie Vorschläge mit `tesserae schema-drift`; einen genehmigen bedeutet zunächst `ResearchNodeType` zu editieren, dann `"approved": true` zu setzen — ein nicht auflösbarer Name retypisiert nichts. |

---

## Wer den Graphen liest

| Variable | Standard | Anmerkungen |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **aus** | Zeichnet die Lesevorgänge auf, die Zugriffszähler verschieben — `{tool, actor, node_ids, at, tesserae_version}` — in einer `read_audit`-Tabelle in `.tesserae/sqlite.db`, lesbar zurück durch das `read_audit`-Tool mit einer Pro-Actor-Tally. Eine Zeile wird überall dort geschrieben, wo ein Zugriffszähler verschoben wird, sodass die Zeilenzahl der Oberfläche folgt statt dem Aufruf: ein Werkzeug, das eine Liste von Knoten ausgibt (`search_nodes`, `node_context`, `compile_context`, `graph_map`, `graph_ppr`, `ask` / `query`, `drill_down`, `find_session_findings`) schreibt **eine Zeile pro Aufruf** und benennt jeden Knoten, den es gezählt hat, während `fresh_insights` in seiner eigenen Schleife bumpt und so **eine Zeile pro Knoten** schreibt, den es ausgegeben hat. Ein Aufruf, der nichts ausgibt, schreibt keine, und ein Werkzeug, das keinen Knoten überhaupt liest — `schema`, `graph_summary` — erreicht nie das Audit, weil eine Zeile, die keinen Knoten benennt, keinen Zugriffszähler erklärt. Standardmäßig aus, weil ein immer aktives Audit über jede Leseoberfläche jeden Lesevorgang zu einem Schreibvorgang macht; das Tor sitzt vor dem Öffnen des Stores, da das Erstellen der Tabelle selbst ein Schreibvorgang ist. Nichts darüber erreicht je `graph.json` |
| `TESSERAE_ACTOR` | — | Wem ein Lesevorgang zuzurechnen ist, wenn der Aufruf keine Agent-Sicht trägt. Der Actor ist das `agent`-Argument, wenn der Aufruf eines aufgelöst hat, sonst dies; nicht gesetzt zeichnet den Lesevorgang als anonym auf, statt einen Namen zu erfinden |

Das Ausschalten von `TESSERAE_READ_AUDIT` stoppt die Aufzeichnung, ohne zu löschen, was
bereits aufgezeichnet wurde, und es tritt in Kraft, ohne den Server neu zu starten. Was das
Audit *für* ist, ist [Vergessen-durch-Disuse](agent-memory.de.md#vergessen--keine-löschung):
Zugriffszähler steuern, was absorbiert oder herabgestuft wird, und ohne einen Actor ist ein
chatty Agent, der einen Node abruft, und ein Mensch, der ihn einmal liest, dieselbe Eingabe.

---

## Mehrere Maschinen gegen ein Projekt fahren

Die Konstellation, für die das geschrieben ist: mehrere Server lassen je einen
Coding-Agenten laufen, jeder hat seine eigenen lokalen Session-Transkripte, und
sie teilen sich eine Platte — sehen also dasselbe Projektverzeichnis und dasselbe
`.tesserae/`.

**Geben Sie einem Host das Kompilieren und lassen Sie den Rest nur ernten.**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` zieht die lokalen Transkripte jener Maschine in den geteilten
Session-Store und nimmt nie den Compile-Lock des Projekts. Das beseitigt die
Konkurrenz, statt sie zu schlichten — und deshalb schlägt es jedes
Timeout-Tuning.

**Wenn Sie doch anstehen statt scheitern wollen**, übergeben Sie `--wait`:

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

Ohne das beendet sich ein Compile, der den Lock gehalten vorfindet, mit 2 —
richtig für einen Hook, zermürbend für einen Menschen. `--wait` ist ein Flag und
nichts, das daraus abgeleitet wird, ob stdout ein Terminal ist: derselbe Befehl
darf sein Verhalten unter `tee`, im tmux-Capture oder in CI nicht ändern.
`TESSERAE_COMPILE_LOCK_WAIT=<seconds>` tut dasselbe für einen ganzen
Prozessbaum.

**Jedes Projekt frisch halten** aus einem einzigen Aufruf:

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

Ein fehlschlagendes Projekt stoppt die anderen nicht. Exit `2`, wenn eines
fehlschlug, `1`, wenn eines von einem anderen Lauf gesperrt war, `0`, wenn alles
durchlief. `--jobs` steht standardmäßig auf 1, weil ein Compile LLM-lastig ist
und ein höherer Wert Quota parallel verbrennt.

### Was das sicher macht

Pro-Maschine-Zustand lag früher unter einem einzigen gemeinsamen Namen und wurde
von jedem Host gelesen. Jeder der folgenden Punkte ist jetzt nach Host-Id
partitioniert:

| Zustand | Wo | Warum es pro Host sein muss |
|---|---|---|
| Session-Records | `.tesserae/harness_sessions/` | Ein Host bereinigt nur, was er selbst geerntet hat. Sonst löscht Host B die Sessions von Host A und meldet Erfolg — der Scan jedes Hosts stempelt denselben Produzenten, und ihre `~/.claude`-Pfade lösen identisch auf, nichts sonst unterscheidet sie also |
| Engine-Pidfile | `.tesserae/daemon.<host>.pid` | Liveness ist `os.kill(pid, 0)` gegen die **lokale** Prozesstabelle; eine von einer anderen Maschine geschriebene PID wird gegen einen völlig unbeteiligten lokalen Prozess beurteilt |
| Codex-Scan-Untergrenze | `.tesserae/harness_sessions.db` | Eine einzige geteilte Wasserlinie führte dazu, dass der zuletzt gelaufene Host sie über Transkripte hinausschob, die der andere noch nicht gelesen hatte — die wurden nie importiert |

Die Host-Id wird einmalig nach `~/.tesserae/host_id` generiert (pro Maschine,
**nicht** im geteilten Projektverzeichnis) und lässt sich mit `TESSERAE_HOST_ID`
festnageln. Es ist eine persistierte Id statt des Hostnamens, weil eine Flotte
aus einem einzigen Image Hostnamen wiederverwendet und eine Kollision die Records
der einen Maschine an eine andere übergeben würde.

### Die Annahme, die Sie testen sollten

All das setzt voraus, dass `flock(2)` von dem Dateisystem, auf dem `.tesserae/`
liegt, auch **durchgesetzt** wird. Über NFS und SMB ist das
konfigurationsabhängig, und ohne funktionierenden Lock-Daemon kann `flock` still
zum No-op degradieren — woraufhin zwei Hosts dasselbe Projekt gleichzeitig
kompilieren und jeder von ihnen glaubt, einen exklusiven Lock zu halten.

`tesserae doctor` warnt, wenn das Projekt auf einem Netzwerk-Dateisystem liegt,
aber ein einzelner Host **kann** host-übergreifende Durchsetzung **nicht**
beweisen. Testen Sie es direkt auf der echten Hardware: halten Sie einen Lock auf
Host A und bestätigen Sie, dass Host B abgewiesen wird.

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

### Ein Graph, der vor den Nachbearbeitungsdurchläufen kompiliert wurde

Zwei Korrekturen ändern, wie ein kompilierter Graph aussieht, ohne zu ändern, was das
Modell extrahiert hat: ein Anker je Dokument statt einer je Chunk (ein chunkweise
kompilierter Aufsatz trug 9.4), und ein Knoten je Entitätsname statt einer je Schreibweise
und Typ. Beide laufen innerhalb von `compile`, sodass ein bereits auf der Platte liegender
Graph keines von beidem hat, bis er neu kompiliert wird. `graph-repair` wendet dieselben
Regeln auf die Graph-Bytes an — kein Modell, kein Netzwerk, Sekunden — und ein reparierter
Graph stimmt mit einem neu kompilierten überein.

```sh
tesserae graph-repair --dry-run     # berichtet, was sich ändern würde, und schreibt nichts
tesserae graph-repair               # schreibt .tesserae/graph.json an Ort und Stelle neu
```

Site und Vault sind Projektionen und werden hier nicht neu gebaut; führen Sie danach
`export site` aus, wenn Sie eine bereitstellen.

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

## Agenten schreiben in den Graphen

`graph_write` (MCP) nimmt schemavalidierte typisierte Knoten und Kanten mit verbindlicher Herkunft, sodass ein Agent einen Fund als *Struktur* statt als Prosa aufzeichnet, deren Typen ein Extraktor erraten muss.

Es lehnt ab statt zu erzwingen: Untypte Kanten, Knoten- oder Kantentypen außerhalb des kontrollierten Vokabulars, baumelnde Endpunkte und Schreibvorgänge ohne Herkunft werden alle abgelehnt. Doppelte Schreibvorgänge sind idempotent. Von Agenten geschriebene Knoten überleben eine vollständige Neukompilierung, gelöschtes `graph.json`, `--limit` und vollständige Corpus-Löschung.

## Eine Behauptung gegen den Graphen überprüfen

`verify_claim` (MCP) antwortet, ob der Graph ein Triple lizenziert. Es nimmt `(subject, predicate, object)` — **es gibt keinen Parameter in natürlicher Sprache**, absichtlich, weil ein Parser die vorherige Version dazu brachte, auf die Negation einer Behauptung, die sie unterstützte, mit SUPPORTED zu antworten.

Das Urteil ist eine reine Funktion der Graph-Bytes: kein LLM, keine Einbettung, nirgends auf dem Entscheidungsweg Fuzzy-Matching.

| Urteil | Bedeutung |
|---|---|
| `SUPPORTED` | die Kante existiert, trägt eigene Beweise und dieser Text wurde gegen die Quelldatei neu verankert |
| `PRESENT_UNEVIDENCED` | die Kante existiert, aber nichts Dokumentgestütztes unterstützt sie |
| `CONTRADICTED` | dokumentgestützte `contradicts_claim` zwischen denselben zwei Endpunkten |
| `DISPUTED_UNEVIDENCED` | behauptete Meinungsverschiedenheit, keine nachgewiesen |
| `CONFLICTING` | beide Polaritäten dokumentgestützt — das Tool lehnt es ab, zu entscheiden |
| `ABSENT` | dieser Graph behauptet das Triple nicht. Keine Widerlegung |
| `NOT_RESOLVABLE` | ein Endpunkt oder Prädikat kann nicht genau aufgelöst werden |

Es gibt zwei Dinge, die es absichtlich nicht tut. Es behandelt `supersedes` nie als Widerlegung — diese Beziehung sagt, dass ein *Knoten* ersetzt wurde, nicht dass ein Triple falsch ist. Und ein Agent-Write kann nur eine Herkunftsklasse *schwächen*, niemals eine aktualisieren, also kann nichts, das ein Agent behauptet, als dokumentgestützt dargestellt werden.

Es ist wissenswert beim Lesen von Ergebnissen: auf einem echten Graph mit 15.284 Kanten sind etwa 40% der `SUPPORTED`-Urteile tautologisch — `evidenced_by`-Kanten, deren zitierter Span das eigene Ziel der Kante ist. Wahr, aber nicht informativ.

## Eine Frage weiterleiten

`tesserae ask` wählt einen Abrufpfad nach Frageform aus: Einfache Entitätssuchen gehen zu günstig Backend, Multi-Hop / "was hat sich geändert" / "warum" / Corpus-breite Fragen gehen zum Graph. Diese Aufteilung kodiert eine **Hypothese, keine Messung**: Wir erwarten, dass die Traversierung ihre Kosten bei Multi-Hop-, Zeit- und Synthesefragen einspielt und sie bei einfacher Faktsuche verschwendet. Nichts in diesem Repository prüft das — es gibt hier keinen Retrieval-Benchmark und keine veröffentlichte Zahl hinter der Routing-Tabelle, also behandeln Sie sie als überschreibbaren Standardwert, nicht als Ergebnis.

Die Entscheidung wird in dem zurückgegebenen Umschlag angezeigt, daher ist eine billige Antwort prüfbar. Überschreiben Sie es mit `--route` auf der CLI oder dem Parameter `route` im MCP-Tool.

REGELN:
- NICHT übersetzen: graph_write, verify_claim, SUPPORTED, PRESENT_UNEVIDENCED, CONTRADICTED, DISPUTED_UNEVIDENCED, CONFLICTING, ABSENT, NOT_RESOLVABLE, supersedes, contradicts_claim, evidenced_by, subject, predicate, object, MCP, --route
- Behalten Sie alle Zahlen genau bei: 15.284, 40 %
- Behalten Sie die Tabellenstruktur mit denselben Spaltenkopfzeilen bei
- Übersetzen Sie die Prosa für jede Sprache natürlich
- Fügen Sie am Ende jeder Datei an, ohne vorhandene Inhalte zu stören
