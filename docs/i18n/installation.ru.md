# Установка

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
LLM-Wiki опубликован в PyPI и предоставляет shell-команды, чтобы пользователям не приходилось вручную запускать `python3 -m llm_wiki.cli`.

## Установка из PyPI (рекомендуется)

```bash
pip install llm-research-wiki
```

Готово. `pip` зарегистрирует три консольных скрипта в вашем `PATH`:

```bash
llm_wiki --help
llm-wiki --help
llm_wiki_mcp --help
```

Каноническая команда в документации — `llm_wiki`; `llm-wiki` (с дефисом) является псевдонимом. `llm_wiki_mcp` запускает MCP-сервер.

> **pipx тоже подходит.** Если вы предпочитаете держать CLI-инструменты в отдельных изолированных venv:
> ```bash
> pipx install llm-research-wiki
> ```

## Обновление

```bash
pip install --upgrade llm-research-wiki
```

## Необязательные интеграции

Стандартный wheel намеренно лёгкий. Мастер настройки может установить более тяжёлые companion/runtime-компоненты только тогда, когда вы этого попросите:

```bash
# Understand Anything companion graph + Cognee runtime memory
llm_wiki project setup \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

Для продвинутых рабочих процессов по-прежнему доступна ручная установка пакетов:

```bash
pip install kuzu cognee graphiti-core
```

- `kuzu` — персистентность графа Kuzu.
- `cognee` — runtime-процессы Cognee add/cognify; настройка сохраняет `{python} -m pip install cognee` и повторяет один раз, если Cognee отсутствует.
- Understand Anything — устанавливается через upstream-инсталлятор, когда выбран `--install-understand-anything`; LLM-Wiki сохраняет управляемый refresh wrapper вместо того, чтобы просить пользователей придумывать shell-команду.
- `graphiti-core` — живая синхронизация Graphiti/Neo4j. `export-graphiti` и `sync-graphiti --dry-run` работают и без него.

Путь синтеза на базе Anthropic использует маркер extras:

```bash
pip install "llm-research-wiki[synthesis-llm]"
```

## Установка из исходного кода (для контрибьюторов)

Если вы хотите дорабатывать кодовую базу, установите editable checkout:

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

Также включён удобный установщик: он клонирует репозиторий, создаёт локальный для проекта `.venv`, запускает `pip install -e .` и кладёт wrappers в `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/LLM-Wiki/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Полезные флаги (`./scripts/install.sh --help`):

| Опция | Назначение |
| --- | --- |
| `--dir PATH` | Установить или обновить checkout в `PATH`. |
| `--branch NAME` | Установить конкретную ветку. |
| `--repo URL` | Переопределить URL Git-репозитория. Полезно для форков или локальных smoke tests. |
| `--bin-dir PATH` | Записать command wrappers не в `~/.local/bin`, а в другое место. |
| `--no-venv` | Установить в текущее Python-окружение вместо создания `.venv`. |
| `--skip-shell-config` | Не редактировать `.zshrc` / `.bashrc`. |

Если использовался `--skip-shell-config`, перезапустите shell или выполните:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Проверка установки

```bash
llm_wiki project init --help
llm_wiki project compile --help
llm_wiki project build-site --help
```
