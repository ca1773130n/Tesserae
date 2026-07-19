"""Role-grade agent identity and the per-project org registry.

Spec: docs/superpowers/specs/2026-07-19-layered-agent-kg.md §3. ``HarnessSession``
envelopes only carry the harness product name as ``agent_label`` ("Claude Code",
"Codex"), so naive keying collapses every session to 1-2 "agents" and no
per-agent expertise can emerge. This module derives a role-grade

    agent_key = f"{harness}:{account_slug}:{role}"

purely from the session envelope plus an optional declarative registry at
``.tesserae/agents/registry.json``:

- ``account_slug`` is path-INDEPENDENT — the harness account email parsed from
  the account marker *filename* in the config root when available, else the
  config root's basename. Never an absolute path: renaming ``$HOME`` must not
  mint a new agent.
- ``role`` resolves in priority order: (1) subagent descriptor type from
  ``metadata['subagents']``, (2) registry match rules, (3) ``"default"``.

Everything here is deterministic and structural — no LLM calls, no wall clock —
so agent identity is safe inside the CMP-03 byte-idempotent compile.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from .harness_sessions import HarnessSession

# The implicit root of every org tree. Any observed agent without an explicit
# registry parent reports here, so a user with no registry still gets a working
# two-level org (today's global view is semantically "root's view").
ORG_ROOT = "org:root"

# Tier-3 fallback role when neither a subagent descriptor nor a registry match
# rule names one.
DEFAULT_ROLE = "default"

# Registry location relative to the project root — PER-PROJECT, unlike the
# user-level ProjectRegistry default in mcp_server.py.
AGENT_REGISTRY_RELPATH = Path(".tesserae") / "agents" / "registry.json"

# Recognized fields in a registry match rule. Every field present in a rule
# must match the session envelope for the rule to fire; unknown fields are a
# load-time error (fail loud, never a silently dead rule).
MATCH_RULE_FIELDS = frozenset({"harness", "cwd", "slash_command", "label", "subagent"})

# Account marker files written by the harnesses into their config roots, e.g.
# ~/.claude/claude-<email>.json and ~/.codex/codex-<email>-<plan>.json. Only the
# FILENAME is parsed — the files also hold OAuth/API tokens and are never read.
_ACCOUNT_FILE_PREFIXES = ("claude-", "codex-")

# Leading email inside a marker-file stem (after the harness prefix). Anchored
# match so a codex "-<plan>" suffix after the domain is left behind.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _sanitize_key_component(raw: str) -> str:
    """Sanitize one colon-separated agent_key component.

    Mirrors mcp_server._sanitize_project_name but additionally keeps ``@`` and
    ``.`` so email-derived account slugs stay readable. Lowercased because
    stable_id lowercases its seed — key identity is case-insensitive by
    construction, so normalize here to avoid surprise collisions.
    """
    cleaned = "".join(c if c.isalnum() or c in "-_@." else "_" for c in raw.strip().lower())
    cleaned = cleaned.strip("_-.")
    return cleaned or DEFAULT_ROLE


def sanitize_agent_key(raw: str) -> str:
    """Sanitize a full ``harness:account:role`` key, component by component."""
    parts = [p for p in str(raw).split(":") if p.strip()]
    if not parts:
        return DEFAULT_ROLE
    return ":".join(_sanitize_key_component(part) for part in parts)


def account_slug_for_root(config_root: object) -> str:
    """Path-independent account slug for a harness config root.

    Preference order:
    1. Email parsed from an account marker FILENAME in the root
       (``claude-<email>.json`` / ``codex-<email>-<plan>.json``) — survives any
       rename of ``$HOME`` or the root directory itself.
    2. Basename of the (symlink-resolved) config root — ``~/.claude`` is
       commonly a symlink to the active account directory, so resolve first
       lest symlink and real dir mint two accounts.
    3. ``"unknown"`` when there is no usable root at all.

    Never an absolute path, and never the marker file *contents* (they carry
    OAuth/refresh tokens).
    """
    if not config_root or not isinstance(config_root, (str, Path)):
        return "unknown"
    root = Path(config_root).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    for prefix in _ACCOUNT_FILE_PREFIXES:
        try:
            marker_paths = sorted(root.glob(prefix + "*@*.json"))
        except OSError:
            marker_paths = []
        for marker in marker_paths:
            match = _EMAIL_RE.match(marker.stem[len(prefix):])
            if match:
                return _sanitize_key_component(match.group(0))
    basename = root.name.lstrip(".")
    return _sanitize_key_component(basename) if basename.strip() else "unknown"


def build_agent_key(harness: str, account_slug: str, role: str) -> str:
    """Compose the canonical role-grade key from its three components."""
    return ":".join(
        _sanitize_key_component(part) for part in (harness or "unknown", account_slug, role or DEFAULT_ROLE)
    )


def resolve_agent_key(
    session: HarnessSession,
    registry: Optional["AgentRegistry"] = None,
    subagent: Optional[Mapping[str, object]] = None,
) -> str:
    """Resolve the role-grade agent_key for a session envelope.

    Deterministic and total: a pure function of the envelope (plus registry
    file contents), returning a key for every input. Pass ``subagent`` (an
    entry of ``metadata['subagents']``) to resolve that subagent's identity
    instead of the parent session's own agent.

    Priority order (spec §3.1): subagent descriptor type, then registry match
    rules, then ``"default"``. The result is always alias-resolved so old
    envelope keys land on the canonical agent.
    """
    harness = session.harness or "unknown"
    metadata = session.metadata or {}
    account = account_slug_for_root(metadata.get("config_root"))

    # Tier 1 — subagent descriptor: the transcript-captured type (reviewer,
    # planner, ...) is minted as a first-class role.
    if subagent is not None:
        descriptor_type = str(subagent.get("type") or "").strip()
        if descriptor_type:
            key = build_agent_key(harness, account, descriptor_type)
            return registry.resolve_alias(key) if registry is not None else key

    # Tier 2 — registry match rules map the envelope onto a declared agent.
    if registry is not None:
        matched = registry.match_session(session, subagent=subagent)
        if matched is not None:
            return registry.resolve_alias(matched)

    # Tier 3 — fallback role.
    key = build_agent_key(harness, account, DEFAULT_ROLE)
    return registry.resolve_alias(key) if registry is not None else key


def session_agent_keys(
    session: HarnessSession, registry: Optional["AgentRegistry"] = None
) -> List[str]:
    """All distinct agent keys observed on one session (parent + subagents), sorted."""
    keys = {resolve_agent_key(session, registry)}
    subagents = (session.metadata or {}).get("subagents")
    if isinstance(subagents, list):
        for descriptor in subagents:
            if isinstance(descriptor, Mapping):
                keys.add(resolve_agent_key(session, registry, subagent=descriptor))
    return sorted(keys)


def observed_agent_keys(
    sessions: Iterable[HarnessSession], registry: Optional["AgentRegistry"] = None
) -> List[str]:
    """Distinct agent keys across a session corpus, sorted — the basis for
    ``tesserae agents init``'s proposed registry and ``agents list``."""
    keys: set[str] = set()
    for session in sessions:
        keys.update(session_agent_keys(session, registry))
    return sorted(keys)


class AgentRegistry:
    """File-backed org registry of declared logical agents.

    ProjectRegistry-shaped (duck-types :meth:`list_projects` so
    ``load_federated_graph`` consumes it unmodified) with the same atomic
    tmp-rename save, but PER-PROJECT: the path is always explicit, rooted at
    ``.tesserae/agents/registry.json`` — there is no user-level default.

    Store shape (spec §3.2)::

        {"version": 1,
         "agents": {"claude-code:me:reviewer": {
             "label": "Code reviewer",
             "parent": "org:root",
             "aliases": ["claude-code:old-account:reviewer"],
             "match": [{"harness": "claude-code", "subagent": "reviewer"}]}}}

    Load fails loud on corrupt JSON, unsanitized keys, unknown parent
    references, and alias collisions — never a silently wrong org chart.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Parsed-registry cache. Every resolve/match/parent helper funnels
        # through ``load()``, and one AgentRegistry instance is constructed
        # per compile / CLI command — caching the first validated parse means
        # a single compile resolves EVERY session against exactly one registry
        # state (a registry edit landing mid-compile can no longer mix two
        # versions into one artifact) and does one disk read instead of
        # O(sessions x agents). ``save()`` refreshes it; callers always get
        # independent copies so a mutate-then-failed-save never poisons it.
        self._cache: Optional[Dict[str, object]] = None

    @classmethod
    def for_project(cls, project_root: str | Path) -> "AgentRegistry":
        return cls(Path(project_root) / AGENT_REGISTRY_RELPATH)

    def load(self) -> Dict[str, object]:
        if self._cache is not None:
            return copy.deepcopy(self._cache)
        if not self.path.exists():
            data: Dict[str, object] = {"version": 1, "agents": {}}
            self._cache = copy.deepcopy(data)
            return data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt agent registry at {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Corrupt agent registry at {self.path}: not a JSON object")
        data.setdefault("version", 1)
        data.setdefault("agents", {})
        self._validate(data)
        self._cache = copy.deepcopy(data)
        return data

    def save(self, data: Dict[str, object]) -> None:
        self._validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # sort_keys: the registry file is a determinism-bearing artifact, so
        # the same logical content must serialize to the same bytes no matter
        # which mutation path (init / register / rename / set-parent) last
        # touched which entry — insertion order must never leak into the file.
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.rename(self.path)
        self._cache = copy.deepcopy(data)

    def _validate(self, data: Dict[str, object]) -> None:
        agents = data.get("agents")
        if not isinstance(agents, dict):
            raise ValueError(f"Agent registry {self.path}: 'agents' must be an object")
        seen_aliases: Dict[str, str] = {}
        for key, entry in agents.items():
            if not isinstance(entry, dict):
                raise ValueError(f"Agent registry {self.path}: entry for {key!r} must be an object")
            if key == ORG_ROOT:
                raise ValueError(
                    f"Agent registry {self.path}: {ORG_ROOT!r} is the implicit root and may not be declared"
                )
            if key != sanitize_agent_key(key):
                raise ValueError(
                    f"Agent registry {self.path}: key {key!r} is not sanitized "
                    f"(expected {sanitize_agent_key(key)!r})"
                )
            parent = entry.get("parent")
            if parent is not None and parent != ORG_ROOT and parent not in agents:
                raise ValueError(
                    f"Agent registry {self.path}: {key!r} references unknown parent {parent!r}"
                )
            for alias in entry.get("aliases") or []:
                alias = str(alias)
                if alias in agents:
                    raise ValueError(
                        f"Agent registry {self.path}: alias {alias!r} shadows a canonical agent key"
                    )
                if alias in seen_aliases and seen_aliases[alias] != key:
                    raise ValueError(
                        f"Agent registry {self.path}: alias {alias!r} claimed by both "
                        f"{seen_aliases[alias]!r} and {key!r}"
                    )
                seen_aliases[alias] = key
            for rule in entry.get("match") or []:
                if not isinstance(rule, dict) or not rule:
                    raise ValueError(
                        f"Agent registry {self.path}: {key!r} has an empty or non-object match rule"
                    )
                unknown = set(rule) - MATCH_RULE_FIELDS
                if unknown:
                    raise ValueError(
                        f"Agent registry {self.path}: {key!r} match rule has unknown fields {sorted(unknown)}"
                    )
        # Every parent chain must terminate at the implicit org:root. A
        # self-parent or a hand-edited cycle would silently detach a subtree
        # from the org chart (and mint reports_to loops downstream) — the
        # exact "silently wrong org chart" failure spec §3.2 says must fail
        # loud. Runs after the per-entry loop so every parent reference is
        # already known to be declared.
        for key, entry in agents.items():
            chain = [key]
            parent = entry.get("parent")
            while parent and parent != ORG_ROOT:
                if parent in chain:
                    cycle = " -> ".join(chain + [str(parent)])
                    raise ValueError(
                        f"Agent registry {self.path}: parent cycle {cycle}"
                    )
                chain.append(str(parent))
                parent_entry = agents.get(parent)
                parent = parent_entry.get("parent") if isinstance(parent_entry, dict) else None

    # ---------------- ProjectRegistry duck-type ----------------

    def list_projects(self) -> Dict[str, object]:
        """Duck-type of ProjectRegistry.list_projects: sorted list-of-dicts
        envelope so federation consumers work unmodified."""
        data = self.load()
        return {
            "projects": [
                {"name": name, **entry}
                for name, entry in sorted(data["agents"].items())
            ],
        }

    def list_agents(self) -> Dict[str, object]:
        return {"agents": self.list_projects()["projects"]}

    # ---------------- identity resolution helpers ----------------

    def resolve_alias(self, key: str) -> str:
        """Map an envelope-derived key onto its canonical agent, if aliased.

        Aliases merge keys across harness/account changes (one human/role
        through two harnesses, or after an account switch, stays one logical
        agent). Unaliased keys pass through unchanged.
        """
        data = self.load()
        for canonical, entry in sorted(data["agents"].items()):
            if key in (entry.get("aliases") or []):
                return canonical
        return key

    def effective_parent(self, key: str) -> str:
        """Parent of an agent — explicit registry parent, else the implicit
        ``org:root`` (every observed agent reports to root by default)."""
        data = self.load()
        entry = data["agents"].get(key)
        if isinstance(entry, dict) and entry.get("parent"):
            return str(entry["parent"])
        return ORG_ROOT

    def match_session(
        self,
        session: HarnessSession,
        subagent: Optional[Mapping[str, object]] = None,
    ) -> Optional[str]:
        """First declared agent whose match rules fit the envelope, or None.

        Deterministic: agents are scanned in sorted-key order, rules in their
        declared order, and every field present in a rule must match.
        """
        data = self.load()
        for key, entry in sorted(data["agents"].items()):
            for rule in entry.get("match") or []:
                if self._rule_matches(rule, session, subagent):
                    return key
        return None

    @staticmethod
    def _rule_matches(
        rule: Mapping[str, object],
        session: HarnessSession,
        subagent: Optional[Mapping[str, object]],
    ) -> bool:
        if "harness" in rule and str(rule["harness"]) != (session.harness or ""):
            return False
        if "cwd" in rule and not fnmatch.fnmatch(session.project_root or "", str(rule["cwd"])):
            return False
        if "label" in rule and not fnmatch.fnmatch(session.agent_label or "", str(rule["label"])):
            return False
        if "slash_command" in rule:
            wanted = str(rule["slash_command"])
            if not any(
                cmd == wanted or cmd.startswith(wanted + " ")
                for cmd in (session.commands_run or [])
            ):
                return False
        if "subagent" in rule:
            # Fires only for subagent envelopes; typed descriptors resolve at
            # tier 1 before rules run, so this catches untyped descriptors by
            # type-or-title pattern.
            if subagent is None:
                return False
            descriptor_text = str(subagent.get("type") or subagent.get("title") or "")
            if not fnmatch.fnmatch(descriptor_text, str(rule["subagent"])):
                return False
        return True

    # ---------------- registry mutation (agents CLI substrate) ----------------

    def register(
        self,
        key: str,
        label: str = "",
        parent: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        match: Optional[List[Dict[str, object]]] = None,
    ) -> Dict[str, object]:
        """Declare (or redeclare) an agent. Key is sanitized; parent must be
        ``org:root`` or an already/simultaneously declared agent."""
        canonical = sanitize_agent_key(key)
        data = self.load()
        entry: Dict[str, object] = {
            "label": label or canonical,
            "parent": parent or ORG_ROOT,
            "aliases": [str(a) for a in (aliases or [])],
            "match": [dict(r) for r in (match or [])],
        }
        data["agents"][canonical] = entry
        self.save(data)
        return {"name": canonical, **entry}

    def set_parent(self, child: str, parent: str) -> Dict[str, object]:
        """Reparent a declared agent — both ends validated against known keys."""
        data = self.load()
        agents = data["agents"]
        if child not in agents:
            raise ValueError(f"Unknown agent: {child}")
        if parent != ORG_ROOT and parent not in agents:
            raise ValueError(f"Unknown parent agent: {parent}")
        if parent == child:
            raise ValueError(f"Agent {child!r} cannot be its own parent")
        # Walking the new parent's ancestor chain must never reach the child,
        # or the reparent closes a cycle (a -> b -> a) that detaches both
        # agents from org:root. ``load()`` already rejected pre-existing
        # cycles, so this walk terminates.
        ancestor = parent
        while ancestor != ORG_ROOT:
            if ancestor == child:
                raise ValueError(
                    f"Reparenting {child!r} under {parent!r} would create a cycle"
                )
            ancestor_entry = agents.get(ancestor)
            ancestor = (
                str(ancestor_entry.get("parent") or ORG_ROOT)
                if isinstance(ancestor_entry, dict)
                else ORG_ROOT
            )
        agents[child]["parent"] = parent
        self.save(data)
        return {"name": child, **agents[child]}
