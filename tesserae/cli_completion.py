"""Shell completion for `tesserae`, generated from the real parser tree.

Thirty-odd commands across thirteen groups is more than anyone keeps in their
head, and the alternative to completion is `--help`, a scroll, and a retype.

The command list is INTROSPECTED from the argparse parsers rather than written
down here. A second copy would drift the way ``COMMAND_TREE``'s group blurbs
already had — its ``sessions`` line advertised three subcommands out of five —
and a completion that offers a verb the CLI does not have is worse than none.

No dependency: argcomplete would want a runtime import in the hot path and a
shell hook that execs Python on every TAB. These scripts are static text, so
completion costs one function call in the shell.
"""

from __future__ import annotations

import argparse
from typing import Dict, List, Tuple

SHELLS = ("bash", "zsh", "fish")


def _subcommands(parser: argparse.ArgumentParser) -> List[Tuple[str, str]]:
    """``(name, help)`` for a parser's subcommands, or empty if it has none.

    ``setup`` and ``extract`` are listed under GROUPS in the root help but take
    flags rather than a verb, so "is this a group?" is decided here, by what the
    parser actually has, and never by which section of the help it prints in.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            blurbs = {c.dest: (c.help or "") for c in action._choices_actions}
            return sorted((name, blurbs.get(name, "")) for name in action.choices)
    return []


def command_tree() -> Dict[str, List[Tuple[str, str]]]:
    """Every command -> its subcommands (empty list for a leaf command).

    Importing ``cli`` here rather than at module scope keeps this module out of
    the CLI's own import cycle: ``cli`` imports it only when `completion` runs.
    """
    from . import cli
    from .cli_tree import COMMAND_TREE

    tree: Dict[str, List[Tuple[str, str]]] = {}
    for _section, items in COMMAND_TREE:
        for name, _blurb in items:
            builder = getattr(cli, f"_build_{name.replace('-', '_')}_parser", None)
            tree[name] = _subcommands(builder()) if builder is not None else []
    return tree


def command_blurbs() -> Dict[str, str]:
    """Top-level command -> its one-line description from the root help."""
    from .cli_tree import COMMAND_TREE

    return {name: blurb for _s, items in COMMAND_TREE for name, blurb in items}


def _clean(text: str) -> str:
    """One line, no quotes or colons — the separators every shell format uses."""
    return text.replace("'", "").replace('"', "").replace(":", " -").split("\n")[0].strip()


def _render_bash(tree, blurbs) -> str:
    groups = {k: v for k, v in tree.items() if v}
    cases = "\n".join(
        f"        {name}) COMPREPLY=($(compgen -W '{' '.join(n for n, _ in subs)}' -- \"$cur\")); return ;;"
        for name, subs in sorted(groups.items())
    )
    return f"""# tesserae bash completion. Install:
#   tesserae completion bash > /usr/local/etc/bash_completion.d/tesserae
# or append to ~/.bashrc:  eval "$(tesserae completion bash)"
_tesserae() {{
    local cur prev
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W '{' '.join(sorted(tree))} --help --version' -- "$cur"))
        return
    fi
    if [ "$COMP_CWORD" -eq 2 ]; then
        case "$prev" in
{cases}
        esac
    fi
    # Anything deeper: fall back to filenames, which is what most flags take.
    COMPREPLY=($(compgen -f -- "$cur"))
}}
complete -F _tesserae tesserae
"""


def _render_zsh(tree, blurbs) -> str:
    top = " \\\n        ".join(
        f"'{name}:{_clean(blurbs.get(name, ''))}'" for name in sorted(tree)
    )
    blocks = []
    for name, subs in sorted(tree.items()):
        if not subs:
            continue
        listed = " \\\n                ".join(f"'{s}:{_clean(h)}'" for s, h in subs)
        blocks.append(
            f"            {name})\n"
            f"                _describe -t {name.replace('-', '_')}_cmds 'tesserae {name} command' \\\n"
            f"                    ({listed})\n"
            f"                ;;"
        )
    joined = "\n".join(blocks)
    return f"""#compdef tesserae
# tesserae zsh completion. Install:
#   tesserae completion zsh > "${{fpath[1]}}/_tesserae"    # then restart the shell
# or append to ~/.zshrc:  eval "$(tesserae completion zsh)"
_tesserae() {{
    local -a top
    top=(
        {top}
    )
    if (( CURRENT == 2 )); then
        _describe -t commands 'tesserae command' top
        return
    fi
    if (( CURRENT == 3 )); then
        case "${{words[2]}}" in
{joined}
            *) _files ;;
        esac
        return
    fi
    _files
}}
compdef _tesserae tesserae
"""


def _render_fish(tree, blurbs) -> str:
    lines = [
        "# tesserae fish completion. Install:",
        "#   tesserae completion fish > ~/.config/fish/completions/tesserae.fish",
        "",
        "complete -c tesserae -f",
    ]
    for name in sorted(tree):
        lines.append(
            f"complete -c tesserae -n '__fish_use_subcommand' -a '{name}' "
            f"-d '{_clean(blurbs.get(name, ''))}'"
        )
    for name, subs in sorted(tree.items()):
        for sub, help_text in subs:
            lines.append(
                f"complete -c tesserae -n '__fish_seen_subcommand_from {name}' "
                f"-a '{sub}' -d '{_clean(help_text)}'"
            )
    return "\n".join(lines) + "\n"


def render_completion(shell: str) -> str:
    """The completion script for ``shell``. Raises ValueError for anything else."""
    if shell not in SHELLS:
        raise ValueError(f"unsupported shell {shell!r} — choose from {', '.join(SHELLS)}")
    tree, blurbs = command_tree(), command_blurbs()
    return {"bash": _render_bash, "zsh": _render_zsh, "fish": _render_fish}[shell](tree, blurbs)


def suggest(token: str, tree: Dict[str, List[Tuple[str, str]]]) -> List[str]:
    """Spellings close to ``token``, as full command lines, best first.

    Two kinds of near-miss, and the second is the one a flat list misses: a
    typo of a top-level command (`complie`), and a SUBCOMMAND typed without its
    group (`sync`, which is real but lives under both `vault` and `code`).
    """
    import difflib

    hits = list(difflib.get_close_matches(token, list(tree), n=3, cutoff=0.6))
    for name, subs in sorted(tree.items()):
        for sub, _help in subs:
            if sub == token or difflib.get_close_matches(token, [sub], n=1, cutoff=0.8):
                candidate = f"{name} {sub}"
                if candidate not in hits:
                    hits.append(candidate)
    return hits[:4]
