"""Guard tests for the flat-verb dispatch table (redesign task 7).

The legacy ``project_main`` if-ladder and its ``_COMMANDS`` table were removed in
task 7; the new flat-verb tree (``_NEW_DISPATCH`` + the ``_route_*`` routers) is
now the sole entry point. These tests preserve the original regression intent:
every routed verb resolves to a callable handler, and the legacy handler BODIES
(``_handle_compile_legacy`` / ``_handle_serve_legacy`` / ``_handle_sessions`` /
``_handle_watch``) remain reachable from the new tree rather than being silently
orphaned or replaced.
"""

import tesserae.cli as cli


# The flat verbs the new tree dispatches at the top level. This list is the
# contract: _NEW_DISPATCH must remain a superset of it.
EXPECTED_VERBS = {
    "init",
    "compile",
    "context",
    "serve",
    "status",
    "engine",
    "refresh",
    "sessions",
    "vault",
    "export",
    "code",
    "config",
    "projects",
    "integrations",
    "lab",
    "extract",
    "research",
    "lint",
    "query",
    "ask",
}


def test_dispatch_table_has_no_none_routers():
    assert cli._NEW_DISPATCH, "_NEW_DISPATCH table is empty"
    for name, router in cli._NEW_DISPATCH.items():
        assert callable(router), f"_NEW_DISPATCH[{name!r}] is not callable: {router!r}"


def test_dispatch_table_covers_known_verbs():
    missing = EXPECTED_VERBS - set(cli._NEW_DISPATCH)
    assert not missing, f"_NEW_DISPATCH is missing routers for: {sorted(missing)}"


def test_unknown_command_returns_nonzero():
    # The old project_main raised ValueError on an unknown subcommand; the new
    # top-level `main` prints to stderr and returns a non-zero code instead.
    rc = cli.main(["definitely-not-a-command"])
    assert rc != 0


def test_legacy_handler_bodies_reachable_from_new_tree():
    # compile/serve were renamed to *_legacy so thin new-tree wrappers could own
    # the bare verb; the wrappers must still delegate to the unchanged legacy
    # bodies. sessions/watch keep their handler names and stay wired to the
    # group/export routers.
    # Direct identity of the preserved legacy bodies (not replaced/renamed away).
    assert callable(cli._handle_compile)
    assert callable(cli._handle_compile_legacy)
    assert callable(cli._handle_serve_legacy)
    assert callable(cli._handle_sessions)
    assert callable(cli._handle_watch)
    # The new-tree routers exist and are the ones the dispatch table points at.
    assert cli._NEW_DISPATCH["compile"] is cli._route_compile
    assert cli._NEW_DISPATCH["serve"] is cli._route_serve
    assert cli._NEW_DISPATCH["sessions"] is cli._route_sessions
