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

## Ce qui s'exécute — trois opérations

Chaque déclenchement charge le graphe compilé depuis `.tesserae/graph.json` (si le fichier est absent, le passage est ignoré) et exécute trois opérations de consolidation, dans l'ordre. Ensemble, ils reflètent ce qu'un cerveau au repos fait : compresser le matériel récent bruyant, laisser le matériel jamais revisité s'estomper, et créer de nouvelles associations entre ce qui survit.

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

## Sécurité et déterminisme

- **S'exécute sous le portail de compilation.** La consolidation acquiert le même verrou qu'une recompilation, donc elle **s'exécute en série** avec les compilations et **ne chevauche jamais**. Une compilation en attente attend une consolidation en vol et inversement — le graphe n'est jamais lu en cours d'écriture.
- **Ne remonte jamais dans la boucle du daemon.** Le passage entier est enveloppe ; toute erreur est enregistrée et le thread continue de boucler. Une consolidation échouée ne casse jamais le moteur.
- **Non-op quand le portail est fermé.** Avec `TESSERAE_AGENT_DISTILL` non défini, le passage ne charge rien de coûteux et retourne immédiatement, donc laisser le cycle de sommeil actif ne coûte essentiellement rien.
- **Artefacts déterministes, inchangés.** Les artefacts distillés restent déterministes étant donné leurs entrées ; le cycle de sommeil ne change que *quand* la distillation s'exécute, jamais *ce* qu'elle produit. Le minutage d'inactivité ne fuit jamais dans `graph.json` ou aucune couche distillée.
- **`graph.json` reste octet-idempotent.** Ni nouvelle opération ne l'écrit. L'état d'accès vit dans le side-car `node_memory` et les connexions découvertes dans une superposition cumulative — tous deux sous `.tesserae`, tous deux fusionnés en mémoire uniquement au moment de la lecture. Les octets de graphe faisant autorité ne sont pas affectés par l'historique de récupération ou les liens découverts.
- **Arrêt propre.** Le thread de consolidation observe l'événement d'arrêt du daemon et quitte promptement sur `Ctrl-C` / arrêt. C'est une fonctionnalité en mode d'exécution longue uniquement : `tesserae engine ... --once` ne le démarre jamais.

## Drapeaux CLI

| Drapeau | Par défaut | Effet |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Activez ou désactivez complètement le cycle de sommeil. Activé par défaut (non-op si le portail de distillation n'est pas défini). |
| `--consolidate-idle SECONDS` | `300` | Fenêtre de repos : consolider après ce nombre de secondes sans activité. |
| `--consolidate-every SECONDS` | `21600` | Plafond : consolider au moins aussi souvent indépendamment de l'activité. `0` désactive le plafond. |
| `--consolidate-check SECONDS` | `30` | À quelle fréquence le thread de consolidation se réveille pour réévaluer les déclencheurs. |

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
