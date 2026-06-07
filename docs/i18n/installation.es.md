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

El comando canónico en la documentación es `tesserae`. `tesserae_mcp` inicia el servidor MCP (que ahora expone la herramienta `compile_context` bajo demanda; consulta la Guía rápida).

> **pipx también sirve.** Si prefieres mantener las herramientas CLI en venvs aislados:
> ```bash
> pipx install tesserae
> ```

## Actualizar

```bash
pip install --upgrade tesserae
```

## Integraciones opcionales

La wheel predeterminada es deliberadamente ligera. El asistente de configuración puede instalar las piezas complementarias/de runtime más pesadas solo cuando se lo pidas:

```bash
# Understand Anything companion graph + Cognee runtime memory
tesserae project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

Las instalaciones manuales de paquetes siguen disponibles para flujos avanzados:

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — persistencia de grafos Kuzu.
- `cognee` — flujos runtime add/cognify de Cognee; la configuración guarda `{python} -m pip install cognee` y reintenta una vez si falta Cognee.
- Understand Anything — se instala mediante el instalador upstream cuando se selecciona `--install-understand-anything`; Tesserae guarda un wrapper de actualización gestionado en lugar de pedir a los usuarios que inventen un comando shell.
- `graphiti-core` — sincronización en vivo Graphiti/Neo4j. `export-graphiti` y `sync-graphiti --dry-run` funcionan sin él.

La ruta de síntesis respaldada por Anthropic usa un marcador extras:

```bash
pip install "tesserae[synthesis-llm]"
```

Los embeddings semánticos reales (el carril de recuperación predeterminado desde la v0.5.0) se incluyen tras el extra `semantic`:

```bash
pip install "tesserae[semantic]"
```

Esto instala `model2vec` y descarga un modelo estático ligero, sin conexión y sin torch (unos 8 MB de `potion-base-8M`, descargado una sola vez en el primer uso). Sin él, la recuperación híbrida/por embeddings recurre a un stub no semántico basado en cubos hash y emite una advertencia llamativa, por lo que se recomienda instalar este extra a quien use `project ask`, `project context` o la herramienta MCP `compile_context`.

## Instalar desde el código fuente (para contribuidores)

Si quieres trabajar en el código, instala el checkout editable:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

También se incluye un instalador de conveniencia: clona, crea un `.venv` local del proyecto, ejecuta `pip install -e .` y deja los wrappers en `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Flags útiles (`./scripts/install.sh --help`):

| Opción | Propósito |
| --- | --- |
| `--dir PATH` | Instalar o actualizar el checkout en `PATH`. |
| `--branch NAME` | Instalar una rama específica. |
| `--repo URL` | Reemplazar la URL del repositorio Git. Útil para forks o smoke tests locales. |
| `--bin-dir PATH` | Escribir wrappers de comandos en un lugar distinto de `~/.local/bin`. |
| `--no-venv` | Instalar en el entorno Python actual en vez de crear `.venv`. |
| `--skip-shell-config` | Evitar editar `.zshrc` / `.bashrc`. |

Si usaste `--skip-shell-config`, reinicia la shell o ejecuta:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Verificar la instalación

```bash
tesserae project init --help
tesserae project compile --help
tesserae project build-site --help
```
