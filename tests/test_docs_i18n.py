"""Documentation localization coverage checks."""

from __future__ import annotations

import json
import re
from collections import Counter
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


# ---------------------------------------------------------------------------
# Structural parity between an English doc and each of its seven mirrors.
#
# Six translation passes in one day each reported success and each shipped a
# defect no diff could show, because a diff between a German paragraph and a
# Japanese one says nothing. What they had in common was that the *shape* of
# the page stopped matching: a sentence documenting the `altitude` parameter
# vanished from six of seven mirrors (20 backticked spans in English, 15 in the
# mirrors); a table lost a row; a four-bullet block shipped with three.
#
# This is the cheap mechanical floor, not the ceiling. It cannot see a meaning
# inversion, a false friend, or a non-word — "stood down" rendered as its
# opposite passes every check below, because the shape is identical. Those need
# a reader, and docs/i18n/GLOSSARY.md is what that reader is given.
# ---------------------------------------------------------------------------

#: Languages whose prose carries no Latin script of its own, so a bare English
#: word in the body is visible as leakage. ru/es/fr/de are Latin-script and are
#: excluded — there is no signal to read there.
CJK_LANGS = ("ko", "zh", "ja")

PARITY_BASELINE = Path(__file__).parent / "fixtures" / "docs_i18n_parity_baseline.json"

_REGENERATE = (
    "Regenerate with `uv run python tests/test_docs_i18n.py --write-baseline` "
    "and commit the result — but read the diff first: a NEW entry is a defect "
    "you are about to bless, not a formality."
)

#: A ```` ``` ````/``~~~`` fence, possibly indented or inside a blockquote.
#: Matching on the line rather than with a DOTALL regex matters: several docs
#: open a fence inside a ``>`` quote, and a ``^```` anchored regex walks straight
#: past it and then mis-pairs every backtick in the rest of the file.
_FENCE_LINE = re.compile(r"^[ \t]*(?:>[ \t]*)*(`{3,}|~{3,})")
#: An inline code span, one or more backticks either side.
_CODE_SPAN = re.compile(r"(`+)([^`]+?)\1")
_WS = re.compile(r"\s+")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SWITCHER = re.compile(r"<!-- translations:start -->.*?<!-- translations:end -->", re.S)
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_AUTOLINK = re.compile(r"<[^>\s]+>")
_URL = re.compile(r"https?://\S+")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_BOLD = re.compile(r"\*\*[^*\n]+\*\*")
_BULLET = re.compile(r"^[ \t]*[-*] ")
#: A bare filename in prose (``about.png``). Splitting it into words would
#: report ``about`` as leaked English.
_FILENAME = re.compile(
    r"\b[\w./-]+\.(?:png|gif|jpg|svg|ico|md|json|jsonl|html|py|sh|toml|yml|yaml"
    r"|txt|db|css|js|ts|lock|cfg|ini|xml|csv)\b"
)


def _prose_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks. Fence content is copied verbatim into
    every mirror, so counting it would only add noise."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = _FENCE_LINE.match(line)
        if marker:
            token = marker.group(1)[0] * 3
            if fence is None:
                fence = token
                continue
            if token == fence:
                fence = None
                continue
        if fence is None:
            out.append(line)
    return out


def _code_spans(text: str) -> Counter:
    """Multiset of inline code spans, scoped to a paragraph.

    Paragraph scoping is what keeps this honest. A code span may soft-wrap
    across a newline (``docs/tuning.md`` has ``\\`tesserae\\nsync\\``), so the
    span pattern has to allow newlines — and the moment it does, a single stray
    backtick would otherwise swallow the rest of the file and mint dozens of
    phantom "identifiers" out of ordinary prose. A blank line ends the run.

    A leading/trailing backslash is stripped from the captured text: a mirror
    that writes ``\\`ABSENT\\``` is naming the same identifier, and the broken
    rendering it causes is reported separately as ``escaped_backticks`` below.
    """
    found: Counter = Counter()
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        for _, raw in _CODE_SPAN.findall("\n".join(paragraph)):
            span = _WS.sub(" ", raw).strip().strip("\\")
            if span:
                found[span] += 1
        paragraph.clear()

    for line in _prose_lines(text):
        if line.strip():
            paragraph.append(line)
        else:
            flush()
    flush()
    return found


def _structure_counts(text: str) -> dict[str, int]:
    """Shape counts a translation must preserve exactly."""
    lines = _prose_lines(text)
    body = "\n".join(lines)
    table_rows = [ln for ln in lines if ln.strip().startswith("|")]
    counts = {
        "bullets": sum(1 for ln in lines if _BULLET.match(ln)),
        "fences": sum(1 for ln in text.splitlines() if _FENCE_LINE.match(ln)) // 2,
        "table_rows": len(table_rows),
        # Column parity, summed over the file. A per-table breakdown would be
        # more precise but needs the tables to line up one-for-one, and a mirror
        # that dropped a whole table is exactly the case where they do not.
        "table_cells": sum(ln.count("|") for ln in table_rows),
        "bold": len(_BOLD.findall(body)),
        # Zero in every English doc. Non-zero means the mirror wrote a literal
        # backtick where the source had a code span, so the identifier renders
        # as `` `foo` `` in running text instead of as code.
        "escaped_backticks": text.count("\\`"),
    }
    for level in range(1, 5):
        counts[f"h{level}"] = sum(1 for ln in lines if ln.startswith("#" * level + " "))
    return counts


def _latin_prose_words(text: str) -> set[str]:
    """Lowercase English words in prose — outside code, links, and markup.

    Deliberately lowercase-only and three characters up. These files keep a lot
    of Latin script on purpose (the Korean mirrors carry hundreds of Latin
    tokens as house style: Tesserae, MCP, LLM, JSON, Claude), and every one of
    those is either capitalised or an acronym. Restricting to lowercase words
    throws that entire class of legitimate borrowing away before it can become
    a false positive.
    """
    stripped = _SWITCHER.sub(" ", text)
    stripped = "\n".join(_prose_lines(stripped))
    stripped = _HTML_COMMENT.sub(" ", stripped)
    stripped = _URL.sub(" ", stripped)
    stripped = _LINK_TARGET.sub("] ", stripped)
    stripped = _AUTOLINK.sub(" ", stripped)
    stripped = _CODE_SPAN.sub(" ", stripped)
    stripped = _FILENAME.sub(" ", stripped)
    return {
        word
        for word in _LATIN_WORD.findall(stripped)
        if len(word) >= 3 and word == word.lower()
    }


def _leak_candidates(source: Path, mirror: Path) -> set[str]:
    """Lowercase English prose words a CJK mirror carried over from the source.

    Bare filenames are dropped by `_latin_prose_words`; this subtracts the other
    class that would otherwise dominate — words that appear inside backticks
    *anywhere* in the English (`agent`, `compile`, `cache`), which are terms of
    art the mirrors keep in English on purpose.
    """
    english = source.read_text(encoding="utf-8")
    identifiers = {
        word.lower()
        for span in _code_spans(english)
        for word in _LATIN_WORD.findall(span)
    }
    shared = _latin_prose_words(mirror.read_text(encoding="utf-8")) & _latin_prose_words(english)
    return shared - identifiers


def _pairs() -> list[tuple[Path, Path]]:
    """(English source, mirror) for every doc under docs/ plus the root README."""
    pairs: list[tuple[Path, Path]] = []
    for source in _canonical_docs():
        rel = source.relative_to(DOCS)
        for lang in LANGS:
            mirror = DOCS / "i18n" / rel.with_name(f"{rel.stem}.{lang}{rel.suffix}")
            if mirror.exists():
                pairs.append((source, mirror))
    for lang in LANGS:
        mirror = ROOT / f"README.{lang}.md"
        if mirror.exists():
            pairs.append((ROOT / "README.md", mirror))
    return pairs


def _drift(source: Path, mirror: Path) -> dict:
    """Everything this check can see wrong with one (English, mirror) pair."""
    english = source.read_text(encoding="utf-8")
    translated = mirror.read_text(encoding="utf-8")

    record: dict = {}

    missing = sorted(set(_code_spans(english)) - set(_code_spans(translated)))
    if missing:
        record["missing_spans"] = missing

    en_counts, tr_counts = _structure_counts(english), _structure_counts(translated)
    structure = {
        key: tr_counts[key] - en_counts[key]
        for key in en_counts
        if tr_counts[key] != en_counts[key]
    }
    if structure:
        record["structure"] = structure

    return record


def _measure() -> dict[str, dict]:
    """Drift for every pair, with the CJK leakage candidates corpus-filtered.

    The filter has to run across all mirrors at once, which is why it lives
    here and not in `_drift`: a word is only interesting if *no other* CJK
    mirror in the repo uses it.
    """
    report: dict[str, dict] = {}
    candidates: dict[str, set[str]] = {}
    for source, mirror in _pairs():
        key = str(mirror.relative_to(ROOT))
        record = _drift(source, mirror)
        if record:
            report[key] = record
        if mirror.name.rsplit(".", 2)[-2] in CJK_LANGS:
            candidates[key] = _leak_candidates(source, mirror)

    corpus: Counter = Counter()
    for words in candidates.values():
        corpus.update(words)
    for key, words in candidates.items():
        rare = sorted(word for word in words if corpus[word] == 1)
        if rare:
            report.setdefault(key, {})["untranslated_words"] = rare
    return report


def _baseline() -> dict[str, dict]:
    return json.loads(PARITY_BASELINE.read_text(encoding="utf-8"))["pairs"]


def test_mirrors_do_not_drop_more_of_the_english_code_spans() -> None:
    """Ratchet: every `identifier` in an English doc must survive into its mirrors.

    The highest-value half of the parity check, and the one that would have
    caught the `altitude` regression: an identifier only disappears from a
    mirror when the sentence carrying it was dropped, and dropped sentences are
    invisible to every other guard in this file.

    Failures name the file and the identifiers, because a ratchet whose message
    does not say what broke gets suppressed instead of fixed.
    """
    baseline, current = _baseline(), _measure()
    regressions: list[str] = []
    for path, record in sorted(current.items()):
        allowed = set(baseline.get(path, {}).get("missing_spans", ()))
        new = sorted(set(record.get("missing_spans", ())) - allowed)
        if new:
            regressions.append(f"  {path}: dropped {', '.join(repr(s) for s in new)}")

    assert not regressions, (
        "identifiers present in the English doc are absent from its translation:\n"
        + "\n".join(regressions)
        + "\n\nAn identifier goes missing when the sentence around it was dropped. "
        "Restore the sentence — do not translate the identifier.\n" + _REGENERATE
    )


def test_mirrors_do_not_drift_further_from_the_english_structure() -> None:
    """Ratchet: bullets, headings, fences, table rows/columns and bold spans.

    Each count is a claim the page makes about itself. A mirror with one fewer
    table row documents one fewer thing; a mirror with fewer bold spans has
    quietly de-emphasised a claim the English put in bold on purpose.
    """
    baseline, current = _baseline(), _measure()
    regressions: list[str] = []
    for path, record in sorted(current.items()):
        allowed = baseline.get(path, {}).get("structure", {})
        for key, delta in sorted(record.get("structure", {}).items()):
            if abs(delta) > abs(allowed.get(key, 0)):
                direction = "more" if delta > 0 else "fewer"
                regressions.append(
                    f"  {path}: {abs(delta)} {direction} {key} than the English "
                    f"(was {allowed.get(key, 0):+d}, now {delta:+d})"
                )

    assert not regressions, (
        "translations no longer have the same shape as their English source:\n"
        + "\n".join(regressions)
        + "\n\n`escaped_backticks` means the mirror wrote \\` where the source had a "
        "code span — the identifier renders with visible backticks.\n" + _REGENERATE
    )


def test_cjk_mirrors_do_not_leak_more_untranslated_english_prose() -> None:
    """Ratchet: raw English words left standing in Korean/Chinese/Japanese prose.

    CATCHES a lowercase Latin word, three characters or more, that is all four
    of: present as prose in the English source, present as prose in the mirror,
    never an identifier (inside backticks) anywhere in the English, and used by
    no other CJK mirror in the repo. `mid-call` and `mid-pass` shipped inside
    Chinese sentences and would have been caught by exactly that shape.

    DOES NOT CATCH, all deliberately:

    * Capitalised words and acronyms — Tesserae, MCP, LLM, JSON, Claude. These
      files keep hundreds of Latin tokens on purpose, and essentially all of
      that legitimate borrowing is capitalised or an acronym. Filtering it out
      up front is the only thing that keeps this check from being noise.
    * Terms of art the corpus consistently keeps in English. Measured on this
      repo, 2,395 lowercase English prose tokens survive into the CJK mirrors
      across 173 pairs; only 278 of them are used by exactly one mirror. The
      other 2,117 are house style and are invisible here. A one-off is the only
      thing that reads as an accident.
    * Anything inside backticks or a fenced block, which must never be
      translated in the first place.
    * The inverse and far more serious failure: a term that WAS translated,
      wrongly. `stood down` rendered as "standing by" is structurally perfect.
      That needs a reader, and docs/i18n/GLOSSARY.md is what the reader gets.

    The corpus-rarity filter makes this check relative to the rest of docs/, so
    adding or deleting a mirror can change what counts as rare. The baseline
    absorbs that; only genuinely new words fail.
    """
    baseline, current = _baseline(), _measure()
    regressions: list[str] = []
    for path, record in sorted(current.items()):
        allowed = set(baseline.get(path, {}).get("untranslated_words", ()))
        new = sorted(set(record.get("untranslated_words", ())) - allowed)
        if new:
            regressions.append(f"  {path}: {', '.join(repr(w) for w in new)}")

    assert not regressions, (
        "English prose words left untranslated in a CJK mirror:\n"
        + "\n".join(regressions)
        + "\n\nIf the term is meant to stay in English, put it in backticks or "
        "record it in docs/i18n/GLOSSARY.md.\n" + _REGENERATE
    )


def test_parity_baseline_records_exactly_the_drift_that_still_exists() -> None:
    """The ratchet may only tighten: a fixed mirror must leave the baseline.

    Same idiom as `KNOWN_UNDOCUMENTED` above. Without this, a pass that repairs
    six mirrors leaves six entries behind that silently re-license the same
    defect for whoever touches those files next.

    Scoped to the two fields that record a *defect*. `untranslated_words` is a
    vocabulary snapshot rather than a defect list — a word can leave it because
    a sentence was legitimately reworded, or because another mirror started
    using the same word — so holding it to this standard would fail the suite on
    ordinary edits and teach the next author to delete the guard.
    """
    baseline, current = _baseline(), _measure()
    stale: list[str] = []

    for path, record in sorted(baseline.items()):
        now = current.get(path, {})
        fixed = sorted(set(record.get("missing_spans", ())) - set(now.get("missing_spans", ())))
        if fixed:
            stale.append(f"  {path}: these identifiers are present now: {fixed}")
        for key, delta in sorted(record.get("structure", {}).items()):
            actual = now.get("structure", {}).get(key, 0)
            if abs(actual) < abs(delta):
                stale.append(f"  {path}: structure/{key} improved {delta:+d} -> {actual:+d}")

    assert not stale, (
        "these mirrors were repaired but the baseline still licenses the old "
        "drift, so the same defect could come back unnoticed:\n"
        + "\n".join(stale)
        + "\n\n" + _REGENERATE
    )


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    import sys

    if "--write-baseline" not in sys.argv:
        raise SystemExit("usage: python tests/test_docs_i18n.py --write-baseline")
    pairs = _measure()
    PARITY_BASELINE.write_text(
        json.dumps(
            {
                "_comment": (
                    "Structural drift between each English doc under docs/ and its "
                    "translation, as it stood when this file was written. This is a "
                    "RATCHET: the tests in tests/test_docs_i18n.py fail when a pair "
                    "drifts further than its entry here, and fail again when an entry "
                    "outlives the drift it records. Regenerate with "
                    "`uv run python tests/test_docs_i18n.py --write-baseline`."
                ),
                "pairs": pairs,
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {PARITY_BASELINE} — {len(pairs)} drifting pairs of {len(_pairs())}")
