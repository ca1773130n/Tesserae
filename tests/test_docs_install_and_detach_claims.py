"""Guards on three install/runtime claims the docs are easy to get wrong.

All three are things we measured on this machine rather than assumed:

1. ``uv tool install`` has no ``--extra`` flag (checked against uv 0.10.7), so
   ``uv tool install tesserae --extra semantic`` dies with
   ``unexpected argument '--extra'``. Extras only land via the bracketed form.
   A downstream setup script hit exactly this and silently fell through to its
   failure branch, so the ``semantic`` extra never installed and ``associate``
   plus hybrid retrieval had been quietly running on the non-semantic stub.

2. ``nohup`` does *not* detach from the process group — it only sets ``SIGHUP``
   to ignore. macOS ships no ``setsid``, so the hooks' spawn ladder always lands
   on ``nohup`` there and a harness that reaps the session's process group kills
   the backgrounded compile anyway. The docs used to promise the opposite.

3. Being killed there is recoverable, not harmless. ``_write_artifacts``
   rmtree's ``wiki/`` and ``site/`` before rebuilding them and writes the SQLite
   store AFTER ``graph.json``, so a kill inside that window leaves the
   projections missing and the store a compile behind — only ``graph.json``
   itself is safe, via atomic rename. What saves the operator is the ``graphed``
   completeness marker, not an absence of damage. The docs said "nothing is
   corrupted".

These are text assertions, not behaviour tests: the defect was a false claim in
prose, and prose is the only place it can regress.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Same set as tests/test_docs_i18n.py — kept local rather than imported so this
# module stays readable on its own.
LANGS = ("ko", "zh", "ja", "ru", "es", "fr", "de")

# The claim retired in this change. Kept as a literal so reintroducing the old
# wording (or copy-pasting it into a new doc) fails loudly.
RETIRED_DETACH_CLAIM = "survives session reap"

# Same treatment for the reassurance that replaced it. A kill mid-compile is
# recoverable, not harmless: ``_write_artifacts`` rmtree's ``wiki/`` and
# ``site/`` before rebuilding them and writes the SQLite store AFTER
# ``graph.json``, so a kill in that window really does leave the projections
# missing and the store a compile behind (measured). Only ``graph.json`` itself
# is safe, because it lands via atomic rename.
RETIRED_CORRUPTION_CLAIMS = ("Nothing is corrupted", "nothing corrupted")


def _installation_docs() -> list[Path]:
    return [DOCS / "installation.md"] + [
        DOCS / "i18n" / f"installation.{lang}.md" for lang in LANGS
    ]


def _detach_claim_docs() -> list[Path]:
    """Owned prose that may talk about the hooks' background spawn.

    ``release-notes/`` is excluded on purpose: those are dated records of what
    shipped in a given version, not live guidance, so they are never retro-edited.
    """
    candidates = [ROOT / "PLUGIN-README.md"]
    candidates += [p for p in sorted(DOCS.rglob("*.md")) if "release-notes" not in p.parts]
    return candidates


def test_installation_docs_show_the_bracketed_uv_extras_form() -> None:
    """Every installation doc (en + 7 locales) carries the working uv recipe."""
    missing = [
        str(path.relative_to(ROOT))
        for path in _installation_docs()
        if 'uv tool install "tesserae[semantic]"' not in path.read_text(encoding="utf-8")
    ]
    assert not missing


def _uses_rejected_uv_extra_flag(line: str) -> bool:
    """True for a shell line that calls ``uv tool install`` with a bare ``--extra``.

    ponytail: substring matching, not a shell parse — a scripted uv install is a
    single literal line in practice. If one ever gets built up from variables,
    upgrade this to inspect the resolved argv instead.
    """
    if "uv tool install" not in line:
        return False
    # ``--extra-index-url`` is a real uv flag; only the bare ``--extra`` is not.
    stripped = line.rstrip()
    return "--extra " in stripped or stripped.endswith("--extra")


def test_rejected_uv_extra_flag_detector_matches_the_real_forms() -> None:
    """The detector fires on the broken invocation and not on the working ones."""
    assert _uses_rejected_uv_extra_flag('uv tool install --force "$dir" --extra semantic')
    assert _uses_rejected_uv_extra_flag("uv tool install tesserae --extra")
    assert not _uses_rejected_uv_extra_flag('uv tool install --force "tesserae[semantic]"')
    assert not _uses_rejected_uv_extra_flag("uv tool install --extra-index-url $URL tesserae")
    assert not _uses_rejected_uv_extra_flag("pip install --extra foo")


def test_no_shell_script_invokes_uv_tool_install_with_extra_flag() -> None:
    """No bundled script may use the ``--extra`` form that uv rejects."""
    offenders = [
        f"{script.relative_to(ROOT)}:{lineno}"
        for script in sorted((ROOT / "scripts").glob("*.sh"))
        for lineno, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1)
        if _uses_rejected_uv_extra_flag(line)
    ]
    assert not offenders


def test_docs_do_not_claim_the_background_compile_survives_a_session_reap() -> None:
    """The old PLUGIN-README promise is gone and stays gone."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in _detach_claim_docs()
        if RETIRED_DETACH_CLAIM in path.read_text(encoding="utf-8")
    ]
    assert not offenders


def test_docs_mentioning_nohup_also_state_the_macos_limit() -> None:
    """Any live doc that names the fallback must name what it does not buy you.

    ``macOS`` / ``setsid`` / ``nohup`` are proper nouns and code spans, so they
    survive translation verbatim — the check works across all eight locales.
    """
    incomplete: list[str] = []
    for path in _detach_claim_docs():
        text = path.read_text(encoding="utf-8")
        if "nohup" not in text:
            continue
        if "macOS" not in text or "setsid" not in text:
            incomplete.append(str(path.relative_to(ROOT)))
    assert not incomplete


def test_docs_do_not_claim_a_killed_compile_corrupts_nothing() -> None:
    """The flat reassurance is gone from the English prose and stays gone.

    English-only by construction: the localized siblings translate the sentence,
    so a literal grep cannot see it there. What every locale IS pinned on is the
    positive claim below, which rides on code spans that survive translation.
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{claim}"
        for path in _detach_claim_docs()
        for claim in RETIRED_CORRUPTION_CLAIMS
        if claim in path.read_text(encoding="utf-8")
    ]
    assert not offenders


def test_kill_caveat_names_the_marker_that_repairs_it() -> None:
    """Deleting the reassurance is not enough — say what IS guaranteed.

    A kill can leave the manifest ahead of ``graph.json``; the ``graphed``
    completeness marker is exactly what makes the next ``--changed-only`` refuse
    its no-op and re-extract the whole corpus instead of reporting a clean
    ``processed=0`` on a permanently partial graph. Any doc that warns about the
    kill has to name that repair, or it is warning without recourse.

    ``graph.json`` / ``graphed`` / ``--changed-only`` are code spans, so they
    survive translation verbatim — the check works across all eight locales.
    """
    incomplete: list[str] = []
    for path in _detach_claim_docs():
        text = path.read_text(encoding="utf-8")
        if "nohup" not in text:
            continue
        if not all(token in text for token in ("graph.json", "graphed", "--changed-only")):
            incomplete.append(str(path.relative_to(ROOT)))
    assert not incomplete
