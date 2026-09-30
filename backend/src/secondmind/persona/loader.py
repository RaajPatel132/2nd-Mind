"""The Aditi Rao seed file (S4.10): ``seeds/persona/aditi.yaml`` as a :class:`PersonaSeed`."""

import hashlib
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.persona.spec import WorkspaceSpec
from secondmind.persona.timing import local_instant

# Relative to the resources directory (RESOURCES_DIR): an installed package is not next to it.
SEED_FILE = Path("seeds") / "persona" / "aditi.yaml"
SEED_PATH = DEFAULT_RESOURCES_DIR / SEED_FILE


class SuggestedPrompts(BaseModel):
    """The two first prompts of the demo: one save and one recall (FR-13.5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    save: str
    recall: str


class PersonaSeed(WorkspaceSpec):
    """A persona: a workspace spec, the anchor its times count from, and its suggested prompts."""

    id: str
    version: int
    now: str
    timezone: str
    prompts: SuggestedPrompts

    @property
    def anchor(self) -> datetime:
        """The seed's "now" as a UTC instant. Everything in it is relative to this."""
        return local_instant(self.now, self.timezone)


def seed_hash(path: Path = SEED_PATH) -> str:
    """The hash of the seed file: a deploy reloads the template when it changes (S4.10)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_persona(path: Path = SEED_PATH) -> PersonaSeed:
    return PersonaSeed.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
