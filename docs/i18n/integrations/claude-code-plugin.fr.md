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
* **Auto-enregistrement du serveur `tesserae_mcp`** — l'agent obtient `ask`, `search_nodes`, `list_sessions`, `find_session_findings`, etc. en tant que `mcp__plugin_tesserae_tesserae__<tool>` sans éditions manuelles de configuration.
* **Compétence `using-tesserae`** — se charge automatiquement lorsque vous posez des questions sur le graphe typé, le rappel de sessions passées, le contenu wiki/vault, ou tout workflow tesserae. Apprend à l'agent quel outil MCP utiliser vs quelle commande slash suggérer.
* **4 hooks** — `SessionStart` imprime un résumé du graphe ; `SessionEnd` exécute en arrière-plan import+compile pour que les insights de cette conversation deviennent des nœuds du graphe pour la prochaine session ; `PostToolUse` (opt-in) fait une recompilation incrémentielle sur les éditions de docs/ ; `PreToolUse` filtre les compilations de grands graphes via un dialogue de confirmation.

Les détails complets, les tableaux complets des commandes/hooks et les instructions d'opt-out par projet se trouvent dans le propre [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md) du plugin.

## Pourquoi un plugin ET un serveur MCP ?

Rôles différents :

- **Outils MCP** = requêtes de graphe en lecture seule que l'agent appelle pendant une conversation. Toujours actifs, faible friction.
- **Commandes slash** = actions de workflow que vous invoquez explicitement (compile, refresh, obsidian-sync). Fort effet de levier mais doit être votre décision.

Vous pouvez utiliser le serveur MCP seul (édition manuelle de `claude_desktop_config.json` via `tesserae project mcp-config`). Le plugin l'emballe simplement avec les commandes slash, la compétence et les hooks, rendant l'installation en une étape.

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
