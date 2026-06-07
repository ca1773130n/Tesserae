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

### Smoke de build démo (identique au job CI `build-demo`)

Le flux de release comme la CI compilent Tesserae contre son propre arbre de sources
avec l’extracteur déterministe (aucun appel LLM, aucune clé API) et construisent le site :

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Flux de release

Piloté par la skill `release` (`.claude/skills/release/SKILL.md`). Le tag le plus récent est `v0.5.0`.

- [ ] Sur `main`, arbre de travail propre, exécuter `git pull --ff-only origin main`.
- [ ] Les tests + le smoke de build démo (ci-dessus) passent.
- [ ] Monter `version = "X.Y.Z"` dans `pyproject.toml` (répliquer `package.json` s’il existe) ; committer `release: vX.Y.Z` avec un changelog d’un paragraphe issu de `git log v<prev>..HEAD`.
- [ ] Taguer `git tag -a vX.Y.Z -m "vX.Y.Z"` ; pousser d’abord le commit puis le tag.
- [ ] Attendre que la CI soit verte (`gh run watch <run-id>`) — ne pas publier la release GitHub sur une build rouge.
- [ ] Publier la release GitHub. La publication PyPI est optionnelle (quand c’est prêt).

### GitHub Pages

Le workflow `build-demo` (push sur `main`) téléverse toujours le site dogfood compilé en tant
qu’artefact de workflow inspectable et, **en plus**, le déploie sur GitHub Pages quand Pages est
activé. Les étapes Pages sont `continue-on-error` : le `GITHUB_TOKEN` par défaut ne peut pas
*créer* un site Pages, donc le tout premier déploiement nécessite une bascule manuelle unique
dans **Settings → Pages → Source: GitHub Actions**. Tant que cette bascule n’est pas activée, la
build reste verte et l’artefact est toujours produit.

## Self-dogfood

Les opt-ins d'intégration (Understand Anything, RAG-Anything, cognee) sont
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
#   - activez Understand Anything (plateforme : codex), installer : oui
#   - activez RAG-Anything, installer : oui, parseur : mineru, exécuter ensuite : oui
#   - activez cognee, installer : oui
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Pour une exécution entièrement non interactive, utilisez `tesserae init --yes`
(toutes les intégrations DÉSACTIVÉES), puis activez chaque intégration dans
`.tesserae/config.json` — l'assistant les écrit sous les clés `memory_backends`
(cognee) et `external_tools` (Understand Anything, RAG-Anything) — et exécutez
`tesserae integrations refresh <name>` pour chacune avant de compiler.
Consultez les documents d'intégration pour les clés de configuration exactes.

## Points de discours pour la démo

- Tesserae n'est pas un graphe générique de groupes nominaux. Il utilise une ontology contrôlée.
- Le code de recherche et le code de développement partagent l'infrastructure, mais gardent des schema distincts.
- Markdown et HTML sont des projections, pas des magasins de vérité faisant autorité.
- Le chemin par défaut est local et pratique sans API key.
- Les harnesses d'agent et MCP rendent le graphe utilisable par les coding agents.
- Les pages de sessions harness importées transforment les travaux Claude Code/Codex précédents en mémoire de projet consultable, tout en gardant explicite la découverte des transcript.
