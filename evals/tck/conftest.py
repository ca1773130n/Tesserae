"""The ``adapter`` fixture override the TCK asks implementers to provide.

``tck/tests/conftest.py`` states the contract: *"Implementers must override the
`adapter` fixture in their own conftest.py to provide a concrete BaseAdapter
instance for their system."* The kit ships its own override at
``tck/tests/v1/conftest.py`` wiring ``ReferenceAdapter``, and a nearer conftest
wins, so this file has to land at that path to take effect.

:func:`evals.tck.run_tck.build_run_tree` copies the kit's ``tck/tests/`` tree
into a scratch directory and drops this file over ``tests/v1/conftest.py``
there. The clone is never modified — a mutated checkout is indistinguishable
from upstream at a glance, and the next reader cannot tell which result came
from which code.

The adapter writes to a scratch project root, never to a real project's
``.tesserae/``: ``$TESSERAE_TCK_ROOT`` if set, else a fresh temporary directory.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from evals.tck.adapter import build_adapter_class


def tck_project_root() -> Path:
    """The scratch project root the adapter's two substrates live under."""
    override = (os.environ.get("TESSERAE_TCK_ROOT") or "").strip()
    root = Path(override) if override else Path(tempfile.mkdtemp(prefix="tesserae-tck-"))
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
async def adapter():
    """A ``TesseraeAdapter`` over a scratch project root."""
    adapter_class = build_adapter_class()
    instance = adapter_class(tck_project_root())
    await instance.setup()
    yield instance
    await instance.teardown()
