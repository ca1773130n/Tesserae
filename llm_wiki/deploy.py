"""GitHub Pages deployment for compiled LLM-Wiki sites.

Uses the well-known ``git worktree`` pattern: spin up a temporary worktree
checked out against the orphan ``gh-pages`` branch, copy the compiled site
into it, commit, push, then remove the worktree. The user's main working
tree is never touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROTECTED_BRANCHES = {"main", "master"}


class DeployError(RuntimeError):
    """Raised when a deploy precondition or git operation fails."""


@dataclass(frozen=True)
class RemoteInfo:
    owner: str
    repo: str
    pages_url: str


class GitHubPagesDeployer:
    """Push a static site directory to a project's ``gh-pages`` branch."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    # -- public ---------------------------------------------------------

    def deploy(
        self,
        site_dir: Path,
        *,
        branch: str = "gh-pages",
        remote: str = "origin",
        commit_message: Optional[str] = None,
        dry_run: bool = False,
        force: bool = False,
        force_push: bool = False,
        cname: Optional[str] = None,
        enable_pages: bool = False,
    ) -> dict:
        site_dir = Path(site_dir)
        if not site_dir.exists() or not site_dir.is_dir():
            raise DeployError(
                f"Site directory does not exist: {site_dir}. "
                "Run `llm_wiki project compile` (or `project deploy --build`) first."
            )
        if not any(site_dir.rglob("*")):
            raise DeployError(
                f"Site directory is empty: {site_dir}. "
                "Run `llm_wiki project compile` (or `project deploy --build`) first."
            )

        if not (self.project_root / ".git").exists():
            raise DeployError(
                f"Not a git repository: {self.project_root}. "
                "GitHub Pages deploy requires the project to be a git repo with an origin remote."
            )

        if force_push and branch in PROTECTED_BRANCHES:
            raise DeployError(
                f"Refusing to force-push to a protected branch: {branch!r}. "
                "Use a non-default branch name (default: gh-pages)."
            )

        if not force and self._is_dirty():
            raise DeployError(
                "Working tree is dirty. Commit or stash your changes, or pass --force to deploy anyway. "
                f"(checked: {self.project_root})"
            )

        remote_url = self._remote_url(remote)
        info = parse_remote_url(remote_url)

        message = commit_message or f"Deploy LLM-Wiki site ({branch})"

        # Stage in a temp clone via git worktree to avoid touching main tree.
        with tempfile.TemporaryDirectory(prefix="llm-wiki-pages-") as tmp:
            worktree = Path(tmp) / "worktree"
            commit_sha, files_uploaded = self._stage_and_commit(
                site_dir=site_dir,
                worktree=worktree,
                branch=branch,
                remote=remote,
                message=message,
                cname=cname,
            )

            push_argv = ["push"]
            if force_push:
                push_argv.append("--force")
            else:
                push_argv.append("-u")
            push_argv.extend([remote, branch])

            if dry_run:
                returned_sha: Optional[str] = None
                print("Dry run: would run git " + " ".join(push_argv))
            else:
                self._git(push_argv, cwd=worktree)
                returned_sha = commit_sha

            # Always remove the worktree we created in tmp.
            self._git(
                ["worktree", "remove", "--force", str(worktree)],
                cwd=self.project_root,
                check=False,
            )

        if enable_pages and not dry_run:
            self._enable_pages(info)

        return {
            "branch": branch,
            "remote": remote,
            "commit_sha": returned_sha,
            "files_uploaded": files_uploaded,
            "site_url": info.pages_url,
        }

    # -- internals ------------------------------------------------------

    def _is_dirty(self) -> bool:
        result = self._git(
            ["status", "--porcelain"],
            cwd=self.project_root,
            capture=True,
        )
        return bool(result.stdout.strip())

    def _remote_url(self, remote: str) -> str:
        result = self._git(
            ["remote", "get-url", remote],
            cwd=self.project_root,
            capture=True,
        )
        url = result.stdout.strip()
        if not url:
            raise DeployError(f"Remote {remote!r} has no URL configured.")
        return url

    def _remote_branch_exists(self, remote: str, branch: str) -> bool:
        result = self._git(
            ["ls-remote", "--heads", remote, branch],
            cwd=self.project_root,
            capture=True,
            check=False,
        )
        return bool(result.stdout.strip())

    def _local_branch_exists(self, branch: str) -> bool:
        result = self._git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.project_root,
            capture=True,
            check=False,
        )
        return result.returncode == 0

    def _stage_and_commit(
        self,
        *,
        site_dir: Path,
        worktree: Path,
        branch: str,
        remote: str,
        message: str,
        cname: Optional[str],
    ) -> tuple[str, int]:
        worktree.parent.mkdir(parents=True, exist_ok=True)

        if self._remote_branch_exists(remote, branch):
            # Make sure local has up-to-date refs without touching main tree.
            self._git(["fetch", remote, branch], cwd=self.project_root, check=False)
            if self._local_branch_exists(branch):
                self._git(
                    ["worktree", "add", str(worktree), branch],
                    cwd=self.project_root,
                )
            else:
                self._git(
                    [
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(worktree),
                        f"{remote}/{branch}",
                    ],
                    cwd=self.project_root,
                )
            # Wipe contents (keep .git pointer file) before copying.
            self._clear_worktree(worktree)
        else:
            # Create a detached worktree first, then make it an orphan.
            # We use a temporary detached HEAD via --detach.
            self._git(
                ["worktree", "add", "--detach", str(worktree)],
                cwd=self.project_root,
            )
            self._clear_worktree(worktree)
            self._git(["checkout", "--orphan", branch], cwd=worktree)
            # New orphan inherits the index from prior HEAD; clear it.
            self._git(["rm", "-rf", "--cached", "--ignore-unmatch", "."], cwd=worktree, check=False)

        files_uploaded = self._copy_site(site_dir, worktree)
        # Ensure .nojekyll always exists.
        nojekyll = worktree / ".nojekyll"
        if not nojekyll.exists():
            nojekyll.write_bytes(b"")
            files_uploaded += 1
        if cname:
            (worktree / "CNAME").write_text(cname.strip() + "\n", encoding="utf-8")
            files_uploaded += 1

        self._git(["add", "-A"], cwd=worktree)

        # If nothing changed, force an empty commit so users see the deploy.
        diff = self._git(
            ["diff", "--cached", "--name-only"],
            cwd=worktree,
            capture=True,
        )
        commit_argv = ["commit", "-m", message]
        if not diff.stdout.strip():
            commit_argv.append("--allow-empty")
        self._git(commit_argv, cwd=worktree)
        sha = self._git(["rev-parse", "HEAD"], cwd=worktree, capture=True).stdout.strip()
        return sha, files_uploaded

    def _clear_worktree(self, worktree: Path) -> None:
        for entry in worktree.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def _copy_site(self, site_dir: Path, worktree: Path) -> int:
        count = 0
        for src in site_dir.rglob("*"):
            rel = src.relative_to(site_dir)
            dst = worktree / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                count += 1
        return count

    def _enable_pages(self, info: RemoteInfo) -> None:
        if shutil.which("gh") is None:
            print(
                "gh CLI not found. Enable Pages manually at "
                f"https://github.com/{info.owner}/{info.repo}/settings/pages"
            )
            return
        argv = [
            "gh",
            "api",
            "-X",
            "POST",
            f"/repos/{info.owner}/{info.repo}/pages",
            "-f",
            "source[branch]=gh-pages",
            "-f",
            "source[path]=/",
        ]
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Enabled GitHub Pages: {info.pages_url}")
            return
        # 422 == already configured; treat as success.
        body = (result.stdout or "") + (result.stderr or "")
        if "422" in body or "already" in body.lower():
            print(f"GitHub Pages already enabled: {info.pages_url}")
            return
        print(
            "Could not enable GitHub Pages via gh CLI. "
            f"Configure manually at https://github.com/{info.owner}/{info.repo}/settings/pages"
        )

    def _git(
        self,
        args: list[str],
        *,
        cwd: Path,
        capture: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # Avoid stalling on auth prompts in non-interactive runs.
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if check and result.returncode != 0:
            cmd = " ".join(["git", *args])
            raise DeployError(
                f"git command failed ({result.returncode}): {cmd}\nstderr: {result.stderr.strip()}"
            )
        if capture:
            return result
        return result


_SSH_RE = re.compile(r"^git@([^:]+):([^/]+)/(.+?)(?:\.git)?/?$")
_HTTPS_RE = re.compile(r"^https?://[^/]+/([^/]+)/(.+?)(?:\.git)?/?$")


def parse_remote_url(url: str) -> RemoteInfo:
    """Parse a git remote URL into ``(owner, repo, pages_url)``.

    Supports SSH (``git@github.com:owner/repo.git``), HTTPS
    (``https://github.com/owner/repo.git``), and the no-``.git`` variants.
    Local filesystem URLs (used by tests against bare repos) fall back to a
    synthetic ``local`` owner so the deploy still produces a sensible
    ``site_url``.
    """

    url = url.strip()
    m = _SSH_RE.match(url)
    if m:
        owner, repo = m.group(2), m.group(3)
    else:
        m = _HTTPS_RE.match(url)
        if m:
            owner, repo = m.group(1), m.group(2)
        else:
            # Local path or other transport: synthesize sensible defaults.
            tail = url.rstrip("/").split("/")[-1] or "repo"
            if tail.endswith(".git"):
                tail = tail[:-4]
            owner, repo = "local", tail or "repo"
    if repo.endswith(".git"):
        repo = repo[:-4]
    pages_url = f"https://{owner}.github.io/{repo}/"
    return RemoteInfo(owner=owner, repo=repo, pages_url=pages_url)
