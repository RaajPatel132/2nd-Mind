"""S1.9: prompts are versioned files; released versions are immutable."""

import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel

from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry, lock_violations, read_lock
from secondmind.config.prompts import update_lock
from secondmind.core import ConfigError

PROMPTS = DEFAULT_RESOURCES_DIR / "prompts"


class AnswerVars(BaseModel):
    now: str
    timezone: str


def test_released_prompt_files_match_the_lock() -> None:
    """Fails if a released prompt version was edited, deleted, or a new one was not locked."""
    problems = lock_violations(PromptRegistry.load(PROMPTS), read_lock(PROMPTS))
    assert problems == []


def test_render_substitutes_typed_variables() -> None:
    registry = PromptRegistry.load(PROMPTS)
    rendered = registry.render("answer@1", AnswerVars(now="2026-09-24T10:00", timezone="UTC"))
    assert rendered.ref == "answer@1"
    assert "2026-09-24T10:00 (UTC)" in rendered.text
    assert "{{" not in rendered.text


def test_render_rejects_wrong_variables() -> None:
    class Wrong(BaseModel):
        now: str

    with pytest.raises(ConfigError, match="expects variables"):
        PromptRegistry.load(PROMPTS).render("answer@1", Wrong(now="x"))


def test_editing_a_released_version_is_detected(tmp_path: Path) -> None:
    copy = tmp_path / "prompts"
    shutil.copytree(PROMPTS, copy)
    target = copy / "answer" / "v1.md"
    target.write_text(target.read_text() + "\nOne more sentence.\n")
    problems = lock_violations(PromptRegistry.load(copy), read_lock(copy))
    assert problems == ["answer@1 was edited after release; add a new version instead"]
    with pytest.raises(ConfigError, match="refusing to update the prompt lock"):
        update_lock(copy)


def test_new_version_is_appended_to_the_lock(tmp_path: Path) -> None:
    copy = tmp_path / "prompts"
    shutil.copytree(PROMPTS, copy)
    v3 = (copy / "answer" / "v3.md").read_text()
    (copy / "answer" / "v4.md").write_text(v3.replace("version: 3", "version: 4"))
    assert lock_violations(PromptRegistry.load(copy), read_lock(copy)) == [
        "answer@4 is not in prompts.lock.json; run `python -m secondmind.config.prompt_lock`"
    ]
    assert update_lock(copy) == ["answer@4"]
    assert read_lock(copy)["answer@1"] == read_lock(PROMPTS)["answer@1"]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("no frontmatter", "missing '---' frontmatter"),
        (
            "---\nid: other\nversion: 1\nstep: answer\ndescription: d\n---\nbody\n",
            "frontmatter id 'other' != directory",
        ),
        (
            "---\nid: bad\nversion: 1\nstep: answer\ndescription: d\n---\nHi {{ name }}\n",
            "placeholders",
        ),
    ],
)
def test_malformed_prompt_files_fail_loading(tmp_path: Path, content: str, error: str) -> None:
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "v1.md").write_text(content)
    with pytest.raises(ConfigError, match=error):
        PromptRegistry.load(tmp_path)
