# Graphe de sessions

<!-- translations:start -->
<p align="center"><a href="../../integrations/sessions.md">English</a> · <a href="sessions.ko.md">한국어</a> · <a href="sessions.zh.md">中文</a> · <a href="sessions.ja.md">日本語</a> · <a href="sessions.ru.md">Русский</a> · <a href="sessions.es.md">Español</a> · <a href="sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Le graphe de sessions de Tesserae transforme vos conversations Claude Code / Codex à propos d'un projet en nœuds de première classe dans le graphe de connaissances typé, liés aux documents qui sont apparus. Après une compilation, vous pouvez demander `tesserae project ask "qu'avons-nous décidé à propos de 3D Gaussian Splatting ?"` et obtenir des nœuds spécifiques Insight / Decision / Question / TODO / Hypothesis / Takeaway avec la provenance jusqu'à la session qui les a produits.

## Comment ça marche

Deux passes par session :

1. **Structurelle** (toujours active, sans LLM). Lit les enregistrements `HarnessSession` normalisés que `tesserae sessions discover --import` écrit dans `.tesserae/harness_sessions/`. Pour chaque session, frappe un nœud enveloppe `Session`, émet des arêtes `discussed_in` de chaque document que l'agent a ouvert, et transforme le champ `decisions` existant en nœuds `SessionDecision`.
2. **LLM** (optionnel, s'exécute lorsque `ANTHROPIC_API_KEY` est configuré). Envoie les tours de conversation normalisés (le champ `metadata["turns"]` — pas le fichier de transcription brut) à Claude avec un schéma de résultats JSON-uniquement. Renvoie six types de résultats, chacun citant des tours spécifiques et des IDs de nœud de document spécifiques dans le graphe actuel. Mis en cache par content_hash + project_root_hash, donc les sessions inchangées sautent l'appel à la compilation suivante.

## Configuration

```bash
# Importez les sessions de ce projet dans `.tesserae/harness_sessions/`. Filtre par cwd, donc seules les sessions exécutées dans ce projet sont importées.
tesserae sessions discover --import

# Compilez. La passe structurelle s'exécute gratuitement ; la passe LLM s'exécute automatiquement lorsque la CLI `claude` est connectée — pas de clé API.
tesserae project compile
```

Pour compiler sans sessions (par ex. sur un serveur sans aucun historique de harness) :

```bash
tesserae project compile --no-sessions
```

Pour forcer structurel uniquement (sauter l'appel LLM même lorsqu'une clé est définie) :

```bash
tesserae project compile --sessions-llm=false
```

## Configuration

`.tesserae/config.json` accepte un bloc `sessions` :

```jsonc
{
  "sessions": {
    "enabled": true,
    "llm_enabled": "auto",
    "max_turns_per_chunk": 30,
    "model": "claude-sonnet-4-7-20251201",
    "include_doc_id_context": 200
  }
}
```

Les drapeaux CLI remplacent la configuration. `llm_enabled = "auto"` (par défaut) exécute la passe LLM lorsque la CLI `claude` est connectée ou que `ANTHROPIC_API_KEY` est défini ; sans aucun, seule la passe structurelle s'exécute (pas d'erreur, pas d'appels sortants).

## Requête

Deux outils MCP s'ajoutent aux outils de recherche/wiki existants :

* `list_sessions(since?, limit?)` — enveloppes Session pour le projet actif (id, started_at, title, nombres de résultats).
* `find_session_findings(node_id, kinds?)` — chaque résultat dérivé de session lié à `node_id` via `discussed_in` ou `references`, optionnellement filtré à insight / decision / question / todo / hypothesis / takeaway.

Depuis le CLI :

```bash
tesserae sessions list
tesserae project ask "what did we decide about extractor dedup?"
```

## Confidentialité

* Sans CLI `claude` connectée ET sans `ANTHROPIC_API_KEY` (ou avec `--sessions-llm=false`), il n'y a aucun appel réseau sortant. Seule la passe structurelle s'exécute.
* Lorsque la passe LLM s'exécute, les **tours de conversation normalisés complets** pour les sessions pas encore en cache sont envoyés. Le fichier de transcription lui-même reste sur disque ; seule la sortie JSON du LLM est persistée dans le graphe et le cache par session.
* Les fichiers de cache résident dans `.tesserae/session_findings/<session_id>.findings.json` avec un `content_hash` et un `project_root_hash`. Un fichier de cache copié entre projets est rejeté à la lecture — pas de relecture entre projets.
* Les sessions sont filtrées via `session_matches_project` après chargement, donc une transcription dont le `cwd` était un projet frère ne produit jamais de nœuds dans le graphe de ce projet.

## Disposition du coffre

Les résultats sont rendus sous le coffre Obsidian comme une page par résultat, groupés par session :

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md
      path-index-needs-basename-suppression.md
      ...
```

Les notes utilisateur à l'intérieur du bloc `<!-- user-notes:start -->` … `<!-- user-notes:end -->` sur toute page de résultat survivent à la recompilation — le même contrat que chaque autre page de coffre.

## Dépannage

* **Aucun nœud Session n'apparaît après compilation.** Avez-vous exécuté `tesserae sessions discover --import` d'abord ? Le chemin de compilation ne consomme que `.tesserae/harness_sessions/` ; il ne scanne PAS `~/.claude/projects/` automatiquement (ce scan peut prendre des minutes sur des machines avec des milliers de sessions historiques).
* **Préoccupations de coût LLM.** Le cache signifie que chaque session est envoyée au LLM au plus une fois par content-hash. Les sessions longues sont découpées à `max_turns_per_chunk` (par défaut 30) avec un chevauchement de 5 tours. Pour limiter le coût total, baissez `max_turns_per_chunk`, baissez `include_doc_id_context`, ou définissez `--sessions-llm=false`.
* **Un résultat cite un ID de nœud qui n'existe pas.** L'orchestrateur valide chaque référence citée contre le graphe de documents vivant et abandonne silencieusement les inconnues. Si vous voyez l'avertissement dans les journaux, le LLM a halluciné une citation — les références survivantes sont toujours fiables.

## Spécification

La conception complète vit dans [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../../superpowers/specs/2026-05-19-session-graph-extractor-design.md). Le plan de mise en œuvre est [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../../superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
