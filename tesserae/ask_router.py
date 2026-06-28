"""Smart scope router for ``ask``.

With the active-project concept removed, every bare question must be *routed*:
to one project, to every project (fan-out), or **federated** (one merged,
cross-referenced answer). Federated is the FALLBACK — it sees every project, so
"unsure" never means "wrong project".

Consecutive questions carry a short ``history`` so a follow-up ("and why?") stays
on the prior route while a topic shift ("what about <other project>?") reroutes.
The heuristics resolve the clear cases for free; an optional ``llm_classify`` hook
handles the genuinely ambiguous middle. Deterministic given the same inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Sequence

# Scope values mirror `tesserae ask --scope`.
SCOPE_CURRENT = "current"
SCOPE_ALL = "all-registered"
SCOPE_FEDERATED = "federated"


@dataclass
class Route:
    scope: str                       # current | all-registered | federated
    aliases: List[str] = field(default_factory=list)  # target project(s)
    reason: str = ""


_COMPARE = re.compile(
    # Deliberately NOT bare 'vs'/'across' — they false-fire on 'vs code',
    # 'across organizations'. Require an unambiguous cross-project phrasing.
    r"\b(compare|comparison|compared|versus|which project|best project|"
    r"every project|all projects|each project|between projects|cross[- ]project)\b",
    re.I,
)
_FOLLOWUP = re.compile(
    r"^\s*(and|also|but|what about|how about|why|then|so|it|its|that|those|these|this|"
    r"more|elaborate|continue|go on|tell me more|expand|again)\b",
    re.I,
)


def is_followup(question: str) -> bool:
    """A short/anaphoric question that continues the previous one."""
    q = (question or "").strip()
    return bool(_FOLLOWUP.match(q)) or len(q.split()) <= 3


def _mentioned_projects(question: str, names: Sequence[str]) -> List[str]:
    q = question or ""
    return [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", q, re.I)]


def route_ask(
    question: str,
    project_names: Sequence[str],
    *,
    history: Optional[Sequence[Route]] = None,
    cwd_alias: Optional[str] = None,
    llm_classify: Optional[Callable[[str, Sequence[str], Optional[Sequence[Route]]], Optional[Route]]] = None,
) -> Route:
    """Decide the scope/targets for ``question``. ``cwd_alias`` is the registered
    project the caller is standing in (if any); ``history`` is prior routes."""
    names = [n for n in project_names if n]
    if not names:
        return Route(SCOPE_CURRENT, [], "no registered projects")
    if len(names) == 1:
        return Route(SCOPE_CURRENT, names[:1], "only one project registered")

    q = question or ""
    mentioned = _mentioned_projects(q, names)

    # 1. comparative / cross-project cue -> federated. If specific projects are
    #    named ("compare alpha and gamma"), federate JUST those; otherwise all.
    if _COMPARE.search(q):
        targets = sorted(mentioned) if len(mentioned) >= 2 else names
        return Route(SCOPE_FEDERATED, targets, "comparative / cross-project question")

    # 2. follow-up with no new project named -> keep the previous route.
    if history and is_followup(q) and not mentioned:
        return replace(history[-1], reason="follow-up; kept previous route")

    # 3. an explicit project name -> that project (or federated if several named).
    if len(mentioned) == 1:
        return Route(SCOPE_CURRENT, mentioned[:1], f"names project '{mentioned[0]}'")
    if len(mentioned) > 1:
        return Route(SCOPE_FEDERATED, sorted(mentioned), "names multiple projects")

    # 4. short/local question while standing inside a project -> that project.
    if cwd_alias and is_followup(q):
        return Route(SCOPE_CURRENT, [cwd_alias], f"short question inside project '{cwd_alias}'")

    # 5. genuinely ambiguous -> optional LLM classifier.
    if llm_classify is not None:
        decided = llm_classify(question, names, history)
        if decided is not None:
            return decided

    # 6. fallback: federated across everything (sees all -> never the wrong project).
    return Route(SCOPE_FEDERATED, names, "default: federated across all projects")


# --------------------------------------------------------------------------- #
# Optional LLM classifier for the ambiguous middle (step 5 above)             #
# --------------------------------------------------------------------------- #

def llm_route(question, names, history, *, client) -> Optional[Route]:
    """Ask an LLM which project(s) a question targets. Returns None on ANY
    failure so the caller falls back to the federated default — additive, never
    a new failure mode."""
    if client is None:
        return None
    name_list = "\n".join(f"- {n}" for n in names)
    recent = ""
    if history:
        recent = "Recent routes (most recent last):\n" + "\n".join(
            f"- {r.scope} {r.aliases}" for r in list(history)[-3:]
        ) + "\n"
    system = (
        "You route a question to the right project(s) in a multi-project knowledge base. "
        "Pick scope: 'current' (one project), 'all-registered' (answer each project separately), "
        "or 'federated' (ONE merged, cross-referenced answer across several). Use 'current' when the "
        "question clearly targets a single project; 'federated' when it spans, compares, or is general. "
        'Return JSON {"scope": "current|all-registered|federated", "aliases": ["..."]} where aliases is '
        "a subset of the listed projects (empty means all)."
    )
    user = f"Projects:\n{name_list}\n\n{recent}Question: {question}"
    try:
        out = client.complete_json(system=system, user=user, schema_name="ask_route")
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    scope = out.get("scope")
    if scope not in (SCOPE_CURRENT, SCOPE_ALL, SCOPE_FEDERATED):
        return None
    raw_aliases = out.get("aliases")
    if raw_aliases is None:
        raw_aliases = []
    if not isinstance(raw_aliases, list):  # {"aliases": 1} / {"aliases": {...}} -> reject
        return None
    aliases = [a for a in raw_aliases if isinstance(a, str) and a in names]
    if scope == SCOPE_CURRENT:
        if not aliases:
            return None  # 'current' needs a concrete target; let heuristics fall back
        return Route(SCOPE_CURRENT, aliases[:1], "llm router")
    return Route(scope, aliases or list(names), "llm router")


def make_llm_classifier(client_factory):
    """Build the ``llm_classify`` callable from a 0-arg client factory. The client
    is constructed LAZILY on first ambiguous question, so heuristic-resolved
    questions pay nothing."""
    if client_factory is None:
        return None
    cache: dict = {}

    def classify(question, names, history):
        if "client" not in cache:
            try:
                cache["client"] = client_factory()
            except Exception:
                cache["client"] = None
        return llm_route(question, names, history, client=cache["client"])

    return classify
