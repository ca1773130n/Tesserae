"""Academic vetting provider: was this paper accepted, refused, or neither?

One PROVIDER over the general machinery in :mod:`tesserae.vetting`, which knows
nothing about papers. A news-verification provider (fact-check ratings), a legal
one (precedent upheld or overturned) or a standards one sits beside this file and
reuses everything in the core unchanged.

:mod:`tesserae.vetting` is domain-neutral: it knows the states and how to filter
by them, and nothing about papers. This module is the academic PROVIDER that
fills those states in, and it is the only file in the project that knows what
"desk reject" or "spotlight" means. A news-verification or legal-precedent
provider would sit beside it and reuse the same core unchanged.

Stdlib ``urllib`` only, following
:class:`~tesserae.llm_json.OpenAIAPIJsonClient`. Nothing here is imported unless
a caller asks for it, so the base install carries no new dependency and no
network capability it did not already have.

## Which provider knows what, measured 2026-08-23

``OpenAlex`` — works anonymously, no key, no auth. Distinguishes a preprint from
a published article reliably, and is the default. What it CANNOT tell you is
that a paper was rejected: it records what was published, so absence from it is
absence of publication, not evidence of refusal.

``OpenReview`` — the only source that knows a submission was REJECTED, which is
the entire reason to read it beside arXiv. Its API now answers anonymous
requests with ``403 ChallengeRequiredError`` on both ``api.openreview.net`` and
``api2.openreview.net``, so it needs credentials. Without them this provider
reports UNKNOWN and says so, rather than silently degrading to "not rejected".
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from tesserae.vetting import (
    AUTHORITY_KEY,
    PENDING,
    REJECTED,
    UNVETTED,
    VETTED,
    VETTING_KEY,
)

#: This provider's own metadata keys. `vetting_state` and `vetting_authority`
#: are the GENERAL ones every provider writes; these two are academic identifiers
#: it also happens to know.
ARXIV_KEY = "arxiv_id"
OPENREVIEW_KEY = "openreview_id"

#: Venue decision strings -> core states. THIS is the academic vocabulary, and
#: it lives here rather than in `tesserae.review` because "spotlight" is a fact
#: about machine-learning conferences and not about evidence in general.
VENUE_DECISIONS = {
    "accept": VETTED, "accepted": VETTED, "poster": VETTED,
    "oral": VETTED, "spotlight": VETTED, "published": VETTED,
    "reject": REJECTED, "rejected": REJECTED, "desk_reject": REJECTED,
    "desk reject": REJECTED, "withdrawn": REJECTED,
    "under_review": PENDING, "submitted": PENDING, "active": PENDING,
}

#: OpenAlex source types that mean a venue ran review. `repository` (arXiv,
#: bioRxiv, SSRN) deliberately does not appear.
REVIEWED_SOURCE_TYPES = {"conference", "journal", "book series"}

_UA = "tesserae/0.32 (https://github.com/ca1773130n/Tesserae)"


def _get(url: str, *, timeout: float = 25.0,
         headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """GET and parse JSON, or ``None``. Never raises for a network reason."""
    head = {"User-Agent": _UA}
    head.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=head)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # pragma: no cover - best effort
            pass
        print(f"[review_lookup] HTTP {exc.code} {url.split('?')[0]}: {body}")
        return None
    except Exception as exc:  # pragma: no cover - network shapes vary
        print(f"[review_lookup] {type(exc).__name__}: {exc}")
        return None


class OpenAlexProvider:
    """Published-or-preprint, from OpenAlex. Anonymous, no key.

    **Reads every location, not just the primary one.** A paper that appeared on
    arXiv and was later accepted at a conference carries BOTH: an arXiv
    `repository` location and a published `conference` one. Reading
    `primary_location` alone reports such a paper as a preprint, which is the
    exact misclassification this filter exists to avoid — and it would hit
    precisely the papers that matter most, the ones that got in.
    """

    name = "openalex"

    def __init__(self, *, mailto: Optional[str] = None, timeout: float = 25.0) -> None:
        # OpenAlex asks for a contact in the UA for its polite pool. Optional.
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO") or ""
        self.timeout = timeout

    def _work(self, arxiv_id: str) -> Optional[Dict[str, Any]]:
        doi = f"10.48550/arxiv.{arxiv_id}"
        url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}"
        if self.mailto:
            url += f"?mailto={urllib.parse.quote(self.mailto)}"
        got = _get(url, timeout=self.timeout)
        return got if isinstance(got, dict) and got.get("id") else None

    def lookup(self, arxiv_id: str) -> Dict[str, str]:
        """Metadata to merge into a Paper node, or ``{}``.

        ``{}`` means "not found", and the caller must leave the node's status
        alone rather than writing UNVETTED: a lookup that failed and a paper
        that is genuinely unpublished are different facts.
        """
        work = self._work(arxiv_id)
        if work is None:
            return {}
        out: Dict[str, str] = {ARXIV_KEY: arxiv_id}
        for loc in [work.get("primary_location")] + list(work.get("locations") or []):
            if not isinstance(loc, dict):
                continue
            source = loc.get("source") or {}
            stype = str(source.get("type") or "").casefold()
            if stype in REVIEWED_SOURCE_TYPES and loc.get("is_published"):
                out[VETTING_KEY] = VETTED
                out[AUTHORITY_KEY] = str(source.get("display_name") or "")
                return out
        # Found, and every location is a repository: a genuine preprint.
        out[VETTING_KEY] = UNVETTED
        return out


class OpenReviewProvider:
    """Submission decisions, including REJECTION — the fact arXiv cannot carry.

    Needs credentials. Measured 2026-08-23, anonymous requests to both
    ``api.openreview.net`` and ``api2.openreview.net`` return
    ``403 ChallengeRequiredError``. Set ``OPENREVIEW_USERNAME`` and
    ``OPENREVIEW_PASSWORD``.

    Without credentials :meth:`lookup` returns ``{}`` and
    :attr:`available` is False. It does NOT fall back to "not rejected": the
    only thing this provider adds over OpenAlex is knowing about refusal, and a
    version of it that cannot see refusals while reporting success would be
    worse than not running it.
    """

    name = "openreview"
    BASE = "https://api2.openreview.net"

    def __init__(self, *, username: Optional[str] = None,
                 password: Optional[str] = None, timeout: float = 25.0) -> None:
        self.username = username or os.environ.get("OPENREVIEW_USERNAME") or ""
        self.password = password or os.environ.get("OPENREVIEW_PASSWORD") or ""
        self.timeout = timeout
        self._token: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.username and self.password)

    def _login(self) -> Optional[str]:
        if self._token or not self.available:
            return self._token
        try:
            req = urllib.request.Request(
                f"{self.BASE}/login",
                data=json.dumps({"id": self.username,
                                 "password": self.password}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": _UA},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._token = str(json.load(resp).get("token") or "") or None
        except Exception as exc:
            print(f"[review_lookup] openreview login failed: "
                  f"{type(exc).__name__}: {exc}")
            self._token = None
        return self._token

    def lookup(self, title: str) -> Dict[str, str]:
        token = self._login()
        if not token:
            return {}
        url = (f"{self.BASE}/notes?content.title="
               f"{urllib.parse.quote(title)}&limit=5")
        got = _get(url, timeout=self.timeout,
                   headers={"Authorization": f"Bearer {token}"})
        notes = (got or {}).get("notes") or [] if isinstance(got, dict) else []
        for note in notes:
            content = note.get("content") or {}
            venue = content.get("venue")
            venue = venue.get("value") if isinstance(venue, dict) else venue
            state = decision_state(str(venue or ""))
            if state is None:
                continue
            out = {VETTING_KEY: state, OPENREVIEW_KEY: str(note.get("id") or "")}
            if venue:
                out[AUTHORITY_KEY] = str(venue)
            return out
        return {}


def decision_state(venue_or_decision: str) -> Optional[str]:
    """A venue/decision string mapped to a core state, or ``None``.

    ``None`` rather than UNKNOWN so a caller can tell "this string means nothing
    to me" from "this source says the paper is unreviewed" — writing UNKNOWN for
    an unparsed string would overwrite a better answer another provider found.
    """
    text = str(venue_or_decision or "").casefold()
    if not text:
        return None
    for needle, state in VENUE_DECISIONS.items():
        if needle in text:
            return state
    return None


def annotate(node: Any, *, providers: Optional[list] = None) -> Dict[str, str]:
    """Merge review metadata onto one node in place. Returns what was written.

    Providers are consulted in order and the FIRST that answers wins, so put the
    one that can see rejections first. A provider returning ``{}`` is skipped
    entirely — it did not fail to find a review, it failed to find the paper,
    and those must not be recorded the same way.
    """
    meta = getattr(node, "metadata", None)
    if meta is None:
        return {}
    arxiv_id = str(meta.get(ARXIV_KEY, "") or "")
    title = str(getattr(node, "name", "") or "")
    for provider in (providers if providers is not None else [OpenAlexProvider()]):
        if provider.name == "openreview":
            found = provider.lookup(title) if title else {}
        else:
            found = provider.lookup(arxiv_id) if arxiv_id else {}
        if found:
            meta.update(found)
            return found
    return {}


__all__ = [
    "REVIEWED_SOURCE_TYPES", "VENUE_DECISIONS", "OpenAlexProvider",
    "OpenReviewProvider", "annotate", "decision_state",
]
