"""The hosted embedding backend — explicit-only, order-safe, batched.

It exists for one reason: a published benchmark protocol (LongMemEval-MAB and
the baselines Tesserae would be compared against) fixes
``text-embedding-3-small`` for every system. Running our default model2vec
against those numbers would vary the embedder and the memory architecture at
once, and the difference would be unattributable.

Every test here runs offline. The API is stubbed; nothing in this file may
reach the network or spend a cent.
"""

from __future__ import annotations

import json
import io
import pytest

import tesserae.retrieval.hybrid as H


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_api(monkeypatch, dim=4, *, shuffle=False, record=None):
    """Stub the embeddings endpoint. Returns vectors keyed to input order."""

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        inputs = body["input"]
        if record is not None:
            record.append(list(inputs))
        data = [
            {"index": i, "embedding": [float(i)] * dim}
            for i, _ in enumerate(inputs)
        ]
        if shuffle:  # the API promises an index, not an order
            data = list(reversed(data))
        return _FakeResponse(json.dumps({"data": data}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    H.reset_embedding_backend()
    yield
    H.reset_embedding_backend()


def test_auto_never_selects_the_metered_backend(monkeypatch):
    """`auto` must not reach a backend that bills per call.

    Every other backend here is free and local. If `auto` could resolve to the
    hosted one, an ordinary `search_nodes` would silently become a metered
    request — so this is pinned even with a key present and the local backends
    unavailable.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(H, "Model2VecBackend", lambda *a, **k: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(H, "SentenceTransformersBackend", lambda *a, **k: (_ for _ in ()).throw(ImportError()))

    with pytest.warns(UserWarning):
        backend = H.active_embedding_backend("auto")

    assert not isinstance(backend, H.OpenAIEmbeddingBackend)
    assert isinstance(backend, H.HashEmbeddingBackend)


def test_explicit_request_without_a_key_raises_rather_than_degrading(monkeypatch):
    """The module's contract: an explicit preference that cannot be built is
    re-raised. Degrading here would silently swap the benchmark's embedder."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        H.active_embedding_backend("openai")


def test_vectors_follow_the_index_not_the_arrival_order(monkeypatch):
    """A permuted batch would attach every vector to the wrong text.

    The API returns an explicit `index` per item; the backend must sort on it.
    This stub returns the batch REVERSED, so a backend trusting arrival order
    fails here and is invisible downstream in production.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    _stub_api(monkeypatch, dim=3, shuffle=True)
    backend = H.OpenAIEmbeddingBackend()

    got = backend.embed(["first", "second", "third"])

    assert got == [[0.0] * 3, [1.0] * 3, [2.0] * 3]


def test_long_input_is_batched_and_reassembled_in_order(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    calls: list = []
    _stub_api(monkeypatch, dim=2, record=calls)
    backend = H.OpenAIEmbeddingBackend()

    texts = [f"t{i}" for i in range(200)]
    got = backend.embed(texts)

    assert len(calls) == 3, f"expected 3 batches at _BATCH=96, got {len(calls)}"
    assert [len(c) for c in calls] == [96, 96, 8]
    assert len(got) == 200
    # Each batch restarts its index at 0, so reassembly must concatenate.
    assert got[0] == [0.0, 0.0] and got[96] == [0.0, 0.0]


def test_name_carries_the_model_so_a_swap_re_embeds(monkeypatch):
    """`vector_cache` keys on (backend_name, dim, sha256(text)). If two models
    shared a name, one would serve the other's vectors from cache."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    small = H.OpenAIEmbeddingBackend("text-embedding-3-small")
    large = H.OpenAIEmbeddingBackend("text-embedding-3-large")

    assert small.name != large.name
    assert small.name == "openai:text-embedding-3-small"
    assert (small.dim, large.dim) == (1536, 3072)


def test_it_counts_as_semantic(monkeypatch):
    """The candidate gate refuses semantic work on the hash stub; a hosted
    embedder must not be mistaken for one."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    backend = H.OpenAIEmbeddingBackend()
    assert H.backend_is_semantic(backend) is True
