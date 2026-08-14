# MCP — connecter Tesserae à Claude Code, Codex, Cursor

<!-- translations:start -->
<p align="center"><a href="../../integrations/mcp.md">English</a> · <a href="mcp.ko.md">한국어</a> · <a href="mcp.zh.md">中文</a> · <a href="mcp.ja.md">日本語</a> · <a href="mcp.ru.md">Русский</a> · <a href="mcp.es.md">Español</a> · <a href="mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae fournit un serveur stdio [Model Context Protocol](https://modelcontextprotocol.io) qui expose le graphe typé compilé à tout client compatible MCP : Claude Code, Codex CLI, Cursor, Claude Desktop, et d'autres. Le serveur annonce trois surfaces MCP complètes — **tools**, **resources** et **prompts** — afin que les clients puissent à la fois interroger le graphe à la demande et amorcer le contexte à moindre coût depuis des URI canoniques.

## Prérequis

Le serveur lit depuis `.tesserae/graph.json`, donc une compilation initiale est requise :

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

Recompilez chaque fois que vos sources changent. Le serveur prendra en compte le nouveau graphe au prochain appel d'outil, sans nécessiter de redémarrage.

## 1) Générer la configuration client

```bash
tesserae projects mcp-config
```

Affiche un fragment JSON ressemblant à :

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

Le chemin exact est rempli à partir du projet courant. Passez `--name <alias>` si vous souhaitez un nom d'entrée de serveur différent de `tesserae`.

## 2) Coller la configuration dans votre client MCP

| Client | Emplacement de la configuration |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → coller le JSON |
| Hermes | `~/.hermes/config.toml` (utilisez le bloc équivalent TOML affiché par `mcp-config --format hermes`) |

Redémarrez le client après modification. La session suivante se connectera et découvrira la surface Tesserae.

## 3) Ce que voit le client

### Tools — invoqués par le modèle

Chaque outil accepte un `graph_path` ou un `project` (alias du registre) optionnel, de sorte qu'un seul serveur peut résoudre n'importe quel vault enregistré à chaque appel. À défaut, repli sur le projet actif.

**Requêtes sur le graphe et récupération**

| Outil | Rôle |
|---|---|
| `graph_map` | **Commencez ici.** Carte budgétée de la hiérarchie du graphe — le point d'entrée de Descent. Sans portée, renvoie l'ensemble de cartes racine (compteurs, principaux hubs, une carte par communauté la plus grossière) ; `scope='<scope_id d'une carte>'` descend d'un niveau du dendrogramme ; `org:root` parcourt l'arbre organisationnel des agents. Oriente l'agent sans qu'il ait à deviner des termes de recherche |
| `schema` | Vocabulaire contrôlé des nœuds, arêtes et wiki-kinds |
| `graph_summary` | Compteurs de nœuds et d'arêtes ainsi que distributions de types pour le projet actif |
| `search_nodes` | Filtre les nœuds publics du graphe par `query`, `type`/`types`, `kind`, `limit`, `mode`/`weights` hybrides ; `include_superseded` affiche les nœuds retirés |
| `node_context` | Un nœud + ses arêtes incidentes + les nœuds voisins. `use_ppr` classe les voisins via un PageRank personnalisé plutôt qu'un parcours à 1 saut ; `include_superseded` et `limit` bornent le résultat |
| `embedding_status` | Indique le backend d'embeddings actif qui alimente la recherche hybride |
| `search_facts` | Faits temporels projetés depuis le graphe (style Graphiti), classés sur le CONTENU du fait — sujet, prédicat, objet, preuve — jamais sur le fait sérialisé, si bien qu'un fragment d'id ou de métadonnée n'est pas une correspondance ; `dated` (`any`, `dated`, `undated`) sélectionne selon que le fait porte un `valid_from` exploitable ; `current_only` filtre les faits courants, `as_of` répond à une date passée. Les deux ensemble sont refusés — ils expriment des horloges différentes — et `undated_included` indique combien des lignes renvoyées ne portent aucune date |
| `timeline` | Faits ordonnés par le `valid_from` ANALYSÉ pour une vue longitudinale, les faits sans date étant regroupés derrière tous les faits datés et renvoyés comme `undated_events` plutôt qu'intercalés ; `dated` (`any`, `dated`, `undated`) sélectionne selon que le fait porte un `valid_from` exploitable ; `as_of` répond à une date passée — un instant pivot sur les intervalles de validité, non une borne de plage — et `undated_included` indique combien des lignes renvoyées ne portent aucune date. `as_of` conserve les faits sans date, donc ce décompte est ce qui distingue une réponse maigre d'une réponse complète |
| `graph_ppr` | PageRank personnalisé amorcé sur un ou plusieurs `seed_node_id` ; renvoie les top-K nœuds les plus pertinents avec `alpha`, `directed`, `edge_type_weights` réglables |
| `wiki_page` | Le corps de la page markdown compilée pour un nœud, plus les liens internes qu'elle référence |
| `raw_source` | Le markdown source d'origine (plafonné à 16 KB) |
| `verify_claim` | Vérifie UN triplet contre le graphe — recherche exacte, sans LLM, sans correspondance floue, sans résultats classés. Renvoie `{verdict, reason, triple, citation, provenance, advisory}` ; `verdict` vaut `SUPPORTED` (l'arête existe **et** sa preuve est un extrait littéral de document), `PRESENT_UNEVIDENCED`, ou un refus. Enchaînez `search_nodes` → `verify_claim` quand vous n'avez que de la prose |
| `doctor_run` | Exécute les contrôles de santé et renvoie le rapport en JSON (`findings`, `exit_code` 0/1/2). **Toujours en lecture seule** — les réparations ne s'exécutent jamais via MCP ; utilisez `tesserae doctor --fix` en CLI |
| `doctor_report` | Le contenu de `.tesserae/doctor-report.md` (plafonné à 64 KB) ; vide tant que `tesserae doctor` n'a pas tourné |
| `lint_report` | Les derniers résultats de lint produits à la compilation (plafonné à 64 KB) |

**Compilateur de contexte à la demande** (Phase 7)

| Outil | Rôle |
|---|---|
| `compile_context` | Compile un document de contexte **avec citations** sur mesure pour une `query` ou des `seeds` explicites. Parcourt un sous-graphe de profondeur bornée (`depth`, 1–10, défaut 2), classe via PPR et remplit un `budget` de caractères (défaut 32000 ; `0` pour illimité). Déterministe par défaut ; avec `synthesize: true`, produit une tranche narrative "topic" rédigée par le LLM. Renvoie `body`, `citations`, `selected_node_ids` et `char_budget_used`. `view` restreint le parcours à une partition d'arête nommée — `semantic`, `temporal`, `causal` ou `entity` ; passez un tableau de noms pour effectuer un parcours par vue et les fusionner (RRF pondéré). Dès qu'une vue est demandée — un nom ou plusieurs — chaque citation porte `via_views` (les vues dont le parcours l'a atteinte) |
| `get_handle` | Pagine par tranches (`offset`, `limit`) une charge volumineuse renvoyée précédemment sous forme de `handle` (p. ex. `compile_context` avec `preview`) — en récupérer davantage à la demande plutôt que de tout déverser dans le contexte |
| `list_communities` | Liste les nœuds `COMMUNITY_SUMMARY` créés par la passe post-compilation, classés par nombre de membres (`min_size`, `limit`) ; avec `node_context`, suivez les arêtes `summarizes` jusqu'aux membres |
| `fresh_insights` | Constats de session classés par un score de décroissance à la Ebbinghaus (les plus récents et les plus consultés d'abord) ; écarte ceux remplacés par des quasi-doublons plus récents. `kind`, `limit`, `include_superseded` optionnels |

**Mémoire de session** (voir [sessions.md](sessions.fr.md))

| Outil | Rôle |
|---|---|
| `list_sessions` | Enveloppes de session (id, started_at, title, files_touched, compteurs de constats) pour le projet actif ; `since`, `limit` |
| `find_session_findings` | Tous les constats de session liés à `node_id` via `discussed_in` / `references`, filtrables par `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Étend un constat de session aux symboles `CodeFunction`/`CodeClass`/`CodeMethod` qu'il mentionne, via les arêtes `discusses` de la passe optionnelle de liaison insight↔symbole. La couche de code est optionnelle : sans entrée `external_tools` pour `codegraph`, ceci ne renvoie rien |
| `activity_summary` | Digest quotidien/hebdomadaire des projets enregistrés — sessions, constats, commits git, PR et documents ingérés, chacun fenêtré par **son propre** horodatage, jamais par le `started_at` d'une session. Rend un markdown déterministe et, sauf désactivation, y ajoute en tête un récit produit par le LLM |
| `query_decisions` | Décisions prises dans les projets enregistrés sur une plage de temps : choix **humains** explicites, analysés de façon déterministe depuis l'`AskUserQuestion` de Claude Code (la question et l'option retenue), plus les décisions d'agent extraites de la conversation |

**Mémoire d'agent et écriture en retour** (voir [agent-memory.fr.md](../agent-memory.fr.md))

| Outil | Rôle |
|---|---|
| `agent_view_explain` | Explique une vue restreinte à un agent *sans la charger* : mode de résolution (worker / manager / org), agents membres, chemin et nombre de nœuds de chaque artefact L1, ainsi que le repère d'obsolescence `distilled_through` |
| `drill_down` | Résout un `member_ref` de distillat jusqu'à son nœud L0 brut — l'escalade explicite et journalisée d'un responsable au-delà de la visibilité distillée. Renvoie l'état `alive` / `changed` / `absorbed` / `gone` ; chaque appel est consigné dans le sidecar |
| `graph_write` | Écrit des nœuds et arêtes typés directement dans le graphe — sans markdown, sans passe d'extraction. L'écriture est ajoutée à une surcouche append-only et rejouée comme producteur de compilation : elle **survit donc à la recompilation**. C'est strict : types inconnus, arête sans preuve ou extrémité qui n'est ni dans la charge utile ni un id de nœud existant sont refusés. **Pour rétracter** quelque chose de simplement faux, sans inventer de remplacement : pointez une arête `retracts` sur le nœud erroné **par id** — la cible est supprimée de toute lecture par défaut (`search_nodes`, `fresh_insights`, `node_context`, `compile_context`), reste accessible avec `include_superseded: true`, et rien n'est effacé |

**Questions-réponses et registre**

| Outil | Rôle |
|---|---|
| `ask` | Questions-réponses en langage naturel via le backend de mémoire configuré (raganything, cognee, ou wiki compilé). `backend`, `top_k` ; diffusion multi-vault via `scope`/`scope_aliases` ; `claude_config_dir` pour le routage multi-comptes |
| `query` | Recherche brute, sans LLM — miroir de `tesserae query`. `backend='wiki'` (par défaut) effectue une recherche déterministe BM25/sémantique sur le wiki compilé et renvoie des résultats classés avec extraits ; `backend='raganything'` interroge l'index RAG multimodal optionnel lorsque le projet l'a activé. Utilisez `ask` pour une réponse synthétisée et citée |
| `ingest` | Ingère du contenu web/texte brut (p. ex. un extrait de navigateur) dans le graphe de connaissances du projet résolu |
| `list_projects` | Liste les projets enregistrés |
| `register_project` | Ajoute un projet au registre |
| `unregister_project` | Retire un projet du registre (il n'existe pas de projet « actif » privilégié) |

**Configuration guidée**

| Outil | Rôle |
|---|---|
| `tesserae_setup_plan` | Détecte l'environnement et propose un plan de configuration en JSON. Lecture seule — ne touche jamais à `.tesserae/` |
| `tesserae_setup_apply` | Applique un plan (éventuellement édité) : écrit `.tesserae/config.json` et exécute des actions d'installation/exécution protégées. Conditionné par `confirm_install_actions` / `confirm_run_actions` |

### Resources — chargées automatiquement dans le contexte du modèle

URI que le client peut tirer via son sélecteur de ressources sans consommer un tour d'outil :

- `tesserae://graph/schema` — même charge utile que l'outil `schema`, prête à servir de contexte statique
- `tesserae://graph/summary` — résumé du projet actif
- `tesserae://lint-report` — le dernier lint report au format markdown

Plus des templates d'URI que le client peut construire à la demande :

- `tesserae://wiki/{kind}/{slug}` — le corps de n'importe quelle page wiki compilée
- `tesserae://raw/{source_path}` — n'importe quel markdown source brut

### Prompts — modèles de recherche en un clic

Ils apparaissent dans le menu slash du client (par ex. la palette `/` de Claude Code) :

| Prompt | Arguments | Ce qu'il fait |
|---|---|---|
| `summarize-paper` | `slug` (requis) | Appelle `node_context` + `wiki_page` + éventuellement `raw_source`, puis renvoie un résumé structuré : contribution, esquisse de méthode, résultats principaux, limites, nœuds liés |
| `find-related-work` | `topic` (requis), `limit` | Enchaîne `search_nodes` + `node_context` pour les K éléments liés les plus pertinents avec justifications de pertinence |
| `compare-approaches` | `a`, `b` (les deux requis) | Récupère `node_context` pour les deux + `search_facts` pour les affirmations de performance ; renvoie une comparaison côte à côte avec synthèse |
| `gap-analysis` | `topic` (optionnel) | Fait remonter les questions ouvertes non résolues, les benchmarks manquants, les affirmations peu étayées |
| `triage-open-questions` | _aucun_ | Liste chaque nœud `OpenQuestion`, les regroupe par sujet, propose un ordre de priorité |

Chaque prompt se matérialise par un unique message utilisateur qui indique au modèle exactement quels outils Tesserae enchaîner, afin que le modèle n'ait pas à redécouvrir la surface à chaque fois.

## Multi-projet : enregistrer plusieurs vaults sous un même serveur

Un registre persistant à `~/.tesserae/registry.json` permet au même serveur MCP de résoudre n'importe quel projet enregistré par son nom :

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Après cela, tout outil acceptant `project` ou `graph_path` résoudra `project: "research"` via le registre au lieu d'exiger un chemin complet. Le serveur vérifie même que le `graph_path` enregistré existe toujours et renvoie une erreur claire si une recompilation est nécessaire.

### Fan-out sur chaque vault enregistré

L'outil `ask` accepte `scope: "all-registered"` pour interroger en parallèle chaque projet enregistré et renvoyer les résultats agrégés :

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "Where is splatting used?",
    "scope": "all-registered"
  }
}
```

Restreignez à un sous-ensemble avec `scope_aliases: ["research", "notes"]`.

## CLI Claude multi-comptes

Si votre outil `ask` passe par la CLI Claude et que vous avez plusieurs comptes (par ex. `~/.claude` et `~/.claude-personal2`), passez `claude_config_dir` à chaque appel :

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

Le serveur exporte `CLAUDE_CONFIG_DIR` pour la durée de cet appel uniquement et restaure la valeur précédente ensuite. Aucune fuite entre les appels.

## Vérification

Après le redémarrage de votre client MCP, confirmez la connexion :

- Claude Code : `/mcp` devrait lister `tesserae` avec le nombre d'outils.
- Cursor : l'icône MCP dans la barre de chat devrait afficher `tesserae: connected` avec les compteurs de tools/resources/prompts.
- Codex / Hermes : invoquez n'importe quel outil par son nom (par ex. `schema`) et vérifiez la réponse.

Si rien n'apparaît, vérifiez à deux fois que `--graph` pointe vers un `.tesserae/graph.json` existant — le serveur valide désormais cela au démarrage et à chaque appel d'outil, vous verrez donc un message d'erreur clair plutôt qu'une 500 silencieuse.

## Où cela s'inscrit

Le serveur MCP est l'**interface de lecture** du graphe typé. Pour le **chemin d'écriture** (ingestion des sources, recompilation, rafraîchissement d'outils compagnons comme RAG-Anything), utilisez la CLI directement. Les deux sont découplés : la CLI met à jour `.tesserae/`, le serveur MCP lit ce qui s'y trouve au prochain appel d'outil.
