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
CLI sous `~/.tesserae/llm_cache`, indexé par (document, type, guidage) plus le
modèle et l'effort de raisonnement — donc changer de modèle re-demande au lieu
de servir les réponses du modèle précédent. Seules les réponses parseables sont
stockées, donc une mauvaise génération ne peut pas devenir permanente.

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

`tesserae config status` affiche le serveur résolu et le vérifie pour vérifier qu'il répond.

---

## Passes de compilation

| Variable | Par défaut | Ce qu'elle contrôle |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **activé** | Passe de résumé style GraphRAG. Un appel LLM par cluster ≥ 5 membres, mis en cache par résumé d'adhésion. Désactiver avec `false`/`0`/`no`/`off` |
| `TESSERAE_ENABLE_LLM_PASSES` | désactivé | Passes d'enrichissement LLM optionnelles au-delà de l'extraction |
| `TESSERAE_AGENT_DISTILL` | désactivé | Artefacts d'expertise L1 par agent (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | désactivé | Nœuds de mémoire distillée Runbook/Gotcha |
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

---

## Chemins et infrastructure

| Variable | Par défaut | Remarques |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Emplacement du registre des projets |
| `TESSERAE_DISCOVERY_CACHE` | — | Cache de découverte de session |
| `TESSERAE_ARXIV_CACHE` | — | Cache de métadonnées arXiv |
| `TESSERAE_NO_FEDERATION_CACHE` | désactivé | Désactive le LRU du graphe fédéré |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | désactivé | Émet le graphe combiné entre projets |
| `TESSERAE_FLEET_PIDFILE` | — | Fichier pidfile de la flotte du moteur |
| `TESSERAE_CLIP_TOKEN` | — | Secret partagé pour le presse-papiers Web |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | désactivé | Applique les propositions de dérive de schéma (`tesserae lab`) |

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

\`graph_write\` (MCP) prend des nœuds et des arêtes typés validés par schéma avec provenance obligatoire, de sorte qu'un agent enregistre une découverte en tant que *structure* plutôt que comme prose qu'un extracteur doit deviner les types.

Il refuse plutôt que de coercer : les arêtes non typées, les types de nœud ou d'arête en dehors du vocabulaire contrôlé, les extrémités flottantes et les écritures sans provenance sont tous rejetés. Les écritures dupliquées sont idempotentes. Les nœuds écrits par un agent survivent à une recompilation complète, \`graph.json\` supprimé, \`--limit\` et suppression complète du corpus.

## Vérifier une affirmation contre le graphique

\`verify_claim\` (MCP) répond si le graphique autorise un triple. Il prend \`(subject, predicate, object)\` — **il n'y a pas de paramètre en langage naturel**, par conception, car un parseur a fait que la version précédente réponde SUPPORTED à la négation d'une affirmation qu'elle soutenait.

Le verdict est une fonction pure des octets du graphique : pas de LLM, pas d'imbrication, pas de correspondance floue nulle part sur le chemin de décision.

| Verdict | Sens |
|---|---|
| \`SUPPORTED\` | l'arête existe, porte ses propres preuves, et ce texte a été re-ancré au fichier source |
| \`PRESENT_UNEVIDENCED\` | l'arête existe mais rien adossé à un document ne la soutient |
| \`CONTRADICTED\` | un \`contradicts_claim\` adossé à un document entre les deux mêmes extrémités |
| \`DISPUTED_UNEVIDENCED\` | désaccord affirmé, aucun documenté |
| \`CONFLICTING\` | les deux polarités adossées à un document — l'outil refuse d'arbitrer |
| \`ABSENT\` | ce graphique n'affirme pas le triple. Pas une réfutation |
| \`NOT_RESOLVABLE\` | une extrémité ou un prédicat ne peut pas être résolu exactement |

Il y a deux choses qu'il ne fera délibérément pas. Il ne traite jamais \`supersedes\` comme une réfutation — cette relation dit qu'un *nœud* a été remplacé, non qu'un triple est faux. Et une écriture d'agent ne peut que *affaiblir* une classe de provenance, jamais en mettre à jour une, donc rien de ce qu'un agent affirme ne peut se présenter comme adossé à un document.

Il vaut la peine de savoir en lisant les résultats : sur un vrai graphique de 15 284 arêtes, environ 40% des verdicts \`SUPPORTED\` sont tautologiques — des arêtes \`evidenced_by\` dont la portée citée est la cible propre de l'arête. Vrai, mais non informatif.

## Acheminer une question

\`tesserae ask\` choisit un chemin de récupération par forme de question : les recherches d'entité unique vont au backend bon marché, les questions multi-sauts / "qu'est-ce qui a changé" / "pourquoi" / corpus vont au graphique. Les tests indépendants montrent que les graphiques mènent sur les questions multi-sauts, temporelles et de synthèse, et *traînent* sur la recherche de faits simples et le coût — donc payer les tarifs du graphique pour chaque question est une perte.

La décision apparaît dans l'enveloppe retournée, une réponse bon marché est donc auditable. Remplacez-la avec \`--route\` sur la CLI, ou le paramètre \`route\` sur l'outil MCP.

RÈGLES :
- NE traduisez PAS : graph_write, verify_claim, SUPPORTED, PRESENT_UNEVIDENCED, CONTRADICTED, DISPUTED_UNEVIDENCED, CONFLICTING, ABSENT, NOT_RESOLVABLE, supersedes, contradicts_claim, evidenced_by, subject, predicate, object, MCP, --route
- Conservez tous les nombres exacts : 15 284, 40 %
- Conservez la structure du tableau avec les mêmes en-têtes de colonne
- Traduisez la prose naturellement pour chaque langue
- Ajoutez à la fin de chaque fichier sans perturber le contenu existant
