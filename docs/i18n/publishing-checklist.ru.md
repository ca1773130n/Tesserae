# Чеклист публикации

<!-- translations:start -->
<p align="center"><a href="../publishing-checklist.md">English</a> · <a href="publishing-checklist.ko.md">한국어</a> · <a href="publishing-checklist.zh.md">中文</a> · <a href="publishing-checklist.ja.md">日本語</a> · <a href="publishing-checklist.ru.md">Русский</a> · <a href="publishing-checklist.es.md">Español</a> · <a href="publishing-checklist.fr.md">Français</a> · <a href="publishing-checklist.de.md">Deutsch</a></p>
<!-- translations:end -->
Используйте этот чеклист перед публичной презентацией Tesserae.

## Гигиена репозитория

- [ ] README объясняет, что это за проект и какую проблему он решает.
- [ ] Команда установки работает из свежего shell.
- [ ] Quickstart использует `tesserae`, а не `python3 -m`.
- [ ] Документация по архитектуре объясняет raw evidence → graph → projections.
- [ ] Карта функций перечисляет реализованные возможности без преувеличения будущей работы.
- [ ] Документация по истории сессий объясняет явный импорт, проверку приватности, сгенерированные routes и transcript typography.
- [ ] Демо Self-dogfood было запущено и задокументировано.
- [ ] Сгенерированные артефакты воспроизводимы и либо игнорируются, либо намеренно публикуются.

## Проверка

```bash
.venv/bin/pytest tests/ -x          # ПРЕРВАТЬ при любом сбое — никогда не выпускайте красную сборку
./scripts/install.sh --help
tesserae init --help
tesserae compile --help
tesserae context --help     # Компилятор контекста по запросу
```

### Smoke-сборка демо (вручную — в CI этого нет)

Запускать руками перед каждым релизом. Раньше это повторяло CI-задачу `build-demo`,
которая шла на каждый push в `main`; тот воркфлоу удалён, поэтому этот путь сборки
проверяется теперь только здесь. `tests.yml` гоняет юнит-сьют и не прогоняет
`init` → `compile` → `export site` целиком.

Компилирует Tesserae по его собственному дереву исходников с детерминированным
экстрактором (без вызовов LLM, без API-ключей) и собирает сайт:

```bash
.venv/bin/python -m tesserae init --yes --source .
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```

## Релизный поток

Управляется навыком `release` (`.claude/skills/release/SKILL.md`). Последний тег — `v0.5.0`.

- [ ] На `main`, рабочее дерево чистое, выполнить `git pull --ff-only origin main`.
- [ ] Тесты + smoke-сборка демо (выше) проходят.
- [ ] Поднять `version = "X.Y.Z"` в `pyproject.toml` (синхронизировать `package.json`, если есть); закоммитить `release: vX.Y.Z` с однопараграфным changelog из `git log v<prev>..HEAD`.
- [ ] Тег `git tag -a vX.Y.Z -m "vX.Y.Z"`; запушить сначала коммит, затем тег.
- [ ] Дождаться зелёного CI (`gh run watch <run-id>`) — не выпускать GitHub-релиз на красной сборке.
- [ ] Опубликовать GitHub-релиз. Публикация в PyPI — опционально (когда будет готово).

### GitHub Pages

**Сайт больше не деплоит ни один воркфлоу.** Воркфлоу `build-demo` делал это на каждый
push в `main`; он удалён. Сайт, который он задеплоил последним, всё ещё отдаётся, и
README всё ещё ссылается на него как на живое демо — то есть эта страница теперь
снимок, замороженный на последнем запуске `build-demo`, а не текущий вид `main`.

Переопубликовать — это ручной `tesserae export site` плюс загрузка, либо новый воркфлоу.
В любом случае решать надо осознанно: демо-ссылка, тихо расходящаяся с кодом, хуже, чем
её отсутствие.

## Self-dogfood

Подключения интеграций (RAG-Anything) теперь
являются **интерактивными запросами мастера**, а не CLI-флагами. Запустите
мастер и ответьте на них:

```bash
tesserae init \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts
# когда мастер спросит:
#   - включите RAG-Anything, установить: да, парсер: mineru, запустить после: да
tesserae compile
tesserae sessions list
tesserae export site
tesserae serve --port 8765
```

Для полностью неинтерактивного запуска используйте `tesserae init --yes` (все
интеграции ВЫКЛЮЧЕНЫ), затем включите каждую интеграцию в
`.tesserae/config.json` — мастер записывает их под ключами `memory_backends`
и `external_tools` (RAG-Anything) — и выполните
`tesserae integrations refresh <name>` для каждой перед компиляцией. Точные
ключи конфигурации см. в документах по интеграциям.

## Тезисы для демо

- Tesserae — не универсальный граф именных фраз. Он использует контролируемую ontology.
- Исследовательский и разработческий код используют общую инфраструктуру, но сохраняют разные schema.
- Markdown и HTML — это проекции, а не авторитетные хранилища истины.
- Путь по умолчанию локален и удобен без API key.
- Агентские harness и MCP делают граф пригодным для coding agents.
- Импортированные страницы сессий harness превращают предыдущую работу Claude Code/Codex в доступную для поиска память проекта, сохраняя обнаружение transcript явным.
