"""
Minimal git-aware context for the backend agent.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class GitCommit:
    hash: str
    author: str
    date: str
    message: str


@dataclass(slots=True)
class GitContext:
    available: bool
    diff_head: str
    staged_diff: str
    recent_commits: list[GitCommit]


class GitContextProvider:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def get_context(self) -> GitContext:
        if not self._is_git_repo():
            return GitContext(False, "", "", [])

        diff_head = self._run_git(["diff", "HEAD", "--stat"], timeout=15)[:8000]
        staged_diff = self._run_git(["diff", "--staged", "--stat"], timeout=15)[:4000]
        log_raw = self._run_git(
            ["log", "--oneline", "--pretty=format:%H\t%an\t%ad\t%s", "--date=short", "-12"],
            timeout=15,
        )
        commits: list[GitCommit] = []
        for line in log_raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            commit_hash, author, date, *message_parts = parts
            commits.append(GitCommit(
                hash=commit_hash[:8],
                author=author,
                date=date,
                message=" ".join(message_parts).strip(),
            ))
        return GitContext(True, diff_head, staged_diff, commits)

    def format_context(self, ctx: GitContext) -> str:
        if not ctx.available:
            return ""
        sections: list[str] = []
        if ctx.recent_commits:
            sections.append("\n".join([
                "RECENT GIT COMMITS:",
                *[f"  {c.hash} {c.date} {c.author}: {c.message}" for c in ctx.recent_commits[:8]],
            ]))
        if ctx.diff_head.strip():
            sections.append(f"GIT DIFF HEAD (stat):\n{ctx.diff_head.strip()}")
        if ctx.staged_diff.strip():
            sections.append(f"STAGED CHANGES (stat):\n{ctx.staged_diff.strip()}")
        return "\n\n".join(sections)

    def _is_git_repo(self) -> bool:
        git_dir = os.path.join(self.workspace_root, ".git")
        if os.path.exists(git_dir):
            return True
        probe = self._run_git(["rev-parse", "--is-inside-work-tree"], timeout=5)
        return probe.strip() == "true"

    def _run_git(self, args: list[str], timeout: int) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return ""
        return (completed.stdout or "").strip()
