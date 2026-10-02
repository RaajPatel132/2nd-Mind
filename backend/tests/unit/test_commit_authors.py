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


def _check_range(
    cwd: Path, first: str, allowed: str = "dev@example.com"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), first, "HEAD"],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], "COMMIT_AUTHORS": allowed},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def branch(tmp_path: Path) -> Path:
    """main with one commit, and a branch whose first commit is by someone else and whose last is
    by the owner: the case a one-commit fallback misses."""
    if not shutil.which("git") or not shutil.which("bash"):
        pytest.skip("needs git and bash")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "switch", "-q", "-c", "work")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "cloud", author="Claude <c@example.org>")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "mine")
    return tmp_path


def test_a_range_that_git_cannot_read_is_not_reported_as_fine(tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("needs git")
    result = _check_range(tmp_path, "base")  # not a repository at all
    assert result.returncode != 0
    assert "every commit" not in result.stdout


def test_an_unknown_start_falls_back_to_the_merge_base_with_main_not_the_last_commit(
    branch: Path,
) -> None:
    result = _check_range(branch, "0123456789abcdef0123456789abcdef01234567")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Claude" in result.stderr  # the earlier commit of the push, not just the last
    assert "every commit" not in result.stdout


def test_an_empty_start_is_treated_as_unknown_too(branch: Path) -> None:
    result = _check_range(branch, "")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Claude" in result.stderr


def test_with_no_merge_base_to_fall_back_on_it_refuses_to_pass(tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("needs git")
    _git(tmp_path, "init", "-q", "-b", "work")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "only")
    result = _check_range(tmp_path, "0123456789abcdef0123456789abcdef01234567")
    assert result.returncode == 2
    assert "every commit" not in result.stdout


def test_a_known_start_still_checks_exactly_its_range(branch: Path) -> None:
    ok = _check_range(branch, "main")
    assert ok.returncode == 1  # the cloud commit is in main..HEAD
    only_mine = _check_range(branch, "HEAD~1")
    assert only_mine.returncode == 0, only_mine.stderr
