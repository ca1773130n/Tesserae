# Checklist de publication

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Utilisez cette checklist avant de présenter Tesserae publiquement.

## Hygiène du dépôt

- [ ] Le README explique ce qu'est le projet et le problème qu'il résout.
- [ ] La commande d'installation fonctionne depuis un shell neuf.
- [ ] Le Quickstart utilise `tesserae`, pas `python3 -m`.
- [ ] La documentation d'architecture explique raw evidence → graph → projections.
- [ ] La carte des fonctionnalités liste les fonctionnalités implémentées sans survendre le travail futur.
- [ ] La documentation d'historique de sessions explique l'import explicite, la revue de confidentialité, les routes générées et la transcript typography.
- [ ] La démo Self-dogfood a été exécutée et documentée.
- [ ] Les artefacts générés sont reproductibles et soit ignorés, soit publiés intentionnellement.

## Vérification

```bash
.venv/bin/pytest tests/ -x          # INTERROMPRE à la moindre erreur — ne jamais livrer une build rouge
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # Compilateur de contexte à la demande
```

### Smoke de build démo (manuel — rien dans la CI ne le couvre)

À exécuter à la main avant chaque release. Cela reproduisait autrefois un job CI
`build-demo` qui tournait à chaque push sur `main` ; ce workflow a été supprimé, donc ce
chemin de compilation n’est plus vérifié qu’ici. `tests.yml` exécute la suite unitaire et
ne couvre pas `init` → `compile` → `export site` de bout en bout.

Il compile Tesserae contre son propre arbre de sources avec l’extracteur déterministe
(aucun appel LLM, aucune clé API) et construit le site :

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Flux de release

Piloté par la skill `release` (`.claude/skills/release/SKILL.md`), qui fait
autorité sur ce flux — en cas de divergence, c’est la skill qui prime et cette
liste qu’il faut corriger.

- [ ] Sur `main`, arbre de travail propre, `git pull --ff-only origin main`.
- [ ] Tests au vert (`uv run pytest tests/ -x`, ~9 min). Le smoke de build démo
      est manuel et n’est plus couvert par la CI — voir plus haut.
- [ ] Incrémenter **les trois** fichiers de version : `pyproject.toml`,
      `.claude-plugin/plugin.json`, `npm/package.json`. Ils doivent concorder
      entre eux et avec le tag ; le wrapper npm épingle
      `tesserae==<version npm>`.
- [ ] Rédiger la note de release et ses 7 traductions ; `uv run pytest
      tests/test_docs_i18n.py -q` doit être au vert.
- [ ] Lancer `uv lock` et stager `uv.lock` — il épingle `tesserae` à sa propre
      version, et la CI exécute `uv sync --locked`, qui échoue sur un lock
      obsolète.
- [ ] Committer `release: vX.Y.Z` avec un paragraphe de changelog issu de
      `git log v<prev>..HEAD`.
- [ ] **Ouvrir une PR — `main` est protégée et refuse les pushes directs**
      (`GH006` ; `enforce_admins` actif, trois checks requis). Merger seulement
      quand les trois legs sont au vert. Ne jamais taguer un build rouge.
- [ ] Taguer le commit mergé (`git tag -a vX.Y.Z -m "vX.Y.Z"`) et pousser le tag.
      C’est le point de non-retour : le push du tag déclenche le workflow OIDC
      npm, et une version npm publiée ne peut jamais être réutilisée.
- [ ] Publier la release GitHub.
- [ ] **Publication PyPI — OBLIGATOIRE, pas optionnelle.** Construire depuis un
      worktree propre du tag, uploader, puis vérifier une installation en venv
      neuf avec `--no-cache-dir` (pip met l’index en cache et déclare absente une
      version déjà en ligne).
- [ ] **Publication npm — OBLIGATOIRE.** Elle se fait automatiquement via OIDC au
      push du tag ; surveiller le run et faire le smoke avec
      `npx -y @jokerized/tesserae@X.Y.Z status`. Ne jamais publier à la main : il
      n’y a pas de token, et une publication manuelle saute l’attestation de
      provenance.

### GitHub Pages

**Plus aucun workflow ne déploie le site.** Le workflow `build-demo` le faisait à chaque
push sur `main` ; il a été supprimé. Le site qu’il a déployé en dernier est toujours servi,
et le README le référence toujours comme démo en ligne — cette page est donc désormais un
instantané figé à la dernière exécution de `build-demo`, et non une vue actuelle de `main`.

Republier, c’est un `tesserae export site` manuel plus un upload, ou un nouveau workflow.
Quoi qu’il en soit, décidez délibérément : un lien de démo qui dérive silencieusement du
code est pire que pas de lien du tout.

## Self-dogfood

Les opt-ins d'intégration (RAG-Anything) sont
désormais des **invites interactives de l'assistant**, et non des flags CLI.
Exécutez l'assistant et répondez-y :

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# lorsque l'assistant demande :
#   - activez RAG-Anything, installer : oui, parseur : mineru, exécuter ensuite : oui
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Pour une exécution entièrement non interactive, utilisez `tesserae init --yes`
(toutes les intégrations DÉSACTIVÉES), puis activez chaque intégration dans
`.tesserae/config.json` — l'assistant les écrit sous les clés `memory_backends`
et `external_tools` (RAG-Anything) — et exécutez
`tesserae integrations refresh <name>` pour chacune avant de compiler.
Consultez les documents d'intégration pour les clés de configuration exactes.

## Points de discours pour la démo

- Tesserae n'est pas un graphe générique de groupes nominaux. Il utilise une ontology contrôlée.
- Le code de recherche et le code de développement partagent l'infrastructure, mais gardent des schema distincts.
- Markdown et HTML sont des projections, pas des magasins de vérité faisant autorité.
- Le chemin par défaut est local et pratique sans API key.
- Les harnesses d'agent et MCP rendent le graphe utilisable par les coding agents.
- Les pages de sessions harness importées transforment les travaux Claude Code/Codex précédents en mémoire de projet consultable, tout en gardant explicite la découverte des transcript.
