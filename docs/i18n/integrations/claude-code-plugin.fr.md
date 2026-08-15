# Plugin Claude Code

<!-- translations:start -->
<p align="center"><a href="../../integrations/claude-code-plugin.md">English</a> · <a href="claude-code-plugin.ko.md">한국어</a> · <a href="claude-code-plugin.zh.md">中文</a> · <a href="claude-code-plugin.ja.md">日本語</a> · <a href="claude-code-plugin.ru.md">Русский</a> · <a href="claude-code-plugin.es.md">Español</a> · <a href="claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae fournit un plugin [Claude Code](https://docs.claude.com/en/docs/claude-code) afin que vous puissiez exécuter l'ensemble du workflow Tesserae depuis l'intérieur d'une session TUI — commandes slash, un serveur MCP auto-enregistré, une compétence qui oriente l'agent, et quatre hooks qui bouclent la boucle agent↔mémoire-de-projet. Le plugin se trouve dans le dépôt à `plugin/`.

## Installation

```bash
# Prérequis : `tesserae` déjà installé (`pip install tesserae` ou `pipx install tesserae`).
/plugin install /path/to/Tesserae/
```

Prérequis : `tesserae` déjà installé (`pip install tesserae` ou `pipx install tesserae`). En cas d'installation via pipx, assurez-vous que `~/.local/bin` est dans le PATH que Claude Code hérite au lancement.

## Ce qui est livré

* **9 commandes slash** — sept wrappers 1:1 autour du CLI (`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) plus deux macros de workflow (`/tesserae:refresh` enchaîne import + compile + obsidian-sync ; `/tesserae:status` affiche les compteurs du graphe et la dernière compilation).
* **Auto-enregistrement du serveur `tesserae`** — l'agent obtient toute la surface d'outils en tant que `mcp__plugin_tesserae_tesserae__<tool>` sans éditions manuelles de configuration : requêtes de graphe (`search_nodes`, `node_context`, `graph_ppr`, `search_facts`), le compilateur à la demande `compile_context` / `list_communities` / `fresh_insights`, la mémoire de session (`ask`, `list_sessions`, `find_session_findings`, `find_code_symbol_mentions`) et la configuration guidée (`tesserae_setup_plan` / `tesserae_setup_apply`). La liste complète est dans [mcp.fr.md](mcp.fr.md).
* **Compétence `using-tesserae`** — se charge automatiquement lorsque vous posez des questions sur le graphe typé, le rappel de sessions passées, le contenu wiki/vault, ou tout workflow tesserae. Apprend à l'agent quel outil MCP utiliser vs quelle commande slash suggérer.
* **5 hooks** — `SessionStart` imprime un résumé du graphe ; `SessionEnd` exécute en arrière-plan import+compile pour que les insights de cette conversation deviennent des nœuds du graphe pour la prochaine session ; deux hooks `PostToolUse` se déclenchent sur `Edit`/`Write`/`MultiEdit` — l'un fait une recompilation incrémentielle optionnelle sur les éditions de docs/, l'autre applique un debounce (~30 s) à la synchronisation du graphe de code ; `PreToolUse` (sur `Bash`) filtre les compilations de grands graphes via un dialogue de confirmation.

> **La compilation en fin de session est opportuniste, pas garantie.** Le hook
> détache sa tâche d'arrière-plan avec `setsid` lorsqu'il existe, et retombe sur
> `nohup` sinon. macOS ne fournit pas `setsid`, et `nohup` se contente d'ignorer
> `SIGHUP` — il laisse la tâche dans le groupe de processus de la session — donc
> un harness qui récupère le groupe à la fermeture de la session peut encore tuer
> la compilation en plein vol. Ce qui reste alors est récupérable, pas intact :
> `graph.json` est écrit par rename atomique et n'est donc jamais un demi-fichier,
> mais les projections générées `wiki/` et `site/` sont effacées au début de
> l'écriture des artefacts et le magasin SQLite est écrit après `graph.json`, si
> bien qu'un kill dans cette fenêtre les laisse absentes ou en retard d'une
> compilation. Cela n'arrive jamais en silence pour autant :
> `.tesserae/manifest.json` ne marque un document `graphed` qu'une fois les
> artefacts posés, donc la prochaine `compile --changed-only` refuse son no-op,
> annonce `graph.json is not known to cover every tracked document` et ré-extrait
> tout le corpus, ce qui reconstruit aussi les projections.
> Cette ré-extraction de corpus entier est un ré-parcours, pas un ré-achat. Les réponses des fournisseurs CLI codex et claude sont mises en cache sous `~/.tesserae/llm_cache`, adressées par un digest du prompt réellement envoyé, donc chaque document que l'exécution interrompue avait déjà terminé est relu depuis le disque sans frais et la réparation ne paie que pour les documents qu'elle n'a jamais atteints. Un arrêt vous coûte le temps écoulé de l'exécution, pas ses extractions. Deux choses annulent cela : supprimer le répertoire de cache, et utiliser le fournisseur API direct, qui n'a que la mise en cache de prompt de courte durée du SDK et rien qui ne survit à un arrêt. Dans les deux cas, la réparation réachète l'ensemble du corpus auprès du fournisseur au prix fort.
> Ne construisez pas un flux qui suppose qu'une longue compilation
> survit à la session qui l'a lancée — exécutez-la au premier plan, ou via
> `tesserae engine`.
>
> De toute façon vous pouvez le regarder. Une compilation sans terminal attaché — détachée, redirigée, ou sous CI — enregistre une ligne par document sur stderr sur le canal `tesserae.compile`, indiquant la position, le chemin, et si ce document provient du cache ou a coûté un appel de modèle ; `--quiet` le désactive.

Les détails complets, les tableaux complets des commandes/hooks et les instructions d'opt-out par projet se trouvent dans le propre [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) du plugin.

## Pourquoi un plugin ET un serveur MCP ?

Rôles différents :

- **Outils MCP** = requêtes de graphe en lecture seule que l'agent appelle pendant une conversation. Toujours actifs, faible friction.
- **Commandes slash** = actions de workflow que vous invoquez explicitement (compile, refresh, obsidian-sync). Fort effet de levier mais doit être votre décision.

Vous pouvez utiliser le serveur MCP seul (édition manuelle de `claude_desktop_config.json` via `tesserae projects mcp-config`). Le plugin l'emballe simplement avec les commandes slash, la compétence et les hooks, rendant l'installation en une étape.

## Vérifier l'installation

```
/plugin list
/mcp
/tesserae:status
```

## Désinstaller

```
/plugin uninstall tesserae
```

Réversible. Ne touche au répertoire `.tesserae/` d'aucun projet.

## Voir aussi

- [Plan d'implémentation](../../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Spécification de conception](../../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Intégration des sessions](sessions.fr.md) — la fonctionnalité du graphe de sessions dont les hooks du plugin ferment la boucle
