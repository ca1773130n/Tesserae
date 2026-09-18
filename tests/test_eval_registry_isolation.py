"""A benchmark harness keeps its throwaway projects out of the developer's registry.

`tesserae init` and `tesserae compile` add their project to
`~/.tesserae/registry.json`. Each harness builds one project per query or
conversation, and one DeepScholar run left 63 of them there, named `0` to
`62`, for every federated query to search.
"""

from __future__ import annotations

import subprocess

import pytest

from evals.deepscholar import stage
from evals.growth import run as growth
from evals.lme_mab import adapter as lme_mab
from evals.locomo import adapter as locomo

_HELPERS = {
    "deepscholar": (stage, lambda work: stage.default_compile(work)),
    "growth": (growth, lambda work: growth.compile_slice(work, first=True)),
    "lme_mab": (lme_mab, lambda work: lme_mab._default_compile(work)),
    "locomo": (locomo, lambda work: locomo._default_compile(work)),
}


@pytest.mark.parametrize("harness", sorted(_HELPERS))
def test_harness_init_and_compile_register_inside_the_work_dir(harness, tmp_path, monkeypatch):
    module, compile_in = _HELPERS[harness]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv[3], kwargs.get("env") or {}))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    compile_in(tmp_path)

    assert [verb for verb, _ in calls] == ["init", "compile"]
    for verb, env in calls:
        assert env.get("TESSERAE_REGISTRY") == str(tmp_path / "registry.json"), verb
