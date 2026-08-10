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


def test_doctor_doc_table_lists_every_registered_check() -> None:
    """docs/doctor.md's check table must match the checks doctor actually runs.

    The doc used to open with a hard-coded "Twenty checks"; adding
    `filesystem_locking` made that wrong in English and in all seven
    translations at once, and nothing failed. The count is gone — this asserts
    the thing that actually matters instead, which is that the table neither
    omits a check nor advertises one that no longer exists.
    """
    import re

    from tesserae.doctor import CHECKS

    table = (DOCS / "doctor.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE))
    registered = {c.id for c in CHECKS}

    assert documented == registered, (
        f"undocumented: {sorted(registered - documented)}; "
        f"documented but not registered: {sorted(documented - registered)}"
    )


def test_mcp_tool_docs_match_the_real_tool_list() -> None:
    """docs/integrations/mcp.md must name exactly the tools the server serves.

    Removing `find_code_symbol_mentions` left it documented as available in
    four English docs plus their seven mirrors each — 47 files naming a tool
    that now raises. That is worse than stale: an agent following the docs
    calls something that does not exist.

    Nothing tied the documented tool list to `list_tools()`, so the drift was
    invisible. The doctor check-table test above is the only other
    docs-match-code guard in this suite, and it is precisely what forced
    docs/doctor.md to be corrected in the same change — same pattern, applied
    to the tool list.
    """
    import re

    from tesserae.mcp_server import LLMWikiMCPServer

    served = {t["name"] for t in LLMWikiMCPServer(default_graph_path=None).list_tools()}
    text = (DOCS / "integrations" / "mcp.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.MULTILINE))

    assert documented, "found no tool rows in mcp.md — the table shape changed, fix this test"

    # The direction that actually breaks an agent: a tool named in the docs
    # that the server does not serve. Hard failure, no exceptions.
    phantom = sorted(documented - served)
    assert not phantom, f"documented but NOT served (agents will call these and fail): {phantom}"

    # The other direction is a discoverability gap rather than a broken call,
    # and it predates this guard: mcp.md had never listed 14 of the served
    # tools. The set was frozen as a ratchet — free to SHRINK, never to grow —
    # and the 0.30.0 docs pass emptied it. It stays here, empty, because the
    # assertions below are what keep it empty: a new tool must be documented,
    # and an exemption may not outlive its documentation.
    KNOWN_UNDOCUMENTED: set = set()
    newly_undocumented = sorted(served - documented - KNOWN_UNDOCUMENTED)
    assert not newly_undocumented, (
        f"new tool(s) served but not documented in mcp.md: {newly_undocumented}"
    )
    stale_exemptions = sorted(KNOWN_UNDOCUMENTED & documented)
    assert not stale_exemptions, (
        "these are documented now — remove them from KNOWN_UNDOCUMENTED so the "
        f"ratchet keeps tightening: {stale_exemptions}"
    )
