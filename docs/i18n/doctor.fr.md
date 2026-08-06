# `tesserae doctor` — bilans de santé du projet

<!-- translations:start -->
<p align="center"><a href="../doctor.md">English</a> · <a href="doctor.ko.md">한국어</a> · <a href="doctor.zh.md">中文</a> · <a href="doctor.ja.md">日本語</a> · <a href="doctor.ru.md">Русский</a> · <a href="doctor.es.md">Español</a> · <a href="doctor.fr.md">Français</a> · <a href="doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` inspecte un espace de travail Tesserae de bout en bout —
initialisation, intégrité du graphe, cohérence du registre, fraîcheur, verrous,
connexion LLM et hygiène disque — et affiche une checklist. Il est **en lecture
seule par défaut** ; `--fix` n’applique que les réparations sûres à relancer et
ne peut jamais détruire un état vivant.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## Ce qu’il vérifie

Les vérifications, regroupées par catégorie :

| Vérification | Catégorie | Ce qu’elle vérifie | Action `--fix` |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` existe et ressemble à un espace de travail Tesserae | rapport seulement (suggère `tesserae init`) |
| `graph_parse` | core | `graph.json` se parse et a la forme attendue | rapport seulement (suggère `tesserae compile`) |
| `config_valid` | core | `.tesserae/config.json` se parse et se valide contre le modèle d’init | rapport seulement |
| `vault_configured` | core | le chemin de vault configuré se résout | **SAFE** : crée le répertoire de vault résolu lorsqu’il vit à l’intérieur du projet |
| `registry_consistent` | registry | les entrées de `~/.tesserae/registry.json` pointent vers de vraies racines de projet | **SAFE** : élague les entrées dont la racine a disparu, supprime la clé héritée `active` ; un graphe manquant reste rapport seulement |
| `graph_staleness` | freshness | delta git depuis le `git_head` enregistré par la dernière compilation | rapport seulement (suggère `tesserae refresh` — les compilations sont lourdes) |
| `site_search_index` | freshness | le site statique / `search-index.json` est plus récent que `graph.json` | **SAFE** : reconstruit le site |
| `backend_artifacts` | freshness | les artefacts RAG-Anything sont à jour | rapport seulement (leur rafraîchissement est lourd en LLM/réseau) |
| `session_chunks` | freshness | la couverture des [chunks de session quotidiens](session-chunks.fr.md) n’a pas de trous dans la fenêtre récente | rapport seulement (suggère `tesserae sessions chunk-backfill`) |
| `wiki_lint` | graph | dérive graphe ⇄ wiki + constats de lint trivialement corrigeables | **SAFE** : applique les corrections triviales du lint (`fix_trivial`) |
| `compile_lock` | processes | si un verrou de compilation vivant est détenu, et par quel pid **et quel hôte** | rapport seulement — doctor **ne tue jamais et ne supprime jamais un verrou vivant** |
| `filesystem_locking` | processes | si `.tesserae/` se trouve sur un système de fichiers réseau, où `flock(2)` peut être un no-op silencieux | rapport seulement (il ne peut pas prouver l’application inter-hôtes — voir plus bas) |
| `daemon_pid` | processes | `daemon.<host>.pid` pointe vers un processus moteur vivant | **SAFE** : supprime le pidfile **de cet hôte** quand son propriétaire est mort ; celui d’une autre machine est rapporté, jamais touché |
| `llm_login` | environment | si les répertoires de config que le projet utiliserait réellement existent | rapport seulement — **ne vérifie pas les identifiants** (voir plus bas) |
| `optional_deps` | environment | statut des dépendances optionnelles (memex, raganything) | rapport seulement (les installations passent par le réseau) |
| `embedding_backend` | environment | un vrai backend d’embeddings sémantiques est disponible | rapport seulement (suggère `pip install tesserae[semantic]`) |
| `environment` | environment | résumé de détection d’environnement en bloc | section rapport seulement |
| `build_history` | hygiene | taille et forme de `.build-history` | **SAFE** : le tronque, en préservant toujours l’entrée `git_head` la plus récente (la vérification de staleness en dépend) |
| `idempotence` | hygiene | le fil-piège `idempotence_suspect` du snapshot de sortie | rapport seulement (c’est un signal de bug, pas quelque chose à auto-réparer) |
| `orphan_worktrees` | hygiene | enregistrements `git worktree` périmés | **SAFE** : `git worktree prune` ; la suppression de répertoires reste rapport seulement |
| `hook_log_bloat` | hygiene | croissance de `.tesserae/.session-*-hook.log` | **SAFE** : fait tourner/tronque les logs au-delà de 10 Mo |

Une vérification qui plante est rapportée comme un constat d’erreur — doctor lui-même ne lève jamais d’exception.

## Ce que `llm_login` dit, et ce qu’il ne dit pas

Il rapporte qu’un répertoire de config existe. Il ne rapporte **pas** que la CLI
qui s’y trouve détient un jeton valide, et il le dit dans le texte de son propre
constat.

La distinction n’est pas de la pédanterie. La vérification rapportait autrefois
`credentialed LLM CLI: claude, codex` sur la foi de fichiers comme
`~/.claude/history.jsonl` — qui prouvent que la CLI a été *utilisée*, pas qu’elle
peut s’authentifier *maintenant*. Lancés coup sur coup dans la même seconde,
`tesserae compile` affichait `Claude CLI not logged in (tried 1 config dir)`
pendant que doctor affichait une coche verte. Un diagnostic qui contredit la
panne dans laquelle vous êtes est pire que pas de diagnostic du tout.

Vérifier les identifiants revient à dépenser un vrai appel LLM à chaque
`tesserae doctor`, un coût que cette vérification ne prend pas de sa propre
initiative. Elle n’énonce donc que ce qu’elle a vérifié. Pour la réponse qui fait
autorité, utilisez `tesserae compile`.

La vérification est limitée aux répertoires que le projet essaierait réellement,
résolus par le même chemin qu’emprunte `ProjectWiki._build_json_client` — et elle
ne dit rien des répertoires de config claude quand le fournisseur du projet est
`codex`.

## Disques partagés et `flock(2)`

Toute garantie de concurrence dans Tesserae — le verrou de compilation avant tout
— repose sur le fait que `flock(2)` soit réellement appliqué par le système de
fichiers qui héberge `.tesserae/`. En NFS et SMB, cela dépend de la configuration :
sans lock daemon opérationnel, `flock` peut se dégrader silencieusement en no-op,
et deux hôtes compileront alors le même projet en même temps en croyant chacun
détenir un verrou exclusif.

`filesystem_locking` rapporte ce qu’un seul hôte peut déterminer : le type de
système de fichiers qui porte le projet, s’il s’agit d’un système de fichiers
réseau, et si une acquisition `flock` réussit tout court. Il avertit sur un
système de fichiers réseau.

Il **ne peut pas** prouver l’application inter-hôtes, et ne prétend pas le faire.
Qu’un hôte prenne un verrou ne dit rien sur le fait qu’un second hôte soit
empêché de le prendre. Si vous faites tourner Tesserae depuis plusieurs machines
sur un stockage partagé, testez-le directement sur le matériel réel avant de vous
fier au verrou de compilation.

## Politique `--fix`

- `--fix` exécute **uniquement** les vérifications marquées SAFE ci-dessus, puis
  re-détecte pour que le rapport reflète l’état après correction.
- Chaque correction est idempotente : lancer `doctor --fix` deux fois laisse le
  second passage propre.
- Doctor **ne tue jamais un processus et ne supprime jamais un verrou de
  compilation vivant** — un verrou détenu est rapporté avec le pid et l’hôte qui
  le détiennent, et laissé tranquille.
- Doctor **ne touche jamais au pidfile d’une autre machine.** Sur un stockage
  partagé, la table des processus locale ne dit rien d’un pid écrit par un autre
  hôte, donc `daemon.<other-host>.pid` est rapporté et sauté inconditionnellement
  — il n’est même pas lu pour tester la vivacité. Seul le pidfile de cet hôte-ci
  peut être supprimé.
- Les opérations lourdes ou réseau (recompilations, installations de
  dépendances, rafraîchissements de backend) ne sont jamais intégrées à
  `--fix` ; doctor affiche la commande à lancer vous-même.

## Codes de sortie

Même convention que `tesserae lint` :

| Code de sortie | Signification |
|---|---|
| `0` | sain — aucun constat au-dessus de OK |
| `1` | avertissements présents |
| `2` | erreurs présentes |

## Artefacts de rapport

Chaque exécution écrit les deux formes de rapport dans l’espace de travail :

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` affiche en plus le rapport JSON sur stdout à la place de la checklist
markdown. `--all` itère sur chaque projet du registre (en ignorant
`--project`) et rapporte projet par projet.

## MCP : `doctor_report`

Le serveur MCP expose le même rapport via l’outil `doctor_report` (à l’image de
`lint_report`, y compris son plafond d’octets sur le contenu retourné), afin
qu’un agent puisse vérifier la santé de l’espace de travail en pleine
conversation sans passer par le shell. Il requiert une racine de projet —
passez `graph_path`/`project` ou configurez un graphe par défaut.
