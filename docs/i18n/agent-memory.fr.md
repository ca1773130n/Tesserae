# Mémoire d'agent en couches — graphes de connaissance par agent

<!-- translations:start -->
<p align="center"><a href="../agent-memory.md">English</a> · <a href="agent-memory.ko.md">한국어</a> · <a href="agent-memory.zh.md">中文</a> · <a href="agent-memory.ja.md">日本語</a> · <a href="agent-memory.ru.md">Русский</a> · <a href="agent-memory.es.md">Español</a> · <a href="agent-memory.fr.md">Français</a> · <a href="agent-memory.de.md">Deutsch</a></p>
<!-- translations:end -->

Personne ne se souvient de tout — et aucune fenêtre de contexte d'agent ne peut contenir tout.
La réponse de Tesserae est une **base de connaissance en couches** : chaque agent développe
sa propre mémoire à partir de ses propres sessions, cette mémoire est **distillée** périodiquement
(organisée, compressée, polie, affinée — et oubliée en toute sécurité), et les gestionnaires
ne voient que la couche distillée de leurs rapports. Le gestionnaire du gestionnaire voit
un rollup supplémentaire. Comme une véritable organisation, aucun lecteur individuel n'a
besoin de l'archive complète.

Tout ce qui suit est optionnel et additif : les projets qui n'exécutent jamais `tesserae distill`
se comportent exactement comme avant.

## Les couches

- **L0 — graphe de projet** (`.tesserae/graph.json`). Inchangé, reste
  byte-idempotent. La passe structurelle de compilation génère maintenant un nœud `Agent`
  par agent observé plus des arêtes `performed_by` de chaque session — attribution brute,
  coût LLM nul.
- **L1 — un artefact par agent** (`.tesserae/agents/<key>/distilled.graph.json`).
  Écrit par `tesserae distill`. Un fichier graphe ordinaire limité à **une lecture de 48 ko**,
  de sorte que tout agent peut charger toute sa mémoire distillée en un seul appel.
- **L2 — rollups de gestionnaires.** Lors de la distillation d'un agent qui a des rapports,
  les L1 des rapports sont remontés : dédupliqué par lignée, groupé par preuves brutes partagées
  et conserver la meilleure note **textuellement** — la profondeur de resommation LLM est
  limitée à 1, donc un résumé n'est jamais une paraphrase d'un résumé. La même passe se
  répète récursivement à toute profondeur d'organisation.

## Identité de l'agent

Les agents sont codés `harness:account:role` — niveau de rôle, de sorte qu'un sous-agent
`reviewer` et un sous-agent `planner` développent une *expertise différente* même sur une seule machine.
Les rôles proviennent des descripteurs de sous-agent dans les transcriptions, puis des règles
de correspondance du registre déclaratif, puis reviennent à `default`.

```bash
tesserae agents init         # analyser les sessions, proposer .tesserae/agents/registry.json
tesserae agents list         # clés observées, libellés, parents, comptages de session
tesserae agents set-parent claude-code:me:reviewer claude-code:me:manager
tesserae agents rename <old> <new>   # migre atomiquement le répertoire d'artefacts + registre
```

Fonctionnement sans configuration : chaque agent observé rapporte implicitement à `org:root`,
et `agent="org"` fournit une vue d'équipe plate sans registre.

## Distillation

```bash
tesserae distill                      # tous les agents, feuilles en premier, gestionnaires en dernier
tesserae distill --agent <key>        # un agent
tesserae distill --dry-run            # estimer les appels LLM, ne rien écrire
tesserae distill --max-llm-calls 50   # budget fixe ; les exécutions limitées convergent sur les réexécutions
tesserae distill --retry-fallbacks    # réessayer les grappes qui ont échoué
tesserae distill --full               # ignorer les filigranes, redistiller à partir de zéro
```

La passe regroupe les conclusions d'un agent, résume chaque grappe (liste blanche de citations
et vérification de fidélité), et génère des notes distillées dont l'identité est une **clé de lignée** —
le hash de la preuve brute L0 sous-jacente, jamais la formulation LLM. La mise en cache est agressive
et partagée : les entrées inchangées sont ignorées par filigrane, les grappes croissantes se plient
progressivement, les défaillances du fournisseur sont arrêtées en circuit et produisent des solutions
de secours structurelles déterministes (marquées, renouvelables, jamais mises en cache comme succès).

La distillation est **optionnelle** : définissez `TESSERAE_AGENT_DISTILL=1` (ou
`{"agent_distill": {"enabled": true}}` dans `config.json`). Lorsqu'elle est activée, `tesserae refresh`
distille également automatiquement — mais seulement les agents sous *pression mémoire* (leurs conclusions
non distillées ne s'adaptent plus à la moitié d'une lecture de contexte), déclencheur de consolidation
style MemGPT.

## Oublier — jamais suppression

- **Absorber** : une conclusion décroissante, faible confiance couverte par une distillation de haute qualité
  llm est repliée en elle (`absorbed_refs`) et supprimée dans les lectures par défaut — mais reste
  accessible via `include_superseded` et `drill_down`.
- **Rétrogradation** : tout le reste tombe dans le pire des cas du corps complet à une ligne titre+référence
  dans la note d'index de l'agent. L'âge seul ne rend jamais la connaissance invisible.
- **Livre** : chaque promotion/rétrogradation/absorption est annexée au livre de l'oubli et visible
  par `tesserae lint` (`AGENT_FORGET_LEDGER`), ainsi qu'une métrique de travail non distillé par agent
  (`AGENT_UNDISTILLED_BACKLOG`).

## Lecture en tant qu'agent — argument `agent=`

Chaque outil de lecture de graphe MCP accepte `agent=` :

- **clé de travailleur** → expérience brute propre ∪ notes distillées propres, préférence distillée
  (le brut absorbé est automatiquement supprimé par une superposition dérivée au moment du chargement —
  rien n'est jamais réécrit dans `graph.json`).
- **clé de gestionnaire** → une fédération des artefacts L1 des rapports seulement. Les conclusions
  brutes ne fuient jamais vers le haut.
- **`org`** → tous les artefacts distillés, sans configuration.

Outils de soutien : `agent_view_explain` (membres + filigrane `distilled_through` de péremption —
l'ancienneté de l'expertise de chaque rapport), et `drill_down` (résoudre `member_refs` d'une note
distillée de retour aux preuves brutes L0 avec état vivant/modifié/absorbé/disparu — chaque appel est
enregistré à titre de suivi). `compile_context --multi-pool` réserve des emplacements budgétaires pour
les notes distillées et les profils d'expertise, et étiquette les connaissances obsolètes ou de qualité
fallback dans la sortie.

## La boucle de croissance

- **Harnais par agent** : le mode d'agent `write_harness` émet un répertoire de harnais par agent
  dont la configuration MCP atteint la vue résolue de cet agent, plus une page de mission `purpose.md`
  ensemencée une seule fois générée à partir de son profil d'expertise.
- **Orientation par agent** : diriger la distillation d'un agent via `.tesserae/extraction-guidance-<key>.md`,
  en couches sur le `.tesserae/distill-guidance.md` au niveau du projet. Modifier le flux d'un agent
  redistille uniquement cet agent.
- **Ponts sémantiques** (optionnel) : liez les distillations *connexes* entre agents avec des arêtes
  `shares_concept_with` dans les vues gestionnaire/organisation — des arêtes, jamais des fusions.
- **Cartes thématiques** : `agent_topics` enroule l'ensemble de distillations d'un agent dans un
  `topics.md` déterministe — la table des matières de l'agent.
- **Promotion du sous-agent** : les exécutions de sous-agent typées génèrent des conclusions sous
  la propre clé du sous-agent, de sorte que le travail délégué s'accumule dans l'expertise du délégué.

## Garanties de déterminisme

Le graphe de projet reste byte-idempotent ; les artefacts distillés sont déterministes étant donné
(octets de graphe, registre, répertoire de cache, artefact antérieur, options). Le temps est toujours
l'**horloge de corpus** — l'instant le plus récent dans les sessions elles-mêmes, récursivement le
filigrane enfant le plus récent pour les gestionnaires — jamais horloge murale. L'identité des nœuds
ne dépend pas de la prose LLM. Une sonde lint rejette les métadonnées en forme d'horodatage/compteur
sur les nœuds au niveau de l'agent, car cette classe exacte d'état a déjà rompu le byte-idempotent.

Justification de conception complète : `docs/superpowers/specs/2026-07-19-layered-agent-kg.md`.
