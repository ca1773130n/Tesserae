# Instalación

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki se publica en PyPI y expone comandos de shell para que los usuarios no tengan que ejecutar `python3 -m llm_wiki.cli` manualmente.

## Instalar desde PyPI (recomendado)

```bash
pip install llm-research-wiki
```

Eso es todo. `pip` registra tres scripts de consola en tu `PATH`:

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

El comando canónico en la documentación es `llm_wiki`; `llm-wiki` (con guion) es un alias. `llm_wiki_mcp` inicia el servidor MCP.

> **pipx también sirve.** Si prefieres mantener las herramientas CLI en venvs aislados:
> ```bash
> pipx install llm-research-wiki
> ```

## Actualizar

```bash
pip install --upgrade llm-research-wiki
```

## Integraciones opcionales

La wheel predeterminada es deliberadamente ligera. El asistente de configuración puede instalar las piezas complementarias/de runtime más pesadas solo cuando se lo pidas:

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
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
- Understand Anything — se instala mediante el instalador upstream cuando se selecciona `--install-understand-anything`; LLM-Wiki guarda un wrapper de actualización gestionado en lugar de pedir a los usuarios que inventen un comando shell.
- `graphiti-core` — sincronización en vivo Graphiti/Neo4j. `export-graphiti` y `sync-graphiti --dry-run` funcionan sin él.

La ruta de síntesis respaldada por Anthropic usa un marcador extras:

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## Instalar desde el código fuente (para contribuidores)

Si quieres trabajar en el código, instala el checkout editable:

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

También se incluye un instalador de conveniencia: clona, crea un `.venv` local del proyecto, ejecuta `pip install -e .` y deja los wrappers en `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

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
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
