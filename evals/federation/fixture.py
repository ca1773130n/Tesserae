"""Labeled fixture for the federation semantic-link eval.

Each entry is a Concept as it appears in one project. ``cluster`` groups the SAME
underlying idea expressed differently across projects — so a GOLD cross-project
link is any pair of entries with the same non-empty ``cluster`` in DIFFERENT
projects. ``cluster=None`` entries are distractors; several are deliberately
*domain-adjacent hard negatives* (related to a gold cluster but a distinct
concept) to test precision, not just recall.

Used by run_eval.py (sweep min_cosine -> precision/recall/F1) and
tests/test_federation_eval.py (regression guard on the chosen defaults).
"""

from __future__ import annotations

# (project, name, description, cluster)
CONCEPTS = [
    # --- gold clusters: same concept, two projects, different wording ---------
    ("research", "Personalized PageRank", "ranking graph nodes by random-walk restart probability", "ppr"),
    ("work", "PPR node ranking", "personalized pagerank scores nodes via teleporting random walks", "ppr"),

    ("research", "Attention mechanism", "weighting tokens by query-key similarity in a sequence model", "attention"),
    ("work", "Self-attention layer", "scaled dot-product attention relating positions within a sequence", "attention"),

    ("work", "Memoization", "caching a function's results keyed by its arguments to avoid recompute", "memoization"),
    ("notes", "Result caching", "store the output of an expensive computation and reuse it on repeat calls", "memoization"),

    ("research", "Stochastic gradient descent", "optimize parameters using gradients from minibatches", "sgd"),
    ("work", "SGD optimizer", "iterative weight updates from per-batch gradient estimates", "sgd"),

    ("research", "Word embeddings", "map words to dense vectors capturing semantic similarity", "embeddings"),
    ("notes", "Dense word vectors", "represent vocabulary tokens as continuous semantic vectors", "embeddings"),

    ("research", "Overfitting", "a model fits noise in the training data and generalizes poorly", "overfitting"),
    ("notes", "Memorizing the training set", "the model learns training examples instead of the pattern", "overfitting"),

    ("work", "Unit testing", "automated checks that exercise individual functions in isolation", "unit_testing"),
    ("notes", "Automated test cases", "small programmatic tests asserting a function behaves correctly", "unit_testing"),

    ("research", "Transformer architecture", "sequence model built from stacked self-attention and feedforward blocks", "transformer"),
    ("work", "Transformer model", "an attention-based neural network for sequence-to-sequence tasks", "transformer"),

    ("research", "k-nearest neighbors", "classify a point by majority vote of its closest labeled neighbors", "knn"),
    ("notes", "Nearest-neighbor classification", "assign a label from the most similar stored examples", "knn"),

    ("research", "Backpropagation", "compute gradients of the loss by reverse-mode chain rule", "backprop"),
    ("work", "Reverse-mode autodiff", "propagate derivatives backward through the computation graph", "backprop"),

    # --- distractors / domain-adjacent HARD NEGATIVES (cluster=None) ----------
    ("research", "Dropout regularization", "randomly zero activations during training to reduce co-adaptation", None),   # near 'overfitting'
    ("work", "Garbage collection", "automatic reclamation of unreachable heap memory at runtime", None),               # near 'memoization' (memory)
    ("notes", "Database indexing", "a B-tree structure that speeds up row lookups in a table", None),                  # near 'embeddings' (indexing)
    ("research", "Convolutional neural network", "a vision model using learned convolution filters over a grid", None),# near 'transformer/attention'
    ("work", "Continuous integration", "automatically build and test every commit on a shared branch", None),         # near 'unit_testing'
    ("notes", "Gradient clipping", "cap the gradient norm to stabilize training", None),                              # near 'sgd/backprop'
    ("work", "Quarterly budget planning", "allocate the department's spending across the next three months", None),    # unrelated
    ("notes", "Banana bread recipe", "mash ripe bananas, fold into batter, and bake for an hour", None),              # unrelated
]


def gold_cross_project_pairs(node_id):
    """Return the set of GOLD cross-project link pairs as frozensets of namespaced
    node ids. ``node_id(project, index) -> str`` builds the federated id.

    A gold pair = two fixture entries in the same cluster but different projects.
    """
    by_cluster = {}
    for index, (project, _name, _desc, cluster) in enumerate(CONCEPTS):
        if cluster:
            by_cluster.setdefault(cluster, []).append((project, index))
    gold = set()
    for members in by_cluster.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (pa, ia), (pb, ib) = members[i], members[j]
                if pa != pb:
                    gold.add(frozenset((node_id(pa, ia), node_id(pb, ib))))
    return gold
