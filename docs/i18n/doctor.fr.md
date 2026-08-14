# `tesserae doctor` — bilans de santé du projet

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` inspecte un espace de travail Tesserae de bout en bout —
initialisation, intégrité du graphe, cohérence du registre, fraîcheur, verrous,
connexion LLM et hygiène disque — et affiche une checklist. Il est **en lecture
seule par défaut** ; `--fix` n’applique que les réparations sûres à relancer et
ne peut jamais détruire un état vivant.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## Ce qu’il vérifie

Les vérifications, regroupées par catégorie :

| Vérification | Catégorie | Ce qu’elle vérifie | Action `--fix` |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` existe et ressemble à un espace de travail Tesserae | rapport seulement (suggère `tesserae init`) |
| `graph_parse` | core | `graph.json` se parse et a la forme attendue | rapport seulement (suggère `tesserae compile`) |
| `config_valid` | core | `.tesserae/config.json` se parse et se valide contre le modèle d’init | rapport seulement |
| `vault_configured` | core | le chemin de vault configuré se résout | **SAFE** : crée le répertoire de vault résolu lorsqu’il vit à l’intérieur du projet |
| `registry_consistent` | registry | les entrées de `~/.tesserae/registry.json` pointent vers de vraies racines de projet | **SAFE** : élague les entrées dont la racine a disparu, supprime la clé héritée `active` ; un graphe manquant reste rapport seulement |
| `graph_staleness` | freshness | delta git depuis le `git_head` enregistré par la dernière compilation | rapport seulement (suggère `tesserae refresh` — les compilations sont lourdes) |
| `site_search_index` | freshness | le site statique / `search-index.json` est plus récent que `graph.json` | **SAFE** : reconstruit le site |
| `backend_artifacts` | freshness | les artefacts RAG-Anything sont à jour | rapport seulement (leur rafraîchissement est lourd en LLM/réseau) |
| `session_chunks` | freshness | la couverture des [chunks de session quotidiens](session-chunks.fr.md) n’a pas de trous dans la fenêtre récente | rapport seulement (suggère `tesserae sessions chunk-backfill`) |
| `wiki_lint` | graph | dérive graphe ⇄ wiki + constats de lint trivialement corrigeables | **SAFE** : applique les corrections triviales du lint (`fix_trivial`) |
| `compile_lock` | processes | si un verrou de compilation vivant est détenu, et par quel pid **et quel hôte** | rapport seulement — doctor **ne tue jamais et ne supprime jamais un verrou vivant** |
| `filesystem_locking` | processes | si `.tesserae/` se trouve sur un système de fichiers réseau, où `flock(2)` peut être un no-op silencieux | rapport seulement (il ne peut pas prouver l’application inter-hôtes — voir plus bas) |
| `daemon_pid` | processes | `daemon.<host>.pid` pointe vers un processus moteur vivant | **SAFE** : supprime le pidfile **de cet hôte** quand son propriétaire est mort ; celui d’une autre machine est rapporté, jamais touché |
| `llm_login` | environment | si les répertoires de config que le projet utiliserait réellement existent | rapport seulement — **ne vérifie pas les identifiants** (voir plus bas) |
| `optional_deps` | environment | statut des dépendances optionnelles (memex, raganything) | rapport seulement (les installations passent par le réseau) |
| `embedding_backend` | environment | un vrai backend d’embeddings sémantiques est disponible | rapport seulement (suggère `pip install tesserae[semantic]`) |
| `environment` | environment | résumé de détection d’environnement en bloc | section rapport seulement |
| `build_history` | hygiene | taille et forme de `.build-history` | **SAFE** : le tronque, en préservant toujours l’entrée `git_head` la plus récente (la vérification de staleness en dépend) |
| `idempotence` | hygiene | le fil-piège `idempotence_suspect` du snapshot de sortie | rapport seulement (c’est un signal de bug, pas quelque chose à auto-réparer) |
| `orphan_worktrees` | hygiene | enregistrements `git worktree` périmés | **SAFE** : `git worktree prune` ; la suppression de répertoires reste rapport seulement |
| `hook_log_bloat` | hygiene | croissance de `.tesserae/.session-*-hook.log` | **SAFE** : fait tourner/tronque les logs au-delà de 10 Mo |
| `sidecars` | hygiene | entrées de `.tesserae/` face au registre des sidecars (`tesserae/sidecars.py`) : `*.tmp.<pid>.<hex>` orphelins, copies manuelles `graph.json.bak-*`, entrées non classées | **SAFE** : supprime uniquement les fichiers tmp orphelins dont le pid écrivain est mort et vieux de plus de 24 h ; sauvegardes et non classés en rapport seulement |
| `code_scope_leftovers` | hygiene | restes de la couche de code retirée : `code-graph*.json`, lignes de types de code dans `sqlite.db` | rapport seulement — le nettoyage est une suppression de masse, il vit donc sur son propre verbe (voir ci-dessous) |

Une vérification qui plante est rapportée comme un constat d’erreur — doctor lui-même ne lève jamais d’exception.

## Ce que `llm_login` dit, et ce qu’il ne dit pas

Il rapporte qu’un répertoire de config existe. Il ne rapporte **pas** que la CLI
qui s’y trouve détient un jeton valide, et il le dit dans le texte de son propre
constat.

La distinction n’est pas de la pédanterie. La vérification rapportait autrefois
`credentialed LLM CLI: claude, codex` sur la foi de fichiers comme
`~/.claude/history.jsonl` — qui prouvent que la CLI a été *utilisée*, pas qu’elle
peut s’authentifier *maintenant*. Lancés coup sur coup dans la même seconde,
`tesserae compile` affichait `Claude CLI not logged in (tried 1 config dir)`
pendant que doctor affichait une coche verte. Un diagnostic qui contredit la
panne dans laquelle vous êtes est pire que pas de diagnostic du tout.

Vérifier les identifiants revient à dépenser un vrai appel LLM à chaque
`tesserae doctor`, un coût que cette vérification ne prend pas de sa propre
initiative. Elle n’énonce donc que ce qu’elle a vérifié. Pour la réponse qui fait
autorité, utilisez `tesserae compile`.

La vérification est limitée aux répertoires que le projet essaierait réellement,
résolus par le même chemin qu’emprunte `ProjectWiki._build_json_client` — et elle
ne dit rien des répertoires de config claude quand le fournisseur du projet est
`codex`.

## Disques partagés et `flock(2)`

Toute garantie de concurrence dans Tesserae — le verrou de compilation avant tout
— repose sur le fait que `flock(2)` soit réellement appliqué par le système de
fichiers qui héberge `.tesserae/`. En NFS et SMB, cela dépend de la configuration :
sans lock daemon opérationnel, `flock` peut se dégrader silencieusement en no-op,
et deux hôtes compileront alors le même projet en même temps en croyant chacun
détenir un verrou exclusif.

`filesystem_locking` rapporte ce qu’un seul hôte peut déterminer : le type de
système de fichiers qui porte le projet, s’il s’agit d’un système de fichiers
réseau, et si une acquisition `flock` réussit tout court. Il avertit sur un
système de fichiers réseau.

Il **ne peut pas** prouver l’application inter-hôtes, et ne prétend pas le faire.
Qu’un hôte prenne un verrou ne dit rien sur le fait qu’un second hôte soit
empêché de le prendre. Si vous faites tourner Tesserae depuis plusieurs machines
sur un stockage partagé, testez-le directement sur le matériel réel avant de vous
fier au verrou de compilation.

## `tesserae doctor migrate-code-scope`

Un nettoyage ponctuel pour un espace de travail compilé avant que le code source ne
sorte du périmètre de Tesserae. Les nouvelles compilations ne produisent plus la
couche de code, mais un espace de travail plus ancien la porte encore, et l'essentiel
ne se résorbe que si vous le demandez.

```bash
tesserae doctor migrate-code-scope            # simulation — rapporte, ne supprime rien
tesserae doctor migrate-code-scope --apply    # supprime réellement
```

Supprime, dans cet ordre :

* les pages projetées sous `.tesserae/markdown_projection/` dont le frontmatter
  `type:` propre nomme un type de code retiré ;
* les mêmes pages dans le coffre Obsidian — celui configuré comme celui par défaut
  dans le projet, car un projet ayant ensuite pointé vers un vrai coffre laisse
  l'ancien tel quel, plein de ces pages. Une page de code dont le contenu
  `user-notes` n'est pas vide est conservée et comptée, jamais supprimée ;
* `code-graph.json` et `code-graph-cache.json` ;
* les lignes des tables annexes SQLite (`node_provenance`, `edge_provenance`,
  `node_memory`) dont le nœud ou l'arête n'existe plus, puis `VACUUM`.

Deux choses à savoir.

**Lisez le nombre de survivants, pas le nombre de suppressions.** Le répertoire de
projection est massivement dérivé du code — mesuré ici, 218 796 pages sur 224 876 —
si bien qu'un bug de prédicat qui supprimerait tout et une exécution correcte se
ressemblent presque au nombre de suppressions près. Le rapport commence par le
nombre de pages non-code qui survivent, précisément le nombre qui s'effondrerait si
le prédicat était faux. La décision se prend strictement fichier par fichier, sur
son propre frontmatter.

**Compilez d'abord, migrez ensuite.** Les tables `nodes` / `edges` et les annexes de
provenance sont réécrites à chaque compilation : c'est donc la compilation qui rend
ces lignes caduques, et ce verbe qui récupère la place, car SQLite ne rétrécit pas
avec `DELETE`. L'exécuter avant est sans danger — il le dit et ne trouve rien à
récupérer. `VACUUM` n'est jamais exécuté au sein d'une compilation : il prend un
verrou exclusif et exige un espace libre de l'ordre du fichier de base, et il est
ignoré avec une note quand le disque ne peut pas encaisser la reconstruction.

Il est délibérément hors d'atteinte de `--fix`, documenté comme réparations sûres
uniquement.

## Politique `--fix`

- `--fix` exécute **uniquement** les vérifications marquées SAFE ci-dessus, puis
  re-détecte pour que le rapport reflète l’état après correction.
- Chaque correction est idempotente : lancer `doctor --fix` deux fois laisse le
  second passage propre.
- Doctor **ne tue jamais un processus et ne supprime jamais un verrou de
  compilation vivant** — un verrou détenu est rapporté avec le pid et l’hôte qui
  le détiennent, et laissé tranquille.
- Doctor **ne touche jamais au pidfile d’une autre machine.** Sur un stockage
  partagé, la table des processus locale ne dit rien d’un pid écrit par un autre
  hôte, donc `daemon.<other-host>.pid` est rapporté et sauté inconditionnellement
  — il n’est même pas lu pour tester la vivacité. Seul le pidfile de cet hôte-ci
  peut être supprimé.
- Les opérations lourdes ou réseau (recompilations, installations de
  dépendances, rafraîchissements de backend) ne sont jamais intégrées à
  `--fix` ; doctor affiche la commande à lancer vous-même.

## Codes de sortie

Même convention que `tesserae lint` :

| Code de sortie | Signification |
|---|---|
| `0` | sain — aucun constat au-dessus de OK |
| `1` | avertissements présents |
| `2` | erreurs présentes |

## `tesserae lint` — les codes de constat

`doctor` n'exécute que le sous-ensemble trivialement réparable du lint ;
`tesserae lint` exécute l'ensemble complet, et c'est là que vivent les détails.
Chaque constat porte un code stable : vous pouvez donc grepper un rapport ou
conditionner la CI à l'un d'eux. `--severity {info,warning,error}` fixe le seuil
du **code de sortie** — les constats en dessous restent rapportés.

| Code | Sévérité | Signification |
|---|---|---|
| `AGENT_METADATA_KEY` | error | Un nœud d'agent porte une clé de métadonnées hors de l'ensemble contrôlé. Le seul code de niveau erreur ; un agent malformé casse les vues restreintes. |
| `ORPHAN_PAPER` | warning | Un Paper sans arête sortante et sans rien d'autre que `mentioned_in` en entrée — ingéré, jamais relié. |
| `MISSING_IMPLEMENTED_IN` | warning | Un Paper et un Repository partagent un `arxiv_id` mais aucune arête `implemented_in` ne les relie. `--fix-trivial` l'ajoute. |
| `STALE_CITATION` | warning | Une page wiki pointe vers une page qui n'existe pas. |
| `DANGLING_HTML_LINK` | warning | Le HTML généré pointe vers un fichier absent. |
| `GRAPH_WIKI_DRIFT` | warning | Le graphe et le wiki divergent — un nœud public sans page, ou une page sans nœud. |
| `CONTRADICTING_CLAIMS` | warning · info | Deux affirmations se contredisaient ; indique comment cela a été tranché. |
| `REASONING_EDGE_RATIO` | warning | Trop peu d'arêtes portent du raisonnement. Un graphe de `mentions` nus est un index de recherche, pas une base de connaissances. |
| `SYNTHESIS_GHOST_INPUT` | warning | Le frontmatter d'une synthèse cite un id de nœud qui n'existe plus. `--fix-trivial` l'élague. |
| `AGENT_FORGET_LEDGER` | warning | La dernière distillation a rétrogradé des constats — le registre de ce qu'un agent a cessé de faire remonter. |
| `INTERVAL_COVERAGE` | info | *Combien de faits n'ont pas de `valid_from`* et se retrouvent donc en fin de tri dans toute réponse temporelle. Auparavant silencieux, désormais énoncé en pourcentage. |
| `LINT_PROBE_FAILED` | info | `INTERVAL_COVERAGE` n'a pas pu tourner car le graphe ne se chargeait pas — le contrôle s'abstient, et on le dit au lieu de le compter comme réussi. |
| `PROCEDURAL_POOLS` | info | Quelle part de la couche procédurale détenue par les producteurs a réellement été créée. La place procédurale réservée se mérite par la provenance ; ce code signale quand elle ne peut pas être remplie honnêtement. |
| `AGENT_UNDISTILLED_BACKLOG` | info | Un agent a accumulé des constats bien au-delà de sa marque de distillation. |
| `LOW_TITLE_QUALITY` | info | Le titre d'un Paper ressemble à un nom de fichier ou à un fragment plutôt qu'à un titre. |
| `SUGGESTED_MERGE` | info | Plusieurs nœuds Repository partagent une URL `github_repo` — candidats à la fusion, jamais fusionnés automatiquement. |
| `SUGGESTED_SUBTYPE` | info | Un cluster de nœuds du même type pour lequel schema-drift a proposé un sous-type — surfacé, jamais adopté automatiquement. La promotion est une édition manuelle de `ResearchNodeType`, puis `"approved": true` dans `.tesserae/schema-drift-proposals.json`. |
| `PENDING_REVIEW` | info | Paires candidates à la fusion qui attendent encore un verdict humain dans `.tesserae/candidate-same-as.json`. Une paire rejetée par un relecteur n'est plus jamais proposée : ce nombre mesure le travail en suspens, pas la taille du corpus. Répondez avec `tesserae extract --apply-review-decisions … --reviewed-by <vous>`. |
| `STALE_BUILD_HISTORY` | info | Une entrée d'historique de build de plus de 90 jours. |
| `CODE_GRAPH_BEHIND` · `CODE_GRAPH_HEAD_UNRESOLVED` · `CODE_GRAPH_STALE_FILE` | info | La couche code optionnelle est désynchronisée de `HEAD` — compilée sur un commit plus ancien, sur un commit que git ne résout plus, ou sur des fichiers modifiés depuis. |
| `CLAIM_SUPPORT_SKIPPED` · `CLAIM_SUPPORT_SUMMARY` | info | Résultats de la passe optionnelle `--verify-claims` : ce qui a été échantillonné et noté, ou pourquoi elle n'a pas tourné. |

`--fix-trivial` n'applique que les réparations sûres (`MISSING_IMPLEMENTED_IN`,
`SYNTHESIS_GHOST_INPUT`). Le reste est rapporté pour qu'un humain tranche.
`--verify-claims` est optionnel, requiert un backend LLM et coûte un appel
groupé.

## Artefacts de rapport

Chaque exécution écrit les deux formes de rapport dans l’espace de travail :

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` affiche en plus le rapport JSON sur stdout à la place de la checklist
markdown. `--all` itère sur chaque projet du registre (en ignorant
`--project`) et rapporte projet par projet.

## MCP : `doctor_report`

Le serveur MCP expose le même rapport via l’outil `doctor_report` (à l’image de
`lint_report`, y compris son plafond d’octets sur le contenu retourné), afin
qu’un agent puisse vérifier la santé de l’espace de travail en pleine
conversation sans passer par le shell. Il requiert une racine de projet —
passez `graph_path`/`project` ou configurez un graphe par défaut.
