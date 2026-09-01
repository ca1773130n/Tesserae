# Référence d'ajustement — variables d'environnement

<!-- translations:start -->
<p align="center"><a href="../tuning.md">English</a> · <a href="tuning.ko.md">한국어</a> · <a href="tuning.zh.md">中文</a> · <a href="tuning.ja.md">日本語</a> · <a href="tuning.ru.md">Русский</a> · <a href="tuning.es.md">Español</a> · <a href="tuning.fr.md">Français</a> · <a href="tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Chaque paramètre que Tesserae lit depuis l'environnement, sa valeur par défaut
et quand vous voudriez vraiment le modifier. Rien ici n'est obligatoire : les
valeurs par défaut sont choisies pour qu'une simple `tesserae compile` fonctionne
correctement.

Les paramètres du serveur LLM vivent aussi dans `.tesserae/config.json` et
`~/.tesserae/config.json` ; les variables d'environnement ci-dessous l'emportent
sur les deux pour l'exécution où elles sont définies, et [Serveur LLM](#serveur-llm)
énonce l'ordre complet une fois.

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

**Par défaut `4`, ou `1` quand l'endpoint LLM est sur cette machine.** Documents extraits en parallèle. Chacun est un processus
enfant CLI bloquant prenant environ une minute, donc une boucle séquentielle
fait du temps réel la somme littérale de chaque aller-retour du modèle —
mesurée à ~2 h 40 m pour 161 documents.

Le plafond est la limite de débit du compte de votre fournisseur, pas votre
machine, c'est pourquoi la valeur par défaut est modeste. Définissez `1` pour
un comportement strictement séquentiel.

Un serveur de modèles local est l'exception. Ollama, llama.cpp et LM Studio servent une requête à
la fois, donc quatre workers mettent trois requêtes en file derrière chaque appel, et une requête en
file que le serveur abandonne bloque son worker pendant tout le `TESSERAE_EXTRACT_TIMEOUT` — ce qui
ressemble exactement à un problème de mémoire. Quand le `llm_base_url` résolu pointe vers
`localhost`, `127.0.0.1` ou `::1` et que cette variable n'est pas définie, l'extraction traite un
document à la fois et le dit sur stderr. Un proxy loopback qui relaie vers une API cloud (LiteLLM,
vLLM avec batching) en supporte davantage : définissez la variable explicitement et elle l'emporte
toujours.

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

Quel serveur répond, sur quel protocole, avec quelle accréditation. Chaque clé
ci-dessous se résout de la même façon, et d'une seule façon :

**`TESSERAE_*` var d'env → `.tesserae/config.json` du projet → `~/.tesserae/config.json` → par défaut intégré.**

| Clé de config | Variable d'environnement | Par défaut | Remarques |
|---|---|---|---|
| `llm_provider` | `TESSERAE_LLM_PROVIDER` | `claude` | L'un de `claude`, `codex`, `anthropic`, `openai`, `custom`. Tout autre est refusé par son nom — une faute de frappe était autrefois traitée silencieusement comme `claude`, donc une config disant `openrouter` exécutait contre Anthropic et signalait une erreur sur un modèle que vous n'aviez jamais choisi |
| `llm_api_style` | `TESSERAE_LLM_API_STYLE` | `openai` quand `llm_provider` est `openai`, `anthropic` sinon | Le protocole de transmission, qui est une question différente du serveur. `anthropic` envoie à `{base_url}/v1/messages` par le SDK Anthropic ; `openai` envoie à `{base_url}/chat/completions` |
| `llm_model` | `TESSERAE_LLM_MODEL` | `sonnet` (CLI claude), `gpt-5.6-luna` (CLI codex), `claude-sonnet-4-6` (protocole anthropic), `gpt-4o-mini` (protocole openai) | Limité par fournisseur sur les deux serveurs CLI, pour qu'un modèle de type claude n'atterrisse jamais sur le chemin codex. Un fournisseur d'endpoint configuré garde son modèle même quand le serveur et le modèle ont été définis dans des couches de config différentes |
| `llm_base_url` | `TESSERAE_LLM_BASE_URL`, puis `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` (protocole anthropic), `https://api.openai.com/v1` (protocole openai) | L'endpoint, rogné à ce que chaque protocole ajoute — voir [Points d'accès personnalisés](#points-dacc%C3%A8s-personnalis%C3%A9s) |
| `llm_api_key` | `TESSERAE_LLM_API_KEY`, puis `ANTHROPIC_API_KEY` | — | L'accréditation par clé API : `X-Api-Key` sur le protocole anthropic, `Authorization: Bearer` sur le protocole openai |
| `llm_auth_token` | `TESSERAE_LLM_AUTH_TOKEN`, puis `ANTHROPIC_AUTH_TOKEN` | — | L'accréditation porteuse, `Authorization: Bearer` sur les deux protocoles. Définissez **soit** cela **soit** `llm_api_key` : sur le protocole anthropic le token est remis au SDK comme `auth_token=` et aucune clé API n'est définie, donc les deux ne se heurtent jamais |
| `llm_allow_fallback` | `TESSERAE_LLM_ALLOW_FALLBACK` | désactivé | Permet à un fournisseur d'endpoint configuré de basculer vers un autre serveur au lieu d'échouer — voir [Un fournisseur d'endpoint est un contrat](#un-fournisseur-dendpoint-est-un-contrat). Toute valeur non vide de la var d'env l'active |
| `llm_claude_config_dirs` | `TESSERAE_CLAUDE_CONFIG_DIRS` | la valeur par défaut du CLI | Répertoires de configuration Claude dans l'ordre de rotation, séparés par `os.pathsep` dans la var d'env — le canal d'environnement pour un `--claude-config-dir` répété. Seule une liste *configurée* fait autorité ; le `CLAUDE_CONFIG_DIR` ambiant délibérément pas, car s'y épingler réduit la rotation multi-comptes à un seul compte |
| `llm_codex_homes` | `TESSERAE_CODEX_HOMES` | la valeur par défaut du CLI | Maisons Codex, même forme et même raisonnement que ci-dessus. L'ancien singulier `llm_codex_home` fonctionne toujours et signifie une liste à une maison |
| `llm_codex_reasoning_effort` | `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | L'extraction structurée ne nécessite pas le `xhigh` que vous pourriez définir pour un travail interactif — `xhigh` rend une compilation multi-documents plusieurs fois plus lente |

Les noms `ANTHROPIC_*` fonctionnent toujours, un degré en dessous des noms possédés
par Tesserae : ils sont ambiants — toute session Claude Code les exporte — donc ils
ne doivent pas surclasser une valeur que vous définissez pour Tesserae spécifiquement,
mais ils battent quand même les deux fichiers de config.

`tesserae config llm` écrit le fichier machine-wide ; pour un projet, mettez les mêmes
clés `llm_*` dans son `.tesserae/config.json`. Une accréditation écrite dans l'un ou
l'autre fichier est stockée en **texte clair**, donc préférez `TESSERAE_LLM_API_KEY` /
`TESSERAE_LLM_AUTH_TOKEN` pour ces deux-là.

### Points d'accès personnalisés

`llm_provider` dit quel serveur ; `llm_api_style` dit quel dialecte HTTP parler.
Les garder séparés est ce qui rend un endpoint non-Anthropic accessible du tout :
`custom` impliquait autrefois le protocole Anthropic, donc un serveur compatible
OpenAI n'avait nulle part où être configuré. Non défini, `llm_api_style` se
résout toujours en `anthropic` pour `custom` — un endpoint configuré avant
l'existence de ceci garde le comportement exact qu'il avait.

**Un endpoint compatible OpenAI** — vLLM, LiteLLM, OpenRouter, Together, Ollama,
LM Studio :

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=openai
export TESSERAE_LLM_BASE_URL=http://localhost:8000/v1
export TESSERAE_LLM_MODEL=qwen2.5-coder-32b-instruct
export TESSERAE_LLM_AUTH_TOKEN=sk-...   # ou TESSERAE_LLM_API_KEY — même entête ici
tesserae config status
```

La demande est `POST {base_url}/chat/completions`. Ce protocole est `urllib` stdlib,
donc il ne nécessite aucune installation supplémentaire, et un serveur local sans clé
n'a besoin d'aucune accréditation du tout — laissez les deux non définies et cela
construit toujours.

**Un endpoint compatible Anthropic** — une passerelle parlant l'API Messages :

```bash
export TESSERAE_LLM_PROVIDER=custom
export TESSERAE_LLM_API_STYLE=anthropic
export TESSERAE_LLM_BASE_URL=https://gateway.internal.example   # pas de /v1
export TESSERAE_LLM_MODEL=claude-sonnet-4-6
export TESSERAE_LLM_API_KEY=...         # X-Api-Key ; TESSERAE_LLM_AUTH_TOKEN pour une passerelle porteuse
tesserae config status
```

La demande est `POST {base_url}/v1/messages` par le SDK Anthropic, que
`llm_provider: custom` sur ce protocole et `llm_provider: anthropic` tous les
deux ont besoin :

```bash
pip install "tesserae[synthesis-llm]"
```

**Le `/v1` n'est pas décoration.** Le SDK ajoute `/v1/messages` lui-même, donc
le `https://host/v1` que chaque README de passerelle montre produisait `/v1/v1/messages`
— un 404 qui ressemble à un modèle mauvais. Un trailing `/v1` est maintenant
retiré sur le protocole anthropic et assuré sur le protocole openai. Seulement
ce segment trailing est jamais touché, et il est rogné quel que soit ce qui le
précède — une mandataire servant véritablement `/anthropic/v1` perd aussi ce `/v1`
— donc la réécriture est enregistrée à INFO plutôt que faite silencieusement, et
la ligne de journal est où vous trouvez l'URL réellement utilisée.

### Un fournisseur d'endpoint est un contrat

`anthropic`, `openai` et `custom` portent un endpoint que vous avez choisi —
une URL, un nom de modèle, une accréditation. Quand l'un d'eux est configuré
il est construit seul, et une défaillance soulève `LLMProviderConfigError`
nommant le fournisseur, le protocole, l'URL de base, le modèle et quel type
d'accréditation a été résolu.

C'était autrefois une préférence : un endpoint personnalisé qui ne pouvait pas
être construit basculait vers la CLI Claude, qui était ensuite lancée avec
`--model sonnet` contre votre propre URL de base et signalait un modèle non
supporté que vous n'aviez jamais configuré, sans rien nommant la cause réelle.
Définissez `llm_allow_fallback: true` pour récupérer ce chaînage.

Les deux fournisseurs CLI OAuth chaînent toujours — l'un vers l'autre, et vers
le client API derrière eux. `claude` et `codex` ne prennent aucune URL de base
et leurs modèles sont limités par fournisseur, donc aucun ne peut porter un
endpoint que vous avez choisi vers un serveur que vous n'aviez pas nommé, ce
qui est la seule chose que le contrat existe pour prévenir.

### Voir ce qui est réellement en effet

```bash
tesserae config status                 # serveur résolu + une sonde en direct
tesserae config status --project .     # comme le config.json du projet le voit
tesserae config status --no-ping       # sauter la sonde, ne rien dépenser
```

Il affiche le fournisseur, le protocole, le modèle, l'URL de base, et le *type*
d'accréditation qui s'est résolu — `api_key`, `auth_token` ou aucun, jamais le
secret — chacun étiqueté avec la couche qui l'a emporté, puis la classe et
l'identité du client qui a répondu. Ce client est construit à partir du même
dict d'ajustements qu'une exécution réelle utilise, et la sonde n'est jamais
cachée, donc une ligne réussie signifie que le serveur a répondu juste
maintenant plutôt qu'à un moment du passé.

Quand un appel échoue, la défaillance est classée plutôt qu'aplatie : `401` et
`403` sont signalés comme auth, `404` — et un `400` qui nomme le modèle — comme
l'endpoint, chacun nommant l'endpoint qui l'a produit. Avant cela, une URL mal
configurée était indiscernable de n'avoir aucun LLM installé du tout.

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
| `TESSERAE_VERIFY_BAND` | activé | Faire retrancher par le modèle les signalements incertains de `ask` dans la bande mesurée 0.30–0.70 ; `lo-hi` la remplace. `off` ne garde que les signalements gratuits, qui ne coûtent ni jetons ni réseau |
| `TESSERAE_EMBEDDING_PREFER` | auto | Encodeur de la voie dense : `model2vec` (livré, statique, sans torch), `st` (un modèle sentence-transformers entraîné), `openai`, `hash`. Non défini, l'échelle prend le premier installé |
| `TESSERAE_ST_MODEL` | `BAAI/bge-base-en-v1.5` | Le modèle sentence-transformers que `st` charge ; n'importe quel nom Hugging Face |

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

Activé par défaut dans `ask`, où un client du modèle est déjà en main et où des jetons
ont déjà été dépensés pour la réponse : les signalements exacts coûtent donc peu de plus.
Aucune variante sans modèle de la vérification ne comble l'écart à elle seule — la
racinisation, les n-grammes de caractères, la pondération par rareté et un plongement
local ont chacun été mesurés, et aucun n'a battu la couverture simple —, d'où une
cascade par défaut plutôt qu'une vérification gratuite plus maligne. La fonction de
bibliothèque `check_against_evidence` est intacte et ne coûte toujours rien. L'enveloppe
rapporte `adjudicated` : `null` quand la cascade n'a pas tourné, un décompte quand elle a
tourné. Un modèle incapable de répondre laisse le verdict gratuit en place — un appel
raté ne peut jamais rendre propre une phrase signalée.

### `TESSERAE_EMBEDDING_PREFER`

La voie dense de `hybrid_search` encode avec ce que `active_embedding_backend`
trouve en premier : le modèle statique `model2vec` livré (8 Mo, sans torch, hors
ligne), puis sentence-transformers, puis un substitut par hachage. Le modèle
statique est ce qui garde `pip install tesserae` petit, et sur un petit corpus
il ne coûte rien de mesurable. Sur un grand, c'est le goulot : sur 148
articles, le rappel par documents distincts était de 0.754 @10 / 0.914 @50 avec
le modèle livré et de 0.791 / 0.962 avec `BAAI/bge-base-en-v1.5` dans la même
fusion — la voie dense seule est passée de 0.473 à 0.680 @10. Un simple magasin
vectoriel sur les mêmes fragments obtient 0.784 / 0.942 avec nomic-embed-text
et 0.775 / 0.944 avec ce même bge-base ; l'avance du graphe sur lui reste dans
le bruit sur 57 questions (test des signes apparié p=1.0 à 10, 0.51 à 50).
C'est l'encodeur entraîné qui met le graphe à son niveau plutôt que derrière.

```bash
uv pip install sentence-transformers          # torch, ~2 GB with the model
export TESSERAE_EMBEDDING_PREFER=st
export TESSERAE_ST_MODEL=BAAI/bge-base-en-v1.5   # the default; any Hugging Face name
```

`auto` choisit toujours le modèle statique en premier, donc une installation
qui ne définit jamais la variable se comporte exactement comme avant. La
préférence est lue une seule fois, à la première résolution du backend ; une
valeur qui ne nomme aucun backend est signalée et ignorée plutôt que de
retomber en silence sur le substitut par hachage. Un encodeur entraîné
ré-encode chaque nœud à chaque requête si les vecteurs ne sont pas mis en
cache — `compile_context` et le serveur MCP passent déjà le `VectorCache` du
projet, dont la clé est le backend, si bien que changer de modèle ne sert
jamais de vecteurs périmés.

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

### Un graphe compilé avant les passes de post-extraction

Deux correctifs changent l'aspect d'un graphe compilé sans changer ce que le modèle a
extrait : une ancre par document au lieu d'une par morceau (un article compilé par
morceaux en portait 9.4), et un nœud par nom d'entité au lieu d'un par graphie et par
type. Les deux s'exécutent dans `compile`, si bien qu'un graphe déjà sur disque n'a ni
l'un ni l'autre tant qu'il n'est pas recompilé. `graph-repair` applique les mêmes règles
aux octets du graphe — sans modèle, sans réseau, en quelques secondes — et un graphe
réparé concorde avec un graphe recompilé.

```sh
tesserae graph-repair --dry-run     # rapporte ce qui changerait, n'écrit rien
tesserae graph-repair               # réécrit .tesserae/graph.json sur place
```

Le site et le coffre sont des projections et ne sont pas reconstruits ici ; lancez
`export site` ensuite si vous en servez un.

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
