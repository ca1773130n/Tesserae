"""Pytest bootstrap so this git worktree's tests exercise THIS worktree's
``tesserae`` package rather than the main checkout.

The shared virtualenv installs ``tesserae`` editable via a PEP 660
``_EditableFinder`` (a ``MetaPathFinder``) that hard-maps the ``tesserae``
import to the MAIN checkout
(``/Users/neo/Developer/Projects/Tesserae/tesserae``). Meta-path finders are
consulted before ``sys.path`` / ``PYTHONPATH``, so a plain ``PYTHONPATH=$PWD``
cannot redirect the import here — every test would silently run against the
main checkout's source instead of the code under development in this worktree.

This conftest drops that editable finder and pins this worktree's root to the
front of ``sys.path`` so ``import tesserae`` resolves locally. It is scoped to
this worktree (the conftest lives at its root) and never mutates the shared venv
on disk.
"""

from __future__ import annotations

import importlib
import os
import sys

_WORKTREE_ROOT = os.path.dirname(os.path.abspath(__file__))


def _pin_local_tesserae() -> None:
    # Drop the editable-install meta-path finder(s) that map ``tesserae`` to the
    # main checkout, so the standard PathFinder honours ``sys.path`` below.
    sys.meta_path = [
        finder
        for finder in sys.meta_path
        if type(finder).__name__ != "_EditableFinder"
    ]

    # Ensure this worktree wins on sys.path.
    if _WORKTREE_ROOT in sys.path:
        sys.path.remove(_WORKTREE_ROOT)
    sys.path.insert(0, _WORKTREE_ROOT)

    importlib.invalidate_caches()

    # Evict any ``tesserae`` modules already imported from the main checkout so
    # the next import re-resolves against this worktree.
    for name in list(sys.modules):
        if name == "tesserae" or name.startswith("tesserae."):
            module = sys.modules[name]
            mod_file = getattr(module, "__file__", "") or ""
            if not mod_file.startswith(_WORKTREE_ROOT):
                del sys.modules[name]


_pin_local_tesserae()
