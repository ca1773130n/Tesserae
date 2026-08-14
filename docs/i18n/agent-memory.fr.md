# Mémoire d'agent en couches — graphiques de connaissances par agent

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Personne ne se souvient de tout — et aucune fenêtre contextuelle d'agent ne peut tout contenir.
La réponse de Tesserae est une **base de connaissances en couches** : chaque agent cultive sa propre mémoire à partir de ses propres sessions, cette mémoire est périodiquement **distillée** (organisée, compactée, polie, affinée — et oubliée en toute sécurité), et les gestionnaires ne voient que la couche distillée de leurs rapports. Le gestionnaire du gestionnaire voit un cumul supplémentaire. Comme dans une organisation réelle, aucun lecteur unique n'a jamais besoin de l'ensemble des archives.

Tout ce qui suit est facultatif et additif : les projets qui n'exécutent jamais `tesserae distill` se comportent exactement comme avant.

## Les couches

- **L0 — le graphique du projet** (`.tesserae/graph.json`). Inchangé. Reste
  idempotent par octet. La passe structurelle de la compilation frappe maintenant un nœud `Agent`
  par agent observé plus des arêtes `performed_by` de chaque session — attribution brute, coût LLM zéro.
- **L1 — un artefact par agent** (`.tesserae/agents/<key>/distilled.graph.json`).
  Écrit par `tesserae distill`. Un fichier graphique ordinaire limité à **une lecture de 48 k**,
  donc n'importe quel agent peut charger sa mémoire distillée entière en un seul appel.
- **L2' — cumuls de gestionnaires.** Lors de la distillation d'un agent qui a des rapports, cumule
  L1 des rapports : déduplication par lignée, regroupement par preuve brute partagée, et
  porter la meilleure note **au pied de la lettre** — la profondeur de résumé LLM est plafonnée à 1, donc un résumé n'est jamais une reformulation d'un résumé. Le même passage récurse à toute profondeur organisationnelle.

## Identité de l'agent

Les agents sont codés `harness:account:role` — niveau de rôle, donc un sous-agent `reviewer` et un sous-agent `planner` développent *différentes* expertises même sur une machine. Les rôles proviennent de descripteurs de sous-agent dans les transcriptions, puis de règles de correspondance de registre déclaratif, puis reviennent à `default`.

```bash
tesserae agents init         # analyser les sessions, INFÉRER l'organisation, écrire .tesserae/agents/registry.json
tesserae agents tree         # l'organigramme, avec les décomptes de session + l'obsolescence de la distillation
tesserae agents list         # clés observées, étiquettes, parents, décomptes de session
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migre le répertoire d'artefacts + le registre de manière atomique
```

`init` déduit la hiérarchie du signal de rôle. Un rôle de sous-agent(`claude-code:me:reviewer`) est subordonné à l'agent principal qui l'a engendré(`claude-code:me:default`), donc une commande vous donne une organisation multi-niveaux fonctionnelle — `set-parent` n'est pas nécessaire. Passez `--flat` pour forcer l'ancien graphique "tous sous la racine". `set-parent` est uniquement pour les hiérarchies plus profondes conçues à la main. La configuration zéro fonctionne toujours : sans registre, chaque agent rapporte à `org:root` et `agent="org"` est la vue d'ensemble de l'équipe plate.

## Distillation

```bash
tesserae distill                      # chaque agent, feuilles en premier, gestionnaires en dernier
tesserae distill --agent <key>        # un agent
tesserae distill --dry-run            # estimer les appels LLM, ne rien écrire
tesserae distill --max-llm-calls 50   # budget rigoureux ; les exécutions limitées convergent sur les réexécutions
tesserae distill --retry-fallbacks    # réessayer les grappes qui ont échoué
tesserae distill --full               # ignorer les filigranes, redistiller à partir de zéro
```

La passe regroupe les résultats de l'agent, résume chaque cluster (liste blanche de citations et vérification de fidélité) et frappe les notes distillées dont l'identité est une **clé de lignée** — le hachage de la preuve brute L0 en dessous, jamais la formulation du LLM. La mise en cache est agressive et partagée : les entrées inchangées sont ignorées en filigrane, les clusters croissants se replient progressivement, les défaillances du fournisseur sont interrompues et produisent des replis structurels déterministes (marqués, réessayables, jamais mis en cache comme succès).

La distillation est **facultative** : définissez `TESSERAE_AGENT_DISTILL=1` (ou `{"agent_distill": {"enabled": true}}` dans `config.json`). Lorsqu'il est activé, `tesserae refresh` distille également automatiquement — mais uniquement les agents sous **pression mémoire** (leurs résultats non distillés ne rentrent plus en demi-lecture contextuelle), le déclencheur de consolidation de style MemGPT.

## Consolidation automatique (cycle de sommeil)

Vous n'avez pas besoin de vous souvenir de distiller. Comme un cerveau qui consolide la mémoire au repos, le démon toujours activé `tesserae engine` se consolide tout seul chaque fois qu'un projet devient **inactif**(pas d'éditions ou de sessions pendant quelques minutes), plus un plafond périodique afin qu'un projet continuellement occupé se consolide toujours. Chaque exécution effectue trois opérations : **compresse et oublie**(la passe de distillation ci-dessous), laisse le savoir non récupéré **s'estomper par désuétude**(la dégradation LRU ci-dessus), et **découvre de nouvelles connexions** entre ce qui survit. L'étape de distillation enveloppe exactement le déclencheur `maybe_distill_on_refresh` décrit ci-dessus — la même porte d'acceptation, filigrane par agent et vérifications de pression mémoire — donc le cycle est un non-op à moins que `TESSERAE_AGENT_DISTILL` ne soit défini, s'exécute sous la porte de compilation et ne perturbe les artefacts déterministes.

Comportement complet, drapeaux CLI(`--consolidate-idle` / `--consolidate-every` / `--consolidate-check`) et notes de flotte :
[docs/engine-consolidation.md](engine-consolidation.fr.md).

## Oubli — jamais suppression

- **Absorber**：une découverte décadente, de basse confiance couverte par une distillée de qualité llm est repliée en elle (`absorbed_refs`) et supprimée dans les lectures par défaut — mais reste accessible via `include_superseded` et `drill_down`.
- **Rétrograder**: tout le reste dans le pire des cas tombe du corps complet à une ligne de titre+référence dans la note d'index de l'agent. L'âge seul ne rend jamais la connaissance invisible.
- **Par désuétude (LRU)**: la décadence est entraînée par *récence de récupération*, pas seulement l'âge de création. Lire les accès de surface d'enregistrement — `last_accessed_at` / `access_count` — dans un `node_memory` side-car (jamais dans `graph.json`). La distillation fusionne cet état d'accès en direct dans sa vue de travail **avant** de calculer la décadence, de sorte qu'une découverte que personne ne récupère jamais se dégrade et devient admissible à l'absorption ou à la rétrogradation, tandis qu'une qui a été lue récemment est conservée quel que soit l'âge. Un side-car vide reproduit exactement l'ancien comportement d'âge uniquement.
- **Livre**：chaque promotion/rétrogradation/absorption est ajoutée à un livre d'oubli et présentée par `tesserae lint` (`AGENT_FORGET_LEDGER`), ainsi qu'une métrique de carnet non distillé par agent (`AGENT_UNDISTILLED_BACKLOG`).
- **Qui l'a lu** (sur activation explicite) : le compteur d'accès ci-dessus dit
  qu'un nœud a été lu, pas par qui — un agent bavard qui sonde un nœud et un
  humain qui le lit une fois sont donc la même entrée pour l'oubli. Définissez
  `TESSERAE_READ_AUDIT=1` sur le serveur MCP et chaque lecture est aussi
  enregistrée comme `{tool, actor, node_ids, at, tesserae_version}` dans le même
  sidecar `.tesserae/sqlite.db`, lisible via l'outil `read_audit` avec un
  décompte par acteur. L'acteur est l'argument `agent` quand l'appel résout une
  vue d'agent, sinon `TESSERAE_ACTOR` ; sans l'un ni l'autre, la lecture est
  enregistrée comme anonyme plutôt qu'attribuée à un nom inventé. **Désactivé
  par défaut, délibérément** — un journal toujours actif sur chaque surface de
  lecture transforme chaque lecture en écriture. L'éteindre arrête
  l'enregistrement sans effacer l'enregistré, et rien de tout cela n'atteint
  jamais `graph.json`.

## Connexions découvertes

Au-delà de la compression et de l'oubli, la consolidation **découvre également de nouvelles connexions** entre les notes distillées — entre les agents du projet, pas seulement au sein d'un agent. Il intègre les notes et les lie comme des arêtes `shares_concept_with` (portant un marqueur `federation_semantic`). La découverte est **fermée par l'intégration** — elle s'exécute uniquement quand un vrai backend d'intégration est configuré et ignore le stub de hachage — elle ne fabrique donc jamais de fausses liaisons. Les bords sont écrites dans une superposition de **side-car cumulatif** sous `.tesserae`, *jamais* dans `graph.json`, et sont fusionnés en mémoire au moment de la requête/PPR/fédération (exactement comme la superposition de vue de portée). Chaque cycle de consolidation déduplique et prolonge ce que les cycles antérieurs ont découvert. Voir
[docs/engine-consolidation.md](engine-consolidation.fr.md) pour l'opération du cycle de sommeil qui l'exécute.

## Lecture d'une vue de portée

Depuis la **CLI**, `--agent KEY` définit la portée de `query`, `ask` et `context`:

```bash
tesserae query "release checklist" --agent claude-code:me:reviewer   # vue travailleur
tesserae ask "what does my team know about deploys?" --agent org      # l'équipe entière
tesserae agents show claude-code:me:manager    # mode, membres, obsolescence
tesserae agents drill SessionInsight:abc123 --agent claude-code:me:reviewer
```

En **MCP**, chaque outil de lecture de graphe accepte le même `agent=`. Dans les deux cas, la clé se résout à l'une des suivantes :

- **clé de travailleur** → expérience brute propre ∪ notes distillées propres, distillat préféré (la brute absorbée est automatiquement supprimée par une superposition dérivée au chargement — jamais écrite dans `graph.json`).
- **clé de gestionnaire** → une fédération des seuls artefacts L1 des rapports. Les résultats bruts ne fuient jamais vers le haut.
- **`org`** → tous les artefacts distillés, configuration zéro.

Outils d'assistance : `agents show` / `agent_view_explain`(membres + filigrane `distilled_through` de l'ancienneté — quelle ancienneté l'expertise de chaque rapport a), et `agents drill` / `drill_down`(résoudre `member_refs` de note distillée en preuve brute L0 avec le statut vivant/modifié/absorbé/disparu — chaque appel est enregistré dans le journal d'audit). `compile_context --multi-pool` réserve des emplacements de budget pour les notes distillées et les profils d'expertise et étiquette les connaissances obsolètes ou de qualité de secours dans la sortie. Seul un nœud réellement créé par un producteur peut prendre un emplacement — les passes de distillation, la passe session-event ou le `graph_write` d'un agent — de sorte qu'un pool dont le type n'est peuplé que par l'extraction documentaire reste vide, et la CLI comme `knobs.pool_reservations` nomment les pools qui n'ont rien renvoyé.

## La boucle de croissance

- **Harnais par agent** : le mode agent `write_harness` émet un répertoire de harnais par agent dont la configuration MCP atteint la vue résolue de cet agent, plus une page de mission `purpose.md` à graines uniques générée à partir de son profil d'expertise.
- **Orientation par agent** : diriger la distillation d'un agent via `.tesserae/extraction-guidance-<key>.md`, stratifiée sur le niveau projet `.tesserae/distill-guidance.md`. L'édition du flux d'un agent redistille uniquement cet agent.
- **Ponts sémantiques** (facultatif) : lier les *distillés connexes* entre les agents avec des arêtes `shares_concept_with` dans les vues gestionnaire/organisation — arêtes, jamais fusions.
- **Cartes thématiques** : `agent_topics` déploie l'ensemble de distillés d'un agent en `topics.md` déterministe — la table des matières de l'agent.
- **Promotion de sous-agent** : les exécutions de sous-agent typées émettent des découvertes sous la clé propre du sous-agent, de sorte que le travail délégué s'accumule dans l'expertise du délégué.

## Garanties de déterminisme

Le graphique du projet reste idempotent par octet ; les artefacts distillés sont
déterministes étant donné (octets de graphique, registre, répertoire de cache, artefact antérieur,
options). Le temps est toujours l'**horloge du corpus** — l'instant le plus récent dans
les sessions elles-mêmes, récursivement le filigrane enfant le plus récent pour les gestionnaires —
jamais l'horloge murale. L'identité du nœud ne dépend jamais de la prose LLM. Une sonde Lint
rejette les métadonnées en forme d'horodatage/compteur sur les nœuds de couche agent, car
cette classe exacte d'état a brisé l'idempotence par octet avant.

Rationnelle de conception complet : `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
