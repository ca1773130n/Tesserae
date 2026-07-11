# Установка

<!-- translations:start -->
<p align="center"><a href="../installation.md">English</a> · <a href="installation.ko.md">한국어</a> · <a href="installation.zh.md">中文</a> · <a href="installation.ja.md">日本語</a> · <a href="installation.ru.md">Русский</a> · <a href="installation.es.md">Español</a> · <a href="installation.fr.md">Français</a> · <a href="installation.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae публикуется на PyPI и предоставляет shell-команды, чтобы
пользователям не приходилось вручную запускать `python3 -m tesserae.cli`.

## Установка из PyPI (рекомендуется)

```bash
pip install tesserae
```

Вот и всё. `pip` регистрирует два консольных скрипта на вашем `PATH`:

```bash
tesserae --help
tesserae_mcp --help
```

Каноническая команда в документации — `tesserae`. `tesserae_mcp` запускает
MCP-сервер (который теперь выставляет инструмент `compile_context` по
требованию — см. Quickstart).

> **pipx тоже подходит.** Если вы предпочитаете держать CLI-инструменты в
> собственных изолированных venv:
> ```bash
> pipx install tesserae
> ```

## Обновление

```bash
pip install --upgrade tesserae
```

## Настройка на уровне машины (задать один раз, для всех проектов)

Настройте Tesserae один раз вместо настройки в каждом проекте и установите
опциональные зависимости одной командой:

```bash
# Interactive machine-wide setup — pick LLM provider/effort + which deps to install:
tesserae setup
# …or non-interactively (written to ~/.tesserae/config.json) + install everything:
tesserae setup --llm-provider codex --reasoning-effort medium --install all

# Just see / manage optional dependencies
tesserae config deps                     # show what's installed
tesserae config deps --install memex     # fast transcript search (needs cargo)
```

Известные опциональные зависимости: **memex** (быстрый поиск по транскриптам),
**cognee**, **raganything**. Проектный
`.tesserae/config.json` по-прежнему переопределяет эти глобальные значения
(порядок разрешения: env → проект → глобальный → встроенный дефолт).
`tesserae init` также предлагает установить memex во время интерактивной
настройки.

## Опциональные интеграции (на проект)

Дефолтный wheel намеренно лёгкий, а опциональные memory-бэкенды **выключены по
умолчанию**. `tesserae init` — единственный шаг онбординга на проект — его
визард выбирает LLM-провайдера и обнаруженные источники; более тяжёлые
companion/runtime-части устанавливаются на уровне машины через `tesserae setup
--install …` (или `tesserae config deps --install …`) и включаются на проект в
`.tesserae/config.json`:

```bash
# Machine-wide installs of the optional pieces:
tesserae setup --install raganything --install cognee

# Then per project: enable what you want in .tesserae/config.json
#   memory_backends.raganything.enabled: true
#   memory_backends.cognee.enabled: true        (query via `tesserae query --backend …`)
```

Ручная установка пакетов по-прежнему доступна для продвинутых рабочих
процессов:

```bash
pip install kuzu graphiti-core
pip install "tesserae[cognee]"
```

- `kuzu` — персистентность графа в Kuzu.
- `tesserae[cognee]` — опциональные рабочие процессы Cognee add/cognify
  (выключены по умолчанию; режим cognify с патчем Codex удалён).
- RAG-Anything — устанавливается через `pip install 'raganything[all]'`
  (`tesserae setup --install raganything`); Tesserae хранит управляемую обёртку
  обновления для мультимодальных запусков парсера.
- `graphiti-core` — живая синхронизация Graphiti/Neo4j. `export graphiti` и
  `export graphiti --sync --dry-run` работают и без него.

Путь синтеза на базе Anthropic использует extras-маркер:

```bash
pip install "tesserae[synthesis-llm]"
```

Настоящие семантические эмбеддинги (дефолтный retrieval-путь начиная с v0.5.0)
поставляются за экстрой `semantic`:

```bash
pip install "tesserae[semantic]"
```

Это подтягивает `model2vec` и скачивает лёгкую, офлайновую, свободную от torch
статическую модель (~8 МБ `potion-base-8M`, загружается один раз при первом
использовании). Без неё гибридный/эмбеддинговый поиск откатывается к
несемантической hash-bucket-заглушке и выводит громкое предупреждение, поэтому
установка этой экстры рекомендуется всем, кто использует `tesserae ask`,
`tesserae context` или MCP-инструмент `compile_context`.

Для мультимодального стека RAG-Anything со всеми предустановленными парсерами:

```bash
pip install 'tesserae[raganything-all]'
```

> **Системное требование:** парсинг `.doc/.docx/.ppt/.pptx/.xls/.xlsx` требует
> LibreOffice на хосте. Установите его через пакетный менеджер вашей платформы
> (например, `brew install --cask libreoffice`, `apt-get install libreoffice`);
> RAG-Anything пропускает Office-документы с предупреждением, когда LibreOffice
> отсутствует.

## Установка из исходников (для контрибьюторов)

Если вы хотите работать над кодовой базой, установите editable-копию:

```bash
git clone https://github.com/ca1773130n/Tesserae.git
cd Tesserae
pip install -e .
```

Также в комплекте удобный инсталлер — он клонирует, создаёт проектный `.venv`,
запускает `pip install -e .` и кладёт обёртки в `~/.local/bin`:

```bash
# Quick: clone + install in one shot
curl -fsSL https://raw.githubusercontent.com/ca1773130n/Tesserae/main/scripts/install.sh | bash

# From an existing checkout
./scripts/install.sh --dir "$PWD"
```

Полезные флаги (`./scripts/install.sh --help`):

| Опция | Назначение |
| --- | --- |
| `--dir PATH` | Установить или обновить checkout по пути `PATH`. |
| `--branch NAME` | Установить конкретную ветку. |
| `--repo URL` | Переопределить URL Git-репозитория. Полезно для форков или локальных smoke-тестов. |
| `--bin-dir PATH` | Записать обёртки команд не в `~/.local/bin`, а в другое место. |
| `--no-venv` | Установить в текущее окружение Python вместо создания `.venv`. |
| `--skip-shell-config` | Не редактировать `.zshrc` / `.bashrc`. |

Если использовался `--skip-shell-config`, либо перезапустите шелл, либо
выполните:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Проверка установки

```bash
tesserae init --help
tesserae compile --help
tesserae export site --help
```
