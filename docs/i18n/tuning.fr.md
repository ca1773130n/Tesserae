# Référence d'ajustement — variables d'environnement

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Chaque paramètre que Tesserae lit depuis l'environnement, sa valeur par défaut
et quand vous voudriez vraiment le modifier. Rien ici n'est obligatoire : les
valeurs par défaut sont choisies pour qu'une simple `tesserae compile` fonctionne
correctement.

La configuration du projet et la configuration globale (`.tesserae/config.json`,
`~/.tesserae/config.json`) prennent la priorité sur les paramètres du serveur LLM ;
les variables d'environnement ci-dessous les remplacent dans l'exécution où elles
sont définies.

---

## Crochets qui dépensent de l'argent

Le plugin Claude Code est livré avec des crochets qui peuvent lancer une compilation en arrière-plan. Tout ce qui dépense est **désactivé par défaut** :

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # opt in to automatic recompiles
```

Protégés : `posttooluse-edit.sh` (se déclenche à chaque Edit/Write) et `session-end.sh`.
Non protégés, car ils ne coûtent rien : `session-start.sh` exécute `tesserae code
sync`, qui est déterministe, et `pretooluse-compile.sh` n'intercepte qu'une
`tesserae compile` que vous avez tapée vous-même.

Cette valeur par défaut existe parce que l'alternative a été mesurée. Une base de connaissances à
`~/.tesserae` fait que `$HOME` ressemble à une racine de projet, et le résolveur de crochet
remontait *vers le haut* à partir du répertoire de travail jusqu'au premier `.tesserae/` trouvé — donc
toute session en dehors d'un projet enregistré se résolvait en `$HOME` et compilait
le répertoire personnel entier : 15k fichiers, un graphique de 795 MB, **~10 heures de dépense LLM**,
à partir d'un processus détaché qui a survécu à la session qui l'a lancé.

`resolve_project_root()` refuse maintenant `$HOME` par l'une ou l'autre voie, et retourne vide
plutôt que de revenir au répertoire de travail, donc les appelants ne font rien au lieu de
deviner. Un crochet qui lance en arrière-plan du travail modèle devrait être activé délibérément,
pas désactivé après l'arrivée de la facture.

---

## Extraction

### `TESSERAE_EXTRACT_TIMEOUT`

**Par défaut `1800` (secondes), par tentative.** Délimite chaque appel
d'extraction codex/claude pour qu'un processus enfant bloqué ne puisse pas
bloquer la compilation.

C'est arrivé : une compilation a été observée à 0% CPU pendant **5 h 43 m**
avec un processus enfant `codex exec` inactif pendant **4 h 6 m**, maintenant
`.tesserae/compile.lock` tout du long. Il avait déjà construit 32 résumés
de communauté en mémoire et n'a jamais pu les persister.

Par tentative, pas par document — en cas de timeout, le client bascule vers
le répertoire de configuration suivant `CODEX_HOME` / claude, donc le pire cas
pour un document est `timeout × profils configurés`.

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # plus de temps pour très gros documents
export TESSERAE_EXTRACT_TIMEOUT=0      # pas de limite — exécuter jusqu'à fin
```

Une valeur définie mais inutilisable (`10m`, `600s`, négatif, `inf`) affiche
un avertissement sur stderr et conserve la valeur par défaut. Une faute de frappe
ne doit pas désactiver silencieusement un clapet de sécurité.

### `TESSERAE_EXTRACT_CONCURRENCY`

**Par défaut `4`.** Documents extraits en parallèle. Chacun est un processus
enfant CLI bloquant prenant environ une minute, donc une boucle séquentielle
fait du temps réel la somme littérale de chaque aller-retour du modèle —
mesurée à ~2 h 40 m pour 161 documents.

Le plafond est la limite de débit du compte de votre fournisseur, pas votre
machine, c'est pourquoi la valeur par défaut est modeste. Définissez `1` pour
un comportement strictement séquentiel.

La concurrence ne change jamais le résultat : la liste de travail est fixée en
ordre de chemin et les résultats sont collectés par index, donc une exécution
parallèle est identique octet-par-octet à une séquentielle.

### `TESSERAE_LLM_CACHE`

**Activé par défaut.** Cache adressable par contenu des réponses du fournisseur
CLI sous `~/.tesserae/llm_cache`, reposant sur un résumé du message d'invite
réellement envoyé, plus le modèle et l'effort de raisonnement — ainsi une
question différente re-demande, et changer de modèle re-demande au lieu de
servir les réponses du modèle précédent. Seules les réponses parseables sont
stockées, donc une mauvaise génération ne peut pas devenir permanente.

Les entrées plus anciennes sont inaccessibles par conception : la clé était
auparavant une étiquette fournie par l'étape appelante plutôt qu'un résumé du
message d'invite, ainsi des questions sans rapport pouvaient partager une entrée.
Rien ne les migre — le répertoire peut être supprimé sans risque, et une
compilation le remplira à nouveau.

```sh
export TESSERAE_LLM_CACHE=0   # toujours re-demander
```

### `TESSERAE_LLM_CHUNK_CHARS`

Caractères par fragment quand un document est trop volumineux pour un appel.
Laissez non défini sauf si vous frappez les limites de contexte.

---

## Serveur LLM

| Variable | Par défaut | Remarques |
|---|---|---|
| `TESSERAE_LLM_PROVIDER` | `claude` | `codex`, `claude`, `anthropic`, `custom` |
| `TESSERAE_LLM_MODEL` | spécifique au fournisseur | Limité par fournisseur pour qu'un modèle de type claude n'atterrisse jamais sur le chemin codex |
| `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | L'extraction structurée ne nécessite pas le `xhigh` que vous pourriez définir pour un travail interactif — `xhigh` rend une compilation multi-documents plusieurs fois plus lente |
| `TESSERAE_CLAUDE_CONFIG_DIRS` | — | Répertoires de configuration Claude séparés par `os.pathsep`, dans l'ordre de rotation — le canal d'environnement pour un `--claude-config-dir` répété. Seule une liste *configurée* fait autorité ; le `CLAUDE_CONFIG_DIR` ambiant délibérément pas, car s'y épingler réduit la rotation multi-comptes à un seul compte |

`tesserae config status` affiche le serveur résolu et le vérifie pour vérifier qu'il répond.

---

## Passes de compilation

| Variable | Par défaut | Ce qu'elle contrôle |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **activé** | Passe de résumé style GraphRAG. Un appel LLM par cluster ≥ 5 membres, mis en cache par résumé d'adhésion. Désactiver avec `false`/`0`/`no`/`off` |
| `TESSERAE_ENABLE_LLM_PASSES` | désactivé | Passes d'enrichissement LLM optionnelles au-delà de l'extraction |
| `TESSERAE_AGENT_DISTILL` | désactivé | Artefacts d'expertise L1 par agent (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | désactivé | Nœuds de mémoire distillée Runbook/Gotcha |
| `TESSERAE_SESSION_EVENT_PASS` | **activé** | Nœuds `Event` par tour issus des transcriptions de session. Sans LLM et déterministe à l'octet près, mais un nœud par tour significatif — volumineux sur un corpus long. `false`/`0`/`no`/`off` le désactive |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | activé | Lie les insights de session aux symboles de code |
| `TESSERAE_SUPERSEDE_PASS` | activé | Arêtes `superseded_by` entre affirmations révisées |
| `TESSERAE_PROMPT_SIGNATURES` | désactivé | Enregistre les signatures de requête pour la détection de dérive |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | Secondes à attendre `.tesserae/compile.lock` avant d'abandonner |

**À propos des résumés de communauté :** la passe de compilation couvre
précipitamment le niveau le plus grossier ; `graph_map` matérialise en plus
lazily un résumé la première fois que vous descendez dans une portée froide,
mis en cache par niveau. Désactiver la passe est une stratégie de coûts légitime
— vous ne payez que pour les branches que vous visitez réellement — avec une
mise en garde : **la descente fédérée ne matérialise jamais lazily.** Les cartes
d'un projet frère ne peuvent être nommées que à partir de ses résumés dans le
graphe ou de caches déjà chauds, donc un projet que vous naviguez entre projets
veut la passe hâtive activée.

---

## Requête et synthèse

| Variable | Par défaut | Remarques |
|---|---|---|
| `TESSERAE_QUERY_LLM` | désactivé | Planificateur LLM pour `tesserae query` |
| `TESSERAE_QUERY_DRY_RUN` | désactivé | Plan sans appeler le modèle |
| `TESSERAE_SYNTHESIS_LLM` | désactivé | Synthèse en prose dans `tesserae ask` |
| `TESSERAE_SYNTHESIS_MODEL` | — | Remplace le modèle de synthèse |
| `TESSERAE_SYNTHESIS_WORKERS` | — | Travailleurs de synthèse parallèles |
| `TESSERAE_SYNTHESIS_DRY_RUN` | désactivé | Ignorer le modèle, exécuter le pipeline |
| `TESSERAE_VERIFY_BAND` | désactivé | Faire retrancher par le modèle les signalements incertains de `ask`. `on` prend la bande mesurée 0.30–0.70 ; `lo-hi` la remplace. Désactivé, les signalements ne coûtent ni jetons ni réseau |

### `TESSERAE_VERIFY_BAND`

Chaque réponse d'`ask` porte des signalements de relecture par phrase qui ne coûtent
rien. Ils sont moins exacts que d'interroger un modèle — 0.870 contre 0.926 sur 755
phrases tenues à l'écart — et presque tout l'écart vient de fausses alertes sur des
paraphrases fidèles, qui partagent peu de vocabulaire avec leur source.

Les deux se trompent sur des phrases différentes : payer le modèle uniquement là où la
vérification gratuite hésite récupère donc l'exactitude pour une fraction du coût.
Céder la couverture 0.30–0.70 a donné 0.932 sur 42% des appels : indiscernable d'une
interrogation phrase par phrase (McNemar p=0.52), pour 42% de la dépense.

```bash
export TESSERAE_VERIFY_BAND=on          # la bande mesurée 0.30-0.70
export TESSERAE_VERIFY_BAND=0.40-0.60   # plus étroite : 22% des appels, 0.914
```

Désactivé par défaut, car ces signalements sont documentés comme ne coûtant ni jetons
ni réseau, et une cascade qui s'activerait d'elle-même romprait cette promesse pour
tout appelant. L'enveloppe rapporte `adjudicated` : `null` quand la cascade n'a pas
tourné, un décompte quand elle a tourné. Un modèle incapable de répondre laisse le
verdict gratuit en place — un appel raté ne peut jamais rendre propre une phrase
signalée.

---

## Chemins et infrastructure

| Variable | Par défaut | Remarques |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Emplacement du registre des projets. Honoré par **toutes** les commandes — jusqu'à 0.28.7, seul le mode flotte du moteur le lisait, donc le définir ailleurs restait silencieusement sans effet et les commandes continuaient d'utiliser le vrai registre |
| `TESSERAE_HOST_ID` | généré une seule fois dans `~/.tesserae/host_id` | L'identité de cette machine. Voir [faire tourner plusieurs machines](#faire-tourner-plusieurs-machines-sur-un-seul-projet) |
| `TESSERAE_DISCOVERY_CACHE` | — | Cache de découverte de session |
| `TESSERAE_ARXIV_CACHE` | — | Cache de métadonnées arXiv |
| `TESSERAE_NO_FEDERATION_CACHE` | désactivé | Désactive le LRU du graphe fédéré |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | désactivé | Émet le graphe combiné entre projets |
| `TESSERAE_FLEET_PIDFILE` | — | Fichier pidfile de la flotte du moteur |
| `TESSERAE_CLIP_TOKEN` | — | Secret partagé pour le presse-papiers Web |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | désactivé | Applique les enregistrements **approved** de `.tesserae/schema-drift-proposals.json` à la compilation (déterministe, pas de LLM). Écrivez les propositions avec `tesserae schema-drift` ; approuver une proposition signifie éditer `ResearchNodeType` en premier, puis définir `"approved": true` — un nom non résolvable ne retype rien. |

---

## Qui a lu le graphe

| Variable | Par défaut | Remarques |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **désactivé** | Enregistre les lectures qui déplacent les comptages d'accès — `{tool, actor, node_ids, at, tesserae_version}` — dans une table `read_audit` dans `.tesserae/sqlite.db`, relue via l'outil `read_audit` avec un décompte par acteur. Une ligne est écrite partout qu'un comptage d'accès soit incrémenté, donc le compte de lignes suit la surface plutôt que l'appel : un outil qui surface une liste de nœuds (`search_nodes`, `node_context`, `compile_context`, `graph_map`, `graph_ppr`, `ask` / `query`, `drill_down`, `find_session_findings`) écrit **une ligne par appel** nommant chaque nœud qu'il a compté, tandis que `fresh_insights` incrémente dans sa propre boucle et écrit donc **une ligne par nœud** qu'il a surfacé. Un appel qui ne surface rien n'écrit rien, et un outil qui ne lit aucun nœud — `schema`, `graph_summary` — ne touche jamais l'audit, car une ligne sans nœud n'explique pas de comptage d'accès. Désactivé par défaut car un audit toujours actif sur chaque surface de lecture transforme chaque lecture en écriture ; la porte se tient avant l'ouverture du store, car créer la table est elle-même une écriture. Rien ne sort jamais de `graph.json` |
| `TESSERAE_ACTOR` | — | Qui attribuer une lecture à quand l'appel ne porte pas de vue d'agent. L'acteur est l'argument `agent` si l'appel en a résolu un, autrement celui-ci ; non défini enregistre la lecture comme anonyme plutôt que d'inventer un nom |

Éteindre `TESSERAE_READ_AUDIT` arrête l'enregistrement sans effacer ce qui
a déjà été enregistré, et ça prend effet sans redémarrer le serveur. Ce pour quoi
l'audit existe *est* [oublier par non-utilisation](agent-memory.fr.md#oubli--jamais-suppression):
les compteurs d'accès pilotent ce qui est absorbé ou rétrogradé, et sans acteur un
agent bavard qui sonde un nœud et un humain le lisant une fois sont la même entrée.

---

## Faire tourner plusieurs machines sur un seul projet

La configuration visée : plusieurs serveurs exécutent chacun un agent de code,
chacun a ses propres transcriptions de session locales, et ils partagent un
disque — ils voient donc le même répertoire de projet et le même `.tesserae/`.

**Confiez la compilation à un seul hôte, et laissez les autres se contenter de
moissonner.**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` suit en continu les transcriptions locales de cette machine vers
le magasin de sessions partagé et ne prend jamais le verrou de compilation du
projet. Cela supprime la contention au lieu de l'arbitrer, et c'est pourquoi
c'est meilleur que d'ajuster des délais d'attente.

**Quand vous voulez au contraire faire la queue plutôt qu'échouer**, passez
`--wait` :

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

Sans lui, une compilation qui trouve le verrou détenu sort en 2 — correct pour un
crochet, exaspérant pour un humain. `--wait` est un drapeau plutôt qu'une
déduction à partir du fait que stdout soit un terminal, parce que la même
commande ne doit pas changer de comportement sous `tee`, dans une capture tmux ou
en CI. `TESSERAE_COMPILE_LOCK_WAIT=<seconds>` fait la même chose pour tout un
arbre de processus.

**Garder tous les projets à jour** depuis une seule invocation :

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

L'échec d'un projet n'arrête pas les autres. Sortie `2` si l'un d'eux a échoué,
`1` si l'un d'eux était verrouillé par une autre exécution, `0` si tout s'est
exécuté. `--jobs` vaut 1 par défaut parce qu'une compilation est lourde en LLM et
que l'augmenter dépense du quota en parallèle.

### Ce qui rend cela sûr

L'état propre à chaque machine était auparavant stocké sous un seul nom partagé
et lu par tous les hôtes. Chacun des éléments suivants est désormais partitionné
par id d'hôte :

| État | Où | Pourquoi ce doit être par hôte |
|---|---|---|
| Enregistrements de session | `.tesserae/harness_sessions/` | Un hôte n'élague que ce qu'il a moissonné. Sinon l'hôte B supprime les sessions de l'hôte A et annonce un succès — l'analyse de chaque hôte appose le même producteur et leurs chemins `~/.claude` se résolvent à l'identique, donc rien d'autre ne les distingue |
| Pidfile du moteur | `.tesserae/daemon.<host>.pid` | La vivacité est un `os.kill(pid, 0)` contre la table des processus **locale** ; un pid écrit par une autre machine est jugé contre un processus local sans rapport |
| Plancher de scan Codex | `.tesserae/harness_sessions.db` | Une seule borne partagée signifiait que l'hôte ayant tourné en dernier la déplaçait au-delà des transcriptions que l'autre n'avait pas lues — celles-là n'ont jamais été importées du tout |

L'id d'hôte est généré une seule fois dans `~/.tesserae/host_id` (par machine,
**pas** dans le répertoire de projet partagé) et peut être fixé avec
`TESSERAE_HOST_ID`. C'est un id persisté plutôt que le nom d'hôte parce qu'une
flotte construite à partir d'une même image réutilise les noms d'hôte, et qu'une
collision livrerait les enregistrements d'une machine à une autre.

### L'hypothèse que vous devriez tester

Tout ce qui précède suppose que `flock(2)` est **réellement appliqué** par le
système de fichiers qui héberge `.tesserae/`. En NFS et SMB, cela dépend de la
configuration, et sans lock daemon opérationnel `flock` peut se dégrader
silencieusement en no-op — moment à partir duquel deux hôtes compilent le même
projet simultanément, chacun croyant détenir un verrou exclusif.

`tesserae doctor` avertit quand le projet se trouve sur un système de fichiers
réseau, mais un seul hôte **ne peut pas** prouver l'application inter-hôtes.
Testez-le directement sur le matériel réel : détenez un verrou sur l'hôte A et
vérifiez que l'hôte B se le voit refuser.

---

## Récupération d'un corpus dégradé

Quand l'extraction échoue pour un document, il est servi par la ligne de base
déterministe et **marqué** dans `.tesserae/manifest.json`. Sans la marque, il
serait indistinguishable d'une extraction propre, donc `--changed-only` le
sauterait à jamais et la dégradation serait permanente jusqu'à ce que le
contenu du fichier change.

```sh
tesserae compile --changed-only --retry-fallbacks
```

Re-tente seulement les documents marqués ; les propres restent sautés.

## Inspection de la hiérarchie

```sh
tesserae graph-map                          # carte racine
tesserae graph-map --scope <scope_id>       # descendre
tesserae graph-map --scope '<alias>::'      # un projet frère enregistré
```

Chaque carte rapporte `size` et `leaf_member_count` du fichier annexe de
hiérarchie, plus `live_member_count` — combien de membres le graphe *actuel*
porte réellement. Un `0` là-bas signifie que la portée est morte
(anomalie annexe/graphe) : sautez-la plutôt que de descendre.

## Les agents écrivent dans le graphique

`graph_write` (MCP) prend des nœuds et des arêtes typés validés par schéma avec provenance obligatoire, de sorte qu'un agent enregistre une découverte en tant que *structure* plutôt que comme prose qu'un extracteur doit deviner les types.

Il refuse plutôt que de coercer : les arêtes non typées, les types de nœud ou d'arête en dehors du vocabulaire contrôlé, les extrémités flottantes et les écritures sans provenance sont tous rejetés. Les écritures dupliquées sont idempotentes. Les nœuds écrits par un agent survivent à une recompilation complète, `graph.json` supprimé, `--limit` et suppression complète du corpus.

## Vérifier une affirmation contre le graphique

`verify_claim` (MCP) répond si le graphique autorise un triple. Il prend `(subject, predicate, object)` — **il n'y a pas de paramètre en langage naturel**, par conception, car un parseur a fait que la version précédente réponde SUPPORTED à la négation d'une affirmation qu'elle soutenait.

Le verdict est une fonction pure des octets du graphique : pas de LLM, pas d'imbrication, pas de correspondance floue nulle part sur le chemin de décision.

| Verdict | Sens |
|---|---|
| `SUPPORTED` | l'arête existe, porte ses propres preuves, et ce texte a été re-ancré au fichier source |
| `PRESENT_UNEVIDENCED` | l'arête existe mais rien adossé à un document ne la soutient |
| `CONTRADICTED` | un `contradicts_claim` adossé à un document entre les deux mêmes extrémités |
| `DISPUTED_UNEVIDENCED` | désaccord affirmé, aucun documenté |
| `CONFLICTING` | les deux polarités adossées à un document — l'outil refuse d'arbitrer |
| `ABSENT` | ce graphique n'affirme pas le triple. Pas une réfutation |
| `NOT_RESOLVABLE` | une extrémité ou un prédicat ne peut pas être résolu exactement |

Il y a deux choses qu'il ne fera délibérément pas. Il ne traite jamais `supersedes` comme une réfutation — cette relation dit qu'un *nœud* a été remplacé, non qu'un triple est faux. Et une écriture d'agent ne peut que *affaiblir* une classe de provenance, jamais en mettre à jour une, donc rien de ce qu'un agent affirme ne peut se présenter comme adossé à un document.

Il vaut la peine de savoir en lisant les résultats : sur un vrai graphique de 15 284 arêtes, environ 40% des verdicts `SUPPORTED` sont tautologiques — des arêtes `evidenced_by` dont la portée citée est la cible propre de l'arête. Vrai, mais non informatif.

## Acheminer une question

`tesserae ask` choisit un chemin de récupération par forme de question : les recherches d'entité unique vont au backend bon marché, les questions multi-sauts / "qu'est-ce qui a changé" / "pourquoi" / corpus vont au graphique. Ce découpage encode une **hypothèse, pas une mesure** : nous attendons que le parcours rentabilise son coût sur les questions multi-sauts, temporelles et de synthèse, et qu'il le gaspille sur la recherche de faits simples. Rien dans ce dépôt ne le vérifie — il n'y a ici aucune mesure de performance de récupération ni aucun chiffre publié derrière la table de routage, alors traitez-la comme une valeur par défaut à surcharger, pas comme un résultat.

La décision apparaît dans l'enveloppe retournée, une réponse bon marché est donc auditable. Remplacez-la avec `--route` sur la CLI, ou le paramètre `route` sur l'outil MCP.

RÈGLES :
- NE traduisez PAS : graph_write, verify_claim, SUPPORTED, PRESENT_UNEVIDENCED, CONTRADICTED, DISPUTED_UNEVIDENCED, CONFLICTING, ABSENT, NOT_RESOLVABLE, supersedes, contradicts_claim, evidenced_by, subject, predicate, object, MCP, --route
- Conservez tous les nombres exacts : 15 284, 40 %
- Conservez la structure du tableau avec les mêmes en-têtes de colonne
- Traduisez la prose naturellement pour chaque langue
- Ajoutez à la fin de chaque fichier sans perturber le contenu existant
