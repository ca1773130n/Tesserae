# Tesserae comme moteur de contexte — Analyse des écarts

<!-- translations:start -->
<p align="center"><a href="../context-engine-audit.md">English</a> · <a href="context-engine-audit.ko.md">한국어</a> · <a href="context-engine-audit.zh.md">中文</a> · <a href="context-engine-audit.ja.md">日本語</a> · <a href="context-engine-audit.ru.md">Русский</a> · <a href="context-engine-audit.es.md">Español</a> · <a href="context-engine-audit.fr.md">Français</a> · <a href="context-engine-audit.de.md">Deutsch</a></p>
<!-- translations:end -->
> **Mission (2026-06-02) :** Tesserae est un *moteur de contexte* — il génère
> un contexte prêt pour les agents en reconstruisant une base de connaissances
> **qui s'améliore d'elle-même** via trois piliers : **(1) surveillance des
> sessions**, **(2) ingestion proactive autonome** et **(3) documents à la
> demande**. La connaissance doit être **en temps réel et évolutive**, prête à
> être remise aux agents.

Ce document audite la base de code actuelle au regard de cette mission. C'est
le résultat d'une revue parallèle à quatre voies (ingestion/sessions,
auto-amélioration, sortie/face aux agents, orchestration/cycle de vie).

> **Statut au moment de la v0.5.0 (2026-06-06) :** Ce document est un **audit ponctuel** (instantané du 2026-06-02) conservé tel quel pour archive. La plupart de ses constats transversaux sont désormais **résolus** : le démon superviseur et l'orchestrateur de pipeline in-process qui manquaient ont été livrés (colonne vertébrale du moteur, `tesserae/engine/`), le tailing de sessions en direct remplace le scan a posteriori (Pilier 1), les passes d'auto-amélioration sont activées et persistées via le sidecar `node_memory` (supersede activé par défaut avec suppression, confiance de récurrence numérique — Pilier 2), l'embedding par défaut à seaux de hachage est remplacé par un vrai backend qui échoue bruyamment (Pilier 3), et **le compilateur de contexte à la demande du Pilier 3 existe désormais** (`compile_context`). Une couche incrémentale conçue à travers le port `GraphStore` a atterri en tant qu'infrastructure mais reste **avec le flag OFF/expérimental**, et l'unification serve+watch+deploy (étape 7 de l'ordre de construction) reste ouverte. Voir le statut par phase dans la [feuille de route par phases](context-engine-roadmap.fr.md) et le résumé des changements dans les [notes de version v0.5.0](release-notes/v0.5.0.fr.md). Les constats ci-dessous sont laissés sans modification, tels que l'instantané d'origine.

## Verdict en une ligne

Tesserae aujourd'hui est un **compilateur CLI par lots mécaniquement sain et
bien testé**. Au regard de la vision du moteur de contexte, il est à
**déclenchement manuel + a posteriori + verrouillé sur git-HEAD** sur les
trois piliers. La mécanique pour bâtir le moteur existe déjà sous forme de
primitives ; ce qui manque, c'est la **couche continue et auto-pilotée** qui
les compose.

La plus grosse pièce manquante dans chaque tranche : un **superviseur/démon de
longue durée possédant une unique boucle d'événements** et pilotant de manière
autonome le suivi des sessions, l'ingestion, la compilation incrémentale et la
publication. Tout le reste est incrémental par-dessus.

---

## Pilier 1 — Surveillance des sessions → **a posteriori, pas en direct**

| Statut | Constat | Ce qu'il faut |
|---|---|---|
| écart | La capture de session est un balayage a posteriori : `discover_harness_sessions()` parcourt les transcriptions terminées uniquement quand un humain lance `sessions discover --import` ou `compile`. `compile` refuse délibérément de balayer `~/.claude/projects/` (latence). | Un **tailer** qui surveille les fichiers JSONL du harness et ingère les tours au fil de la session. |
| écart | Le seul vrai surveillant (`watch.py WatchLoop`) couvre le **markdown source**, sonde toutes les 2 s et déclenche un `compile` complet. Il ne surveille ni les sessions ni le code. | Étendre aux déclencheurs session + outils source sous un seul superviseur. |
| écart | La boucle « en direct » de `vault_watch.py` agit sur la **sortie** (resynchronisation inverse d'Obsidian), pas sur l'ingestion. | Pas un substitut à l'extraction de connaissances en direct. |
| rugueux | La ré-extraction de session est mise en cache par `session_id` mais **par session entière** : un seul nouveau tour invalide tout le cache et relance la passe LLM complète. | Incrémentalité au niveau du tour pour le tailing en direct. |
| rugueux | Le magasin `harness_sessions` est un glob plat avec re-balayage total à chaque list/write. | Magasin indexé/à ajout pour un ensemble de captures en croissance continue. |
| absent | Pas d'horodatage de fraîcheur/provenance par nœud ; l'actualité n'est suivie qu'au niveau de l'artefact (git HEAD). | Fraîcheur par fait pour « quelle est sa fraîcheur ? ». |

## Pilier 2 — Base de connaissances qui s'améliore d'elle-même → **ré-extraction one-shot, évolution rapportée**

Les passes « évolutives » existent, mais s'exécutent **uniquement à l'intérieur
d'un seul `compile`** (une ré-extraction à partir de zéro), et la plupart sont
**opt-in via un flag d'environnement ou une CLI manuelle**. Les faits sont
recalculés à chaque compilation, pas révisés sur place.

| Statut | Constat | Ce qu'il faut |
|---|---|---|
| écart | La **décroissance (Decay)** (`memory/decay.py`, demi-vie d'Ebbinghaus de 14 j) n'est calculée qu'*au moment de la requête*, jamais persistée ni réécrite à la compilation. | Écriture de la décroissance à la compilation + score persisté. |
| écart | La boucle d'accès de la décroissance est **morte** : `last_accessed_at == first_seen_at`, `access_count` n'est jamais incrémenté. Le signal « je n'arrête pas de le regarder → ça compte » ne fait rien. | Une surface d'enregistrement d'accès (lecture MCP → incrément). |
| écart | Le **remplacement (Supersede)** (`memory/supersede.py`) est verrouillé derrière `TESSERAE_SUPERSEDE_PASS=true` (désactivé par défaut) et ne fait qu'*ajouter* des arêtes — il ne rétrograde/masque jamais le contenu obsolète. La révision des croyances est cosmétique. | Activé par défaut + consommateurs supprimant les faits remplacés en sortie. |
| écart | Les **contradictions (Contradictions)** sont *détectées* (`lint.py`, sévérité info, correspondance de chaînes fragile) mais jamais *résolues*. Pas d'arbitrage de confiance. | Une passe de résolution, pas une simple sonde. |
| écart | La **dérive de schéma (Schema drift)** (`schema_drift.py`) est une sous-commande manuelle `schema-drift` qui n'écrit que des propositions ; le schéma ne s'auto-affine jamais. | Chemin d'application + intégration au pipeline. |
| écart | La **canonicalisation (Canonicalization)** ne fusionne automatiquement que les alias à haute confiance ; le reste est mis en file d'attente pour approbation humaine par CLI. | Fusion automatique arbitrée par LLM au fil du temps. |
| écart | **Boucle de rétroaction à moitié fermée** : l'extracteur de base déterministe *ignore totalement les directives* (`selective_extractor.py:43`) ; seul le chemin LLM optionnel consomme les corrections. LLM désactivé, les corrections humaines ne reviennent jamais dans l'extraction. | Respect des directives par le chemin déterministe, ou LLM par défaut. |
| écart | Pas de **renforcement des insights récurrents** : rien ne renforce la confiance quand un insight réapparaît entre sessions. `temporal.infer_confidence` est une heuristique de chaînes grossière. | Fréquence inter-sessions → confiance numérique. |
| rugueux | L'appariement des candidats au remplacement est en **Jaccard lexical (0.55)** ; les reformulations sémantiques à faible recouvrement lexical ne deviennent jamais candidates. | Génération de candidats fondée sur les embeddings. |
| absent | **Toute la tranche d'auto-amélioration est non testée** (pas de tests decay/supersede/feedback/drift/canonical/temporal). | Des tests avec tout changement ici. |

## Pilier 3 — Documents à la demande → **n'existe pas encore**

La plomberie requête/récupération est mature (RRF hybride, PPR, ~20 outils MCP,
ask par page, exports pour IA). Mais **chaque artefact est soit une projection
statique de tout le corpus, soit une recherche d'un seul nœud.** « L'utilisateur
demande „donne-moi du contexte sur X“ → document sur mesure » n'est pas
implémenté. Les primitives pour le construire sont toutes présentes mais jamais
composées.

| Statut | Constat | Ce qu'il faut |
|---|---|---|
| absent | **Génération de documents à la demande (l'écart central du Pilier 3).** Aucun module ne produit un document sur mesure et borné par requête à partir d'une demande. `report.py` est un résumé de lint à la compilation, pas un artefact de connaissance. | Nouveau `context_compiler` : recherche → PPR → parcours du voisinage → assemblage du corps → synthèse LLM optionnelle. |
| écart | `wiki_page` renvoie un corps de nœud pré-compilé ; pas d'outil d'assemblage multi-nœuds borné par requête. | Outil MCP `compile_context(query|seeds, depth, budget)`. |
| écart | `ask` renvoie de la prose ou une liste de résultats, jamais un artefact de contexte téléchargeable/transmissible. | Mode de réponse émettant un bundle de contexte structuré et cité. |
| écart | `agent_harness.py` est une remise **statique** (top-12 nœuds en dur + liste fixe), pas bornée par requête ni par tâche. | Accepter un sujet/une amorce → rendre un brief borné. |
| écart | `node_context` est à 1 saut, sans classement. Faible comme primitive de contexte pour agent. | Router via PPR pour un contexte k-sauts classé. |
| écart | Les exports (`llms.txt`, `graph.jsonld`) sont des dumps de tout le corpus ; pas de tranche par sujet. | Sous-graphe borné par sujet → tranche llms-txt. |
| rugueux | Le couloir d'embeddings par défaut est un **pseudo-embedding déterministe par seaux de hachage** (blake2b, 128 dim) ; vrai backend sémantique seulement si `sentence-transformers` est installé, et `auto` rétrograde en silence. La récupération « sémantique » prête à l'emploi est factice. | De vrais embeddings par défaut, ou un avertissement bruyant sur le couloir de hachage. |
| rugueux | `query.answer()` **jette une réponse LLM valide** si elle manque la correspondance regex de citation de nœud. | Conserver la réponse ; signaler plutôt les citations manquantes. |
| rugueux | Le widget ask de l'hôte statique sert un **`DEMO_QA` en conserve** ; le vrai ask ne fonctionne que sous `serve`. Le « ask » public sur Pages est du théâtre. | Acceptable en démo ; non consommable par un agent sur le site publié. |
| rugueux | Le backend `auto` de `ask` avale les exceptions et rétrograde de manière invisible vers BM25. | Exposer quel backend a répondu et pourquoi les replis se sont déclenchés. |

## Transversal — Orchestration & cycle de vie → **CLI par lots, pas de moteur**

| Statut | Constat | Ce qu'il faut |
|---|---|---|
| écart | **Pas de processus démon/moteur.** Répartiteur argparse plat à un coup ; le processus se termine après chaque sous-commande. Zéro gestion signal/SIGTERM/pidfile/launchd ; les surveillants meurent sur un `KeyboardInterrupt` nu. | Un démon supervisé de longue durée possédant une boucle d'événements + arrêt gracieux. |
| écart | « Continu » = sondeur markdown `while True: time.sleep(interval)`. Pas d'événements du système de fichiers, de contre-pression ni de streaming. | Cœur orienté événements avec un ordonnanceur unique. |
| écart | **« Refresh » vit dans un skill markdown de slash-command**, pas dans le code — il enchaîne `sessions discover --import` → `compile` → `obsidian-sync`. | Orchestrateur de pipeline in-process de première classe, partagé par démon/CLI/MCP. |
| rugueux | La compilation incrémentale `changed_only` est **fragile et auto-décrite comme un contournement** : le manifeste est `{path: sha256}` ; il faut recharger le graphe précédent, retirer les nœuds projecteur/synthèse, évincer les nœuds source ré-extraits, puis fusionner — sinon une édition de 21 fichiers effondre 2400 nœuds à 1700. | Une couche incrémentale/streaming conçue, circulant par le port `GraphStore`. |
| rugueux | `cli.py` est un répartiteur-dieu d'environ 2000 lignes (échelle `if args.command == ...`) ; `ask`/`wiki` ont des parseurs faits main séparés. | Registre de commandes / modules de sous-commande. |
| rugueux | Les flags à porte de phase livrent une surface à moitié finie : l'aide de `--sessions-llm` dit *« sera honoré une fois que Phase 5 aura atterri »*. | Finir ou cacher. |
| rugueux | `graph_stores/url_resolver.py` enveloppe un magasin async dans `asyncio.run` **à chaque appel** — une nouvelle boucle d'événements par upsert. Pathologique pour le streaming. | Runtime async persistant si le moteur passe en production. |
| rugueux | Les protocoles hexagonaux de `ports/` sont définis, mais le pipeline autonome les **contourne**, allant droit aux artefacts JSON. Seul HypePaper utilise le port. | Faire circuler le pipeline cœur par `GraphStore` de façon cohérente. |
| rugueux | Trois formats de persistance (artefact JSON, magasin SQLite, Kuzu) sans source unique de vérité ; l'adaptateur Kuzu enveloppe chaque champ en base64 pour esquiver un bug de corruption de 0.16. | Converger vers une seule source de vérité. |
| rugueux | `serve` (`TCPServer.serve_forever`) et `watch` sont des **processus bloquants séparés** — impossible de servir + recompiler automatiquement ensemble. `deploy` est un git push manuel, découplé. | Unifier serve + watch + deploy sous le superviseur pour une publication continue. |
| absent | `frontend.py` est un **module mort déprécié** encore livré, dupliquant `tesserae/site/`. | Supprimer ou migrer les appelants. |
| rugueux | La boucle de revue humaine de `review_workflow.py` émet un JSON `"action": "TODO: merge|keep_separate"` à typage chaîne pour édition manuelle ; pas de chemin d'application programmatique. | File de revue intégrée câblée dans la compilation. |
| note | Les marqueurs TODO/FIXME sont véritablement rares — la vraie dette, ce sont les **contournements documentés en commentaire** (fusion changed-only, base64 de Kuzu, asyncio-par-appel), pas des TODO épars. | — |

---

## Ordre de construction recommandé (delta d'architecture → vision)

1. **Démon superviseur + orchestrateur de pipeline in-process.** Une boucle
   d'événements, signaux/arrêt, remplaçant la chaîne de refresh du skill
   markdown. *Débloque tous les autres piliers.*
2. **Moniteur de sessions en direct.** Tail du JSONL du harness → extraction
   incrémentale au niveau du tour → alimenter la boucle. (Remplace le manuel
   `sessions discover --import`.)
3. **Vraie compilation incrémentale/streaming** par le port `GraphStore`,
   mettant à la retraite le fragile patch d'éviction `changed_only`.
4. **Activer les passes d'auto-amélioration par défaut + les persister** :
   écriture de décroissance à la compilation, incrément d'access-count aux
   lectures MCP, supersede activé (avec suppression), résolution des
   contradictions, confiance des insights récurrents.
5. **Compilateur de contexte à la demande** (outil MCP `compile_context` +
   CLI) : requête → PPR/hybride → parcours du voisinage → document assemblé,
   cité et prêt pour les agents.
6. **De vrais embeddings par défaut** (ou un avertissement bruyant de
   dégradation) pour que la récupération sémantique ne soit pas un stub de
   hachage prêt à l'emploi.
7. **Unifier serve + watch + deploy** pour une publication continue ; ajouter
   des tests de cycle de vie (la couche dont la vision dépend le plus est
   aujourd'hui la moins couverte).

## Forces à préserver

Compilation déterministe identique octet pour octet ; large couverture de tests
sur la mécanique par lots ; récupération RRF hybride propre + pondération
réfléchie des arêtes PPR ; surface d'outils MCP large et correctement
partitionnée (publique/privée) ; exports statiques solides (`llms.txt`,
JSON-LD, RSS) et un widget ask soucieux de la sécurité. La fondation est
solide ; le travail consiste à ajouter par-dessus la couche dynamique et
auto-pilotée.
