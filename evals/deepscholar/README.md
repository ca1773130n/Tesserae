# DeepScholar-Bench

Two arms that write a Related Works section for a paper, from the same corpus,
on the same budget, through the same model — so that any difference between
their scores belongs to how the evidence was chosen and not to anything else.

* **`tesserae`** — stage the parent paper's cited abstracts, compile them,
  and walk `SourceDocument --contains--> EvidenceSpan <--evidenced_by-- Claim` for
  verbatim, paper-anchored sentences.
* **`bm25`** — the flat control. Okapi BM25 over the same abstracts. No graph,
  no claim node, no `evidenced_by` edge.

Nothing here forks or patches the benchmark. Both arms write
`<file-id>/intro.md` + `<file-id>/paper.csv`, which is exactly what
`eval/parsers/deepscholar_base.py` reads, and scoring is the benchmark's own
CLI unchanged.

## Running it

```bash
# what would it cost? prints the banner and stops
uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4

# stage corpora and paper.csv and stop — no compile, no LLM, no network
uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4 \
    --work ~/.blackhole/Tesserae/deepscholar/work \
    --output ~/.blackhole/Tesserae/deepscholar/out --stage-only

# both arms, one backbone call each per query
uv run python -m evals.deepscholar.run --dataset <clone>/dataset --file-ids 0-4 \
    --work ~/.blackhole/Tesserae/deepscholar/work \
    --output ~/.blackhole/Tesserae/deepscholar/out \
    --arms tesserae,bm25 --i-know-this-costs-money --yes
```

Then, in the benchmark clone:

```bash
.venv/bin/python -m eval.main --modes deepscholar_base \
    --evals cite_p claim_coverage nugget_coverage \
    --input-folder <out>/tesserae --file-id 1 --output-folder <results>/tesserae
```

`--work` must be outside this repository; `guard_work_dir` refuses otherwise,
because a compile there overwrites Tesserae's own `graph.json`.

## What is held constant, and what is not

The control is only a control if it is not handicapped, so the shared half is
shared *code*, not shared intention. Both arms use:

| | shared through |
|---|---|
| corpus | one `Query`, passed to both unchanged |
| evidence budget | `evidence.apply_budget` |
| sentence units | `tesserae.research_graph.split_sentences` |
| evidence table layout | `evidence.render_table` |
| prompt, word cap, citation format | `writer.SYSTEM_PROMPT`, `writer.build_user_prompt` |
| citation repair, retry, validation | `writer.render` |
| backbone, model, calls per query | one `Backbone`, one call, one retry policy |

They differ in exactly one thing: **which sentences** end up on a card, and —
only when `--paper-budget` is set — which papers do.

The Tesserae arm tops a card up from the paper's own abstract when its claims
do not fill the line allowance, so the two tables carry the same number of lines
about the same papers. Without that, a paper yielding one claim would contribute
one line against the control's three, and any gap would be volume rather than
selection. `EvidenceCard.claim_lines` records how many lines were actually
claim-anchored, and the runner prints it per query.

Where the control is *favoured*: its lines are ranked by query relevance while
the Tesserae arm's top-up lines are in reading order, and on the queries
measured during development that left the control with slightly more evidence
text under the identical budget. A floor should err upward.

## Things measured here that will bite

* **The version suffix is load-bearing.** `paper.csv` is keyed on the exact
  string after `arxiv.org/abs/`, and the dataset's links carry `v1`. A link
  without it resolves to an empty title and an empty abstract, and the
  entailment judge scores that sentence 0 with no error. `CitedPaper` carries
  both spellings and only `arxiv_versioned` ever reaches a URL or a csv row;
  `writer.repair_versions` rewrites an unambiguous slip rather than losing the
  sentence, and counts it.
* **`--extractor deterministic` is passed explicitly.** `tesserae compile`
  defaults to `llm`, and on an LLM-extracted graph `EvidenceSpan --part_of-->
  Paper` was emitted 0 times out of 12,307 in the graph measured for this
  phase. Both of this arm's anchors vanish, and it costs one call per abstract.
* **One project per query.** `paper.csv` is per-file-id, so a paper from another
  parent's corpus is retrievable evidence that cannot resolve — a zero with no
  error. `verify_staged` refuses a reused graph that indexes anything else.
* **Citation density, not grounding, is the binding constraint.** `cite_p` and
  `claim_coverage` append 0 for any sentence of 50 characters or more with no
  resolvable citation. Measured on the human ground truth across all 63 papers,
  only 767 of 1,498 sentences carry one, capping both metrics at ~0.51 for text
  written the way people write it. On the development queries both arms scored
  exactly their own structural ceiling — every *cited* sentence was judged
  supported — so the prompt's citation density, which is shared, moves the
  number far more than the retrieval does.
* **The instrument cannot resolve ±0.02 on a subset.** Per-paper standard
  deviation is 0.073 (`cite_p`) and 0.080 (`claim_coverage`); a ±0.02 comparison
  needs 52–61 of the 63 papers. Report a subset as a subset.

## Tests

`tests/test_deepscholar_stage.py` and `tests/test_deepscholar_arms.py`. Offline
and synthetic: no dataset clone, no compile, no model, no network. The staging
tests include a transcription of the benchmark's own parser, so the
version-suffix contract is asserted against the code that will actually score
the output rather than against our reading of it.
