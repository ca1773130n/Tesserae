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


#: Credential shapes. These lived privately in :mod:`tesserae.harness_sessions`,
#: applied at ingest to every stored turn, and nowhere else — which is the exact
#: shape of the omission this module's docstring describes. They are here so a
#: producer that publishes transcript-derived text can reach them without
#: importing the ingest layer.
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"sk-[A-Za-z0-9]{12,}"),
    # ...and the vendor-prefixed form the rule above cannot see. An Anthropic
    # key is ``sk-ant-<version>-<blob>``: the hyphens break ``[A-Za-z0-9]{12,}``
    # after three characters, so the single most likely credential on a machine
    # running this codebase passed the redactor untouched. Segment names are
    # spelled out rather than admitting any hyphenated word after ``sk-``,
    # which would start redacting ordinary file names.
    re.compile(r"sk[-_](?:ant|proj|live|test)[-_][A-Za-z0-9_\-]{8,}"),
    # A credential passed as a FLAG value. The generic rule above needs ``:``
    # or ``=``; ``--api-key <value>`` is separated by a space, and widening the
    # generic rule to accept whitespace would redact the word after every
    # occurrence of "token" in ordinary prose — in a corpus that discusses
    # tokens constantly. A leading ``--`` is the whole difference.
    re.compile(r"(?i)--(?:api[-_]?key|token|secret|password|passwd)[=\s]+\S+"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
)

REDACTED = "[REDACTED]"


def redact_secrets(text: str) -> str:
    """Replace credential-shaped substrings with ``[REDACTED]``.

    Applied at ingest to every stored turn, and again by any producer that
    copies transcript text into a node or edge. Re-applying is harmless: the
    replacement contains no credential shape.
    """
    if not text:
        return ""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_published_text(text: str) -> str:
    """Both rules, in the order ingest applies them: secrets, then home paths.

    The one call a producer should make on any string it is about to write into
    ``graph.json``, the vault or the site. Secrets first so a home path inside a
    credential-bearing fragment is not what survives.
    """
    return redact_home_paths(redact_secrets(text))


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
