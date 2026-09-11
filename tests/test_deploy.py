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
from unittest.mock import patch

import pytest

_REAL_SUBPROCESS_RUN = subprocess.run

from tesserae.deploy import (
    DeployError,
    GitHubPagesDeployer,
    parse_remote_url,
)


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Make commits deterministic / non-interactive in case the runner has no
    # global git identity configured.
    env.setdefault("GIT_AUTHOR_NAME", "Tesserae Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Tesserae Test")
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
    _git("config", "user.name", "Tesserae Test", cwd=project)
    _git("config", "commit.gpgsign", "false", cwd=project)
    (project / "README.md").write_text("# project\n", encoding="utf-8")
    (project / ".gitignore").write_text(".tesserae/\n", encoding="utf-8")
    _git("add", "README.md", ".gitignore", cwd=project)
    _git("commit", "-m", "initial", cwd=project)
    _git("remote", "add", "origin", str(bare), cwd=project)
    _git("push", "-u", "origin", "main", cwd=project)
    return project, bare


def _make_site(project: Path) -> Path:
    site = project / ".tesserae" / "site"
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


def test_failed_push_still_removes_the_worktree_registration(tmp_path):
    """A push failure must not wedge the repo for every later deploy.

    Before the try/finally, a DeployError from the push skipped
    `git worktree remove`; TemporaryDirectory then deleted the directory and
    the repo kept a prunable registration, so the NEXT `git worktree add
    <branch>` failed with "already used by worktree at /tmp/tesserae-pages-...".
    """
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)
    # Seed the branch so the second deploy takes the `worktree add <branch>` path.
    GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")

    # Point origin somewhere that cannot be pushed to.
    _git("remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"), cwd=project)
    with pytest.raises(DeployError):
        GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")

    listed = _git("worktree", "list", "--porcelain", cwd=project).stdout
    registrations = [ln for ln in listed.splitlines() if ln.startswith("worktree ")]
    assert len(registrations) == 1, f"stale worktree registration left behind:\n{listed}"

    # And a deploy against a working remote succeeds afterwards.
    _git("remote", "set-url", "origin", str(bare), cwd=project)
    (site / "index.html").write_text("<html>after failure</html>", encoding="utf-8")
    result = GitHubPagesDeployer(project).deploy(site, branch="gh-pages", remote="origin")
    assert _remote_sha(bare, "refs/heads/gh-pages") == result["commit_sha"]


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
        deployer.deploy(project / ".tesserae" / "site")
    assert "compile" in str(exc_missing.value).lower()

    empty = project / ".tesserae" / "site"
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


def test_enable_pages_reports_unsupported_private_repo_plan(tmp_path):
    project, bare = _make_project_with_remote(tmp_path)
    site = _make_site(project)
    deployer = GitHubPagesDeployer(project)

    failed = subprocess.CompletedProcess(
        args=["gh"],
        returncode=1,
        stdout='{"message":"Your current plan does not support GitHub Pages for this repository.","status":"422"}',
        stderr="gh: Your current plan does not support GitHub Pages for this repository. (HTTP 422)",
    )
    def fake_run(args, *run_args, **run_kwargs):
        if args and args[0] == "gh":
            return failed
        return _REAL_SUBPROCESS_RUN(args, *run_args, **run_kwargs)

    with patch("tesserae.deploy.shutil.which", return_value="/usr/bin/gh"), patch(
        "tesserae.deploy.subprocess.run", side_effect=fake_run
    ):
        with pytest.raises(DeployError) as exc:
            deployer.deploy(site, enable_pages=True)

    message = str(exc.value).lower()
    assert "does not support pages" in message or "does not support" in message
    assert "gh-pages" in message
    # The site branch was still pushed; only Pages activation failed.
    files = _list_remote_tree(bare, "refs/heads/gh-pages")
    assert "index.html" in files


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
