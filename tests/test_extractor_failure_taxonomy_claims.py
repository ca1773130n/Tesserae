"""The CLI's selective-router comment must describe the taxonomy that exists.

``_build_doc_extractor`` wraps the LLM extractor in the selective router and
explains, in a comment, which failures that router absorbs. The comment named a
single cause — "None/invalid generation -> GraphJSONValidationError" — after the
taxonomy had already been split into three siblings. That is not cosmetic: the
whole point of the split is that the three demand OPPOSITE operator responses
(wait out a capacity window / raise the timeout / fix the prompt), and reading
them as one is what made a provider outage look like 8x worse model schema
compliance for three compiles.

Text assertion plus the structural fact it depends on, in the style of
``tests/test_docs_install_and_detach_claims.py``: the defect was a false claim
in prose, so prose is where it can regress — but the claim is only *true*
because the three classes are siblings, so pin that too.
"""

from __future__ import annotations

import inspect

import pytest


CAUSES = (
    "ProviderUnavailableError",
    "ProviderAuthError",
    "ExtractionTimeoutError",
    "GraphJSONValidationError",
)


def test_router_comment_names_every_failure_it_absorbs() -> None:
    from tesserae.cli import _build_doc_extractor

    source = inspect.getsource(_build_doc_extractor)
    router_comment = source[: source.index("det = ResearchGraphExtractor()")]
    for cause in CAUSES:
        assert cause in router_comment, (
            f"the selective-router comment does not mention {cause}; an operator "
            "reading it will misdiagnose that failure"
        )


@pytest.mark.parametrize("name", CAUSES)
def test_the_causes_are_siblings_not_a_hierarchy(name: str) -> None:
    """What makes the comment's "siblings, NOT a hierarchy" wording true.

    If any of these ever becomes a subclass of another, ``except`` ladders and
    the reported class name silently collapse again — and the comment above
    becomes a false claim a second time.
    """
    from tesserae import llm_extractor

    cls = getattr(llm_extractor, name)
    others = [getattr(llm_extractor, other) for other in CAUSES if other != name]
    for other in others:
        assert not issubclass(cls, other), f"{name} must not subclass {other.__name__}"
