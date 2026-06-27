# Federation semantic-link eval

Embedding backend: `model2vec:minishlab/potion-base-8M`. Fixture: 28 concepts across 3 projects, 10 gold cross-project pairs + domain-adjacent hard negatives. Regenerate: `python -m evals.federation.run_eval`.

## 1. Threshold (min_cosine) — link precision/recall

| min_cosine | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| 0.30 | 10 | 33 | 0 | 0.23 | 1.00 | 0.38 |
| 0.35 | 10 | 15 | 0 | 0.40 | 1.00 | 0.57 |
| 0.40 | 9 | 7 | 1 | 0.56 | 0.90 | 0.69 |
| 0.45 | 7 | 4 | 3 | 0.64 | 0.70 | 0.67 |
| 0.50 | 7 | 1 | 3 | 0.88 | 0.70 | 0.78 |
| 0.55  ⬅ default ⭐ | 7 | 0 | 3 | 1.00 | 0.70 | 0.82 |
| 0.60 | 5 | 0 | 5 | 1.00 | 0.50 | 0.67 |
| 0.65 | 3 | 0 | 7 | 1.00 | 0.30 | 0.46 |
| 0.70 | 2 | 0 | 8 | 1.00 | 0.20 | 0.33 |

**Best F1** at min_cosine=0.55 (F1=0.82). **Default 0.55** → F1=0.82, precision=1.00, recall=0.70 (Δ vs best F1 = 0.00).

## 2. Edge weight — does the bridge surface B without swamping A?

Bridge link a::rw ↔ b::ppr formed: **True**. Seed = A's `Random-walk graph ranking`. Rank (1=top):

| shares_concept_with weight | A_neighbour | B_bridged | B_far(bridge-only) | B_unrelated |
|---|---|---|---|---|
| 0.00 (no bridge) | 2 | None | None | None |
| 0.25 | 2 | 3 | 4 | None |
| 0.50  ⬅ default | 2 | 3 | 4 | None |
| 1.00 | 3 | 2 | 4 | None |
| 2.00 | 3 | 2 | 4 | None |
