"""Documentation localization coverage checks."""

from __future__ import annotations

import re
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


# ``| `name` | `kind` | ... |`` — the classification rows of docs/sidecars.md.
# Identifiers stay verbatim in every translation, so the same regex reads the
# mirrors.
_SIDECAR_ROW = re.compile(r"^\| `([^`]+)` \| `([a-z]+)` \|", re.MULTILINE)
#: Code spans on the single line following the safe-list marker.
_SAFE_LIST = re.compile(r"<!-- sidecars:safe-list -->\n((?:`[^\n]*\n)+)")


def _sidecar_docs() -> list[Path]:
    return [DOCS / "sidecars.md"] + [DOCS / "i18n" / f"sidecars.{lang}.md" for lang in LANGS]


def test_sidecar_doc_classification_matches_the_registry() -> None:
    """docs/sidecars.md may not disagree with `tesserae/sidecars.py`.

    The doc exists to answer "what breaks if I delete this", so a row that
    names a file the registry no longer knows — or calls it `cache` after the
    registry made it `accumulated` — is worse than no doc: it is a confident
    wrong answer about data loss. Renaming a sidecar without touching the page
    used to be silent, in English and in all seven mirrors at once.
    """
    from tesserae.sidecars import SCOPE_PROJECT, SCOPE_USER, classify

    problems: list[str] = []
    for doc in _sidecar_docs():
        text = doc.read_text(encoding="utf-8")
        rows = _SIDECAR_ROW.findall(text)
        assert rows, f"no classification rows found in {doc.name} — the table shape changed, fix this test"
        for name, kind in rows:
            entry = classify(name, scope=SCOPE_PROJECT) or classify(name, scope=SCOPE_USER)
            if entry is None:
                problems.append(f"{doc.name}: `{name}` is documented but no registry entry claims it")
            elif entry.kind != kind:
                problems.append(f"{doc.name}: `{name}` documented as {kind}, registry says {entry.kind}")

    assert not problems, "\n".join(problems)


def test_sidecar_doc_names_every_kind_and_every_unsafe_entry() -> None:
    """The doc must cover all four kinds and every entry a reset must not remove.

    Coverage in one direction only, deliberately: the safe-to-delete entries are
    summarised as a list because losing one costs a recompile, but an
    `accumulated` file or an LLM-backed cache that nobody wrote down is exactly
    the deletion this page exists to prevent. Adding one to the registry must
    therefore fail here until the page names it.
    """
    from tesserae.sidecars import KINDS, SIDECARS

    text = (DOCS / "sidecars.md").read_text(encoding="utf-8")
    spans = set(re.findall(r"`([^`\n]+)`", text))

    missing_kinds = sorted(k for k in KINDS if f"`{k}`" not in text)
    assert not missing_kinds, f"kinds absent from docs/sidecars.md: {missing_kinds}"

    undocumented = sorted({s.name for s in SIDECARS if not s.safe_to_delete} - spans)
    assert not undocumented, (
        f"entries a bulk reset must not remove, absent from docs/sidecars.md: {undocumented}"
    )


def test_sidecar_doc_safe_list_is_actually_safe() -> None:
    """Everything the page tells you to reclaim freely is `safe_to_delete`.

    The dangerous direction: flipping a registry entry to unsafe (the way
    `session_findings` was, once its findings became nodes) while the page still
    lists it among the free reclaims.
    """
    from tesserae.sidecars import SCOPE_PROJECT, classify

    text = (DOCS / "sidecars.md").read_text(encoding="utf-8")
    block = _SAFE_LIST.search(text)
    assert block, "safe-list marker or its list is gone from docs/sidecars.md"

    wrong: list[str] = []
    for name in re.findall(r"`([^`\n]+)`", block.group(1)):
        entry = classify(name, scope=SCOPE_PROJECT)
        if entry is None:
            wrong.append(f"`{name}` is listed as safe but no registry entry claims it")
        elif not entry.safe_to_delete:
            wrong.append(f"`{name}` is listed as safe but the registry marks it unsafe to delete")

    assert not wrong, "\n".join(wrong)
