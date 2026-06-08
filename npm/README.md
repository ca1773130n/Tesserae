# @jokerized/tesserae

A thin **Node convenience wrapper** for [Tesserae](https://github.com/ca1773130n/Tesserae) — a context engine that compiles agent-ready knowledge from your project. The engine itself is a Python package; this wrapper just lets Node-centric users invoke it without leaving their toolchain.

```bash
npx @jokerized/tesserae --help
npx @jokerized/tesserae init --yes
npx @jokerized/tesserae compile
```

## Requirements

- **Python 3.10+** on your PATH. The wrapper runs the real Python CLI; it does not reimplement it.

## How it resolves the runner

On each invocation, first match wins:

1. `$TESSERAE_PYTHON -m tesserae` — explicit override (power users / CI).
2. `python3` / `python` on PATH that can `import tesserae` — uses the Tesserae you already installed.
3. `pipx run --spec tesserae==<this version> tesserae` — ephemeral, pinned to the matching Python release (first run installs it).
4. Otherwise it prints install guidance (`pipx install tesserae`) and exits non-zero.

Arguments, stdio, and the exit code are forwarded transparently, so `npx @jokerized/tesserae <anything>` behaves exactly like the real `tesserae` CLI.

## Why a wrapper instead of a native package?

Tesserae's engine (graph extraction, retrieval/PPR, the on-demand context compiler, MCP server) is Python. Reimplementing it in Node would fork the project; wrapping it keeps a single source of truth on [PyPI](https://pypi.org/project/tesserae/) and gives npm users a one-command entry point.

The npm version tracks the Python release it pins (`@jokerized/tesserae@X.Y.Z` → `tesserae==X.Y.Z`).

## License

MIT — see the [main repository](https://github.com/ca1773130n/Tesserae).
