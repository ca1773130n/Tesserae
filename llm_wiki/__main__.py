"""Module entry point so ``python -m llm_wiki ...`` works.

The package ships a ``llm_wiki`` console script via ``pyproject.toml``,
but environments without that script on PATH (CI containers, embedded
interpreters, ``uv run`` invocations) need the ``-m`` form. This file
keeps both surfaces in sync — both call ``llm_wiki.cli:main``.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
