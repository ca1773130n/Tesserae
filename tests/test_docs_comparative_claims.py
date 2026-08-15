"""Guard on comparative performance claims the docs have no measurement for.

Tesserae shipped two claims that compared it to other systems and had nothing
behind them:

1. ``tesserae/temporal.py::render_competitive_report`` — a hardcoded markdown
   string naming MegaMem, Graphiti/Zep and Qdrant, written to
   ``.tesserae/competitive_report.md`` on *every* compile and advertised in
   ``docs/architecture.md`` as "comparison vs. MegaMem / Graphiti / others".
   It took no arguments and read nothing: every user compiling their own
   project got the same bytes, and the sidecar registry described it as
   "a compile re-renders it from the graph", which was false. Deleted.

2. ``docs/tuning.md`` — "Independent benchmarks put graphs ahead on multi-hop,
   temporal and synthesis questions, and *behind* on simple fact lookup and
   cost." No benchmark was named, linked or numbered anywhere in the file, and
   the sentence shipped in all seven translations. Rewritten as the hypothesis
   it always was.

3. ``docs/feature-map.md`` — "✅ Competitive report describing absorbed ideas
   from MegaMem, Graphiti/Zep, MCP graph servers, agentic RAG", in English and
   all seven mirrors. Deleting claim 1's producer turned this line from merely
   unmeasured into *false about the product*: a ✅ for a feature that no longer
   exists. It survived the first pass because it carries no superiority phrase
   — nothing here can infer that a report "describing absorbed ideas" was the
   competitive report — so it is pinned as a literal below instead.

The premise has since moved, and `_repo_reports_a_retrieval_metric` below
re-checks it on every run rather than trusting this docstring. A first-party
answer-quality score now exists — ``evals/qa/scorer.py`` computes exact match
and token F1 — so the sentence "nothing here measures anything" is no longer
the reason the ban holds. The reason it holds now is narrower and is the
harness's own: the scorer has been **run against no competitor**, no report is
committed, and its ``fairness_blockers()`` gate refuses to publish a comparison
until the systems' model, embedding backend, corpus, question set and answer
shape all match — which for the two systems it ships they do not.

So a doc still may not report a comparative result, and the day one legitimately
can, `test_no_first_party_doc_claims_a_measured_comparison` gets relaxed to
permit claims that **cite that benchmark and its numbers** — not reopened to
unmeasured ones. These are text assertions, not behaviour tests: the defect was
a false claim in prose, and prose is the only place it can regress.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Same set as tests/test_docs_i18n.py — kept local so this module reads alone.
LANGS = ("ko", "zh", "ja", "ru", "es", "fr", "de")

#: Directories under docs/ holding dated records rather than live guidance.
#: Release notes and handoffs describe what was true when they were written and
#: are never retro-edited; ``launch/`` and ``superpowers/`` are short-lived
#: working artifacts. Same exclusion rationale as
#: ``tests/test_docs_install_and_detach_claims.py``.
DATED_RECORD_DIRS = {"release-notes", "handoffs", "superpowers", "launch"}

#: Retrieval-quality metrics. Written as fragments joined at import so this
#: module does not match itself when it scans ``tests/``.
_METRIC_TOKENS = ("nd" + "cg", "m" + "rr", "recall@", "precision@", "hits@",
                  "mean reciprocal")

#: ``token_f1`` / "token F1" is the QA scorer's headline number, and it is what
#: `test_a_measurement_exists_and_the_ban_still_holds_anyway` now finds in
#: ``evals/qa/scorer.py``. It was written here before that scorer existed, as a
#: tripwire, so the arrival of a real measurement could not pass unnoticed.
#:
#: Bare precision/recall/F1 is deliberately NOT a token here, and the
#: distinction is the whole reason this check is narrow: ``evals/federation/
#: run_eval.py`` has scored link precision/recall/F1 against a hand-labelled
#: fixture since long before this guard existed. That measures one Tesserae
#: default (``min_cosine``) against a gold set we wrote — it produces no
#: number about any other system, so it licenses no comparative claim. What
#: this tripwire waits for is an *answer-quality* score, which is the first
#: measurement that could honestly back a sentence about a rival.
_METRIC = re.compile(
    r"|".join([*(re.escape(tok) for tok in _METRIC_TOKENS), r"token[ _-]f1"]),
    re.I,
)

#: Phrases that assert one thing performs better or worse than another.
#: Deliberately requires an explicit comparison — a bare "fast" or "efficient"
#: is marketing, not a claimed measurement, and banning it would false-positive
#: on every page in the repo.
#:
#: Written as *shapes* rather than a list of literal phrases, because a literal
#: list only ever recognises the sentence someone already wrote: "wins on",
#: "retrieves 30% more relevant facts than" and "2x faster" are the same claim
#: as "outperforms" and all three walked past the first version of this.
#:
#: Every mirror language gets its own alternation. This repo's documented
#: failure mode is a claim that is wrong in all eight locales at once (#157),
#: and an English-only matcher is blind to seven of them — including to the
#: mirrors of the very sentence retired here, which rendered "ahead on" as
#: 앞서고 / 先行し / 领先 / führend / опережают with no "than" particle in sight.
_SUPERIORITY = re.compile(
    # --- English -----------------------------------------------------------
    r"outperform\w*"
    # "more/less/fewer <up to three words> than"
    r"|\b(?:more|less|fewer)\s+(?:\w+\s+){0,3}than\b"
    # any "-er than", minus the two that are ordinary English connectives
    r"|\b(?!rather\b|other\b)\w+er\s+than\b"
    r"|\bahead\s+(?:on|of)\b|\bbehind\s+on\b"
    r"|\bbeats?\b|\bwins?\s+(?:on|against|over)\b"
    r"|\bleads?\b[^.!?\n]{0,40}?\bby\s+\d"
    r"|\bsuperior\s+to\b"
    r"|\b\d+(?:\.\d+)?\s*[x×]\s*(?:faster|slower|better|cheaper|smaller|larger|more|fewer)\b"
    r"|\bonly\s+(?:system|engine|tool|library|product|database|one)\s+that\b"
    # --- Korean ------------------------------------------------------------
    # 보다 is also the verb "to see", so it only counts next to a comparative.
    r"|보다\s*\S*(?:빠[르른릅]|빨[라랐]|우수|뛰어|앞서|능가|정확|낫|좋)"
    r"|앞서|뒤떨어|뒤처|능가|우수하"
    # --- Japanese ----------------------------------------------------------
    # より is also "from/by", so likewise it must sit beside a comparative.
    r"|より\s*\S*(?:高速|速い|優れ|上回)"
    r"|先行|上回|下回|凌駕|優位"
    # --- Chinese -----------------------------------------------------------
    r"|比[^。！？\n]{0,24}?(?:更快|更好|更强|更准确|更高效)"
    r"|领先|落后|优于|逊于|超越"
    # --- Russian -----------------------------------------------------------
    r"|опережа|отста[юё]|превосход|уступа"
    # --- Spanish -----------------------------------------------------------
    r"|\bsupera\w*|\badelante\s+en\b|\batrás\s+en\b|\baventaja"
    r"|\b(?:más|menos)\s+\w+\s+que\b|\bmejor\s+que\b|\bpeor\s+que\b"
    # --- French ------------------------------------------------------------
    r"|\bsurpasse|\bdevance|\bmène(?:nt)?\s+sur\b|\btraîne(?:nt)?\b"
    r"|\bplus\s+\w+\s+que\b|\bmeilleur\w*\s+que\b|\bpire\s+que\b"
    # --- German ------------------------------------------------------------
    r"|\bführend\b|\bübertrifft\b|\büberlegen\b|hinterherhink"
    r"|\b(?:schneller|besser|schlechter|langsamer|genauer)\s+als\b",
    re.I,
)

#: Words that dress a claim up as a measured result, in every mirror language
#: for the same reason `_SUPERIORITY` is multilingual.
_EVIDENCE = re.compile(
    r"\bbenchmark(?:s|ed|ing)?\b"
    r"|벤치마크|ベンチマーク|基准|基準"
    r"|бенчмарк|независимы\w*\s+тест"
    r"|puntos?\s+de\s+referencia|tests?\s+indépendants?",
    re.I,
)

#: A number that reads like a score rather than a count: a decimal or a
#: percentage. Bare integers are excluded on purpose — "3 backends", "0.19"
#: as a release number and "12 nodes" are everywhere, and treating them as
#: measurements is what made the first draft of `_number_comparison` fire on
#: the cognee-removal note in every quickstart mirror.
_SCORE = re.compile(r"\d+\.\d+|\d+\s*%")

#: Words that turn two numbers into a comparison *between* them. Required
#: alongside a rival name, because "Cognee was removed in 0.19. The backend was
#: demoted in 0.18" is two decimals next to a rival and is not a claim.
_VERSUS = re.compile(
    r"\bvs\.?\b|\bversus\b|\bagainst\b|\bcompared\s+(?:to|with)\b|\brelative\s+to\b"
    r"|对比|相比|に対して|と比べ|대비|와\s*비교|과\s*비교",
    re.I,
)

#: Systems Tesserae is compared against. Naming one is fine and common — the
#: docs legitimately describe a Graphiti export and a Kuzu adapter. Only a name
#: sharing a sentence with `_SUPERIORITY` is a comparative claim.
_RIVAL = re.compile(
    r"\b(?:MegaMem|Graphiti|Zep|Qdrant|Neo4j|FalkorDB|cognee|LightRAG|GraphRAG)\b",
    re.I,
)

#: Markdown line shapes that carry a claim without ever reaching a full stop.
#: The first version of this module scanned only ``[^.!?\n]*[.!?]``, so a
#: bullet or a table row with no terminal period was never looked at — and the
#: claim retired here *was* a markdown table. Re-adding it under different
#: headings would have fired nothing.
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_FENCE = re.compile(r"^\s*(?:`{3,}|~{3,})")

#: A sentence boundary. Latin terminators must be followed by whitespace so
#: that "0.82 nDCG against Graphiti's 0.61" stays in one piece — splitting on
#: every ``.`` tears a decimal in half and hides exactly the number-vs-number
#: shape `_number_comparison` exists to catch. CJK terminators need no such
#: guard and are usually not followed by a space at all.
_SENT_BREAK = re.compile(r"(?<=[.!?])(?=\s)|(?<=[。！？])")

# The exact wording retired from docs/tuning.md, kept as a literal so
# reintroducing it — or pasting it into a new page — fails loudly.
RETIRED_BENCHMARK_CLAIM = "Independent benchmarks put graphs ahead"

# The same sentence as each mirror actually rendered it. Pinned per language
# because a literal English grep is blind to a translated claim: this defect
# shipped in eight locales at once and only the English was ever read.
RETIRED_BENCHMARK_CLAIM_BY_LANG = {
    "ko": "독립적인 벤치마크",
    "zh": "独立基准",
    "ja": "独立したベンチマーク",
    "ru": "Независимые тесты показывают",
    "es": "Los puntos de referencia independientes",
    "fr": "Les tests indépendants montrent",
    "de": "Unabhängige Benchmarks zeigen",
}

# Section headings and framing from the deleted competitive report. Each one
# asserts parity-or-better against a named rival, so each is banned outright
# rather than merely required to carry a caveat.
#
# The `feature-map` entries are claim 3, and they are here rather than in
# `_comparative_claims` for a reason worth keeping: "Competitive report
# describing absorbed ideas from MegaMem, ..." names rivals but asserts no
# superiority, so no shape-based detector can tell it from honest prose. What
# made it a defect was not its wording but the fact that the feature it ticks
# ✅ was deleted in the same change. Only a literal can pin that.
RETIRED_COMPETITIVE_REPORT_CLAIMS = (
    "Competitive Hardening Report",
    "Open-source advantages absorbed",
    "Tesserae differentiators retained",
    "comparison vs. MegaMem",
    # docs/feature-map.md and its seven mirrors, as each actually rendered it.
    "Competitive report describing absorbed ideas",
    "흡수한 아이디어를 기술하는 경쟁 보고서",
    "吸收的想法的竞争报告",
    "から吸収したアイデアを記述する競合レポート",
    "Конкурентный отчёт, описывающий впитанные идеи",
    "Informe competitivo describiendo ideas absorbidas",
    "Rapport concurrentiel décrivant les idées absorbées",
    "Competitive-Report zu übernommenen Ideen",
)


def _first_party_docs() -> list[Path]:
    """Prose Tesserae asserts in its own voice.

    ``examples/`` is excluded on purpose and is the only interesting exclusion:
    ``examples/demo-corpus/`` holds verbatim arXiv abstracts used as compiler
    *input*, and one of them says Plenoxels optimize "two orders of magnitude
    faster than Neural Radiance Fields". That is a third-party paper's claim
    about third-party software, quoted as test data. Linting it would be
    editing someone else's abstract.
    """
    paths = [ROOT / "README.md", ROOT / "PLUGIN-README.md"]
    paths += [ROOT / f"README.{lang}.md" for lang in LANGS]
    paths += [
        p
        for p in sorted(DOCS.rglob("*.md"))
        if not (DATED_RECORD_DIRS & set(p.relative_to(DOCS).parts))
    ]
    return [p for p in paths if p.exists()]


def _blocks(text: str):
    """Bullets, table cells, headings and paragraphs, each yielded as one string.

    A block is the unit a claim can hide in. Prose is joined across wrapped
    lines so a sentence split by the formatter is still seen whole; bullets and
    headings stand alone; a table row is broken into cells, which is the
    tighter reading — a rival named in one column and a superiority word in
    another are usually a feature matrix, not an assertion about both.

    Fenced code is skipped: it is transcripts and CLI output, not prose
    Tesserae asserts in its own voice.
    """
    para: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            if para:
                yield " ".join(para)
                para = []
            continue
        if fenced:
            continue
        stripped = line.strip()
        if not stripped:
            if para:
                yield " ".join(para)
                para = []
            continue
        if _TABLE_ROW.match(line):
            if para:
                yield " ".join(para)
                para = []
            for cell in stripped.strip("|").split("|"):
                if cell.strip():
                    yield cell.strip()
            continue
        if _BULLET.match(line) or _HEADING.match(line):
            if para:
                yield " ".join(para)
            para = [_BULLET.sub("", _HEADING.sub("", line)).strip()]
            continue
        para.append(stripped)
    if para:
        yield " ".join(para)


def _units(text: str) -> list[str]:
    """Every span of prose small enough to read as a single assertion."""
    units: list[str] = []
    for block in _blocks(text):
        for piece in _SENT_BREAK.split(block):
            unit = " ".join(piece.split())
            if unit:
                units.append(unit)
    return units


def _number_comparison(unit: str) -> bool:
    """A rival, two scores and a word putting them against each other.

    The shape with no superiority word in it at all: "In our benchmark Tesserae
    scored 0.82 nDCG against Graphiti's 0.61" is the most specific comparative
    claim a doc can make and contains none of `_SUPERIORITY`. All three parts
    are required — two decimals beside a rival name, on their own, are a
    version note.
    """
    return (
        _RIVAL.search(unit) is not None
        and len(_SCORE.findall(unit)) >= 2
        and (_VERSUS.search(unit) or _EVIDENCE.search(unit)) is not None
    )


def _comparative_claims(text: str) -> list[str]:
    """Units asserting a measured comparison against another system.

    Unit-scoped, and requires a superiority phrase to co-occur with either a
    rival's name or an evidence word. Both halves are needed: "worse than no
    edge at all" and "sort ahead of everything else" are ordinary prose that a
    bare superiority match would flag, and "no retrieval benchmark here" is the
    honest disclaimer this change introduced. That co-occurrence rule is the
    only thing keeping the widened patterns above quiet — measured across all
    218 first-party docs, the pair fires zero times.
    """
    claims: list[str] = []
    for unit in _units(text):
        if _SUPERIORITY.search(unit) and (_RIVAL.search(unit) or _EVIDENCE.search(unit)):
            claims.append(unit)
        elif _number_comparison(unit):
            claims.append(unit)
    return claims


#: First-party code that happens to live under ``evals/``.
#:
#: ``evals/`` as a whole is gitignored because it also holds ~877MB of vendored
#: upstream clones (cognee, MegaMem) whose own harnesses do compute retrieval
#: metrics — and a metric someone else measured on their own system is not
#: evidence about this one. But ``.gitignore`` re-admits, by name, the
#: harnesses Tesserae wrote itself, and those are first-party evidence like
#: any other module. So the scan is by explicit path rather than by walking
#: ``evals/``: an rglob there would read a vendored clone as our own
#: measurement, and would walk 877MB to do it.
#:
#: ``metrics.py`` and ``qa/`` are the QA-scorer harness. They are listed
#: before they exist — a path that is not on disk yet contributes nothing and
#: costs nothing, and listing it now means the tripwire fires the moment that
#: work lands rather than silently continuing to assert "no measurement
#: exists" about a tree that has one.
FIRST_PARTY_EVAL_PATHS = ("metrics.py", "qa", "federation", "growth")


def _first_party_python() -> list[Path]:
    """Every ``.py`` file Tesserae wrote, wherever it lives."""
    paths: list[Path] = []
    for directory in ("tesserae", "scripts", "tests"):
        paths += sorted((ROOT / directory).rglob("*.py"))
    for name in FIRST_PARTY_EVAL_PATHS:
        target = ROOT / "evals" / name
        if target.is_dir():
            paths += sorted(target.rglob("*.py"))
        elif target.is_file():
            paths.append(target)
    return paths


def _repo_reports_a_retrieval_metric() -> bool:
    """True once any first-party module or script computes a retrieval metric."""
    here = Path(__file__).resolve()
    return any(
        path.resolve() != here and _METRIC.search(path.read_text(encoding="utf-8"))
        for path in _first_party_python()
    )


def test_a_measurement_exists_and_the_ban_still_holds_anyway() -> None:
    """The premise every assertion below rests on, re-decided.

    This test used to assert the opposite — that no first-party module computed
    a retrieval metric — as a tripwire set to fire on the run that merged one.
    It fired: ``evals/qa/scorer.py`` computes exact match and token F1, and
    ``evals/metrics.py`` and ``evals/qa/`` were listed in `_first_party_python`
    ahead of time for exactly this moment.

    Its instruction was to relax the ban *deliberately* rather than delete the
    guard, so: **not yet, and here is why.** A metric that exists is not a
    result. The scorer has been run against no competitor, no report is
    committed (``evals/qa/README.md`` ships a ``--score`` command instead of
    one), and its own ``fairness_blockers()`` gate refuses to publish a
    comparison whose systems disagree on model, embedding backend, corpus,
    question set or answer shape — which the two systems it ships do, on the
    last of those, by construction.

    So the ban in `test_no_first_party_doc_claims_a_measured_comparison` stands
    unchanged. What changes is what would lift it: a doc may report a
    comparison once it can **cite that benchmark's numbers from a run that
    cleared the gate**, and this test is where that decision gets recorded.
    """
    assert _repo_reports_a_retrieval_metric(), (
        "the QA scorer's token F1 has disappeared from the first-party tree — "
        "if the measurement was removed, this module's premise reverts and its "
        "docstring is now wrong"
    )
    # The narrower reason the ban survives the measurement: the harness itself
    # will not publish a comparison, and has not been asked to produce one.
    report = ROOT / "evals" / "qa" / "report.md"
    assert not report.exists(), (
        f"{report.relative_to(ROOT)} exists — a committed comparative table is "
        "exactly what this module bans in prose; keep generated reports outside "
        "the repo (run_qa_eval.py's --out defaults there)"
    )


def test_comparative_claim_detector_matches_the_real_forms() -> None:
    """The detector fires on both retired claims and not on ordinary prose."""
    # The two claims this change retired.
    assert _comparative_claims(
        "Independent benchmarks put graphs ahead on multi-hop, temporal and "
        "synthesis questions, and behind on simple fact lookup and cost."
    )
    assert _comparative_claims("Tesserae outperforms Graphiti on temporal recall.")
    assert _comparative_claims("Our graph beats Qdrant for multi-hop questions.")

    # Real sentences from this repo that a looser detector flagged.
    assert not _comparative_claims("A wrong edge is worse than no edge at all.")
    assert not _comparative_claims("Sort ahead of everything else.")
    assert not _comparative_claims(
        "User often knows the canonical name better than the extractor."
    )
    # Naming a rival, with no comparison attached.
    assert not _comparative_claims(
        "graphiti_episodes.jsonl is a dependency-free Graphiti episode export."
    )
    # The honest disclaimer that replaced claim 2.
    assert not _comparative_claims(
        "There is no retrieval benchmark here and no published number behind "
        "the routing table."
    )
    # Ordinary English that a bare "-er than" rule flags. Both are in the repo.
    assert not _comparative_claims("Use a worktree rather than a branch.")
    assert not _comparative_claims("Anything other than a dict is rejected.")


#: Claims the first version of this detector let through. Every one is a real
#: sentence shape someone could write tomorrow, and 13 of the 14 slipped past a
#: matcher built from eleven literal phrases and a ``.!?``-terminated-sentence
#: split. They are kept as data so that tightening the patterns later cannot
#: quietly drop coverage.
CLAIMS_THAT_MUST_FIRE = (
    # Shapes the literal-phrase list already had.
    "Independent benchmarks put graphs ahead on multi-hop, temporal and "
    "synthesis questions, and behind on simple fact lookup and cost.",
    "Tesserae outperforms Graphiti on temporal recall.",
    "Our graph beats Qdrant for multi-hop questions.",
    # No terminal punctuation: a bullet and a table row.
    "- Tesserae outperforms Graphiti on temporal recall",
    "| Tesserae | beats MegaMem on multi-hop |",
    # Comparative shapes that are not one of the eleven phrases.
    "Tesserae retrieves 30% more relevant facts than Graphiti.",
    "Tesserae wins on temporal recall against Graphiti",
    "Tesserae leads Graphiti by 20 points",
    "Tesserae is 2x faster, compared with Graphiti",
    "Tesserae is the only system that unifies temporal and semantic recall, "
    "unlike Graphiti",
    # A bare number-vs-number comparison, with no superiority word at all.
    "In our benchmark Tesserae scored 0.82 n" + "DCG against Graphiti's 0.61.",
    # Mirror-only claims. An English matcher cannot see any of these, which is
    # the repo's own documented failure mode.
    "독립적인 벤치마크에 따르면 Tesserae는 Graphiti보다 빠릅니다.",
    "ベンチマークでは Tesserae は Graphiti より高速です。",
    "基准测试表明 Tesserae 比 Graphiti 更快。",
)


#: Claim 2 as each locale actually rendered it, taken from the files this
#: change edited. `RETIRED_BENCHMARK_CLAIM_BY_LANG` pins these one sentence at
#: a time and would not notice a *different* claim appearing in a mirror; this
#: tuple exists to prove the detector itself can read all eight, which before
#: this change it could not — it saw the English and was blind to the other
#: seven. Note how little the mirrors share: "ahead on" came back as 앞서고,
#: 先行し, 领先, führend, опережают, adelante and mènent, and only the Korean and
#: Japanese used a "than" particle at all.
RETIRED_CLAIM_AS_EACH_MIRROR_RENDERED_IT = (
    "Independent benchmarks put graphs ahead on multi-hop, temporal and "
    "synthesis questions, and *behind* on simple fact lookup and cost.",
    "독립적인 벤치마크는 그래프가 멀티홉, 시간적 및 합성 질문에서 앞서고, 간단한 "
    "사실 조회와 비용에서 *뒤떨어진다*는 것을 보여줍니다.",
    "独立基准表明图在多跳、时间和合成问题上领先，在简单事实查询和成本上*落后*。",
    "独立したベンチマークは、グラフがマルチホップ、時間的および合成質問で先行し、"
    "単純な事実検索とコストで*後退*していることを示しています。",
    "Независимые тесты показывают, что графы опережают по мульти-хопу, временным "
    "и синтетическим вопросам, и *отстают* по простому поиску фактов и стоимости.",
    "Los puntos de referencia independientes muestran que los gráficos están "
    "adelante en preguntas multi-salto, temporales y de síntesis.",
    "Les tests indépendants montrent que les graphiques mènent sur les questions "
    "multi-sauts, temporelles et de synthèse.",
    "Unabhängige Benchmarks zeigen, dass Graphen bei Multi-Hop-, Zeit- und "
    "Synthesefragen führend sind.",
)


def test_detector_fires_on_every_claim_shape_that_once_slipped_through() -> None:
    """A claim must not become invisible by being phrased differently."""
    missed = [claim for claim in CLAIMS_THAT_MUST_FIRE if not _comparative_claims(claim)]
    assert not missed, "comparative claims the detector no longer sees:\n" + "\n".join(
        f"  {claim}" for claim in missed
    )


def test_detector_reads_the_retired_claim_in_all_eight_locales() -> None:
    """A mirror-only claim must be detectable, not merely pinned as a literal."""
    missed = [
        claim
        for claim in RETIRED_CLAIM_AS_EACH_MIRROR_RENDERED_IT
        if not _comparative_claims(claim)
    ]
    assert not missed, (
        "the retired claim is invisible to the detector in these renderings, so "
        "a NEW claim in the same locale would be too:\n"
        + "\n".join(f"  {claim}" for claim in missed)
    )


def test_detector_reads_bullets_and_table_cells_as_whole_units() -> None:
    """The segmentation fix, asserted on its own rather than only via a claim.

    The retired competitive report was a markdown table, and the first version
    of this module could only see text that reached a ``.``, ``!`` or ``?`` on
    the same line. Everything below is invisible under that rule.
    """
    assert _units("- a bullet with no full stop") == ["a bullet with no full stop"]
    assert _units("| left | right |") == ["left", "right"]
    assert _units("## A heading") == ["A heading"]
    # A decimal is not a sentence boundary.
    assert _units("Scored 0.82 today.") == ["Scored 0.82 today."]
    # CJK terminators end a unit even with no following space.
    assert _units("速いです。遅いです。") == ["速いです。", "遅いです。"]
    # Wrapped prose is rejoined, so a claim split by the formatter is seen whole.
    assert _units("Tesserae beats\nGraphiti here.") == ["Tesserae beats Graphiti here."]
    # Fenced code is not prose Tesserae asserts in its own voice.
    assert _units("```\nTesserae outperforms Graphiti\n```") == []


def test_no_first_party_doc_claims_a_measured_comparison() -> None:
    """No page may report a comparison the repository cannot produce."""
    offenders = [
        f"{path.relative_to(ROOT)}: {claim}"
        for path in _first_party_docs()
        for claim in _comparative_claims(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "comparative performance claims with no benchmark behind them:\n"
        + "\n".join(offenders)
        + "\n\nEither cite a published benchmark with its numbers, or write the "
        "sentence as the hypothesis it is."
    )


def test_retired_benchmark_claim_is_gone_from_english_and_every_mirror() -> None:
    """Claim 2 stays retired in all eight locales, not just the one we read."""
    offenders: list[str] = []
    english = DOCS / "tuning.md"
    if RETIRED_BENCHMARK_CLAIM in english.read_text(encoding="utf-8"):
        offenders.append(str(english.relative_to(ROOT)))
    for lang, retired in RETIRED_BENCHMARK_CLAIM_BY_LANG.items():
        mirror = DOCS / "i18n" / f"tuning.{lang}.md"
        if retired in mirror.read_text(encoding="utf-8"):
            offenders.append(str(mirror.relative_to(ROOT)))
    assert not offenders


def test_routing_guidance_is_labelled_a_hypothesis() -> None:
    """Deleting the claim is not enough — say what the routing table actually is.

    English-only by construction: the mirrors translate the sentence, so a
    literal grep cannot see it there. What pins the mirrors is the bold-span
    parity check in ``tests/test_docs_i18n.py`` — the marker is a ``**...**``
    span, and a mirror that drops it fails there.
    """
    text = (DOCS / "tuning.md").read_text(encoding="utf-8")
    assert "**hypothesis, not a measurement**" in text
    assert "no retrieval benchmark here" in text


def test_no_module_or_doc_ships_the_deleted_competitive_report() -> None:
    """Claim 1's producer, its output and its doc pointer stay gone.

    Scans the shipped package as well as the docs: the report was a hardcoded
    string in ``tesserae/temporal.py``, so a docs-only guard would not have
    seen it.
    """
    scanned = _first_party_docs() + sorted((ROOT / "tesserae").rglob("*.py"))
    offenders = [
        f"{path.relative_to(ROOT)}: {claim!r}"
        for path in scanned
        for claim in RETIRED_COMPETITIVE_REPORT_CLAIMS
        if claim in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "the retired competitive report is back:\n" + "\n".join(offenders)
    )
