# Tesserae → moteur de contexte — Feuille de route par phases

<!-- translations:start -->
<p align="center"><a href="../context-engine-roadmap.md">English</a> · <a href="context-engine-roadmap.ko.md">한국어</a> · <a href="context-engine-roadmap.zh.md">中文</a> · <a href="context-engine-roadmap.ja.md">日本語</a> · <a href="context-engine-roadmap.ru.md">Русский</a> · <a href="context-engine-roadmap.es.md">Español</a> · <a href="context-engine-roadmap.fr.md">Français</a> · <a href="context-engine-roadmap.de.md">Deutsch</a></p>
<!-- translations:end -->
Dérivée de [`context-engine-audit.md`](./context-engine-audit.fr.md).
Transforme l'ordre de construction en 7 étapes en phases séquencées avec
dépendances, périmètre concret et critères d'acceptation.

**Étoile polaire :** un moteur en exécution continue qui surveille les
sessions, ingère le savoir de façon autonome, améliore sa base par lui-même et
sert du contexte à la demande prêt pour les agents — remplaçant la CLI de
compilation par lots manuelle d'aujourd'hui.

## Forme des dépendances

```
P0 Orchestrateur de pipeline (extraire la chaîne refresh en code)
        │
P1 Démon superviseur (la boucle) ──────────────┐
        │                                      │
   ┌────┴─────────────── Piste A ──────┐   ┌── Piste B (parallélisable) ──┐
   P2 Moniteur de sessions en direct   │   P5 Vrais embeddings par défaut
   P3 Compilation incrémentale/streaming│  P6 Compilateur de contexte à la demande
   P4 Persistance de l'auto-amélioration┘   (P6 dépend de P5)
        │                                      │
        └──────────────► P7 Unifier serve+watch+deploy ◄──┘
```

La Piste A (ingestion en temps réel) et la Piste B (sortie face aux agents)
peuvent tourner en parallèle dès que P1 atterrit. P7 les fait converger.

---

## Phase 0 — Orchestrateur de pipeline (fondation de réduction des risques)

**Objectif :** Sortir le pipeline de refresh du markdown de slash-command vers
un orchestrateur in-process de première classe appelé par le démon, la CLI et
MCP.

- **Pourquoi maintenant :** Chaque phase ultérieure a besoin d'un seul chemin
  de code partagé pour `ingest → compile → project → publish`. Aujourd'hui cette
  séquence n'existe que comme prose dans un skill. Rien d'autre ne peut être
  automatisé tant qu'elle n'est pas appelable.
- **Périmètre :** Nouveau `tesserae/engine/pipeline.py` (un objet `Pipeline`
  enveloppant la chaîne actuelle `sessions discover --import → compile →
  obsidian-sync`). Router les sous-commandes de `cli.py` à travers lui. Commencer
  à décomposer le répartiteur-dieu `project_main` d'environ 2000 lignes en une
  table de commandes (mécanique, sans changement de comportement).
- **Livrables :** `Pipeline.run(steps, changed_only=…)` ; la CLI délègue à lui ;
  tests unitaires de séquencement d'étapes + propagation des échecs.
- **Acceptation :** `tesserae project refresh` existe comme code (pas un skill)
  et reproduit la chaîne markdown octet pour octet sur le corpus de démo.
- **Risque :** Faible. Refactor pur ; les tests existants gardent le
  comportement.
- **Constats d'audit clos :** « refresh vit dans un skill »,
  « répartiteur-dieu cli ».

## Phase 1 — Démon superviseur (la boucle du moteur)

**Objectif :** Un processus supervisé de longue durée possédant une boucle
d'événements et pilotant `Pipeline` sur déclencheurs, avec une vraie gestion du
cycle de vie.

- **Pourquoi maintenant :** C'est la colonne vertébrale. La plus grosse lacune
  unique de l'audit. Tout ce qui est « continu/autonome » en dépend.
- **Périmètre :** Nouveau `tesserae/engine/daemon.py` — boucle d'événements,
  file de déclencheurs, debounce/coalescence, arrêt gracieux `SIGTERM`/`SIGINT`,
  pidfile, logging structuré. Point d'entrée CLI `tesserae engine` /
  `tesserae daemon`. Remplacer la mort par `KeyboardInterrupt` nu dans
  `watch.py`/`vault_watch.py` en les pliant comme *sources de déclenchement*
  alimentant la file.
- **Livrables :** Démon tournant indéfiniment, coalesçant les rafales en une
  exécution de pipeline, s'arrêtant proprement ; unité d'exemple
  launchd/systemd.
- **Acceptation :** Éditer un fichier source → le démon coalesce et exécute un
  `compile(changed_only)` dans la fenêtre de debounce ; `SIGTERM` sort avec 0
  sans threads orphelins ; survit à une exception de pipeline sans mourir.
- **Risque :** Moyen — exactitude concurrence/arrêt. Atténuer avec un cœur
  asyncio monothread + supervision explicite des tâches.
- **Constats d'audit clos :** « pas de démon », « continu = poller sleep »,
  mort du surveillant par `KeyboardInterrupt`, pas de gestion des signaux.

## Phase 2 — Moniteur de sessions en direct (Pilier 1)

**Objectif :** Tailer les transcriptions du harness en direct et ingérer les
tours au fil des sessions, remplaçant le `sessions discover --import` a
posteriori.

- **Pourquoi maintenant :** A besoin de la boucle de P1 pour s'alimenter. Livre
  le pilier « surveillance des sessions ».
- **Périmètre :** Nouvelle source de déclenchement de tail de session
  (surveiller les événements d'ajout JSONL de `~/.claude` / `~/.codex`) →
  enfiler. Extraction incrémentale au niveau du tour dans `session_graph*.py`
  pour qu'un nouveau tour n'invalide pas le cache de toute la session. Magasin
  indexé/à ajout pour `harness_sessions` (retirer le glob de re-balayage total).
- **Livrables :** Le démon ingère les tours d'une session dans les secondes
  suivant leur écriture ; `test_session_tailer.py`.
- **Acceptation :** Démarrer une session d'agent en direct dans un projet
  surveillé → de nouveaux constats apparaissent dans le graphe sans commande
  manuelle ; taux de succès de cache au niveau du tour mesuré > ré-extraction de
  session entière.
- **Risque :** Moyen — les formats JSONL diffèrent entre harnesses ; lectures de
  ligne partielle.
- **Constats d'audit clos :** balayage de session a posteriori, invalidation de
  cache de session entière, magasin glob plat.

## Phase 3 — Compilation incrémentale/streaming par le port GraphStore

**Objectif :** Remplacer le fragile patch d'éviction de graphe `changed_only`
par une couche incrémentale conçue, circulant par `ports/graph_store.py`.

- **Pourquoi maintenant :** L'ingestion continue (P2) fait du contournement
  reload-strip-evict-merge actuel une dette d'exactitude (le piège documenté
  « 2400→1700 nœuds »). L'auto-amélioration (P4) a besoin d'upserts par nœud.
- **Périmètre :** Faire circuler le pipeline autonome par `GraphStore`
  (aujourd'hui il contourne les ports et va droit au JSON). Upsert/delete par
  nœud avec provenance + horodatages de fraîcheur. Faire converger la
  persistance vers une seule source de vérité (audit : artefact JSON vs SQLite
  vs Kuzu). Corriger le `asyncio.run`-par-appel de `url_resolver.py` (runtime
  async persistant).
- **Livrables :** Compilation incrémentale qui ajoute/met à jour/supprime
  uniquement les nœuds modifiés correctement ; `first_seen_at`/`last_updated_at`
  par nœud.
- **Acceptation :** Une édition de 21 fichiers met à jour exactement les nœuds
  affectés (sans effondrement) ; parité de compilation complète octet pour
  octet conservée comme test doré.
- **Risque :** Élevé — touche le cœur du modèle de données. Verrouiller derrière
  un feature flag ; diff contre la sortie de compilation complète jusqu'à
  confiance.
- **Constats d'audit clos :** `changed_only` fragile, ports contournés,
  asyncio par appel, trois formats de persistance, pas de fraîcheur par nœud.

## Phase 4 — Activer et persister l'auto-amélioration (Pilier : auto-amélioration)

**Objectif :** Faire que la base de connaissances évolue réellement sur place,
activée par défaut, persistée au moment de la compilation.

- **Pourquoi maintenant :** Dépend des upserts par nœud de P3. Clôt la tranche
  la moins testée.
- **Périmètre :** Persister les scores de **décroissance** à la compilation
  (`memory/decay.py` n'est plus uniquement au moment de la requête) ; incrémenter
  `access_count`/`last_accessed_at` aux lectures MCP. **Supersede** activé par
  défaut avec *suppression* en aval du contenu obsolète (pas seulement ajout
  d'arêtes). Ajouter la **résolution des contradictions** (élever la détection de
  `lint.py` en une passe arbitrée par confiance). **Renforcement des insights
  récurrents** (fréquence inter-sessions → confiance numérique). Câbler le chemin
  d'application de la **dérive de schéma** et la **guidance de rétroaction** dans
  l'extraction (le chemin déterministe l'ignore aujourd'hui). Génération de
  candidats supersede fondée sur les embeddings (retirer le Jaccard lexical).
- **Livrables :** Chaque passe tourne dans le pipeline par défaut et réécrit ;
  une nouvelle suite `tests/` couvrant
  decay/supersede/feedback/drift/contradiction.
- **Acceptation :** Reformuler un fait entre sessions élève sa confiance ; un
  fait remplacé cesse d'apparaître dans la sortie de contexte ; les scores de
  décroissance persistent et se décalent entre exécutions ; la suite
  d'auto-amélioration est verte (zéro test aujourd'hui).
- **Risque :** Moyen — changements de comportement de la sortie d'extraction ;
  garder avec des fixtures dorés.
- **Constats d'audit clos :** toute la table du Pilier 2.

## Phase 5 — Vrais embeddings par défaut (fondation de la Piste B)

**Objectif :** Cesser de livrer un pseudo-embedding déterministe par seaux de
hachage comme couloir « sémantique » par défaut.

- **Pourquoi maintenant :** Le compilateur de contexte de P6 ne vaut que ce que
  vaut la récupération. Indépendant du démon — peut démarrer dès que P0 atterrit.
- **Périmètre :** Livrer un vrai backend d'embeddings par défaut (ou faire que
  `auto` échoue bruyamment au lieu de rétrograder en silence vers blake2b dans
  `retrieval/hybrid.py`). Laisser le couloir d'embeddings générer des candidats
  (pas seulement re-ranker) une fois les embeddings réels.
- **Livrables :** L'installation par défaut produit une récupération sémantique
  authentique, ou un avertissement explicite et visible « tourne sur un stub de
  hachage ».
- **Acceptation :** Les requêtes par paraphrase/synonyme font remonter des
  nœuds pertinents que BM25 manque ; qualité de récupération mesurée sur un petit
  jeu étiqueté face à la base hash.
- **Risque :** Moyen — poids des dépendances / installation hors-ligne. Offrir
  un défaut à paliers.
- **Constats d'audit clos :** défaut seaux de hachage, porte des candidats du
  couloir d'embeddings.

## Phase 6 — Compilateur de contexte à la demande (Pilier 3)

**Objectif :** La fonctionnalité phare — « donne-moi du contexte sur X » → un
document sur mesure, cité et prêt pour les agents.

- **Pourquoi maintenant :** Dépend de P5 (qualité de récupération). Profite de
  P4 (base plus propre). La proposition de valeur centrale du produit.
- **Périmètre :** Nouveau `tesserae/context_compiler.py` : requête/amorces →
  PPR + recherche hybride → parcours de voisinage k-sauts classé → assembler les
  corps wiki → synthèse LLM optionnelle → un document markdown borné avec
  citations + contrôle de budget. Exposer comme MCP `compile_context(query|seeds,
  depth, budget)` et CLI `tesserae context …`. Rendre `agent_harness` borné par
  sujet ; router `node_context` par PPR ; tranches d'export `llms.txt` bornées
  par sujet.
- **Livrables :** Un outil qui renvoie un bundle de contexte téléchargeable et
  cité pour toute requête ; tests affirmant la forme du bundle + l'intégrité des
  citations.
- **Acceptation :** `compile_context("X")` renvoie un document multi-nœuds
  cohérent dont toutes les citations se résolvent ; le brief du harness se
  régénère par sujet plutôt que top-12 en dur.
- **Risque :** Moyen — qualité de synthèse ; garder un mode d'assemblage
  déterministe sans LLM.
- **Constats d'audit clos :** « la génération de documents à la demande
  n'existe pas », synthèse bornée par requête, harness statique, `node_context`
  non classé, exports de tout le corpus.

## Phase 7 — Unifier serve + watch + deploy + tests de cycle de vie

**Objectif :** Un processus supervisé sert le site, recompile au changement et
publie en continu ; la couche de cycle de vie obtient une couverture de tests.

- **Pourquoi maintenant :** Convergence. A besoin de P1 (démon) et du côté
  sortie (P6) pour valoir la peine de publier en continu.
- **Périmètre :** Plier `serve.py` (`TCPServer` bloquant) et `deploy.py` (git
  push manuel) dans le démon pour que serve + watch + publish partagent un
  superviseur. Publication continue/avec debounce. Ajouter les tests manquants
  `test_watch`/`test_serve`/de cycle de vie du démon. Supprimer le module mort
  déprécié `frontend.py`. Câbler la boucle TODO à typage chaîne de
  `review_workflow.py` vers un vrai chemin d'application.
- **Livrables :** `tesserae engine --serve --publish` exécute la boucle
  complète ; suite de tests de cycle de vie ; code mort supprimé.
- **Acceptation :** Une édition de source se propage à une page servie en direct
  et (optionnellement) à un déploiement publié sans commandes manuelles ; tests
  de cycle de vie verts.
- **Risque :** Faible–Moyen — surtout de l'intégration.
- **Constats d'audit clos :** division serve/watch/deploy, deploy manuel,
  `frontend.py` déprécié, stub de la boucle de revue, tests de cycle de vie
  manquants.

---

## Résumé du séquencement

| Phase | Thème | Dépend de | Parallélisable avec |
|---|---|---|---|
| P0 | Orchestrateur de pipeline | — | — |
| P1 | Démon superviseur | P0 | — |
| P2 | Moniteur de sessions en direct | P1 | P5 |
| P3 | Compilation incrémentale | P1 | P5 |
| P4 | Persistance de l'auto-amélioration | P3 | P5, P6 |
| P5 | Vrais embeddings | P0 | P2, P3, P4 |
| P6 | Compilateur de contexte à la demande | P5 | P2, P3, P4 |
| P7 | Unifier serve/watch/deploy | P1, P6 | — |

**Moteur minimal viable :** P0 + P1 + P2 + P3 — un démon en exécution qui
surveille les sessions en direct et compile de façon incrémentale. **Produit
différencié :** ajouter P5 + P6 (contexte d'agent à la demande). **Peaufiné :**
P4 + P7.
