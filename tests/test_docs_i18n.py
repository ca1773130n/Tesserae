"""Documentation localization coverage checks."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Supported localization targets. German (de) was added on 2026-05-17 to cover
# the DE/AT/CH dev population (~3M devs) on top of the prior six. See the
# "Docs i18n is mandatory" project memory for the rule.
LANGS = ("ko", "zh", "ja", "ru", "es", "fr", "de")

# Directories under docs/ that are excluded from the i18n rule. ``launch/``
# holds short-lived launch artifacts (e.g. hn-post.md drafts); ``handoffs/``
# holds session handoffs, which are dated working state addressed to whoever
# picks the work up next and are stale within days — translating them seven ways
# would cost more than the documents are worth and would age just as fast;
# ``superpowers/`` was removed from the remote in May 2026 but the exclusion
# stays defensive in case the directory ever reappears.
EXCLUDED_TOP_DIRS = {"i18n", "launch", "handoffs", "superpowers", "screencasts", "assets"}


def _canonical_docs() -> list[Path]:
    docs: list[Path] = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS)
        if rel.parts[0] in EXCLUDED_TOP_DIRS:
            continue
        docs.append(path)
    return docs


def test_all_docs_have_localized_counterparts_except_superpowers() -> None:
    """Every first-party doc under docs/ has ko/zh/ja/ru/es/fr/de copies."""
    missing: list[str] = []
    for source in _canonical_docs():
        rel = source.relative_to(DOCS)
        for lang in LANGS:
            localized = DOCS / "i18n" / rel.with_name(f"{rel.stem}.{lang}{rel.suffix}")
            if not localized.exists():
                missing.append(str(localized.relative_to(ROOT)))

    assert not missing


def test_docs_translation_switchers_are_present() -> None:
    """Canonical and localized docs expose a visible language switcher."""
    checked: list[Path] = []
    for source in _canonical_docs():
        checked.append(source)
        rel = source.relative_to(DOCS)
        for lang in LANGS:
            checked.append(DOCS / "i18n" / rel.with_name(f"{rel.stem}.{lang}{rel.suffix}"))

    missing = [str(path.relative_to(ROOT)) for path in checked if "<!-- translations:start -->" not in path.read_text(encoding="utf-8")]
    assert not missing


def test_root_readme_translations_use_github_markdown_names() -> None:
    """GitHub renders README.<lang>.md as Markdown; README.md.<lang> is plain text."""
    for lang in LANGS:
        assert (ROOT / f"README.{lang}.md").exists()
        assert not (ROOT / f"README.md.{lang}").exists()
