"""The memory vocabulary (ADR-0018): kinds, lifecycle states, entities, roles, links, keys,
clocks and the epistemic scales. Shared by memory, ingestion, policy and the API.

Kinds are closed (adding one is a product change); subtypes, categories, predicates and
relations are open slugs normalised per workspace.
"""

from enum import StrEnum


class Kind(StrEnum):
    """What a memory is, defined by how it behaves over time."""

    FACT = "fact"
    PREFERENCE = "preference"
    EPISODE = "episode"
    PLAN = "plan"
    TASK = "task"
    INTENTION = "intention"
    RESOURCE = "resource"
    NOTE = "note"
    RULE = "rule"
    PATTERN = "pattern"


# Lifecycle states per kind; the first one is the initial state. The database enforces the same
# table with a CHECK constraint (see the 0002 migration).
KIND_STATES: dict[Kind, tuple[str, ...]] = {
    Kind.FACT: ("current", "superseded"),
    Kind.PREFERENCE: ("current", "superseded"),
    Kind.EPISODE: ("happened",),
    Kind.PLAN: ("scheduled", "happened", "moved", "cancelled"),
    Kind.TASK: ("open", "done", "dropped"),
    Kind.INTENTION: ("wanted", "active", "fulfilled", "dropped"),
    Kind.RESOURCE: ("saved", "consumed"),
    Kind.NOTE: ("current",),
    Kind.RULE: ("proposed", "active", "retired"),
    Kind.PATTERN: ("proposed", "confirmed", "retired"),
}

# Kinds that model extraction may propose; patterns are derived only (S9).
EXTRACTABLE_KINDS: tuple[Kind, ...] = tuple(k for k in Kind if k is not Kind.PATTERN)

# States that count as "current" for core memory and latest-value lookups.
LIVE_STATES: frozenset[str] = frozenset(
    {"current", "active", "confirmed", "wanted", "open", "scheduled", "saved"}
)


def initial_state(kind: Kind) -> str:
    return KIND_STATES[kind][0]


def valid_state(kind: Kind, state: str) -> bool:
    return state in KIND_STATES[kind]


class ResourceFormat(StrEnum):
    ARTICLE = "article"
    VIDEO = "video"
    PDF = "pdf"
    IMAGE = "image"
    LINK = "link"
    OTHER = "other"


class Source(StrEnum):
    USER_MESSAGE = "user_message"
    LINK = "link"
    FILE = "file"
    DERIVED = "derived"


class Trust(StrEnum):
    USER_STATED = "user_stated"
    CONTENT_DERIVED = "content_derived"


class ItemStatus(StrEnum):
    """About the record ("deleted"), not the world (that is ``state``)."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class Modality(StrEnum):
    ASSERTED = "asserted"
    PLANNED = "planned"
    HYPOTHETICAL = "hypothetical"
    REPORTED = "reported"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    SECRET = "secret"  # noqa: S105 - a label, not a credential


class TimePrecision(StrEnum):
    DATETIME = "datetime"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class TimeClock(StrEnum):
    """Which clock a time expression sets: when it happened, when it was true, when it is due,
    or when to remind. Recall also filters on when something was mentioned (said or saved)."""

    OCCURRED = "occurred"
    VALID = "valid"
    DUE = "due"
    TRIGGER = "trigger"
    MENTIONED = "mentioned"


class EntityKind(StrEnum):
    SELF = "self"
    PERSON = "person"
    PLACE = "place"
    ORG = "org"
    THING = "thing"
    WORK = "work"
    TOPIC = "topic"
    PROJECT = "project"
    LIST = "list"


class EntityRole(StrEnum):
    ABOUT = "about"
    WITH = "with"
    FOR = "for"
    BY = "by"
    AT = "at"
    OWNER = "owner"
    PART_OF = "part_of"


class LinkType(StrEnum):
    SUPERSEDES = "supersedes"
    FULFILS = "fulfils"
    PART_OF = "part_of"
    FOLLOWS = "follows"
    BECAUSE = "because"
    EVIDENCE_FOR = "evidence_for"
    DUPLICATE_OF = "duplicate_of"
    DERIVED_FROM = "derived_from"
    GIFT_FOR_EVENT = "gift_for_event"
    # The new row replaces an old one that was recorded by mistake (S3.12), not a change in
    # the world (that is ``supersedes``).
    CORRECTS = "corrects"


class KeyKind(StrEnum):
    TEXT = "text"
    VERBAL = "verbal"
    ALT = "alt"
    CUE = "cue"
    QUESTION = "question"
    CHANGE = "change"


class TriggerOn(StrEnum):
    TIME = "time"
    PERSON = "person"
    PLACE = "place"
    TOPIC = "topic"
    SITUATION = "situation"


class TriggerState(StrEnum):
    PENDING = "pending"
    FIRED = "fired"
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VocabKind(StrEnum):
    SUBTYPE = "subtype"
    PREDICATE = "predicate"
    RELATION = "relation"


class WriteOp(StrEnum):
    """Every change the memory writer can make; each one is a write-log row."""

    CREATE = "create"
    UPDATE = "update"
    LINK = "link"
    UNLINK = "unlink"
    DELETE = "delete"
    RESTORE = "restore"
    SUPERSEDE = "supersede"
    FULFIL = "fulfil"
    SET_STATE = "set_state"
    UPSERT_ENTITY = "upsert_entity"
    RELATE = "relate"
    UNRELATE = "unrelate"
    CORRECT = "correct"


class TargetType(StrEnum):
    ITEM = "item"
    ENTITY = "entity"
    TRIGGER = "trigger"
    LINK = "link"
    ITEM_ENTITY = "item_entity"
    RELATION = "relation"


class ReconcileDecision(StrEnum):
    NEW = "new"
    ADD_DETAIL = "add_detail"
    SUPERSEDE = "supersede"
    FULFIL = "fulfil"
    LINK = "link"
    NO_OP = "no_op"
