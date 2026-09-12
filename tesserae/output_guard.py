"""Refuse to delete an output path that Tesserae did not create.

Several exporters open by removing their destination — `write_site` rmtree's the
site directory, the Kuzu adapter removes its database before rewriting it — and
every one of them takes that destination from a user-supplied ``--output``. One
mistyped argument therefore deleted whatever was already there, with no prompt,
no dry-run, and nothing to undo it.

The rule here is deliberately not "is the directory empty". Re-exporting over a
previous export is the common case and must stay silent, or the guard is an
obstacle rather than a safety net. So each exporter says what ITS OWN output
looks like, and anything else is somebody's data:

    guard_output(path, is_ours=_looks_like_a_built_site, kind="a Tesserae site",
                 force=overwrite)

``force`` is the escape hatch, spelled ``--overwrite`` at the CLI. It is a
separate flag from ``--force`` on purpose — ``export site --force`` already
means "deploy a dirty tree", and one flag with two meanings is how somebody
deletes a directory while trying to publish one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class OutputRefused(RuntimeError):
    """``--output`` names something that is not ours to delete.

    Its own type so ``tesserae.cli.main`` can turn it into a one-line refusal
    and exit 2: the user made a typo, and the answer to a typo is a sentence,
    not a stack trace.
    """


def guard_output(
    path: Path,
    *,
    is_ours: Callable[[Path], bool],
    kind: str,
    force: bool = False,
    flag: str = "--overwrite",
) -> None:
    """Raise :class:`OutputRefused` unless ``path`` is safe to delete.

    Safe means: it does not exist, the caller passed ``force``, or ``is_ours``
    recognises it as a previous output of this same exporter.
    """
    if force or not path.exists():
        return
    try:
        if is_ours(path):
            return
    except OSError:
        pass  # unreadable is not recognisable, and therefore not ours
    raise OutputRefused(
        f"refusing to overwrite {path}: it is not {kind}, and writing here "
        f"deletes it first.\n"
        f"  - point --output at a new path or a previous export, or\n"
        f"  - pass {flag} to delete {path} anyway"
    )


def is_empty_dir(path: Path) -> bool:
    """True for a directory with nothing in it — nothing to lose, so not foreign."""
    return path.is_dir() and not any(path.iterdir())
