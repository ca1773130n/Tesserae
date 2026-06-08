#!/usr/bin/env node
"use strict";

/**
 * Node convenience wrapper for Tesserae (a Python CLI).
 *
 * Resolution order for the runner, first match wins:
 *   1. $TESSERAE_PYTHON -m tesserae          (explicit override; power users / tests)
 *   2. <python> -m tesserae  for python3/python on PATH that can `import tesserae`
 *      (fast path — uses the Tesserae the user already installed)
 *   3. pipx run --spec tesserae==<this version> tesserae
 *      (ephemeral, pinned to the matching Python release)
 *   4. otherwise print install guidance and exit 1.
 *
 * Args, stdio, exit code, and SIGINT/SIGTERM are forwarded transparently so
 * `npx @jokerized/tesserae <anything>` behaves exactly like the real CLI.
 */

const { spawnSync } = require("child_process");
const path = require("path");

const PKG_VERSION = require(path.join(__dirname, "..", "package.json")).version;
const ARGS = process.argv.slice(2);

function canImportTesserae(python) {
  const probe = spawnSync(python, ["-c", "import tesserae"], {
    stdio: "ignore",
  });
  return probe.status === 0;
}

function run(cmd, cmdArgs) {
  const res = spawnSync(cmd, cmdArgs, { stdio: "inherit" });
  if (res.error) {
    // ENOENT etc. — signal "runner unavailable" to the caller.
    return null;
  }
  // Mirror signal-terminated children as the conventional 128+signal code.
  if (res.signal) {
    return 128;
  }
  return res.status === null ? 1 : res.status;
}

function resolveAndRun() {
  // 1. Explicit override.
  const override = process.env.TESSERAE_PYTHON;
  if (override) {
    const code = run(override, ["-m", "tesserae", ...ARGS]);
    if (code !== null) return code;
  }

  // 2. An already-installed Tesserae on a discoverable Python.
  for (const python of ["python3", "python"]) {
    if (canImportTesserae(python)) {
      const code = run(python, ["-m", "tesserae", ...ARGS]);
      if (code !== null) return code;
    }
  }

  // 3. Ephemeral, version-pinned, via pipx.
  const pipxProbe = spawnSync("pipx", ["--version"], { stdio: "ignore" });
  if (pipxProbe.status === 0) {
    process.stderr.write(
      `[tesserae] no local install found — running tesserae==${PKG_VERSION} via pipx (first run installs it)\n`
    );
    const code = run(
      "pipx",
      ["run", "--spec", `tesserae==${PKG_VERSION}`, "tesserae", ...ARGS]
    );
    if (code !== null) return code;
  }

  // 4. Nothing worked — guide the user.
  process.stderr.write(
    [
      "Tesserae requires Python 3.10+ and the `tesserae` package.",
      "",
      "Install it one of these ways, then re-run:",
      "  pipx install tesserae        # recommended (isolated)",
      "  pip install tesserae         # into the current environment",
      "",
      "Or set TESSERAE_PYTHON to a Python that already has it:",
      "  TESSERAE_PYTHON=/path/to/python npx @jokerized/tesserae ...",
      "",
    ].join("\n")
  );
  return 1;
}

process.exit(resolveAndRun());
