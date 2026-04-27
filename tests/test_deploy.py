"""Integration tests for the GitHub Pages deployer.

These tests exercise the real ``git`` binary against bare repos created in
``tmp_path``. Nothing is pushed off-machine. The module is skipped if ``git``
is not on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_wiki.deploy import (
    DeployError,
    GitHubPagesDeployer,
    parse_remote_url,
)


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Make commits deterministic / non-interactive in case the runner has no
    # global git identity configured.
    env.setdefault("GIT_AUTHOR_NAME", "LLM-Wiki Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "LLM-Wiki Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _make_project_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    bare = tmp_path / "remote.git"
    _git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)

    project = tmp_path / "project"
    project.mkdir()
    _git("init", "-b", "main", cwd=project)
    _git("config", "user.email", "test@example.com", cwd=project)
    _git("config", "user.name", "LLM-Wiki Test", cwd=project)
    _git("config", "commit.gpgsign", "false", cwd=project)
    (project / "README.md").write_text("# project\n", encoding="utf-8")
    (project / ".gitignore").write_text(".llm-wiki/\n", encoding="utf-8")
    _git("add", "README.md", ".gitignore", cwd=project)
    _git("commit", "-m", "initial", cwd=project)
    _git("remote", "add", "origin", str(bare), cwd=project)
    _git("push", "-u", "origin", "main", cwd=project)
    return project, bare


def _make_site(project: Path) -> Path:
    site = project / ".llm-wiki" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html>hello</html>", encoding="utf-8")
    (site / "graph.json").write_text("{}", encoding="utf-8")
    (site / "assets").mkdir()
    (site / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    return site


def _list_remote_tree(bare: Path, ref: str) -> set[str]:
    """Return the set of file paths in the tree pointed at by ``ref`` in ``bare``."""

    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _remote_sha(bare: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(bare),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# -- deploy --------------------------------------------------------------


def test_deploy_creates_orphan_branch_with_site_files(tmp_path):
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)

    result = GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")

    assert result["branch"] == "gh-pages"
    assert result["files_uploaded"] >= 4  # 3 site files + .nojekyll
    assert result["commit_sha"]
    files = _list_remote_tree(bare, "refs/heads/gh-pages")
    assert "index.html" in files
    assert "graph.json" in files
    assert "assets/app.css" in files
    assert ".nojekyll" in files


def test_deploy_advances_remote_on_subsequent_run(tmp_path):
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)

    first = GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")
    sha_after_first = _remote_sha(bare, "refs/heads/gh-pages")
    assert sha_after_first == first["commit_sha"]

    # Modify the site so the second deploy is a real change.
    (site / "index.html").write_text("<html>hello v2</html>", encoding="utf-8")
    (site / "page2.html").write_text("<html>page2</html>", encoding="utf-8")

    second = GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")
    sha_after_second = _remote_sha(bare, "refs/heads/gh-pages")
    assert sha_after_second == second["commit_sha"]
    assert sha_after_second != sha_after_first
    files = _list_remote_tree(bare, "refs/heads/gh-pages")
    assert "page2.html" in files


def test_dry_run_does_not_push(tmp_path):
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)

    result = GitHubPagesDeployer(project).deploy(
        site, branch="gh-pages", remote="origin", dry_run=True
    )

    assert result["commit_sha"] is None
    assert _remote_sha(bare, "refs/heads/gh-pages") is None


def test_refuses_when_site_dir_missing_or_empty(tmp_path):
    project, _bare = _make_project_with_remote(tmp_path)
    deployer = GitHubPagesDeployer(project)

    with pytest.raises(DeployError) as exc_missing:
        deployer.deploy(project / ".llm-wiki" / "site")
    assert "compile" in str(exc_missing.value).lower()

    empty = project / ".llm-wiki" / "site"
    empty.mkdir(parents=True)
    with pytest.raises(DeployError) as exc_empty:
        deployer.deploy(empty)
    assert "compile" in str(exc_empty.value).lower()


def test_refuses_dirty_working_tree_without_force(tmp_path):
    project, _bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)
    (project / "uncommitted.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(DeployError) as exc:
        GitHubPagesDeployer(project).deploy(site)
    assert "dirty" in str(exc.value).lower()


def test_force_allows_dirty_working_tree(tmp_path):
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)
    (project / "uncommitted.txt").write_text("dirty", encoding="utf-8")

    result = GitHubPagesDeployer(project).deploy(site, force=True)
    assert result["commit_sha"]
    assert _remote_sha(bare, "refs/heads/gh-pages") == result["commit_sha"]


def test_force_push_refuses_protected_branch(tmp_path):
    project, _bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)

    with pytest.raises(DeployError) as exc:
        GitHubPagesDeployer(project).deploy(site, branch="main", force_push=True)
    assert "protected" in str(exc.value).lower()


# -- URL parsing ---------------------------------------------------------


def test_parse_remote_url_https():
    info = parse_remote_url("https://github.com/foo/bar.git")
    assert info.owner == "foo"
    assert info.repo == "bar"
    assert info.pages_url == "https://foo.github.io/bar/"


def test_parse_remote_url_ssh():
    info = parse_remote_url("git@github.com:foo/bar.git")
    assert info.owner == "foo"
    assert info.repo == "bar"
    assert info.pages_url == "https://foo.github.io/bar/"


def test_parse_remote_url_ssh_no_dot_git():
    info = parse_remote_url("git@github.com:foo/bar")
    assert info.pages_url == "https://foo.github.io/bar/"


def test_parse_remote_url_https_no_dot_git():
    info = parse_remote_url("https://github.com/foo/bar")
    assert info.pages_url == "https://foo.github.io/bar/"
