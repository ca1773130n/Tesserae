"""Bridge retrieval: can the graph reach a document that query similarity cannot?

Ported from the 2026-09-02 scratch benchmark (``~/.blackhole/Tesserae/2026-09-02/
bridge/bridge_bench.py``) so the roadmap R4 arm, mention-weighted passage nodes
in PPR, is measured on exactly the same items. Items are built mechanically from
a compiled graph, and ground truth comes from RAW TEXT, never from the graph:

  * take an edge ``a -rel-> b`` extracted from document A, where the bridge
    entity b was NOT introduced by A;
  * the QUERY is a window of A's own text around that edge, with every mention
    of b's name and aliases masked out;
  * the GOLD is the document(s) other than A that mention b's name most in raw
    text (>=3 mentions, within half of the max, at most 3), most-mentioning first;
  * an item is KEPT only if BM25 over documents AND a bge-base chunk store both
    miss every gold doc at 10: the lexically-disjoint second hop.

Metrics, all budget-matched to 10 documents per arm:

  bridge_doc@5    gold[0] (the document that mentions the bridge entity most,
                  i.e. the one the bridge is about) is in the top 5
  answer_R@10     recall of the whole gold set in the top 10
  hit@5 / hit@10  any gold doc in the top k (the 2026-09-02 metric)

Arms:

  bm25 / dense        the filter arms (0 on kept items by construction)
  dense_2hop          top-1 dense doc's centroid as a second query: the honest
                      non-graph second hop, and the arm R4 must beat
  tess_hybrid         shipped hybrid_search over nodes, deduped to documents
  ppr_density         R4: PPR over the graph with document nodes as walk
                      participants, node->doc edges weighted by node_mentions
                      density (replacing the flat 0.25 provenance downweight),
                      seeded by the top hybrid nodes AND the top dense
                      documents; documents ranked by PPR mass (HippoRAG 2 shape)
  ppr_flat            the same walk on the graph as shipped (flat 0.25
                      mentioned_in): isolates what density adds
  ppr_density_nodes   ppr_density without document seeds: isolates what
                      dense document seeding adds
  ppr_density_hr2     ppr_density with HippoRAG 2's passage weight (0.05 of the
                      teleport mass on document seeds instead of 0.5). ADDED
                      after a 12-item smoke run showed the 0.5 split returns the
                      dense top 10 unchanged, which scores 0 on kept items by
                      construction; it is a post-hoc arm and is reported as such

The paired test is a two-sided sign-flip permutation test on per-item metric
differences (exact below 21 non-zero pairs, 20,000 Monte-Carlo flips above).

No LLM anywhere. Nothing is compiled. Every expensive stage is checkpointed under
<out_dir>, so a killed run resumes where it stopped. The run WRITES to
<project_root>/.tesserae/sqlite.db (vector cache, store schema), so point it at a
copy, never at a live project.

usage (from the repo root):
    uv run --extra semantic --with sentence-transformers \\
        python -m evals.bridge.bridge_bench <project_root> <out_dir> \\
        [--corpus-dir corpus] [--max-items 400]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import re
import sqlite3
import sys
import time

import numpy as np

from tesserae.graph_stores.sqlite import SqliteGraphStore
from tesserae.research_graph import graph_from_payload
from tesserae.retrieval.bm25_index import Bm25Index
from tesserae.retrieval.hybrid import SentenceTransformersBackend, _tokenize, hybrid_search
from tesserae.retrieval import ppr as ppr_mod
from tesserae.retrieval.ppr import PROVENANCE_EDGE_DOWNWEIGHT, personalized_pagerank
from tesserae.retrieval.vector_cache import VectorCache


K_FILTER, CHUNK, SEEDS, DOC_SEEDS, HUB, WINDOW = 10, 1200, 20, 10, 50, 400
# PPR arm parameters, fixed before the first run. alpha=0.5 is HippoRAG 2's
# damping; half the teleport mass goes to node seeds, half to document seeds.
PPR_ALPHA, DOC_SEED_MASS, HR2_DOC_SEED_MASS = 0.5, 0.5, 0.05
DOC_TYPES = {"SourceDocument", "Paper", "SourceFile"}
SKIP_TYPES = {"SourceDocument", "Paper", "SourceFile", "Person", "Organization", "Repository",
              "CommunitySummary", "Synthesis", "Session", "Project", "EvidenceSpan", "Claim",
              "PerformanceClaim", "LimitationClaim", "ContributionClaim", "ComparisonClaim",
              "CausalClaim", "Result", "Stub", "Trend", "Event", "Runbook", "Agent"}
ARMS = ("bm25", "dense", "dense_2hop", "tess_hybrid", "ppr_density", "ppr_flat", "ppr_density_nodes",
        "ppr_density_hr2")
GRAPH_ARMS = ("tess_hybrid", "ppr_density", "ppr_flat", "ppr_density_nodes", "ppr_density_hr2")
CHALLENGERS = ("ppr_density", "ppr_flat", "ppr_density_nodes", "ppr_density_hr2", "tess_hybrid")


# ---- statistics -------------------------------------------------------------
def paired_permutation_p(a, b, n_perm=20_000, seed=0):
    """Two-sided sign-flip permutation p for the mean of ``a - b``.

    Under H0 each pair's difference is equally likely to have either sign, so
    the null distribution of the summed difference is enumerated over sign
    flips: exactly when at most 20 pairs differ, Monte Carlo otherwise (with
    the +1 correction, so p is never 0). Zero differences carry no
    information under a sign flip and are dropped.
    """
    d = [x - y for x, y in zip(a, b) if x != y]
    if not d:
        return 1.0
    obs = abs(sum(d)) - 1e-12
    if len(d) <= 20:
        hits = 0
        for mask in range(1 << len(d)):
            s = sum(v if (mask >> i) & 1 else -v for i, v in enumerate(d))
            hits += abs(s) >= obs
        return hits / (1 << len(d))
    rng = np.random.default_rng(seed)
    arr = np.asarray(d, dtype=float)
    signs = rng.choice((-1.0, 1.0), size=(n_perm, len(arr)))
    hits = int((np.abs(signs @ arr) >= obs).sum())
    return (hits + 1) / (n_perm + 1)


def metrics(ranked, gold):
    top5, top10 = set(ranked[:5]), set(ranked[:10])
    return {"bridge_doc@5": float(gold[0] in top5),
            "answer_R@10": len(top10 & set(gold)) / len(gold),
            "hit@5": float(bool(top5 & set(gold))),
            "hit@10": float(bool(top10 & set(gold)))}


# ---- PPR with the transition built once --------------------------------------
class FrozenPPR:
    """``personalized_pagerank`` over one fixed graph, transition built once.

    The product function rebuilds the row-normalised transition from every
    edge on each call, which costs seconds on a 28k-node graph and made a
    400-query run take hours. The transition does not depend on the seeds, so
    this captures the exact ``out_norm`` the product builds (by wrapping its
    numpy power iteration for a single call) and then runs only the seed-
    dependent part per query: the same personalization vector, the same
    ``_power_iteration_numpy``, the same positive-score filter and ordering.
    ``Bench.graph_arms`` asserts on the first queries that the result is
    identical to calling ``personalized_pagerank`` directly.
    """

    def __init__(self, graph, **kwargs):
        self.graph, self.kwargs = graph, kwargs
        real, captured = ppr_mod._power_iteration_numpy, {}

        def capture(n, out_norm, *args):
            captured["out_norm"] = out_norm
            return real(n, out_norm, *args)

        ppr_mod._power_iteration_numpy = capture
        try:
            personalized_pagerank(graph, [graph.nodes[0].id], top_k=1, **kwargs)
        finally:
            ppr_mod._power_iteration_numpy = real
        if "out_norm" not in captured:
            raise SystemExit("numpy power iteration did not run: install the semantic extra")
        self.out_norm = captured["out_norm"]
        self.node_ids = [n.id for n in graph.nodes]
        self.index = {nid: i for i, nid in enumerate(self.node_ids)}

    def __call__(self, seed_weights, alpha):
        n = len(self.node_ids)
        surviving = [s for s in seed_weights if s in self.index]
        total = sum(seed_weights[s] for s in surviving)
        if not surviving or total <= 0.0:
            return []
        p = [0.0] * n
        for s in surviving:
            p[self.index[s]] += seed_weights[s] / total
        seed_indices = [self.index[s] for s in surviving]
        rank = ppr_mod._power_iteration_numpy(n, self.out_norm, p, seed_indices, alpha, 100, 1.0e-6)
        return sorted(((self.node_ids[i], rank[i]) for i in range(n) if rank[i] > 0.0),
                      key=lambda item: item[1], reverse=True)

    def reference(self, seed_weights, alpha):
        return personalized_pagerank(self.graph, list(seed_weights), alpha=alpha, top_k=len(self.graph.nodes),
                                     seed_weights=seed_weights, **self.kwargs)


# ---- corpus and graph -------------------------------------------------------
class Bench:
    def __init__(self, project: pathlib.Path, out: pathlib.Path, corpus_dir: str, max_items: int):
        self.project, self.out, self.max_items = project, out, max_items
        self.corpus_marker = f"/{corpus_dir}/"
        self.rng = random.Random(0)
        self.docs = {p.name: p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted((project / corpus_dir).rglob("*.md"))}
        self.names = list(self.docs)
        if not self.names:
            raise SystemExit(f"no *.md documents under {project / corpus_dir}")
        log(f"corpus {len(self.names)} docs, {sum(map(len, self.docs.values())) / 1e6:.1f} MB")
        self.payload = json.loads((project / ".tesserae/graph.json").read_text())
        self.nodes = {n["id"]: n for n in self.payload["nodes"]}
        self.deg = collections.Counter()
        for e in self.payload["edges"]:
            self.deg[e["source"]] += 1
            self.deg[e["target"]] += 1
        log(f"graph {len(self.nodes)} nodes, {len(self.payload['edges'])} edges")
        self.db_path = project / ".tesserae/sqlite.db"
        self.cache = VectorCache(self.db_path)
        self.bm25 = Bm25Index(out / "bm25.sqlite")
        self.bge = SentenceTransformersBackend("BAAI/bge-base-en-v1.5")
        self.g_shipped = graph_from_payload(self.payload)
        self.g_density, self.doc_node = self._density_graph()
        density_w = {"mentioned_in": 1.0 / PROVENANCE_EDGE_DOWNWEIGHT}
        self.ppr_shipped = FrozenPPR(self.g_shipped, tame_hubs=True)
        self.ppr_density = FrozenPPR(self.g_density, tame_hubs=True, edge_type_weights=density_w)
        self.checks_left = 3  # queries on which FrozenPPR is checked against the product call

    def doc_of(self, node):
        sp = node.get("source_path") or ""
        name = pathlib.Path(sp).name
        return name if self.corpus_marker in sp and name in self.docs else None

    def _density_graph(self):
        """The shipped graph plus document nodes and mention-density node->doc edges.

        Every corpus document gets a walk participant: its existing Paper /
        SourceDocument / SourceFile node, or a synthetic SourceDocument node
        when the compile produced none. Each non-document node then gets one
        ``mentioned_in`` edge per provenance document, weighted by that
        document's share of the node's mentions (``SqliteGraphStore.
        node_documents``); a node with no mention counts at all (claims,
        evidence spans) splits evenly over its documents. Existing
        non-doc -> doc ``mentioned_in`` edges are replaced; doc -> doc ones
        keep their shipped flat weight via ``confidence``.
        """
        doc_node = {}
        for n in self.payload["nodes"]:
            d = self.doc_of(n) if n["type"] in DOC_TYPES else None
            if d and d not in doc_node:
                doc_node[d] = n["id"]
        nodes = list(self.payload["nodes"])
        for d in self.names:
            if d not in doc_node:
                doc_node[d] = f"SourceDocument:bridge-bench:{d}"
                nodes.append({"id": doc_node[d], "type": "SourceDocument", "name": d, "aliases": [],
                              "description": "", "source_path": str(self.project / self.corpus_marker.strip("/") / d),
                              "metadata": {}})
        doc_ids = set(doc_node.values())
        # With tame_hubs on, mentioned_in walks at type_weight * confidence * 0.25.
        # The eval passes type_weight = 1/0.25, so a density edge walks at its
        # density and confidence=0.25 reproduces the shipped flat weight exactly.
        edges = []
        for e in self.payload["edges"]:
            if e["type"] == "mentioned_in":
                if e["source"] in doc_ids and e["target"] in doc_ids:
                    edges.append({**e, "confidence": PROVENANCE_EDGE_DOWNWEIGHT})
                continue  # non-doc -> doc: replaced by a density edge below
            edges.append(e)
        store = SqliteGraphStore(self.db_path)
        t0, added, weighted = time.time(), 0, 0
        for nid, n in self.nodes.items():
            if nid in doc_ids:
                continue
            rows = [(pathlib.Path(p).name, m) for p, m in store.node_documents(nid)
                    if self.corpus_marker in p and pathlib.Path(p).name in doc_node]
            if not rows:
                continue
            total = sum(m for _, m in rows)
            weighted += total > 0
            for d, m in rows:
                w = m / total if total > 0 else 1.0 / len(rows)
                if w > 0:
                    edges.append({"source": nid, "target": doc_node[d], "type": "mentioned_in",
                                  "evidence": "", "metadata": {}, "confidence": w})
                    added += 1
        log(f"density graph: {len(doc_ids)} doc nodes ({len(doc_ids) - sum(1 for n in self.payload['nodes'] if n['id'] in doc_ids)} synthetic), "
            f"{added} node->doc edges ({weighted} nodes mention-weighted) in {time.time() - t0:.0f}s")
        return graph_from_payload({**self.payload, "nodes": nodes, "edges": edges}), doc_node

    # ---- items --------------------------------------------------------------
    def _name_patterns(self, n):
        cands = [n["name"]] + list(n.get("aliases") or [])
        cands = [c.strip() for c in cands if c and len(c.strip()) >= 4 and re.search(r"[A-Za-z]", c)]
        return [re.compile(r"(?<![A-Za-z0-9])" + re.escape(c) + r"(?![A-Za-z0-9])", re.I)
                for c in dict.fromkeys(cands)]

    def build_items(self):
        norm_ws = lambda s: re.sub(r"\s+", " ", s or "").strip()
        mentions = lambda pats, text: sum(len(p.findall(text)) for p in pats)
        prov = {}
        con = sqlite3.connect(self.db_path)
        for s, t, o, p in con.execute("select source, type, target, source_path from edge_provenance "
                                      "where source_path like ?", (f"%{self.corpus_marker}%",)):
            prov.setdefault((s, t, o), pathlib.Path(p).name)
        con.close()
        items, seen, reasons = [], set(), collections.Counter()
        cross = [e for e in self.payload["edges"]
                 if e["type"] != "summarizes" and e["source"] in self.nodes and e["target"] in self.nodes]
        self.rng.shuffle(cross)
        N = len(self.names)
        for e in cross:
            a, b = self.nodes[e["source"]], self.nodes[e["target"]]
            A = prov.get((e["source"], e["type"], e["target"]))
            if not A or A not in self.docs:
                reasons["no_provenance"] += 1; continue
            da, dbb = self.doc_of(a), self.doc_of(b)
            bridge = b if (dbb and dbb != A) else (a if (da and da != A) else None)
            if bridge is None:
                reasons["no_foreign_endpoint"] += 1; continue
            if bridge["type"] in SKIP_TYPES:
                reasons["skip_type"] += 1; continue
            if self.deg[bridge["id"]] > HUB:
                reasons["hub"] += 1; continue
            key = (A, bridge["id"])
            if key in seen:
                reasons["dup"] += 1; continue
            pats = self._name_patterns(bridge)
            if not pats:
                reasons["no_name"] += 1; continue
            tf = {d: mentions(pats, self.docs[d]) for d in self.names}
            df = sum(1 for v in tf.values() if v)
            if df > 0.25 * N:
                reasons["generic_term"] += 1; continue
            if tf[A] == 0:
                reasons["not_in_A"] += 1; continue
            others = {d: v for d, v in tf.items() if d != A and v >= 3}
            if not others:
                reasons["no_elaborating_doc"] += 1; continue
            mx = max(others.values())
            if tf[A] >= mx:
                reasons["A_elaborates_itself"] += 1; continue
            gold = [d for d, _ in sorted(others.items(), key=lambda kv: (-kv[1], kv[0]))
                    if others[d] >= 0.5 * mx][:3]
            text = norm_ws(self.docs[A])
            ev = norm_ws(e.get("evidence"))[:80].lower()
            pos = text.lower().find(ev) if ev else -1
            if pos < 0:
                m = next((p.search(text) for p in pats if p.search(text)), None)
                pos = m.start() if m else -1
            if pos < 0:
                reasons["no_anchor"] += 1; continue
            window = text[max(0, pos - WINDOW): pos + WINDOW]
            for p in pats:
                window = p.sub("[X]", window)
            seen.add(key)
            items.append({"A": A, "bridge": bridge["id"], "bridge_name": bridge["name"],
                          "bridge_type": bridge["type"], "edge": e["type"], "gold": gold, "tf_A": tf[A],
                          "tf_gold": [others[d] for d in gold], "df": df, "query": window})
            if len(items) >= self.max_items:
                break
        log(f"items {len(items)} (skipped: {dict(reasons.most_common())})")
        return items, reasons

    # ---- filter arms: BM25 over documents, bge chunk store --------------------
    def filter_items(self, items):
        names, docs, N = self.names, self.docs, len(self.names)
        toks = {d: _tokenize(docs[d]) for d in names}
        avgdl = sum(map(len, toks.values())) / N
        dfreq = collections.Counter(t for d in names for t in set(toks[d]))
        tfs = {d: collections.Counter(toks[d]) for d in names}

        def bm25_rank(q, k1=1.5, b=0.75):
            qt, sc = set(_tokenize(q)), {}
            for d in names:
                s, L = 0.0, len(toks[d])
                for t in qt:
                    f = tfs[d].get(t)
                    if f:
                        idf = math.log(1 + (N - dfreq[t] + 0.5) / (dfreq[t] + 0.5))
                        s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * L / avgdl))
                sc[d] = s
            return [d for d, _ in sorted(sc.items(), key=lambda kv: -kv[1])]

        chunk_doc, chunk_txt = [], []
        for d in names:
            for i in range(0, len(docs[d]), CHUNK):
                chunk_doc.append(d); chunk_txt.append(docs[d][i:i + CHUNK])
        chunk_doc = np.asarray(chunk_doc)
        npz = self.out / "chunks.npz"
        if npz.exists():
            V = np.load(npz)["V"]; log(f"loaded {len(V)} chunk vectors")
        else:
            t0 = time.time()
            V = np.asarray(self.bge.embed(chunk_txt), dtype=np.float32)
            V /= np.linalg.norm(V, axis=1, keepdims=True) + 1e-9
            np.savez(npz, V=V); log(f"embedded {len(chunk_txt)} chunks in {time.time() - t0:.0f}s")
        if len(V) != len(chunk_doc):
            raise SystemExit(f"{npz} has {len(V)} vectors for {len(chunk_doc)} chunks: stale cache, delete it")
        unit = lambda v: v / (np.linalg.norm(v) + 1e-9)
        centroid = {d: unit(V[np.where(chunk_doc == d)[0]].mean(0)) for d in names}

        def dense_rank_vec(qv, exclude=()):
            sims, best = V @ qv, {}
            for i in np.argsort(-sims):
                d = chunk_doc[i]
                if d not in best and d not in exclude:
                    best[d] = float(sims[i])
                if len(best) >= 50:
                    break
            return list(best.items())

        for it in items:
            it["bm25"] = bm25_rank(it["query"])[:10]
            qv = unit(np.asarray(self.bge.embed([it["query"]])[0], dtype=np.float32))
            dense = dense_rank_vec(qv)[:10]
            it["dense"] = [d for d, _ in dense]
            it["dense_sim"] = [round(s, 6) for _, s in dense]
            top1 = it["dense"][0]
            it["dense_2hop"] = [top1] + [d for d, _ in dense_rank_vec(centroid[top1], exclude={top1})[:9]]
            gold = set(it["gold"])
            it["kept"] = not (set(it["bm25"][:K_FILTER]) & gold) and not (set(it["dense"][:K_FILTER]) & gold)
        return items

    # ---- graph arms -----------------------------------------------------------
    def _ppr_docs(self, ppr, node_seeds, doc_seeds, doc_mass=DOC_SEED_MASS):
        weights = {}
        if node_seeds:
            tot = sum(s for _, s in node_seeds) or 1.0
            share = 1.0 - (doc_mass if doc_seeds else 0.0)
            for nid, s in node_seeds:
                weights[nid] = weights.get(nid, 0.0) + share * s / tot
        if doc_seeds:
            tot = sum(max(s, 0.0) for _, s in doc_seeds) or 1.0
            share = doc_mass if node_seeds else 1.0
            for d, s in doc_seeds:
                nid = self.doc_node[d]
                weights[nid] = weights.get(nid, 0.0) + share * max(s, 0.0) / tot
        if not weights:
            return []
        ranked = ppr(weights, PPR_ALPHA)
        if self.checks_left > 0 and ranked != ppr.reference(weights, PPR_ALPHA):
            raise SystemExit("FrozenPPR diverged from personalized_pagerank: the cached transition is wrong")
        doc_name = {v: k for k, v in self.doc_node.items()}
        out = []
        for nid, _ in ranked:
            d = doc_name.get(nid)
            if d is None:
                node = self.nodes.get(nid)
                d = self.doc_of(node) if node is not None and node["type"] in DOC_TYPES else None
            if d and d not in out:
                out.append(d)
            if len(out) >= 10:
                break
        return out

    def graph_arms(self, it):
        res = hybrid_search(self.g_shipped, it["query"], top_k=200, backend=self.bge,
                            vector_cache=self.cache, bm25_index=self.bm25, source_root=self.project)
        base = collections.Counter()
        for s in res.scored:
            d = self.doc_of(self.nodes[s.node.id]) if s.node.id in self.nodes else None
            if d:
                base[d] += s.score
        node_seeds = [(s.node.id, float(s.score)) for s in res.scored[:SEEDS] if s.score > 0]
        doc_seeds = list(zip(it["dense"][:DOC_SEEDS], it["dense_sim"][:DOC_SEEDS]))
        out = {"tess_hybrid": [d for d, _ in base.most_common(10)],
               "ppr_density": self._ppr_docs(self.ppr_density, node_seeds, doc_seeds),
               "ppr_flat": self._ppr_docs(self.ppr_shipped, node_seeds, doc_seeds),
               "ppr_density_nodes": self._ppr_docs(self.ppr_density, node_seeds, []),
               "ppr_density_hr2": self._ppr_docs(self.ppr_density, node_seeds, doc_seeds,
                                                 doc_mass=HR2_DOC_SEED_MASS),
               "seeds": [(n, round(s, 6)) for n, s in node_seeds]}
        if self.checks_left > 0:
            self.checks_left -= 1
            if self.checks_left == 0:
                log("FrozenPPR matched personalized_pagerank exactly on 3 queries x 4 walks")
        return out


def log(*a):
    print(*a, flush=True)


def summarize(items, project, reasons):
    scored = [it for it in items if all(a in it for a in GRAPH_ARMS)]
    summary = {"project": str(project), "n_items": len(items),
               "n_kept": sum(it["kept"] for it in items), "skipped": dict(reasons),
               "params": {"alpha": PPR_ALPHA, "doc_seed_mass": DOC_SEED_MASS, "hr2_doc_seed_mass": HR2_DOC_SEED_MASS, "node_seeds": SEEDS,
                          "doc_seeds": DOC_SEEDS}, "strata": {}}
    keys = ("bridge_doc@5", "answer_R@10", "hit@5", "hit@10")
    for label, rows in (("kept", [it for it in scored if it["kept"]]),
                        ("easy", [it for it in scored if not it["kept"]])):
        if not rows:
            continue
        m = {arm: [metrics(it[arm], it["gold"]) for it in rows] for arm in ARMS}
        s = {"n": len(rows),
             "mean": {arm: {k: round(sum(r[k] for r in m[arm]) / len(rows), 4) for k in keys} for arm in ARMS},
             "vs_dense_2hop": {}}
        for arm in CHALLENGERS:
            s["vs_dense_2hop"][arm] = {}
            for k in ("bridge_doc@5", "answer_R@10"):
                a = [r[k] for r in m[arm]]; b = [r[k] for r in m["dense_2hop"]]
                s["vs_dense_2hop"][arm][k] = {
                    "delta": round((sum(a) - sum(b)) / len(rows), 4),
                    "win": sum(x > y for x, y in zip(a, b)), "loss": sum(x < y for x, y in zip(a, b)),
                    "p": round(paired_permutation_p(a, b), 4)}
        summary["strata"][label] = s
        log(f"\n== {label} stratum, n={len(rows)}")
        log(f"{'arm':<20}" + "".join(f"{k:>14}" for k in keys))
        for arm in ARMS:
            log(f"{arm:<20}" + "".join(f"{s['mean'][arm][k]:>14.3f}" for k in keys))
        for arm, v in s["vs_dense_2hop"].items():
            log(f"  {arm} vs dense_2hop: " + "; ".join(
                f"{k} d={x['delta']:+.3f} W{x['win']}/L{x['loss']} p={x['p']}" for k, x in v.items()))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--corpus-dir", default="corpus")
    ap.add_argument("--max-items", type=int, default=400)
    args = ap.parse_args(argv)
    project, out = args.project.expanduser(), args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    bench = Bench(project, out, args.corpus_dir, args.max_items)

    pre, arms_log = out / "items_prefilter.jsonl", out / "graph_arms.jsonl"
    if pre.exists():
        items = [json.loads(l) for l in pre.read_text().splitlines() if l.strip()]
        reasons = json.loads((out / "skipped.json").read_text())
        log(f"resumed {len(items)} pre-filtered items")
    else:
        items, reasons = bench.build_items()
        if not items:
            raise SystemExit("no items")
        items = bench.filter_items(items)
        pre.write_text("\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n")
        (out / "skipped.json").write_text(json.dumps(dict(reasons)))
    kept = [it for it in items if it["kept"]]
    easy = [it for it in items if not it["kept"]]
    log(f"filter: kept {len(kept)} (lexically-disjoint second hop), easy {len(easy)}")

    done = {}
    if arms_log.exists():
        for l in arms_log.read_text().splitlines():
            if l.strip():
                r = json.loads(l); done[(r["A"], r["bridge"])] = r
    todo = kept + easy[:len(kept)]
    log(f"graph arms: {len(todo)} queries, {sum((it['A'], it['bridge']) in done for it in todo)} already done")
    t0 = time.time()
    with arms_log.open("a") as fh:
        for i, it in enumerate(todo):
            key = (it["A"], it["bridge"])
            if key not in done:
                r = bench.graph_arms(it)
                done[key] = {"A": it["A"], "bridge": it["bridge"], **r}
                fh.write(json.dumps(done[key]) + "\n"); fh.flush()
            it.update({a: done[key][a] for a in GRAPH_ARMS})
            if i % 25 == 0:
                log(f"  graph arms {i}/{len(todo)} ... {time.time() - t0:.0f}s")
    summary = summarize(items, project, reasons)
    (out / "items.jsonl").write_text("\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    log(f"wrote {out}/items.jsonl and summary.json")


if __name__ == "__main__":
    sys.exit(main())
