"""The one place Tesserae asks a human which LLM backend to use.

Three commands need this conversation — ``tesserae setup`` (machine-wide),
``tesserae init`` (project) and a bare ``tesserae config llm`` — and two of
them used to carry their own copy of it. Both copies had the same hole, which
is the argument for this module existing rather than a third one being written:
each asked a ``custom`` provider for a base URL, an API key and a model, and
neither asked for the **wire protocol** or for a **bearer token**.

That combination cannot work. ``llm_api_style`` defaults to ``anthropic`` for
``custom``, so an OpenAI-compatible gateway configured through either wizard
was sent Anthropic-shaped requests, with its bearer credential in the api-key
header. The only way to configure one was to retype everything as
``tesserae config llm --llm-api-style openai --llm-auth-token ... `` — the long
argument list this module exists to make unnecessary.

Neither copy asked for a claude config dir LIST either, only a single dir, so
the one thing a wizard could write was the single-account pin that dies with
that account's quota.

Answers are returned, never written. Each caller owns its own destination:
``setup`` writes ``~/.tesserae/config.json``, ``init`` folds them into a
``SetupPlan``, ``config llm`` writes the machine-wide file. Keeping the writes
out here is what lets one conversation serve three commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

#: Every provider the CLI parsers accept. The wizards each offered a subset —
#: ``openai``, whose entire point is a non-Anthropic wire, was missing from
#: both — so the interactive path could not reach a backend the flags could.
PROVIDERS: Tuple[str, ...] = ("claude", "codex", "anthropic", "openai", "custom")

#: Providers that talk to an HTTP endpoint, and therefore have a wire protocol,
#: a base URL and a credential to settle. ``claude`` is here too: the CLI can be
#: pointed at a gateway with ``ANTHROPIC_BASE_URL`` + a bearer token and then
#: needs no login at all.
_ENDPOINT_PROVIDERS = frozenset({"anthropic", "openai", "custom"})


@dataclass
class LlmAnswers:
    """What the human said. ``None`` means "leave whatever is configured"."""

    provider: Optional[str] = None
    api_style: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    auth_token: Optional[str] = None
    model: Optional[str] = None
    claude_config_dirs: List[str] = field(default_factory=list)
    codex_home: Optional[str] = None
    reasoning_effort: Optional[str] = None


def _as_list(raw: object) -> List[str]:
    """A stored dir list, the legacy singular string, or nothing."""
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(d) for d in (raw or []) if str(d)]


def _blank_to_none(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def prompt_llm_backend(
    current: Optional[Mapping] = None,
    *,
    provider: Optional[str] = None,
    provider_choices: Optional[Sequence[Tuple[str, str]]] = None,
    ask=None,
    echo=None,
) -> LlmAnswers:
    """Ask for a complete, coherent backend configuration.

    ``current`` is the config being edited; every prompt offers what is already
    there as its default, so re-running a wizard never silently drops a setting
    the user made last time.

    ``provider`` skips the provider question for a caller that already asked it
    its own way — ``init`` uses a numbered list annotated with which CLIs it
    detected as logged in, which is worth more than a uniform prompt. What was
    duplicated, and what both copies got wrong, is everything *after* the
    provider; that is what this function is for.

    ``provider_choices`` labels the options when this function does ask. Values
    outside :data:`PROVIDERS` are ignored, and anything in :data:`PROVIDERS` a
    caller omits is still offered — a wizard must not be able to hide a backend
    the flags accept.

    ``ask`` is the prompt function (defaults to ``rich.prompt.Prompt.ask``) and
    ``echo`` the line printer, both injectable so the conversation can be driven
    by a test without a TTY and routed through a caller's own console.
    """
    if ask is None:  # pragma: no cover — exercised interactively
        from rich.prompt import Prompt

        ask = Prompt.ask

    if echo is None:  # pragma: no cover — exercised interactively
        echo = print

    current = dict(current or {})
    answers = LlmAnswers()

    if provider in PROVIDERS:
        answers.provider = provider
    else:
        labels = {v: label for v, label in (provider_choices or ()) if v in PROVIDERS}
        ordered = [v for v, _ in (provider_choices or ()) if v in PROVIDERS]
        ordered += [v for v in PROVIDERS if v not in ordered]
        for value in ordered:
            if labels.get(value):
                echo(f"  {value:<10} {labels[value]}")
        answers.provider = ask(
            "LLM provider",
            choices=list(ordered),
            default=current.get("llm_provider") or ordered[0],
        )

    if answers.provider == "codex":
        answers.reasoning_effort = ask(
            "Codex reasoning effort",
            choices=["low", "medium", "high", "xhigh"],
            default=current.get("llm_codex_reasoning_effort") or "medium",
        )
        answers.codex_home = _blank_to_none(ask(
            "Codex home (blank = every credentialed ~/.codex*)",
            default=current.get("llm_codex_home") or "",
        ))

    if answers.provider == "claude":
        # Comma-separated, because the flag is repeatable and the list is a
        # ROTATION order. Asking for one dir is how a wizard wrote the
        # single-account pin that takes the whole run down with that account's
        # weekly quota.
        raw = ask(
            "Claude config dirs, in rotation order, comma-separated "
            "(blank = every credentialed ~/.claude*)",
            default=", ".join(
                _as_list(current.get("llm_claude_config_dirs"))
                or _as_list(current.get("llm_claude_config_dir"))
            ),
        )
        answers.claude_config_dirs = [d.strip() for d in str(raw).split(",") if d.strip()]

    # The claude CLI can be routed at a gateway instead of Anthropic, so it is
    # offered the endpoint questions too — but only if the user wants them,
    # since the common case is the CLI's own login.
    if answers.provider == "claude":
        # For the CLI the endpoint is optional — the common case is its own
        # login — so the base URL doubles as the yes/no. One question, not two.
        answers.base_url = _blank_to_none(ask(
            "Custom endpoint base URL for the claude CLI "
            "(blank = Anthropic via the CLI's own login)",
            default=current.get("llm_base_url") or "",
        ))
        wants_endpoint = answers.base_url is not None
    else:
        wants_endpoint = answers.provider in _ENDPOINT_PROVIDERS

    if wants_endpoint:
        if answers.provider not in ("anthropic", "claude"):
            # The wire is a different question from the backend, and it is the
            # one both wizards decided silently.
            answers.api_style = ask(
                "Wire protocol — anthropic (POST {base}/v1/messages) or "
                "openai (POST {base}/chat/completions: vLLM, LiteLLM, "
                "OpenRouter, Ollama, LM Studio)",
                choices=["anthropic", "openai"],
                default=(
                    current.get("llm_api_style")
                    or ("openai" if answers.provider == "openai" else "anthropic")
                ),
            )
            answers.base_url = _blank_to_none(ask(
                "Base URL", default=current.get("llm_base_url") or "",
            ))

        # Bearer vs api key: the gateway decides, and the wrong choice puts the
        # credential in a header the endpoint does not read.
        kind = ask(
            "Credential — bearer sends Authorization: Bearer, "
            "api-key sends the provider's own key header",
            choices=["bearer", "api-key", "none"],
            default=(
                "bearer" if current.get("llm_auth_token")
                else "api-key" if current.get("llm_api_key")
                else "api-key" if answers.provider == "anthropic"
                else "bearer"
            ),
        )
        if kind == "bearer":
            answers.auth_token = _blank_to_none(ask(
                "Bearer token (stored in PLAINTEXT config; blank = "
                "use the TESSERAE_LLM_AUTH_TOKEN env var)",
                default="", password=True,
            ))
        elif kind == "api-key":
            answers.api_key = _blank_to_none(ask(
                "API key (stored in PLAINTEXT config; blank = "
                "use the ANTHROPIC_API_KEY env var)",
                default="", password=True,
            ))

    if answers.provider != "codex":
        answers.model = _blank_to_none(ask(
            "Model name (blank = provider default)",
            default=current.get("llm_model") or "",
        ))
    return answers
