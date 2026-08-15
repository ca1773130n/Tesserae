# Consolidation automatique — le cycle de sommeil du moteur

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

Le cerveau consolide la mémoire pendant le repos. Pendant que vous dormez, l'expérience brute de la journée est réorganisée, comprimée et intégrée — les choses récentes et bruyantes sont repliées dans une structure durable. Le moteur Tesserae fait la même chose. Quand un projet devient **inactif**, le daemon toujours actif cesse d'attendre la prochaine édition et passe le temps calme à réorganiser ce qu'il sait déjà : il **compresse et oublie** les souvenirs récents bruyants, laisse le savoir jamais récupéré **s'estomper par non-utilisation**, et **découvre de nouvelles connexions** entre ce qui survit.

Jusqu'à présent, ce passage n'était exécuté que lorsque vous le demandiez — `tesserae refresh` sous pression mémoire, ou un `tesserae distill` explicite. Le moteur recompilait à chaque fichier et événement de session, mais ne consolidait jamais automatiquement. Le **cycle de sommeil** comble cet écart : laissez `tesserae engine` s'exécuter et la consolidation se produit pendant le repos, sans aucune commande à retenir.

Comme tout dans le système de [mémoire en couches](agent-memory.fr.md), c'est **un non-op à moins que vous ne participiez** — le daemon consolide en inactivité, mais la distillation en dessous ne fonctionne que lorsque `TESSERAE_AGENT_DISTILL` est défini.

## Quand cela se déclenche

Un thread de consolidation dédié se réveille à un **intervalle de vérification** fixe (30 secondes par défaut) et évalue deux déclencheurs indépendants par rapport à une horloge d'activité monotone :

- **Déclencheur d'inactivité.** Le projet n'a pas vu d'événement de déclenchement ni d'exécution de pipeline pendant au moins `--consolidate-idle` secondes (par défaut **300 secondes = 5 minutes**). C'est le cas de la "consolidation pendant le repos" — le moteur a remarqué que vous aviez arrêté de travailler et a utilisé la pause. Un **plancher** depuis la dernière consolidation empêche le flottement, donc un projet occupé qui vient de devenir tranquille ne consolide pas sur un déclencheur sensible.
- **Déclencheur de plafond.** Au moins `--consolidate-every` secondes se sont écoulées depuis la dernière consolidation, **indépendamment de l'activité** (par défaut **21600 secondes = 6 heures**). Cela garantit qu'un projet continuellement occupé se consolide quand même périodiquement au lieu de ne jamais avoir un moment calme. Le définir à `0` désactive le plafond — alors l'inactivité est le seul déclencheur.

Chaque édition, tour de session ou recompilation augmente l'horloge d'activité, donc la fenêtre d'inactivité n'elapses que pendant un vrai repos. Les deux horloges sont **monotones**, jamais l'heure murale, et ne sont jamais persistées dans aucun artefact — le minutage de la consolidation ne peut jamais déranger le graphe déterministe en octets.

## Ce qui s'exécute — cinq opérations

Chaque déclenchement charge le graphe compilé depuis `.tesserae/graph.json` (si le fichier est absent, le passage est ignoré) et exécute cinq opérations de consolidation, dans l'ordre. Ensemble, ils reflètent ce qu'un cerveau au repos fait : compresser le matériel récent bruyant, laisser le matériel jamais revisité s'estomper, créer de nouvelles associations entre ce qui survit, et se préparer — passer un peu d'effort maintenant, pendant que personne n'attend, sur les descriptions qu'un lecteur voudra ensuite.

### 1. Compresser / oublier — distillation

Appelle le même point d'entrée `maybe_distill_on_refresh` que `tesserae refresh` utilise pour réorganiser, compresser et oublier en toute sécurité la mémoire de chaque agent. Cette fonction est **triple controllée** en interne et ne remonte jamais pour un échec par agent :

1. **Portail d'acceptation** — `TESSERAE_AGENT_DISTILL=1` (ou `{"agent_distill": {"enabled": true}}` dans `config.json`). Désactivé par défaut ; le cycle entier est un non-op sûr jusqu'à ce que vous le configuriez.
2. **Repère par agent** — un agent dont les conclusions n'ont pas changé depuis sa dernière distillation est ignoré.
3. **Pression mémoire par agent** — seuls les agents dont les conclusions non distillées ne rentrent plus dans la moitié d'une lecture de contexte sont consolidés (déclencheur de style MemGPT).

Donc, même lorsque la consolidation **se déclenche** selon un calendrier, elle ne **fonctionne** que pour les agents qui ont opted in et qui ont réellement accumulé suffisamment de nouvelle mémoire pour le justifier. Consultez [Mémoire d'agent en couches](agent-memory.fr.md) pour ce que produit la distillation.

### 2. Oublier par non-utilisation — décroissance LRU à la récupération, pas seulement par âge

La décroissance de distillation n'est plus uniquement entraînée par l'âge de création. Chaque surface de lecture enregistre l'accès aux conclusions qu'elle retourne — `last_accessed_at` et `access_count` — dans un **side-car `node_memory`**, jamais dans `graph.json`. Avant que le passage de distillation calcule la décroissance, il fusionne cet état d'accès en direct dans sa vue de travail, donc une conclusion qui n'a pas été récupérée depuis qu'elle a été créée se dégradera et devient éligible pour être absorbée ou rétrogradée, tandis qu'une qui a été récemment lue reste fraîche quel que soit son âge. C'est la **récence de récupération**, l'intuition LRU (moins récemment utilisé) appliquée à la mémoire : le savoir que vous continuez à extraire reste ; le savoir que personne ne demande s'estompe en premier. Un side-car vide reproduit exactement l'ancien comportement basé sur l'âge seul, c'est donc totalement rétro-compatible.

### 3. Associer — découvrir de nouvelles connexions

L'opération finale recherche des relations *nouvelles* entre ce qui a survécu. Elle intègre les notes distillées et relie les paires dont les significations sont proches — **controllée par intégration**, donc elle ne s'exécute que lorsqu'un vrai backend d'intégration est configuré (le stub hash est ignoré, ne produisant jamais de liens bruyants). La découverte s'exécute dans le projet et **entre les agents**, et les connexions qu'elle trouve sont frappées comme des arêtes `shares_concept_with` portant un marqueur `federation_semantic`.

De manière cruciale, ces arêtes découvertes sont écrites dans une **superposition de side-car** sous `.tesserae`, *jamais* dans `graph.json`. La superposition **s'accumule entre les cycles** — chaque passage d'association déduplique par rapport et étend ce que les passages antérieurs ont trouvé. Au moment de la lecture (requête, expansion PPR, vues de fédération), la superposition est fusionnée dans le graphe **uniquement en mémoire**, exactement comme la superposition de vue par agent — donc le `graph.json` déterministe en octets n'est jamais touché. L'opération entière est enveloppe et ne remonte jamais dans la boucle du daemon.

### 4. Résumer — préchauffer les caches de communauté dans lesquels les agents descendent

`graph_map` sert une carte par portée. Une portée dont le cache de résumé est froid obtient une carte *structurelle* déterministe — un décompte des membres et une liste des meilleurs membres — et le premier agent qui la visite paie un appel LLM synchrone pour obtenir la prose. Cette opération déplace ce coût en dehors du chemin de lecture : dans un budget par tick (`--summarize-budget`, par défaut 25 ; `0` le désactive) il matérialise les résumés pour les portées les plus susceptibles d'être visitées ensuite, de sorte que la visite trouve un cache chaud.

Les candidats sont classés par **demande** — les augmentations d'accès propre de la portée à partir de la traversée `graph_map` plus les décomptes d'accès de ses membres — puis par taille, degré et niveau, dans un ordre total, donc deux ticks sur l'état identique choisissent les mêmes portées. Un cache qui est déjà chaud et encore dont le digest est encore valide ne coûte rien ; seule une matérialisation froide le fait. Sans un client LLM, l'opération entière est un non-op.

### 5. Brève — préchauffer les brèves de domaine de la charte

La même forme, un axe au-dessus : les candidats sont les domaines vivants de [la charte](../README.md) plutôt que les communautés du dendrogramme. Un domaine froid s'affiche comme une carte *structurelle* partout où il apparaît — dans `graph_map`, dans le corpus de notation de `charter_route`, et dans le recensement `CHARTER_FALLBACK` de lint — donc ce passage est ce qui donne à l'institution de la charte de la prose.

Le budget est son propre commutateur (`--brief-budget`, par défaut 8 ; `0` le désactive), délibérément séparé de `--summarize-budget` pour qu'aucune opération ne puisse affamer l'autre, et délibérément plus petit : les **divisions** de la charte sont ce que `graph_map` sert comme son ensemble de cartes racine, et il n'y en a que quelques-unes, donc 8 réchauffe le point d'entrée au premier tick d'inactivité et les niveaux plus profonds suivent à 8 par tick derrière.

L'ordre est **en largeur d'abord**, pas un classement de demande. L'ensemble de membres d'un domaine contient son sous-arbre entier, donc la demande d'un parent domine toujours celle de ses enfants et aucun domaine n'est réchauffé avant ses ancêtres. C'est délibéré : les agents descendent de la racine, donc la carte grossière est celle lue en premier et celle qui mérite d'avoir de la prose. Les décomptes d'accès ordonnent les domaines où ni l'un ni l'autre ne contient l'autre, et les **divisions** vivantes — domaines sans parent vivant, la même règle que la racine de `graph_map` utilise, pas `tier == 1` — se classent avant tout le reste.

Certains domaines ne coûtent jamais une fente de budget, car une fente est destinée à être un appel LLM : domaines retirés du service, le recensement `intake` (qui n'a pas de sujet, donc une brève écrite à partir de 25 de ses milliers de membres serait une description confiante d'une fraction d'un pour cent), un domaine dont les membres ont quitté le graphe, et tout ce qui est déjà chaud. Et un domaine dont la matérialisation **échoue** — le plus souvent parce que sa prose n'a cité aucun de ses enfants et a été rejetée — est tenu à distance pendant un nombre de ticks doublant plutôt que réessayé au même classement éternellement, de sorte qu'un domaine définitivement irréchauffable ne peut pas tenir une fente qu'un domaine réchauffable pourrait utiliser.

### Quel est le coût par heure

Les deux budgets sont par **tick**, et un tick se déclenche au maximum une fois par fenêtre `--consolidate-idle`. Par défaut :

| | par tick | ticks/heure à `--consolidate-idle 300` | plafond |
|---|---|---|---|
| Résumer | 25 | 12 | 300 appels LLM/heure |
| Brève | 8 | 12 | 96 appels LLM/heure |
| **Total** | **33** | **12** | **396 appels LLM/heure** |

C'est un **plafond atteint uniquement pendant que les caches sont froids**, et il décroît jusqu'à **zéro** : un cache chaud et dont le digest est encore valide ne coûte aucun appel et aucune fente, de sorte qu'une fois que les portées et domaines d'un projet sont résumés, le cycle de sommeil ne dépense rien jusqu'à ce que le graphe change. Réglez l'un ou l'autre budget à `0` pour désactiver son opération, ou augmentez `--consolidate-idle` pour rendre les ticks plus rares.

**Un budget est un plafond, pas un quota.** Les deux budgets sont dépensés *séquentiellement*
à l'intérieur d'un tick, et le tick détient le portail de compilation pour tout le passage — donc par
défaut, un tick pourrait occuper le portail sur 33 appels LLM consécutifs. Une
sauvegarde de fichier qui se produit en milieu de tick devait attendre tous les appels restants avant que son
exécution de pipeline puisse commencer, ce qui avec un fournisseur CLI est des minutes. Les deux
boucles de préchauffage vérifient maintenant, au début de chaque itération, si une exécution de pipeline
est bloquée sur le portail, et **abandonnent leur budget restant** si c'est le cas :

- le contrôle se fait *entre* les appels, jamais en milieu d'appel, donc l'exécution déjà en
  vol se termine toujours et le pipeline n'attend que cet appel au maximum ;
- arrêter tôt est sans perte. Le préchauffage est idempotent, donc une portée ou un domaine que le
  tick n'a jamais atteint est simplement toujours froid au suivant, au même rang —
  rien n'est perdu, corrompu ou payé deux fois ;
- un domaine abandonné ne reçoit **pas** de coup de back-off. Les coups sont pour un domaine
  dont la tentative de préchauffage a brûlé un appel et a échoué ; un abandonné n'a jamais été
  tenté, donc le pénaliser repousserait un domaine préchauffable plus bas dans la queue parce qu'un
  fichier sans rapport a été enregistré ;
- c'est signalé, pas silencieux. Le dict de résumé du tick obtient `abandoned` et
  `unspent` (combien de slots de budget n'ont pas été utilisés), donc le journal du daemon distingue
  "arrêté pour un pipeline" de "il n'y avait rien à préchauffer".

**Pourquoi ici et pas dans la compilation.** Une brève coûte un appel LLM. Les frapper pendant la compilation mettrait un appel par domaine à chaque compilation, et la compilation est le chemin que ce projet maintient déterministe et bon marché. Les frapper paresseusement à la lecture signifierait qu'un appel `graph_map` pourrait bloquer sur un modèle. Le cycle de sommeil inactif est le seul endroit qui reste qui peut dépenser un appel que personne n'attend.

## Sécurité et déterminisme

- **S'exécute sous le portail de compilation, pour tout le passage.** La consolidation acquiert le même verrou qu'une recompilation, donc elle **s'exécute en série** avec les compilations et **ne chevauche jamais**. Une compilation en attente attend une consolidation en vol et inversement — le graphe n'est jamais lu en cours d'écriture. Le portail n'est délibérément **pas** libéré entre les appels LLM : chaque opération d'un tick lit le `graph.json` unique que le tick a chargé, de sorte que rendre le portail en milieu de passage laisserait une compilation réécrire le graphe dessous et ferait que les brèves écrites tôt dans un passage décriraient un graphe différent de celles écrites tard. C'est pourquoi un tick attendant un pipeline **abandonne son budget restant** plutôt que de libérer le portail — il échange le préchauffage spéculatif contre la latence, jamais la cohérence pour la latence.
- **Ne remonte jamais dans la boucle du daemon.** Le passage entier est enveloppe ; toute erreur est enregistrée et le thread continue de boucler. Une consolidation échouée ne casse jamais le moteur.
- **Non-op quand le portail est fermé.** Avec `TESSERAE_AGENT_DISTILL` non défini, le passage ne charge rien de coûteux et retourne immédiatement, donc laisser le cycle de sommeil actif ne coûte essentiellement rien.
- **Artefacts déterministes, inchangés.** Les artefacts distillés restent déterministes étant donné leurs entrées ; le cycle de sommeil ne change que *quand* la distillation s'exécute, jamais *ce* qu'elle produit. Le minutage d'inactivité ne fuit jamais dans `graph.json` ou aucune couche distillée.
- **`graph.json` reste octet-idempotent.** Aucune opération ici ne l'écrit. L'état d'accès vit dans le side-car `node_memory`, les connexions découvertes dans une superposition cumulative, et les résumés comme les brèves de domaine dans le cache `community_summaries` — tous sous `.tesserae`, tous fusionnés en mémoire uniquement au moment de la lecture. Les octets de graphe faisant autorité ne sont affectés ni par l'historique de récupération, ni par les liens découverts, ni par la prose préchauffée. Les résumés et les brèves sont des **caches, pas de la connaissance** : supprimer le répertoire de cache ne coûte au prochain lecteur qu'une carte structurelle, rien de plus.
- **Arrêt propre.** Le thread de consolidation observe l'événement d'arrêt du daemon et quitte promptement sur `Ctrl-C` / arrêt. C'est une fonctionnalité en mode d'exécution longue uniquement : `tesserae engine ... --once` ne le démarre jamais.

## Drapeaux CLI

| Drapeau | Par défaut | Effet |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Activez ou désactivez complètement le cycle de sommeil. Activé par défaut (non-op si le portail de distillation n'est pas défini). |
| `--consolidate-idle SECONDS` | `300` | Fenêtre de repos : consolider après ce nombre de secondes sans activité. |
| `--consolidate-every SECONDS` | `21600` | Plafond : consolider au moins aussi souvent indépendamment de l'activité. `0` désactive le plafond. |
| `--consolidate-check SECONDS` | `30` | À quelle fréquence le thread de consolidation se réveille pour réévaluer les déclencheurs. |
| `--summarize-budget N` | `25` | Max appels LLM par tick dépensés préchauffant les résumés de communauté. `0` désactive l'opération RÉSUMER. |
| `--brief-budget N` | `8` | Max appels LLM par tick dépensés préchauffant les brèves de domaine de charte. `0` désactive l'opération BRÈVE. |

## Comportement de flotte(`--all`)

`tesserae engine --all` maintient chaque projet enregistré frais dans un processus. Chaque unité de projet obtient son propre thread de consolidation avec les mêmes commandes, et tous les unités partagent un portail de compilation de flotte — donc une consolidation dans un projet s'exécute en série avec les compilations dans toute la flotte, ne chevauchant jamais aucun.

## Exemple travaillé

Activez la distillation, puis exécutez le moteur avec un cycle de sommeil plus rapide pour une démonstration — consolidez après 60 secondes inactives, et au moins toutes les 30 minutes quel qu'en soit:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Travaillez dans votre éditeur et vos agents comme d'habitude ; le moteur observe, supprime le rebond et recompile chaque modification. Arrêtez pendant une minute et le déclencheur d'inactivité se déclenche : le thread de consolidation acquiert le portail de compilation et distille tout agent sous pression mémoire — réorganisant, comprimant et oubliant en toute sécurité — puis se rendort. Continuez à travailler au-delà de la marque de trente minutes sans jamais vous arrêter et le plafond se déclenche aussi, donc un projet impitoyable se consolide toujours.

Pour garder le moteur en marche mais laisser la consolidation à des exécutions manuelles de `tesserae distill`, passez `--no-consolidate`. Pour qu'il s'exécute en inactivité mais jamais selon un calendrier fixe, passez `--consolidate-every 0`.
