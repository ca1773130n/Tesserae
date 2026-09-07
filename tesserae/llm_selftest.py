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
    summary = {
        "provider": resolved_provider,
        "provider_source": sources.get("provider", "default"),
        "model": settings.get("model"),
        "claude_config_dirs": settings.get("claude_config_dirs"),
        "claude_config_dirs_source": sources.get("claude_config_dirs", "default"),
        "codex_homes": settings.get("codex_homes"),
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

    out["ok"] = all(step["ok"] for step in steps)
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
            lines.append(f"  codex_homes: {settings['codex_homes']}")
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
    lines.append("")
    if result.get("ok"):
        lines.append("Backend is answering. Compile, ask and the daemon will use exactly this configuration.")
    else:
        lines.append(
            "Backend is NOT usable. Fix the failing step above; "
            "`tesserae config status` shows where each setting came from, "
            "and `tesserae config llm --help` changes them."
        )
    return "\n".join(lines) + "\n"
