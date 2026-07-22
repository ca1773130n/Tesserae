# Consolidation automatique — le cycle de sommeil du moteur

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

Le cerveau consolide la mémoire pendant le repos. Pendant que vous dormez, l'expérience brute de la journée est réorganisée, comprimée et intégrée — les choses récentes et bruyantes sont pliées dans une structure durable. Le moteur de Tesserae fait de même. Quand un projet devient **inactif**, le daemon toujours actif cesse d'attendre la prochaine modification et passe à la réorganisation silencieuse de ce qu'il sait déjà : il exécute une étape de distillation qui réorganise, comprime et oublie en toute sécurité la mémoire de chaque agent.

Jusqu'à présent, cette étape ne s'exécutait que sur demande — `tesserae refresh` sous pression mémoire, ou un `tesserae distill` explicite. Le moteur recompilait à chaque fichier et événement de session, mais ne se consolidait jamais automatiquement. Le **cycle de sommeil** comble cette lacune : laissez `tesserae engine` en cours d'exécution et la consolidation se produit pendant le repos, sans aucune commande à retenir.

Comme tout dans le système de [mémoire en couches](agent-memory.md), c'est **no-op sauf si vous acceptez** — le daemon consolide en cas d'inactivité, mais la distillation dessous ne fonctionne que lorsque `TESSERAE_AGENT_DISTILL` est défini.

## Quand cela se déclenche

Un fil de consolidation dédié se réveille à un **intervalle de vérification** fixe (30 secondes par défaut) et évalue deux déclencheurs indépendants par rapport à une horloge d'activité monotone:

- **Déclencheur d'inactivité.** Le projet n'a vu aucun événement de déclenchement ni aucune exécution de pipeline pendant au moins `--consolidate-idle` secondes (par défaut **300s = 5 min**). C'est le cas de la "consolidation pendant le repos" — le moteur a remarqué que vous aviez arrêté de travailler et a utilisé la pause. Un **plancher** depuis la dernière consolidation empêche les tremblements, donc un projet occupé qui vient de se calmer ne se consolide pas lors d'un déclencheur sensible. - **Déclencheur de plafond.** Au moins `--consolidate-every` secondes se sont écoulées depuis la dernière consolidation, **indépendamment de l'activité** (par défaut **21600s = 6h**). Cela garantit qu'un projet continuellement occupé se consolide toujours périodiquement au lieu de ne jamais avoir un moment tranquille. Le définir à `0` désactive le plafond — alors l'inactivité est le seul déclencheur.

Chaque édition, tour de session ou recompilation augmente l'horloge d'activité, donc la fenêtre d'inactivité ne s'écoule que pendant le repos véritable. Les deux horloges sont **monotones**, jamais d'horloge murale, et ne sont jamais persistées dans aucun artefact — le timing de consolidation ne peut jamais perturber le graphique byte-déterministe.

## Ce qui s'exécute

Chaque déclenchement charge le graphique compilé depuis `.tesserae/graph.json` (si le fichier est absent, l'étape est ignorée) et appelle le même point d'entrée `maybe_distill_on_refresh` que `tesserae refresh` utilise. Cette fonction est **triple-gated** en interne et ne lève jamais d'erreur par agent:

1. **Porte d'acceptation** — `TESSERAE_AGENT_DISTILL=1` (ou `{"agent_distill": {"enabled": true}}` dans `config.json`). Désactivé par défaut ; tout le cycle est un no-op sûr jusqu'à ce que vous le définissiez.
2. **Marque d'eau par agent** — un agent dont les conclusions n'ont pas changé depuis sa dernière distillation est ignoré.
3. **Pression mémoire par agent** — seuls les agents dont les conclusions non distillées ne rentrent plus dans la moitié d'une lecture de contexte sont consolidés (déclencheur de style MemGPT).

Donc, même quand la consolidation *se déclenche* selon un calendrier, elle ne *fonctionne* que pour les agents qui ont adhéré et ont réellement accumulé suffisamment de nouvelle mémoire pour le justifier. Voir [Mémoire d'agent en couches](agent-memory.md) pour ce que produit la distillation.

## Sécurité et déterminisme

- **S'exécute sous la porte de compilation.** La consolidation acquiert le même verrou qu'une recompilation, elle se sérialise donc avec les compilations et ne **chevauche jamais l'une d'elles**. Une compilation en attente attend une consolidation en vol et vice-versa — le graphique n'est jamais lu pendant l'écriture.
- **Ne lève jamais dans la boucle daemon.** Tout l'étape est enveloppée ; toute erreur est enregistrée et le fil continue la boucle. Une consolidation échouée ne descend jamais le moteur.
- **No-op quand la porte est fermée.** Avec `TESSERAE_AGENT_DISTILL` non défini, l'étape ne charge rien de coûteux et revient immédiatement, donc laisser le cycle de sommeil allumé ne coûte essentiellement rien.
- **Artefacts déterministes, inchangés.** Les artefacts distillés restent déterministes compte tenu de leurs entrées ; le cycle de sommeil ne change que *quand* la distillation s'exécute, jamais *ce* qu'elle produit. Le timing d'inactivité ne s'échappe jamais vers `graph.json` ou aucune couche distillée.
- **Arrêt propre.** Le fil de consolidation observe l'événement d'arrêt du daemon et se termine correctement sur `Ctrl-C` / arrêt. C'est une fonctionnalité en mode long terme uniquement : `tesserae engine ... --once` ne la démarre jamais.

## Drapeaux CLI

| Drapeau | Par défaut | Effet |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Activez ou désactivez complètement le cycle de sommeil. Activé par défaut (no-op si la porte de distillation n'est pas définie). |
| `--consolidate-idle SECONDS` | `300` | Fenêtre de repos : consolide après ce nombre de secondes sans activité. |
| `--consolidate-every SECONDS` | `21600` | Plafond : consolide au moins aussi souvent indépendamment de l'activité. `0` désactive le plafond. |
| `--consolidate-check SECONDS` | `30` | Fréquence à laquelle le fil de consolidation se réveille pour réévaluer les déclencheurs. |

## Comportement de la flotte(`--all`)

`tesserae engine --all` garde chaque projet enregistré frais en un seul processus. Chaque unité de projet obtient son propre fil de consolidation avec les mêmes boutons, et toutes les unités partagent une porte de compilation à l'échelle de la flotte — donc une consolidation dans un projet se sérialise contre les compilations dans toute la flotte, ne chevauchant jamais aucune d'elles.

## Exemple travaillé

Activez la distillation, puis exécutez le moteur avec un cycle de sommeil plus rapide pour une démo — consolidez après 60 secondes d'inactivité, et au moins toutes les 30 minutes indépendamment :

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Travaillez dans votre éditeur et vos agents comme d'habitude ; le moteur regarde, débouche et recompile chaque changement. Arrêtez une minute et le déclencheur d'inactivité se déclenche : le fil de consolidation acquiert la porte de compilation et distille tout agent sous pression mémoire — réorganisant, comprimant et oubliant en toute sécurité — puis se rendort. Continuez à travailler au-delà de la marque d'une demi-heure sans jamais faire de pause et le plafond se déclenche également, donc un projet impitoyable se consolide quand même.

Pour garder le moteur en cours d'exécution mais laisser la consolidation à des exécutions manuelles de `tesserae distill`, passez `--no-consolidate`. Pour qu'il s'exécute inactif mais jamais selon un calendrier fixe, passez `--consolidate-every 0`.
