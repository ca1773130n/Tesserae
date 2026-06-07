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
tesserae project setup --help
tesserae project compile --help
tesserae project context --help     # Компилятор контекста по запросу
```

### Smoke-сборка демо (совпадает с CI-задачей `build-demo`)

И релизный поток, и CI компилируют Tesserae по его собственному дереву исходников
с детерминированным экстрактором (без вызовов LLM, без API-ключей) и собирают сайт:

```bash
.venv/bin/python -m tesserae project setup --yes --no-color --source . \
  --no-cognee --skip-raganything --skip-install-cognee \
  --skip-install-raganything --skip-install-understand-anything
.venv/bin/python -m tesserae project compile
.venv/bin/python -m tesserae project build-site
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

Воркфлоу `build-demo` (push в `main`) всегда загружает скомпилированный dogfood-сайт как
инспектируемый артефакт воркфлоу и **также** деплоит его в GitHub Pages, когда Pages
включён. Шаги Pages помечены `continue-on-error`: дефолтный `GITHUB_TOKEN` не может
*создать* сайт Pages, поэтому самый первый деплой требует однократного ручного
переключения в **Settings → Pages → Source: GitHub Actions**. Пока этот тумблер не включён,
сборка всё равно остаётся зелёной, а артефакт всё равно создаётся.

## Self-dogfood

```bash
tesserae project setup \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
tesserae project compile
tesserae project sessions list
tesserae project build-site
tesserae project serve --port 8765
```

## Тезисы для демо

- Tesserae — не универсальный граф именных фраз. Он использует контролируемую ontology.
- Исследовательский и разработческий код используют общую инфраструктуру, но сохраняют разные schema.
- Markdown и HTML — это проекции, а не авторитетные хранилища истины.
- Путь по умолчанию локален и удобен без API key.
- Агентские harness и MCP делают граф пригодным для coding agents.
- Импортированные страницы сессий harness превращают предыдущую работу Claude Code/Codex в доступную для поиска память проекта, сохраняя обнаружение transcript явным.
