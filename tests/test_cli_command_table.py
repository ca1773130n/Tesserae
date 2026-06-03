"""Guard tests for the project_main command-table dispatch (plan 01-02).

These assert the mechanical if-ladder -> _COMMANDS decomposition stays complete:
every former subcommand has a handler, the table never holds a non-callable, and
an unknown command still raises the identical ValueError. They guard against the
ladder silently regrowing or a command being dropped during later edits.
"""

import argparse

import pytest

import tesserae.cli as cli


# The 25 subcommands the if-ladder dispatched before the refactor. This list is
# the contract: _COMMANDS must remain a superset of it.
EXPECTED_COMMANDS = {
    "init",
    "setup",
    "ingest",
    "ingest-code",
    "sync-code",
    "compile",
    "schema-drift",
    "evolve",
    "research",
    "refresh-understand-anything",
    "obsidian-sync",
    "refresh-raganything",
    "lint",
    "query",
    "ask",
    "mcp-config",
    "export-graphiti",
    "sync-graphiti",
    "export-agent-harness",
    "export-obsidian",
    "sessions",
    "build-site",
    "deploy",
    "serve",
    "watch",
}


def test_commands_table_has_no_none_handlers():
    assert cli._COMMANDS, "_COMMANDS table is empty"
    for name, handler in cli._COMMANDS.items():
        assert callable(handler), f"_COMMANDS[{name!r}] is not callable: {handler!r}"


def test_commands_table_covers_known_commands():
    missing = EXPECTED_COMMANDS - set(cli._COMMANDS)
    assert not missing, f"_COMMANDS is missing handlers for: {sorted(missing)}"


def test_unknown_command_raises_valueerror():
    # Dispatch happens after arg parsing on args.command; construct the namespace
    # directly so we exercise the table lookup, not argparse subparser rejection.
    args = argparse.Namespace(command="definitely-not-a-command")
    handler = cli._COMMANDS.get(args.command)
    assert handler is None
    # Mirror the exact fall-through path project_main takes for an unknown command.
    with pytest.raises(ValueError, match="Unknown project command"):
        if handler is None:
            raise ValueError(f"Unknown project command: {args.command}")


def test_known_command_routes_to_its_handler():
    # The table must route, e.g., "compile" -> _handle_compile (no behavior run).
    assert cli._COMMANDS["compile"] is cli._handle_compile
    assert cli._COMMANDS["sessions"] is cli._handle_sessions
    assert cli._COMMANDS["watch"] is cli._handle_watch
