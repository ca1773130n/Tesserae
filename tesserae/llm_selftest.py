"""Does the configured LLM backend actually answer? — the `tesserae test` verb.

Four steps, in the order a real compile depends on them:

1. **resolve** the settings the way every caller does
   (:func:`tesserae.llm_json.resolve_llm_client_settings` against the project's
   own ``config.json``), and report which layer won each knob.
2. **build** the client the same way ``compile`` and ``ask`` build it — through
   :func:`tesserae.llm_json.build_default_json_client` with the whole resolved
   dict, never a hand-picked subset. Rebuilding from a subset is how a probe
   ends up testing a different client than the run it was meant to vouch for.
3. **json** — one real ``complete_json`` round trip. This is what extraction
   uses, and it is the call that fails when a login expired.
4. **prose** — one real ``complete_text`` round trip. ``ask`` and the synthesis
   passes go through this method, and a backend can serve one and not the other
   (a gateway that rejects the JSON system prompt, a model with no tool mode).

Nothing here is cached: a cached "yes" to "is the backend answering right now"
is a wrong yes. Nothing here writes to the project either, so the verb is safe
to run against a project that is mid-compile.

Separate from ``tesserae doctor`` on purpose. Doctor is read-only and must never
spend an LLM call, so its ``llm_login`` check can only report that a config
directory exists. This module is the other half: it spends exactly two calls and
reports what actually happened.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The prose probe's system prompt. Self-identifying because the harness session
#: monitor captures every CLI call Tesserae makes: the string is registered in
#: ``harness_sessions._TESSERAE_PROMPT_SIGNATURES`` so a self-test never lands in
#: the knowledge base as if it were the user's own work.
PROSE_SYSTEM = "You are a Tesserae backend self-test. Answer in one short line."
PROSE_USER = "Reply with exactly: backend ok"

#: The JSON probe reuses `config status --ping`'s prompt verbatim — same reason,
#: and it is already a registered signature.
JSON_SYSTEM = "You are a Tesserae liveness probe. Return JSON only."
JSON_USER = 'Return {"ok": true} exactly.'


def _step(name: str, ok: bool, detail: str, seconds: float = 0.0, **extra: Any) -> Dict[str, Any]:
    step = {"step": name, "ok": ok, "detail": detail, "seconds": round(seconds, 3)}
    step.update(extra)
    return step


def _project_config(project_root: str | Path) -> dict:
    """The project's ``config.json``, or ``{}`` — never raises.

    A missing or unreadable project config is not a failure of this command:
    the machine-wide config plus the environment is a perfectly valid setup, and
    the resolver treats it that way too.
    """
    import json

    try:
        path = Path(project_root) / ".tesserae" / "config.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt config is reported by doctor, not here
        return {}
    return {}


#: Which backend class serves which ``llm_provider``. Used to tell "the
#: provider you configured answered" apart from "something else answered for
#: it", which the old pass/fail verdict could not express: with
#: ``llm_provider: claude`` out of quota and codex picking up the call, this
#: command printed a green "Backend is answering" over a dead Claude account.
_PROVIDER_BACKENDS = {
    "claude": ("ClaudeCLIJsonClient",),
    "codex": ("CodexCLIJsonClient",),
    "anthropic": ("AnthropicLLMJsonClient",),
    "custom": ("AnthropicLLMJsonClient", "OpenAICompatibleJsonClient"),
    "openai": ("OpenAICompatibleJsonClient",),
}

#: ``outcome`` → the one-line remedy that outcome actually earns. Keyed on the
#: rotation's own verdict rather than on parsed error text, so a phrasing change
#: upstream cannot turn a quota window into a login prompt.
_OUTCOME_REMEDY = {
    "not_logged_in": "CLAUDE_CONFIG_DIR={dir} claude /login",
    "quota": "wait for the window to reset, or add another logged-in account",
    "timeout": "raise TESSERAE_EXTRACT_TIMEOUT, or check this host can reach the provider",
    "binary_missing": "install the CLI and put it on PATH",
    "empty": "the CLI exited 0 with no output — re-run; if it persists, `tesserae doctor`",
    "error": "read the error above; `tesserae config status` shows where each setting came from",
}


def _collect_accounts(client: Any) -> List[Dict[str, Any]]:
    """Per-account rows from the rotation that just ran, newest call only.

    Every CLI client records one :attr:`last_attempts` row per config dir it
    spawned — which account, what happened, the CLI's own words. Reading them
    back costs nothing (the calls are already paid for) and is the difference
    between "backend not usable" and "personal1 is out of quota until Sep 11,
    personal2 answered".
    """
    rows: List[Dict[str, Any]] = []
    for sub in getattr(client, "clients", None) or [client]:
        for attempt in getattr(sub, "last_attempts", None) or []:
            rows.append({"backend": type(sub).__name__, **attempt})
    return rows


def _missing_dirs(settings: dict) -> List[str]:
    """Configured account dirs that do not exist on THIS machine.

    An absolute path copied between machines is the quiet version of this
    failure: the CLI is handed a config dir that isn't there, answers "Not
    logged in", and the user — logged in on both accounts — is told to log in.
    """
    dirs = list(settings.get("claude_config_dirs") or []) + list(
        settings.get("codex_homes") or []
    )
    return [d for d in dirs if not Path(d).is_dir()]


def _credential_kind(settings: dict) -> str:
    if settings.get("auth_token"):
        return "auth_token (Authorization: Bearer)"
    if settings.get("api_key"):
        return "api_key"
    return "none (the CLI's own login)"


def run_llm_selftest(
    project_root: str | Path = ".",
    *,
    provider: Optional[str] = None,
    timeout: Optional[int] = None,
    prose: bool = True,
) -> Dict[str, Any]:
    """Resolve, build, and exercise the configured LLM backend.

    Returns ``{"ok": bool, "provider": str, "client": str|None, "settings": {...},
    "steps": [...]}``. Never raises: a backend that explodes is the answer, so
    every failure is captured as a step with ``ok: False`` and the backend's own
    error text.

    ``prose=False`` skips the ``complete_text`` round trip (one call instead of
    two). ``provider`` overrides the resolved provider for this run only, which
    is how you test a backend you have not committed to yet.
    """
    from .llm_json import (
        LLMProviderConfigError,
        build_default_json_client,
        resolve_llm_client_settings,
    )

    steps: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {"project_root": str(Path(project_root).resolve()), "steps": steps}

    # ---- 1. resolve -------------------------------------------------------
    started = time.monotonic()
    try:
        settings = resolve_llm_client_settings(_project_config(project_root))
    except LLMProviderConfigError as exc:
        # A typo'd llm_provider is a configuration answer, not a crash.
        steps.append(_step("resolve", False, str(exc), time.monotonic() - started))
        out.update(ok=False, provider=None, client=None, settings={})
        return out
    resolved_provider = (provider or settings.get("provider") or "claude").strip().lower()
    sources = settings.get("sources") or {}
    # ``--provider`` outranks every configured layer for this run, so reporting
    # the resolver's source alongside it was simply false: `tesserae test
    # --provider codex` on a claude-configured machine printed
    # "provider=codex [~/.tesserae/config.json]", crediting a file that says
    # something else. The point of the label is to tell you where the value in
    # front of you came from.
    provider_source = "--provider (command line)" if provider else sources.get("provider", "default")
    summary = {
        "provider": resolved_provider,
        "provider_source": provider_source,
        "model": settings.get("model"),
        "claude_config_dirs": settings.get("claude_config_dirs"),
        "claude_config_dirs_source": sources.get("claude_config_dirs", "default"),
        "codex_homes": settings.get("codex_homes"),
        "codex_homes_source": sources.get("codex_homes", "default"),
        "base_url": settings.get("base_url"),
        "base_url_source": sources.get("base_url", "default"),
        "api_style": settings.get("api_style") or "anthropic",
        # The credential KIND, never the credential.
        "credential": _credential_kind(settings),
    }
    out["settings"] = summary
    out["provider"] = resolved_provider
    steps.append(
        _step(
            "resolve",
            True,
            f"provider={resolved_provider} [{summary['provider_source']}]",
            time.monotonic() - started,
        )
    )

    # ---- 2. build ---------------------------------------------------------
    started = time.monotonic()
    try:
        client = build_default_json_client(
            provider=resolved_provider,
            claude_config_dirs=settings.get("claude_config_dirs"),
            codex_homes=settings.get("codex_homes"),
            settings=settings,
            **({} if timeout is None else {"timeout": timeout}),
        )
    except LLMProviderConfigError as exc:
        steps.append(_step("build", False, str(exc), time.monotonic() - started))
        out.update(ok=False, client=None)
        return out
    if client is None:
        steps.append(
            _step(
                "build",
                False,
                "no backend could be built — the CLI is missing from PATH, or no "
                "account/credential is configured for this provider",
                time.monotonic() - started,
            )
        )
        out.update(ok=False, client=None)
        return out
    name = type(client).__name__
    identity = getattr(client, "identity", None)
    out["client"] = name
    steps.append(
        _step(
            "build",
            True,
            f"{name}" + (f" ({identity})" if identity else ""),
            time.monotonic() - started,
            client=name,
        )
    )

    # ---- 3. json round trip ----------------------------------------------
    started = time.monotonic()
    try:
        payload = client.complete_json(
            system=JSON_SYSTEM,
            user=JSON_USER,
            schema_name="probe",
            cache_key=None,  # never cached: see the module docstring
        )
        elapsed = time.monotonic() - started
        if isinstance(payload, dict) and payload:
            steps.append(_step("json", True, f"answered {payload}", elapsed))
        else:
            steps.append(
                _step(
                    "json",
                    False,
                    f"backend returned no usable JSON ({payload!r}) — it is reachable "
                    "but not answering in the shape extraction needs",
                    elapsed,
                )
            )
    except Exception as exc:  # noqa: BLE001 — the backend's own error IS the result
        steps.append(
            _step("json", False, f"{type(exc).__name__}: {str(exc)[:300]}", time.monotonic() - started)
        )

    # ---- 4. prose round trip ---------------------------------------------
    if prose:
        started = time.monotonic()
        try:
            text = client.complete_text(system=PROSE_SYSTEM, user=PROSE_USER)
            elapsed = time.monotonic() - started
            cleaned = (text or "").strip()
            if cleaned:
                steps.append(_step("prose", True, f"answered {cleaned[:80]!r}", elapsed))
            else:
                steps.append(
                    _step(
                        "prose",
                        False,
                        "backend returned empty prose — `tesserae ask` would answer nothing",
                        elapsed,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            steps.append(
                _step("prose", False, f"{type(exc).__name__}: {str(exc)[:300]}", time.monotonic() - started)
            )

    # ---- 5. who actually answered ----------------------------------------
    # Free: the rotation already recorded every account it spawned. Without
    # this the command could only report that SOMETHING answered, which is how
    # a green "Backend is answering" came to sit directly above a Claude
    # account that had been out of quota for three days.
    accounts = _collect_accounts(client)
    out["accounts"] = accounts
    answered_by = next(
        (row["backend"] for row in reversed(accounts) if row.get("outcome") == "ok"),
        None,
    )
    out["answered_by"] = answered_by
    expected = _PROVIDER_BACKENDS.get(resolved_provider, ())
    out["ok"] = all(step["ok"] for step in steps)
    # "Answering, but not through the backend you configured." Not a failure —
    # compile and ask really will keep working — but the configured provider is
    # dead and saying so is the whole point of the command.
    out["degraded"] = bool(
        out["ok"] and answered_by and expected and answered_by not in expected
    )
    out["missing_dirs"] = _missing_dirs(settings)
    return out


def render_selftest(result: Dict[str, Any]) -> str:
    """Human-readable rendering of :func:`run_llm_selftest`."""
    settings = result.get("settings") or {}
    lines: List[str] = []
    lines.append(f"tesserae test — LLM backend ({result.get('project_root', '.')})")
    lines.append("")
    if settings:
        lines.append(f"  provider   : {settings.get('provider')}   [{settings.get('provider_source')}]")
        if settings.get("model"):
            lines.append(f"  model      : {settings['model']}")
        if settings.get("claude_config_dirs"):
            lines.append(
                f"  claude_dirs: {settings['claude_config_dirs']}   "
                f"[{settings.get('claude_config_dirs_source')}]"
            )
        if settings.get("codex_homes"):
            lines.append(
                f"  codex_homes: {settings['codex_homes']}   "
                f"[{settings.get('codex_homes_source')}]"
            )
        if settings.get("base_url"):
            lines.append(
                f"  base_url   : {settings['base_url']}   [{settings.get('base_url_source')}]"
                f"  (api_style: {settings.get('api_style')})"
            )
        lines.append(f"  credential : {settings.get('credential')}")
        lines.append("")
    for step in result.get("steps") or []:
        glyph = "✓" if step.get("ok") else "✗"
        seconds = step.get("seconds") or 0
        timing = f"  ({seconds:.2f}s)" if seconds else ""
        lines.append(f"  [{glyph}] {step['step']:<8} {step['detail']}{timing}")

    accounts = result.get("accounts") or []
    if accounts:
        lines.append("")
        lines.append("  accounts tried (in rotation order):")
        for row in accounts:
            ok = row.get("outcome") == "ok"
            glyph = "✓" if ok else "✗"
            detail = "answered" if ok else row.get("outcome", "?")
            error = (row.get("error") or "").splitlines()
            if error and not ok:
                detail = f"{detail}: {error[0][:120]}"
            lines.append(f"    [{glyph}] {row.get('config_dir', '?')}   {detail}")

    missing = result.get("missing_dirs") or []
    if missing:
        lines.append("")
        lines.append(
            "  configured account dirs that do not exist on THIS machine: "
            + ", ".join(missing)
        )
        lines.append(
            "    A dir that isn't there makes the CLI answer `Not logged in`, which is "
            "what sends an already-logged-in user to `/login`. Absolute paths do not "
            "travel between machines — set llm_claude_config_dirs per machine, or "
            "delete the key and let Tesserae discover the accounts."
        )

    # One remedy per DISTINCT failure, so a five-account rotation does not print
    # five copies of the same line.
    remedies: List[str] = []
    for row in accounts:
        outcome = row.get("outcome")
        if outcome in (None, "ok"):
            continue
        remedy = _OUTCOME_REMEDY.get(outcome)
        if not remedy:
            continue
        if outcome == "not_logged_in" and "Codex" in str(row.get("backend")):
            remedy = "CODEX_HOME={dir} codex login"
        text = remedy.format(dir=row.get("config_dir", "<dir>"))
        if text not in remedies:
            remedies.append(text)
    if remedies:
        lines.append("")
        lines.append("  to revive the accounts that refused:")
        for remedy in remedies:
            lines.append(f"    - {remedy}")

    lines.append("")
    if result.get("degraded"):
        lines.append(
            f"Backend is answering, but NOT through the provider you configured "
            f"({settings.get('provider')}): {result.get('answered_by')} took the call. "
            "Compile and ask keep working through that fallback — the accounts above "
            "say why the configured one refused."
        )
    elif result.get("ok"):
        lines.append("Backend is answering. Compile, ask and the daemon will use exactly this configuration.")
    else:
        lines.append(
            "Backend is NOT usable. Fix the failing step above; "
            "`tesserae config status` shows where each setting came from, "
            "and `tesserae config llm --help` changes them."
        )
    return "\n".join(lines) + "\n"
