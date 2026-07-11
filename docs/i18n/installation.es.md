# Instalación

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae se publica en PyPI y expone comandos de shell para que los usuarios no tengan que ejecutar `python3 -m tesserae.cli` manualmente.

## Instalar desde PyPI (recomendado)

```bash
pip install tesserae
```

Eso es todo. `pip` registra dos scripts de consola en tu `PATH`:

```bash
tesserae --help
tesserae_mcp --help
```

El comando canónico en la documentación es `tesserae`. `tesserae_mcp` arranca el servidor MCP (que ahora expone la herramienta on-demand `compile_context` — ver el Quickstart).

> **pipx también vale.** Si prefieres mantener las herramientas CLI en sus propios venvs aislados:
> ```bash
> pipx install tesserae
> ```

## Actualizar

```bash
pip install --upgrade tesserae
```

## Configuración a nivel de máquina (configura una vez, todos los proyectos)

Configura Tesserae una vez en lugar de por proyecto, e instala las dependencias
opcionales desde un solo comando:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

Dependencias opcionales conocidas: **memex** (búsqueda rápida de transcripts) y
**raganything**. Un `.tesserae/config.json` por proyecto
sigue anulando estos valores globales (orden de resolución: env → proyecto → global →
por defecto integrado). `tesserae init` también ofrece instalar memex durante una configuración interactiva.

## Integraciones opcionales (por proyecto)

El wheel por defecto es intencionadamente ligero, y los backends de memoria opcionales están
**desactivados por defecto**. `tesserae init` es el único paso de onboarding por proyecto —
su asistente elige el proveedor LLM y las fuentes detectadas; las piezas más pesadas
de acompañamiento/runtime se instalan a nivel de máquina vía `tesserae setup
--install …` (o `tesserae config deps --install …`) y se habilitan por proyecto en
`.tesserae/config.json`:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true   (query via `tesserae query --backend raganything`)
```

Las instalaciones manuales de paquetes siguen disponibles para flujos avanzados:

```bash
pip install kuzu graphiti-core
```

- `kuzu` — persistencia de grafo Kuzu.
- RAG-Anything — instalado vía `pip install 'raganything[all]'` (`tesserae setup --install raganything`); Tesserae guarda un wrapper de refresco gestionado para las ejecuciones del parser multimodal.
- `graphiti-core` — sincronización en vivo Graphiti/Neo4j. `export graphiti` y `export graphiti --sync --dry-run` funcionan sin él.

La ruta de síntesis respaldada por Anthropic usa un marcador de extras:

```bash
pip install "tesserae[synthesis-llm]"
```

Los embeddings semánticos reales (la vía de recuperación por defecto desde v0.5.0) se distribuyen tras el extra `semantic`:

```bash
pip install "tesserae[semantic]"
```

Esto trae `model2vec` y descarga un modelo estático ligero, offline y sin torch (~8 MB `potion-base-8M`, descargado una vez en el primer uso). Sin él, la recuperación híbrida/por embeddings recae en un stub no semántico de hash-bucket y emite una advertencia bien visible, así que instalar este extra se recomienda a cualquiera que use `tesserae ask`, `tesserae context` o la herramienta MCP `compile_context`.

Para el stack multimodal de RAG-Anything con todos los parsers preinstalados:

```bash
pip install 'tesserae[raganything-all]'
```

> **Prerrequisito de sistema:** parsear `.doc/.docx/.ppt/.pptx/.xls/.xlsx` requiere LibreOffice en el host. Instálalo vía el gestor de paquetes de tu plataforma (p. ej., `brew install --cask libreoffice`, `apt-get install libreoffice`); RAG-Anything se salta los documentos de Office con una advertencia cuando falta LibreOffice.

## Instalar desde el código fuente (para contribuidores)

Si quieres hackear en el código, instala el checkout editable en su lugar:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

También se incluye un instalador de conveniencia — clona, crea un `.venv` local al proyecto, ejecuta `pip install -e .` y deja los wrappers en `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Flags útiles (`./scripts/install.sh --help`):

| Opción | Propósito |
| --- | --- |
| `--dir PATH` | Instala o actualiza el checkout en `PATH`. |
| `--branch NAME` | Instala una rama específica. |
| `--repo URL` | Anula la URL del repositorio Git. Útil para forks o pruebas de humo locales. |
| `--bin-dir PATH` | Escribe los wrappers de comandos en otro lugar distinto de `~/.local/bin`. |
| `--no-venv` | Instala en el entorno Python actual en lugar de crear `.venv`. |
| `--skip-shell-config` | Evita editar `.zshrc` / `.bashrc`. |

Si se usó `--skip-shell-config`, reinicia la shell o ejecuta:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Verificar la instalación

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
