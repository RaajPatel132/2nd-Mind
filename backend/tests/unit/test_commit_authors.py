"""R.9: CI fails when a pushed commit has an author outside the allowlist."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from secondmind.config import DEFAULT_RESOURCES_DIR

SCRIPT = DEFAULT_RESOURCES_DIR.parent / "scripts" / "check-commit-authors.sh"


def _git(repo: Path, *args: str, author: str = "Dev <dev@example.com>") -> str:
    name, email = author.removesuffix(">").split(" <")
    env = {
        "PATH": os.environ["PATH"],
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "HOME": str(repo),
    }
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _check(repo: Path, allowed: str | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"]}
    if allowed is not None:
        env["COMMIT_AUTHORS"] = allowed
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), "base", "HEAD"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if not shutil.which("git") or not shutil.which("bash"):
        pytest.skip("needs git and bash")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "tag", "base")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "mine")
    return tmp_path


def test_commits_by_allowed_authors_pass(repo: Path) -> None:
    result = _check(repo, "other@example.com, DEV@example.com")
    assert result.returncode == 0, result.stderr


def test_a_commit_by_anyone_else_fails_ci(repo: Path) -> None:
    cloud = "Claude <noreply@anthropic.com>"
    _git(repo, "commit", "-q", "--allow-empty", "-m", "cloud", author=cloud)
    result = _check(repo, "dev@example.com")
    assert result.returncode == 1
    assert cloud in result.stderr


def test_an_unset_allowlist_fails_closed(repo: Path) -> None:
    result = _check(repo, None)
    assert result.returncode == 2
    assert "COMMIT_AUTHORS" in result.stderr
