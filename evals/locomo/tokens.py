"""Token accounting for the context-efficiency arms: one tokenizer, one rule.

The axis these arms are measured on is TOKENS-TO-CORRECT-ANSWER, so the token
count is the instrument and not a decoration. Three decisions are made here and
nowhere else, because a token count computed two ways is two experiments:

1. **What is counted is the COMPLETE SERIALIZED REQUEST**, not the evidence.
   :func:`serialized_request` returns exactly the string the CLI backbones send
   — ``tesserae.llm_json._stitch_json_prompt``'s output, imported rather than
   re-derived — which is system prompt + the JSON-only contract + schema name +
   user turn. Counting only the evidence leaves a smuggling channel open: an arm
   could move instructions, few-shot examples or a fatter schema description
   into the system half and pay nothing for them. The private import is
   deliberate and :mod:`tests.test_locomo_context_arms` asserts the two strings
   stay byte-identical, so a change in ``llm_json`` breaks a test rather than
   silently changing what "tokens" means.

2. **Characters are not a substitute.** The read-only audit at
   ``~/.blackhole/Tesserae/2026-08-22/context-eval/instrument.md`` measured
   chars/token across this corpus's own artifact families at 3.20 (wiki) to 4.31
   (compiled brief) against 3.95 for raw dialogue — a 35% spread. A character
   budget therefore hands one arm more of the rationed resource than another
   while appearing to ration them equally.

3. **One tokenizer for every arm, pinned by digest.** The Qwen3 BPE shipped in
   this machine's Hugging Face cache, verified here at vocab size 151,669 and
   sha256 ``def76fb0…b4b50a``. No GPT-family tokenizer is installed (tiktoken,
   transformers and sentencepiece all fail to import in ``.venv``), and the LLM
   client returns no usage block, so an ABSOLUTE rung label is a proxy with an
   unknown constant factor against whatever the backbone tokenizes with. The
   COMPARISON is unaffected — one tokenizer is applied to every arm — and the
   report declares the proxy rather than implying a GPT count.

Nothing here calls a model or the network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..qa.run_qa_eval import Skip

#: The pinned tokenizer's content digest. Verified against the file on this
#: machine before it was written down; :func:`load_tokenizer` refuses a file
#: whose digest differs rather than silently measuring with another vocabulary.
TOKENIZER_SHA256 = "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"

#: Its vocabulary size, as reported by ``tokenizers`` 0.22.2 when this module
#: was written. Declared so a report can print what it measured with.
TOKENIZER_VOCAB = 151_669

TOKENIZER_NAME = "Qwen/Qwen3-Embedding-0.6B (tokenizer.json, BPE)"

_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
_TOKENIZER_GLOB = (
    "models--Qwen--Qwen3-Embedding-0.6B/snapshots/*/tokenizer.json"
)

#: The schema name every arm's request declares. One value, because the schema
#: name is inside the serialized request and therefore inside the token count.
SCHEMA_NAME = "locomo_answer"

_TOKENIZER: Any = None
_TOKENIZER_PATH: Optional[Path] = None


def find_tokenizer() -> Path:
    """The pinned tokenizer file, or a :class:`Skip` naming what is missing."""
    matches = sorted(_CACHE.glob(_TOKENIZER_GLOB))
    if not matches:
        raise Skip(
            f"no tokenizer at {_CACHE / _TOKENIZER_GLOB}",
            "these arms are measured in tokens and refuse to fall back to a "
            "character count; fetch Qwen/Qwen3-Embedding-0.6B into the "
            "Hugging Face cache, or run --dry-run on a machine that has it",
        )
    return matches[0]


def tokenizer_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tokenizer() -> Any:
    """The one tokenizer, loaded once, digest-checked.

    A digest mismatch is a :class:`Skip` and not a warning. Two runs measured
    with two vocabularies produce two ladders that look like one.
    """
    global _TOKENIZER, _TOKENIZER_PATH
    if _TOKENIZER is not None:
        return _TOKENIZER
    path = find_tokenizer()
    digest = tokenizer_digest(path)
    if digest != TOKENIZER_SHA256:
        raise Skip(
            f"the tokenizer at {path} has sha256 {digest}, not the pinned "
            f"{TOKENIZER_SHA256}",
            "every token number in this harness is measured with the pinned "
            "file; re-pin deliberately, do not measure with whatever is there",
        )
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:  # pragma: no cover — the package is in .venv
        raise Skip(
            f"the `tokenizers` package is not importable ({exc})",
            "pip install tokenizers, or run a mode that spends no tokens",
        ) from exc
    _TOKENIZER = Tokenizer.from_file(str(path))
    _TOKENIZER_PATH = path
    return _TOKENIZER


def tokenizer_controls() -> Dict[str, Any]:
    """What a reader needs to re-derive every token number in the report."""
    load_tokenizer()
    assert _TOKENIZER_PATH is not None
    return {
        "tokenizer": TOKENIZER_NAME,
        "tokenizer_path": str(_TOKENIZER_PATH),
        "tokenizer_sha256": TOKENIZER_SHA256,
        "tokenizer_vocab": int(_TOKENIZER.get_vocab_size()),
        "token_unit": "complete serialized request (system + JSON contract + "
                      "schema name + user), llm_json._stitch_json_prompt",
        "token_proxy": "the backbone returns no usage block, so rung labels are "
                       "a Qwen3-BPE proxy with an unknown constant factor "
                       "against the backbone's own tokenizer; identical across "
                       "arms, so the comparison is unaffected",
    }


def count_tokens(text: str) -> int:
    """Tokens in ``text`` under the pinned BPE. No special tokens added."""
    return len(load_tokenizer().encode(text, add_special_tokens=False).ids)


def serialized_request(system: str, user: str,
                       schema_name: str = SCHEMA_NAME) -> str:
    """The exact string a CLI backbone sends for this ``complete_json`` call.

    Imported from :mod:`tesserae.llm_json` rather than restated. It is private
    there because it is the cache identity; it is used here because the cache
    identity IS the request, and a second copy of the stitching would drift.
    """
    from tesserae.llm_json import _stitch_json_prompt

    return _stitch_json_prompt(system=system, user=user, schema_name=schema_name)


@dataclass(frozen=True)
class Prompt:
    """One request, built and counted BEFORE it is sent.

    Persisting this — not the answer's evidence size — is what makes a token
    claim auditable after the fact. The previously shipped answers file recorded
    ``evidence_chars`` and never the prompt, so no token number in it could be
    re-derived without re-spending the run.
    """

    system: str
    user: str
    schema_name: str
    #: The evidence items, in the order they appear in ``user``.
    items: Sequence[str] = ()
    #: What the arm asked its own knob for, and what fitting cost. Arm-specific.
    fit: Dict[str, Any] = field(default_factory=dict)
    #: True when the arm had to cut a unit of evidence mid-way to fit the
    #: budget. A COUNTED, PRINTED field: a fixed-budget ladder measures
    #: truncation skill rather than compilation unless truncation is visible.
    truncated: bool = False

    @property
    def request(self) -> str:
        return serialized_request(self.system, self.user, self.schema_name)

    @property
    def tokens(self) -> int:
        return count_tokens(self.request)

    @property
    def evidence_chars(self) -> int:
        return sum(len(item) for item in self.items)

    def as_row(self) -> Dict[str, Any]:
        """The per-row token accounting every arm writes."""
        return {
            "prompt_tokens": self.tokens,
            "prompt_chars": len(self.request),
            "evidence_chars": self.evidence_chars,
            "n_evidence": len(self.items),
            "truncated": bool(self.truncated),
            "fit": dict(self.fit),
        }


def numbered_evidence(items: Sequence[str]) -> str:
    """``[1] …\\n\\n[2] …`` — the shape :mod:`evals.locomo.run`'s backbone uses.

    Restated here rather than imported because ``run.build_backbone`` builds it
    inside a closure that returns only the answer. Keeping the SHAPE identical
    is what lets a row from this harness be compared with one from that one; a
    test asserts the two renderings agree.
    """
    return "\n\n".join(f"[{i}] {text}" for i, text in enumerate(items, start=1))


def user_turn(question: str, items: Sequence[str]) -> str:
    """The user half of the request. Empty evidence still declares itself.

    An arm that supplies nothing must not silently look like an arm that was
    never asked: the closed-book control's user turn says so in words, and those
    words are inside the token count like everything else.
    """
    if not items:
        return f"Evidence: none supplied.\n\nQuestion: {question}"
    return f"Evidence:\n{numbered_evidence(items)}\n\nQuestion: {question}"


def fit_by_prefix(system: str, question: str, text: str, *,
                  budget_tokens: int, schema_name: str = SCHEMA_NAME) -> str:
    """The longest character prefix of ``text`` whose request fits the budget.

    Binary search on characters, measured in tokens — the two are not
    proportional, which is the whole reason this function is not a slice. An
    empty return means the framing alone already exceeds the budget, and the
    caller records that rather than paying for a request it knows is over.
    """
    if not text:
        return ""
    if count_tokens(serialized_request(
            system, user_turn(question, [text]), schema_name)) <= budget_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = text[:mid]
        request = serialized_request(
            system, user_turn(question, [candidate]), schema_name)
        if count_tokens(request) <= budget_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low]


__all__ = [
    "SCHEMA_NAME",
    "TOKENIZER_NAME",
    "TOKENIZER_SHA256",
    "TOKENIZER_VOCAB",
    "Prompt",
    "count_tokens",
    "find_tokenizer",
    "fit_by_prefix",
    "load_tokenizer",
    "numbered_evidence",
    "serialized_request",
    "tokenizer_controls",
    "tokenizer_digest",
    "user_turn",
]
