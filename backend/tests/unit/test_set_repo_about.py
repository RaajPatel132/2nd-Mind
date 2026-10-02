"""scripts/set-repo-about.sh fills in the GitHub About box. It sends nothing unless asked, never
needs the network to be tested (curl is a stub here), and keeps to GitHub's own limits."""

import json
import os
import re
import stat
import subprocess
from pathlib import Path

from secondmind.config import DEFAULT_RESOURCES_DIR

SCRIPT = DEFAULT_RESOURCES_DIR.parent / "scripts" / "set-repo-about.sh"


def run(
    tmp_path: Path, *args: str, token: str | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run the script with a curl that records its arguments instead of calling anything."""
    stub = tmp_path / "bin" / "curl"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{tmp_path}/calls"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    environment = {"PATH": f"{stub.parent}:{os.environ['PATH']}", **env}
    if token:
        environment["GITHUB_TOKEN"] = token
    return subprocess.run(  # noqa: S603
        [str(SCRIPT), *args],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def payloads(out: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    about = json.loads(re.search(r"PATCH \S+\s+(\{.*\})", out).group(1))  # type: ignore[union-attr]
    topics = json.loads(re.search(r"topics\s+(\{.*\})", out).group(1))  # type: ignore[union-attr]
    return about, topics


def test_by_default_it_prints_what_it_would_send_and_sends_nothing(tmp_path: Path) -> None:
    result = run(tmp_path, token="t0ken")
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout
    assert calls(tmp_path) == []


def test_apply_without_a_token_stops_before_any_request(tmp_path: Path) -> None:
    result = run(tmp_path, "--apply")
    assert result.returncode != 0
    assert "GITHUB_TOKEN" in result.stderr
    assert calls(tmp_path) == []


def test_apply_makes_the_three_calls_to_the_named_repository_with_the_token(
    tmp_path: Path,
) -> None:
    result = run(tmp_path, "--apply", token="t0ken", REPO="someone/else")
    assert result.returncode == 0, result.stderr
    sent = calls(tmp_path)
    assert len(sent) == 3
    assert "-X PATCH https://api.github.com/repos/someone/else " in sent[0] + " "
    assert "-X PUT https://api.github.com/repos/someone/else/topics" in sent[1]
    assert "private-vulnerability-reporting" in sent[2]
    assert all("Authorization: Bearer t0ken" in line for line in sent)


def test_the_website_is_sent_only_when_one_is_given(tmp_path: Path) -> None:
    without, _ = payloads(run(tmp_path).stdout)
    assert "homepage" not in without
    with_site, _ = payloads(run(tmp_path, HOMEPAGE="https://example.test").stdout)
    assert with_site["homepage"] == "https://example.test"


def test_the_text_keeps_to_githubs_limits_and_to_the_projects_own_words(tmp_path: Path) -> None:
    about, topics = payloads(run(tmp_path).stdout)
    assert len(about["description"]) <= 350
    names = topics["names"]
    assert 1 <= len(names) <= 20
    for name in names:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", name), name
    assert len(set(names)) == len(names)
    lowered = about["description"].lower()
    for word in ("recruit", "hiring", "résumé", "resume", "portfolio"):
        assert word not in lowered


def test_the_script_is_valid_bash() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0  # noqa: S603, S607
