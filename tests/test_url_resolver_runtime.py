"""Unit tests for the persistent async runtime in url_resolver (CMP-04).

These tests exercise the runtime layer directly — no live Postgres, no
network, no sleeps, no pytest-asyncio. They prove that a single persistent
background event loop services many sync calls (instead of a fresh
``asyncio.run`` per call) and that ``shutdown_runtime`` tears it down
cleanly.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

import tesserae.graph_stores.url_resolver as url_resolver
from tesserae.graph_stores.url_resolver import (
    _PostgresGraphStoreSession,
    _runtime,
    shutdown_runtime,
)
from tesserae.ports.graph_store import GraphStore


@pytest.fixture(autouse=True)
def _clean_runtime():
    """Ensure each test starts and ends with no live runtime."""
    shutdown_runtime()
    yield
    shutdown_runtime()


def test_runtime_is_singleton():
    assert _runtime() is _runtime()


def test_runtime_runs_coroutines_on_one_loop():
    async def who():
        return (id(asyncio.get_running_loop()), threading.get_ident())

    results = [_runtime().run(who()) for _ in range(3)]

    loop_ids = {r[0] for r in results}
    thread_ids = {r[1] for r in results}
    # One persistent loop on one daemon thread serves every call.
    assert len(loop_ids) == 1, f"expected one loop, saw {loop_ids}"
    assert len(thread_ids) == 1, f"expected one thread, saw {thread_ids}"
    # And it is NOT the main thread (it's the background daemon loop).
    assert thread_ids != {threading.get_ident()}


def test_no_asyncio_run_in_source():
    source = Path(url_resolver.__file__).read_text()
    assert "asyncio.run(" not in source, (
        "CMP-04 regression: asyncio.run-per-call reintroduced in url_resolver.py"
    )


def test_shutdown_runtime_allows_recreate():
    first = _runtime()
    shutdown_runtime()
    second = _runtime()
    assert second is not first
    # Fresh instance is functional.
    assert _runtime().run(_echo(7)) == 7


async def _echo(value):
    return value


# --- Protocol conformance (codex M7) ------------------------------------ #
#
# Phase 4 added delete_node / delete_nodes_by_source to the runtime-checkable
# GraphStore protocol. The synchronous Postgres wrapper returned by
# resolve_graph_store("postgres://...") must keep satisfying that protocol.
# Constructing it needs no live DB (the async session is opened lazily,
# per-call), so isinstance against the runtime_checkable protocol is enough.


def test_postgres_session_satisfies_graphstore_protocol():
    session = _PostgresGraphStoreSession(owner_user_id=None)
    assert isinstance(session, GraphStore), (
        "M7 regression: _PostgresGraphStoreSession no longer satisfies the "
        "GraphStore protocol (missing delete_node / delete_nodes_by_source?)"
    )


def test_postgres_session_defines_delete_methods():
    for name in ("delete_node", "delete_nodes_by_source"):
        attr = getattr(_PostgresGraphStoreSession, name, None)
        assert callable(attr), f"_PostgresGraphStoreSession missing {name}"


def test_postgres_session_has_full_graphstore_surface():
    # Every method named on the GraphStore protocol must be present.
    for name in (
        "upsert_node",
        "upsert_edge",
        "get_node",
        "iterate_nodes",
        "query_subgraph",
        "find_canonical",
        "delete_node",
        "delete_nodes_by_source",
    ):
        assert hasattr(_PostgresGraphStoreSession, name), (
            f"_PostgresGraphStoreSession missing GraphStore method {name}"
        )


def test_delete_nodes_by_source_empty_is_noop_without_db():
    # Empty source set short-circuits before any async dispatch, so it must
    # return an empty set even with no live Postgres / runtime.
    session = _PostgresGraphStoreSession(owner_user_id=None)
    assert session.delete_nodes_by_source(set()) == set()
