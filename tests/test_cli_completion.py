"""Shell completion, the did-you-mean, and a bare group name.

The generator reads the real argparse tree, so the test that matters is not
"does it emit text" but "does the emitted text load in the shell and offer the
verbs the CLI actually has".
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tesserae.cli import main
from tesserae.cli_completion import SHELLS, command_tree, render_completion, suggest
from tesserae.cli_tree import COMMAND_TREE


def test_the_tree_is_read_from_the_parsers_not_a_second_list():
    tree = command_tree()
    assert tree["vault"] and [n for n, _ in tree["vault"]] == sorted(
        ["sync", "prune", "export", "set-root", "sync-all"]
    )
    # A command that takes flags instead of a verb has no subcommands, even
    # though the root help prints it under GROUPS.
    assert tree["setup"] == []
    assert tree["extract"] == []
    assert "compile" in tree and tree["compile"] == []


def test_group_blurbs_list_the_verbs_that_exist():
    """The drift this generator exists to stop, pinned as a ratchet.

    `sessions` advertised "import | discover | list" long after it grew
    prune-internal and chunk-backfill, so the root help sent readers looking
    for two commands it never mentioned.
    """
    tree = command_tree()
    blurbs = {name: b for _s, items in COMMAND_TREE for name, b in items}
    checked = 0
    for name, subs in tree.items():
        blurb = blurbs.get(name, "")
        if not subs or " — " not in blurb:
            continue
        listed = blurb.split(" — ")[0]
        if not re.fullmatch(r"[a-z0-9|\- ]+", listed):
            continue  # a prose blurb, not a verb list
        assert {v.strip() for v in listed.split("|")} == {n for n, _ in subs}, (
            f"`tesserae {name}` blurb lists {listed!r} but the parser has "
            f"{sorted(n for n, _ in subs)}"
        )
        checked += 1
    assert checked >= 8, "the ratchet stopped covering the groups it was written for"


@pytest.mark.parametrize("shell", SHELLS)
def test_every_script_mentions_every_command(shell):
    script = render_completion(shell)
    tree = command_tree()
    for name in tree:
        assert name in script, f"{shell} completion omits `{name}`"
    for sub, _help in tree["vault"]:
        assert sub in script


def test_an_unsupported_shell_is_refused():
    with pytest.raises(ValueError):
        render_completion("powershell")


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_generated_script_parses_in_the_real_shell(shell, tmp_path):
    binary = shutil.which(shell)
    if binary is None:
        pytest.skip(f"{shell} not installed")
    path = tmp_path / f"completion.{shell}"
    path.write_text(render_completion(shell), encoding="utf-8")
    done = subprocess.run([binary, "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, f"{shell} rejected the script:\n{done.stderr}"


def test_bash_completion_actually_offers_the_subcommands(tmp_path):
    """Syntax-valid is not the same as working. Drive the function for real."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not installed")
    path = tmp_path / "completion.bash"
    path.write_text(render_completion("bash"), encoding="utf-8")
    script = (
        f'source "{path}"; COMP_WORDS=(tesserae vault ""); COMP_CWORD=2; '
        '_tesserae; echo "${COMPREPLY[*]}"'
    )
    done = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert set(done.stdout.split()) == {"sync", "sync-all", "set-root", "export", "prune"}


def test_completion_command_prints_a_script(capsys):
    assert main(["completion", "bash"]) == 0
    assert "complete -F _tesserae tesserae" in capsys.readouterr().out


# ------------------------------------------------------------ did-you-mean


def test_a_typo_is_pointed_at_the_command_it_almost_spelled():
    assert "compile" in suggest("complie", command_tree())


def test_a_subcommand_typed_without_its_group_names_both_groups():
    """`sync` is real, and ambiguous: it lives under vault AND code."""
    assert set(suggest("sync", command_tree())) >= {"vault sync", "code sync"}


def test_nonsense_suggests_nothing():
    assert suggest("zzzzzzzzqqq", command_tree()) == []


def test_unknown_command_message_carries_the_suggestion(capsys):
    assert main(["complie"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'complie'" in err
    assert "did you mean `tesserae compile`?" in err


# ------------------------------------------------------------ bare group


def test_a_bare_group_prints_its_help_instead_of_an_argparse_error(capsys):
    """`tesserae vault` is a request to see what vault does."""
    assert main(["vault"]) == 2
    out = capsys.readouterr().out
    assert "sync-all" in out and "set-root" in out
    assert "the following arguments are required" not in out


def test_a_flag_taking_command_under_groups_is_not_intercepted():
    """`setup` prints under GROUPS but takes flags: a bare call must run it."""
    with pytest.raises(SystemExit) as exc:
        main(["setup", "--help"])
    assert exc.value.code == 0
