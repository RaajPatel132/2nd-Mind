"""Prompt registry (FR-19.1): prompts are versioned files, never inline strings.

Layout: ``prompts/<prompt_id>/v<N>.md``, each starting with YAML frontmatter::

    ---
    id: answer
    version: 1
    step: answer
    description: What this prompt is for.
    variables: [now, timezone]
    ---
    Body with {{ now }} placeholders.

A released version is immutable. ``prompts/prompts.lock.json`` pins the sha256 of every released
file; a test fails if a locked file changes. To change a prompt, add ``v<N+1>.md`` and run
``python -m secondmind.config.prompt_lock``, which appends new versions and refuses to
rewrite existing ones.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from secondmind.core import ConfigError

LOCK_FILE = "prompts.lock.json"
_FILE_RE = re.compile(r"^v(?P<version>[1-9][0-9]*)\.md$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_FRONTMATTER_RE = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


class PromptMeta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    step: str
    description: str = Field(min_length=1)
    variables: list[str] = []


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    meta: PromptMeta
    body: str
    sha256: str
    path: Path

    @property
    def ref(self) -> str:
        return f"{self.meta.id}@{self.meta.version}"


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    ref: str
    text: str


class PromptRegistry:
    def __init__(self, templates: dict[str, PromptTemplate]) -> None:
        self._templates = templates

    @classmethod
    def load(cls, directory: Path) -> "PromptRegistry":
        if not directory.is_dir():
            raise ConfigError(f"prompts directory not found: {directory}")
        templates: dict[str, PromptTemplate] = {}
        for path in sorted(directory.glob("*/v*.md")):
            template = _load_template(path)
            templates[template.ref] = template
        return cls(templates)

    def get(self, ref: str) -> PromptTemplate:
        try:
            return self._templates[ref]
        except KeyError as exc:
            raise ConfigError(f"unknown prompt {ref!r}") from exc

    def render(self, ref: str, variables: BaseModel) -> RenderedPrompt:
        """Render ``ref`` with a typed variables model whose fields match the prompt exactly."""
        template = self.get(ref)
        values = variables.model_dump(mode="json")
        expected = set(template.meta.variables)
        if set(values) != expected:
            raise ConfigError(
                f"prompt {ref} expects variables {sorted(expected)}, got {sorted(values)}"
            )
        text = _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), template.body)
        return RenderedPrompt(ref=ref, text=text)

    def hashes(self) -> dict[str, str]:
        return {ref: t.sha256 for ref, t in sorted(self._templates.items())}

    def refs(self) -> list[str]:
        return sorted(self._templates)


def _load_template(path: Path) -> PromptTemplate:
    match = _FILE_RE.match(path.name)
    if not match:
        raise ConfigError(f"{path}: prompt files are named v<N>.md")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    fm = _FRONTMATTER_RE.match(text)
    if not fm:
        raise ConfigError(f"{path}: missing '---' frontmatter block")
    try:
        meta = PromptMeta.model_validate(yaml.safe_load(fm.group("meta")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{path}: invalid frontmatter:\n{exc}") from exc
    if meta.id != path.parent.name:
        raise ConfigError(f"{path}: frontmatter id {meta.id!r} != directory {path.parent.name!r}")
    if meta.version != int(match.group("version")):
        raise ConfigError(f"{path}: frontmatter version {meta.version} != file name")
    body = fm.group("body").strip() + "\n"
    used = set(_PLACEHOLDER_RE.findall(body))
    declared = set(meta.variables)
    if used != declared:
        raise ConfigError(
            f"{path}: placeholders {sorted(used)} do not match declared variables "
            f"{sorted(declared)}"
        )
    return PromptTemplate(meta=meta, body=body, sha256=hashlib.sha256(raw).hexdigest(), path=path)


def read_lock(directory: Path) -> dict[str, str]:
    path = directory / LOCK_FILE
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ConfigError(f"{path}: expected an object of ref -> sha256")
    return {str(k): str(v) for k, v in data.items()}


def lock_violations(registry: PromptRegistry, lock: dict[str, str]) -> list[str]:
    """Problems between prompt files and the lock: edited, missing, or unreleased versions."""
    problems: list[str] = []
    hashes = registry.hashes()
    for ref, digest in sorted(lock.items()):
        if ref not in hashes:
            problems.append(f"{ref} is released (locked) but its file is missing")
        elif hashes[ref] != digest:
            problems.append(f"{ref} was edited after release; add a new version instead")
    problems.extend(
        f"{ref} is not in {LOCK_FILE}; run `python -m secondmind.config.prompt_lock`"
        for ref in hashes
        if ref not in lock
    )
    return problems


def update_lock(directory: Path) -> list[str]:
    """Append unreleased versions to the lock. Never rewrites an existing entry."""
    registry = PromptRegistry.load(directory)
    lock = read_lock(directory)
    edited = [p for p in lock_violations(registry, lock) if "edited" in p or "missing" in p]
    if edited:
        raise ConfigError("refusing to update the prompt lock:\n  " + "\n  ".join(edited))
    added = [ref for ref in registry.refs() if ref not in lock]
    lock.update({ref: registry.hashes()[ref] for ref in added})
    (directory / LOCK_FILE).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return added
