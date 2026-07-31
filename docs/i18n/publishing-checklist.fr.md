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

Piloté par la skill `release` (`.claude/skills/release/SKILL.md`). Le tag le plus récent est `v0.5.0`.

- [ ] Sur `main`, arbre de travail propre, exécuter `git pull --ff-only origin main`.
- [ ] Les tests + le smoke de build démo (ci-dessus) passent.
- [ ] Monter `version = "X.Y.Z"` dans `pyproject.toml` (répliquer `package.json` s’il existe) ; committer `release: vX.Y.Z` avec un changelog d’un paragraphe issu de `git log v<prev>..HEAD`.
- [ ] Taguer `git tag -a vX.Y.Z -m "vX.Y.Z"` ; pousser d’abord le commit puis le tag.
- [ ] Attendre que la CI soit verte (`gh run watch <run-id>`) — ne pas publier la release GitHub sur une build rouge.
- [ ] Publier la release GitHub. La publication PyPI est optionnelle (quand c’est prêt).

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
