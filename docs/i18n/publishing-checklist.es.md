# Lista de verificación de publicación

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Usa esta lista antes de presentar Tesserae públicamente.

## Higiene del repositorio

- [ ] El README explica qué es el proyecto y qué problema resuelve.
- [ ] El comando de instalación funciona desde una shell nueva.
- [ ] El Quickstart usa `tesserae`, no `python3 -m`.
- [ ] La documentación de arquitectura explica raw evidence → graph → projections.
- [ ] El mapa de funciones enumera las funciones implementadas sin exagerar el trabajo futuro.
- [ ] La documentación del historial de sesiones explica la importación explícita, la revisión de privacidad, las routes generadas y la transcript typography.
- [ ] La demo Self-dogfood se ha ejecutado y documentado.
- [ ] Los artefactos generados son reproducibles y se ignoran o se publican intencionalmente.

## Verificación

```bash
.venv/bin/pytest tests/ -x          # ABORTA ante cualquier fallo — nunca publiques una build roja
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # Compilador de contexto bajo demanda
```

### Smoke de build de demo (manual — nada en CI lo cubre)

Ejecútalo a mano antes de cada release. Antes replicaba un job de CI `build-demo` que
corría en cada push a `main`; ese workflow se eliminó, así que esta ruta de compilación
solo se verifica aquí. `tests.yml` ejecuta la suite unitaria y no ejercita
`init` → `compile` → `export site` de extremo a extremo.

Compila Tesserae contra su propio árbol de fuentes con el extractor determinista (sin
llamadas a LLM, sin API keys) y construye el sitio:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Flujo de release

Lo dirige la skill `release` (`.claude/skills/release/SKILL.md`), que es la
versión autoritativa de este flujo — si ambos discrepan, manda la skill y esta
lista es lo que hay que corregir.

- [ ] En `main`, árbol de trabajo limpio, `git pull --ff-only origin main`.
- [ ] Tests en verde (`uv run pytest tests/ -x`, ~9 min). El smoke de build de
      demo es manual y ya no lo cubre CI — ver arriba.
- [ ] Subir **los tres** ficheros de versión: `pyproject.toml`,
      `.claude-plugin/plugin.json`, `npm/package.json`. Deben coincidir entre sí
      y con el tag; el wrapper de npm fija `tesserae==<versión npm>`.
- [ ] Escribir la nota de release y sus 7 traducciones; `uv run pytest
      tests/test_docs_i18n.py -q` debe estar en verde.
- [ ] Ejecutar `uv lock` y añadir `uv.lock` al commit — fija `tesserae` a su
      propia versión, y CI ejecuta `uv sync --locked`, que falla con un lock
      obsoleto.
- [ ] Commit `release: vX.Y.Z` con un párrafo de changelog de
      `git log v<prev>..HEAD`.
- [ ] **Abrir un PR — `main` está protegida y rechaza pushes directos** (`GH006`;
      `enforce_admins` activo, tres checks requeridos). Mergear solo con las tres
      ramas en verde. No etiquetar nunca una build roja.
- [ ] Etiquetar el commit mergeado (`git tag -a vX.Y.Z -m "vX.Y.Z"`) y subir el
      tag. Es el punto de no retorno: el push del tag dispara el workflow OIDC de
      npm, y una versión npm publicada no se puede reutilizar jamás.
- [ ] Publicar la release de GitHub.
- [ ] **Publicación en PyPI — OBLIGATORIA, no opcional.** Construir desde un
      worktree limpio del tag, subir, y verificar una instalación en venv nuevo
      con `--no-cache-dir` (pip cachea el índice y da por ausente una versión que
      ya está publicada).
- [ ] **Publicación en npm — OBLIGATORIA.** Ocurre automáticamente vía OIDC al
      subir el tag; observa el run y haz el smoke con
      `npx -y @jokerized/tesserae@X.Y.Z status`. Nunca publiques a mano: no hay
      token, y una publicación manual se salta la atestación de procedencia.

### GitHub Pages

**Ningún workflow despliega ya el sitio.** El workflow `build-demo` lo hacía en cada push
a `main`; fue eliminado. El sitio que desplegó por última vez se sigue sirviendo, y el
README lo sigue enlazando como demo en vivo — así que esa página es ahora una instantánea
congelada en la última ejecución de `build-demo`, no una vista actual de `main`.

Republicar es un `tesserae export site` manual más una subida, o un workflow nuevo. Sea
como sea, decídelo deliberadamente: un enlace de demo que se desvía en silencio del código
es peor que no tener enlace de demo.

## Self-dogfood

Las opciones de integración (RAG-Anything) ahora
son **preguntas interactivas del asistente**, no banderas de CLI. Ejecuta el
asistente y respóndelas:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# cuando el asistente pregunte:
#   - activa RAG-Anything, instalar: sí, parser: mineru, ejecutar después: sí
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Para una ejecución totalmente no interactiva, usa `tesserae init --yes` (todas
las integraciones DESACTIVADAS), luego activa cada integración en
`.tesserae/config.json` — el asistente las escribe bajo las claves
`memory_backends` y `external_tools`
(RAG-Anything) — y ejecuta `tesserae integrations refresh <name>` para cada una
antes de compilar. Consulta los documentos de integración para las claves de
configuración exactas.

## Puntos para la demo

- Tesserae no es un grafo genérico de frases nominales. Usa una ontology controlada.
- El código de investigación y el de desarrollo comparten infraestructura, pero mantienen schema distintos.
- Markdown y HTML son proyecciones, no almacenes autoritativos de la verdad.
- La ruta por defecto es local y amigable sin API key.
- Los harnesses de agente y MCP hacen que el grafo sea usable por coding agents.
- Las páginas importadas de sesiones de harness convierten el trabajo previo de Claude Code/Codex en memoria de proyecto buscable, manteniendo explícito el descubrimiento de transcript.
