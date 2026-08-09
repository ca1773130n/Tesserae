"""Redaction rules shared by every producer that writes transcript text.

One module rather than a helper per producer, because the leak this closes was
a leak of OMISSION: :mod:`tesserae.session_event` grew a home-path redactor and
the other four session producers — the ``Session`` display name, the typed
subagent run, the structural decision and the LLM finding body — kept shipping
absolute ``/Users/<name>/`` paths into ``graph.json``, the vault and the static
site. A private copy in one module is exactly how the next producer misses it.

It lives here, not in :mod:`tesserae.research_graph`, because
:mod:`tesserae.harness_sessions` is a leaf of the ingest layer and imports no
graph model; pulling one in to reach a regex would invert the layering.
"""

from __future__ import annotations

import re


#: A POSIX home directory and the account name that identifies its owner.
#: Deliberately anchored on the two literal roots rather than on ``$HOME``:
#: what this redacts becomes bytes in ``graph.json``, so the rule has to be a
#: pure function of the text. Keying on the running user's home would make the
#: output depend on WHO compiled — breaking byte-idempotence across machines —
#: and would leave a second operator's home directory untouched.
#:
#: There is no LEFT boundary, and that is a decision rather than an oversight.
#: A boundary would make the rule stricter and therefore leakier, and the
#: corpus says by how much: of the 3,432 matches over the ingest corpus's turn
#: text, 24 are directly preceded by a word character — every one of them a
#: real home path abutting an escape sequence, ``...\nn/Users/neo/...`` inside
#: a JSON-escaped shell script (22) or ``\x1b[35m/Users/neo/...`` inside ANSI
#: colour output (2). ``(?<![\w])`` would stop redacting all 24. What it would
#: buy is not over-redacting a path that merely CONTAINS ``/Users/<x>`` deeper
#: down (``/opt/Users/shared``); that renders as ``/opt~``, which is ugly and
#: publishes nothing. Leaking 24 real home paths to stop an ugly rendering is
#: the wrong trade, so the rule stays greedy on the left.
HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s]+")


def redact_home_paths(text: str) -> str:
    """Replace ``/Users/<name>`` / ``/home/<name>`` with ``~``.

    Transcript text is full of absolute paths, so an unredacted producer ships
    the operator's home directory — and their account name — into every
    projection, on by default.

    :mod:`tesserae.okf` already refuses to emit a raw ``/Users/...`` for this
    exact reason (§6.2, via :func:`tesserae.temporal.relative_source_path`);
    this keeps the producers that write free-form transcript text in agreement
    with that rule. The path is REPLACED, not dropped: which file a turn
    touched is the point, only whose machine it was on is not.

    Apply before truncation and before any id seed is built, so no minted
    field can carry a home path and the id is a hash of the redacted text.
    Exempting the seed would leave the home path inside the hash input and
    make the id depend on whose machine compiled — the machine-dependence this
    rule exists to prevent.
    """
    if not text:
        return text
    return HOME_PATH.sub("~", text)
