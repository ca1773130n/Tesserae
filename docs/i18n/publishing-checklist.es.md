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

### Smoke de build de demo (coincide con el job de CI `build-demo`)

Tanto el flujo de release como CI compilan Tesserae contra su propio árbol de
fuentes con el extractor determinista (sin llamadas a LLM, sin API keys) y construyen el sitio:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Flujo de release

Conducido por la skill `release` (`.claude/skills/release/SKILL.md`). El tag más reciente es `v0.5.0`.

- [ ] En `main`, árbol de trabajo limpio, ejecuta `git pull --ff-only origin main`.
- [ ] Los tests + el smoke de build de demo (arriba) pasan.
- [ ] Sube `version = "X.Y.Z"` en `pyproject.toml` (replica `package.json` si existe); commitea `release: vX.Y.Z` con un changelog de un párrafo desde `git log v<prev>..HEAD`.
- [ ] Etiqueta `git tag -a vX.Y.Z -m "vX.Y.Z"`; empuja primero el commit y luego el tag.
- [ ] Espera a que CI esté en verde (`gh run watch <run-id>`) — no publiques el release de GitHub sobre una build roja.
- [ ] Publica el release de GitHub. La publicación en PyPI es opcional (cuando esté lista).

### GitHub Pages

El workflow `build-demo` (push a `main`) siempre sube el sitio dogfood compilado como un
artefacto de workflow inspeccionable y, **además**, lo despliega a GitHub Pages cuando Pages
está habilitado. Los pasos de Pages son `continue-on-error`: el `GITHUB_TOKEN` por defecto no
puede *crear* un sitio Pages, así que el primer despliegue necesita un cambio manual único en
**Settings → Pages → Source: GitHub Actions**. Hasta que ese interruptor esté activado, la build
sigue en verde y el artefacto se sigue produciendo.

## Self-dogfood

Las opciones de integración (Understand Anything, RAG-Anything, cognee) ahora
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
#   - activa Understand Anything (plataforma: codex), instalar: sí
#   - activa RAG-Anything, instalar: sí, parser: mineru, ejecutar después: sí
#   - activa cognee, instalar: sí
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Para una ejecución totalmente no interactiva, usa `tesserae init --yes` (todas
las integraciones DESACTIVADAS), luego activa cada integración en
`.tesserae/config.json` — el asistente las escribe bajo las claves
`memory_backends` (cognee) y `external_tools` (Understand Anything,
RAG-Anything) — y ejecuta `tesserae integrations refresh <name>` para cada una
antes de compilar. Consulta los documentos de integración para las claves de
configuración exactas.

## Puntos para la demo

- Tesserae no es un grafo genérico de frases nominales. Usa una ontology controlada.
- El código de investigación y el de desarrollo comparten infraestructura, pero mantienen schema distintos.
- Markdown y HTML son proyecciones, no almacenes autoritativos de la verdad.
- La ruta por defecto es local y amigable sin API key.
- Los harnesses de agente y MCP hacen que el grafo sea usable por coding agents.
- Las páginas importadas de sesiones de harness convierten el trabajo previo de Claude Code/Codex en memoria de proyecto buscable, manteniendo explícito el descubrimiento de transcript.
