"""What the digest step may write: one item's own fields, and nothing else (S4.8).

The schema is closed (``extra="forbid"``): a model that returns an operation, an entity, a rule or
a trigger fails validation, and the digest falls back to the page's own metadata. The output is a
title, a summary, tags and cue situations for the one item being read.
"""

from pydantic import BaseModel, ConfigDict, Field


class DigestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200, description="A plain title for the page, under 12 words.")
    summary: str = Field(max_length=700, description="What it says, in 1 to 3 sentences.")
    tags: list[str] = Field(
        default_factory=list, max_length=6, description="Up to 6 short topic tags."
    )
    cues: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 situations where this page would help, phrased as the situation.",
    )


class DigestVars(BaseModel):
    """Variables of the digest prompt: how the page came to be saved. The page itself is the user
    message, in a data block."""

    model_config = ConfigDict(extra="forbid")

    source: str  # "a web page", "a video", "text the person pasted"
    person_said: str  # what the person said about it, quoted as data by the prompt
    cues_max: int = 3
