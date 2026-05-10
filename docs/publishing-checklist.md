# Publishing Checklist

<!-- translations:start -->
<p align="center"><a href="i18n/publishing-checklist.ko.md">한국어</a> · <a href="i18n/publishing-checklist.zh.md">中文</a> · <a href="i18n/publishing-checklist.ja.md">日本語</a> · <a href="i18n/publishing-checklist.ru.md">Русский</a> · <a href="i18n/publishing-checklist.es.md">Español</a> · <a href="i18n/publishing-checklist.fr.md">Français</a></p>
<!-- translations:end -->
Use this checklist before presenting LLM-Wiki publicly.

## Repository hygiene

- [ ] README explains what the project is and what problem it solves.
- [ ] Install command works from a fresh shell.
- [ ] Quickstart uses `llm_wiki`, not `python3 -m`.
- [ ] Architecture docs explain raw evidence → graph → projections.
- [ ] Feature map lists implemented features without overselling future work.
- [ ] Session-history docs explain explicit import, privacy review, generated routes, and transcript typography.
- [ ] Self-dogfood demo has been run and documented.
- [ ] Generated artifacts are reproducible and either ignored or intentionally published.
- [ ] RAG-Anything index refreshed (if enabled)

## Verification

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
./scripts/install.sh --help
llm_wiki project setup --help
llm_wiki project compile --help
```

## Self-dogfood

```bash
llm_wiki project setup \
  --yes \
  --name llm_wiki_self \
  --source README.md \
  --source docs \
  --source llm_wiki \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
llm_wiki project compile
llm_wiki project sessions list
llm_wiki project build-site
llm_wiki project serve --port 8765
```

## Demo talking points

- LLM-Wiki is not a generic noun-phrase graph. It uses a controlled ontology.
- Research and development code share infrastructure but keep distinct schemas.
- Markdown and HTML are projections, not authoritative truth stores.
- The default path is local and no-API-key friendly.
- Agent harnesses and MCP make the graph usable by coding agents.
- Imported harness session pages turn prior Claude Code/Codex work into searchable project memory while keeping transcript discovery explicit.
