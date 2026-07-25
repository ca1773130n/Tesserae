"""Guards on two install/runtime claims the docs are easy to get wrong.

Both are things we measured on this machine rather than assumed:

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
