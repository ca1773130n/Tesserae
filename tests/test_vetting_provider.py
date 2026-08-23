"""The academic review provider — an extension over the domain-neutral core.

Offline: every case drives a stub. The live checks that this maps real papers
correctly are in the commit message, because a test that reaches OpenAlex would
fail in CI for a network reason and teach nobody anything.
"""

from __future__ import annotations

from tesserae.ingest.vetting_lookup import (
    OpenAlexProvider,
    OpenReviewProvider,
    annotate,
    decision_state,
)
from tesserae.vetting import (
    AUTHORITY_KEY,
    VETTED,
    UNVETTED,
    REJECTED,
    VETTING_KEY,
    PENDING,
    vetting_state,
)


class _Node:
    def __init__(self, name="", **metadata):
        self.name = name
        self.metadata = metadata


def test_venue_jargon_is_translated_here_not_in_core():
    assert decision_state("ICLR 2026 Poster") == VETTED
    assert decision_state("NeurIPS 2025 Spotlight") == VETTED
    assert decision_state("Submitted to ICLR 2026") == PENDING
    assert decision_state("ICLR 2026 Reject") == REJECTED
    assert decision_state("Desk Reject") == REJECTED


def test_a_string_that_means_nothing_returns_none_not_unknown():
    """None lets a second provider still answer; UNKNOWN would overwrite it."""
    assert decision_state("arXiv") is None
    assert decision_state("") is None


def test_a_published_paper_is_not_reported_as_a_preprint(monkeypatch):
    """The misclassification this provider exists to avoid.

    A paper on arXiv and later accepted carries BOTH locations. Reading
    `primary_location` alone calls it a preprint — and it would do so for
    precisely the papers that got in.
    """
    provider = OpenAlexProvider()
    monkeypatch.setattr(provider, "_work", lambda a: {
        "id": "https://openalex.org/W1",
        "primary_location": {"source": {"type": "repository",
                                        "display_name": "arXiv"},
                             "is_published": False},
        "locations": [
            {"source": {"type": "repository", "display_name": "arXiv"},
             "is_published": False},
            {"source": {"type": "conference", "display_name": "ICLR"},
             "is_published": True},
        ],
    })
    found = provider.lookup("2510.27246")
    assert found[VETTING_KEY] == VETTED
    assert found[AUTHORITY_KEY] == "ICLR", (
        "the authority that did the vetting is general metadata; a fact-check\n         publisher or a court would fill the same key"
    )


def test_a_genuine_preprint_reads_as_preprint(monkeypatch):
    provider = OpenAlexProvider()
    monkeypatch.setattr(provider, "_work", lambda a: {
        "id": "https://openalex.org/W2",
        "primary_location": {"source": {"type": "repository"},
                             "is_published": False},
        "locations": [{"source": {"type": "repository"}, "is_published": False}],
    })
    assert provider.lookup("1234.5678")[VETTING_KEY] == UNVETTED


def test_a_paper_that_was_not_found_writes_nothing(monkeypatch):
    """"Lookup failed" and "genuinely unpublished" are different facts.

    Writing UNVETTED for a paper OpenAlex simply does not have would turn a
    network miss into a claim about the paper.
    """
    provider = OpenAlexProvider()
    monkeypatch.setattr(provider, "_work", lambda a: None)
    assert provider.lookup("9999.9999") == {}

    node = _Node(name="Something", arxiv_id="9999.9999")
    assert annotate(node, providers=[provider]) == {}
    assert VETTING_KEY not in node.metadata


def test_openreview_without_credentials_reports_nothing_not_clean(monkeypatch):
    """Its ONLY added value is seeing rejections. A version that cannot see
    them while reporting success is worse than not running it."""
    monkeypatch.delenv("OPENREVIEW_USERNAME", raising=False)
    monkeypatch.delenv("OPENREVIEW_PASSWORD", raising=False)
    provider = OpenReviewProvider()
    assert provider.available is False
    assert provider.lookup("Beyond a Million Tokens") == {}


def test_annotate_takes_the_first_provider_that_answers():
    """Ordering is the policy: put the one that can see rejections first."""

    class _Stub:
        def __init__(self, name, out):
            self.name, self._out = name, out

        def lookup(self, _):
            return dict(self._out)

    node = _Node(name="A paper", arxiv_id="2510.27246")
    annotate(node, providers=[
        _Stub("openreview", {VETTING_KEY: REJECTED}),
        _Stub("openalex", {VETTING_KEY: UNVETTED}),
    ])
    assert vetting_state(node) == REJECTED, (
        "a rejection known to OpenReview must not be overwritten by OpenAlex "
        "reporting the arXiv copy as a preprint"
    )
