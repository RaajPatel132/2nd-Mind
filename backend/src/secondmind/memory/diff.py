"""Memory-diff entries, built only from the write-log rows an op produced (S2.2, S2.11).

Keys never appear: they are derived data. Link rows that belong to a supersede, a fulfil or a
new item's entity roles are folded into that entry instead of shown on their own.
"""

import uuid
from typing import Any

from secondmind.core import DiffEntry, DiffOp, FieldChange, Layer, TargetType, WriteOp
from secondmind.memory.ops import (
    AttachEntity,
    CreateItem,
    DeleteEntity,
    DeleteItem,
    DetachEntity,
    FulfilIntention,
    LinkItems,
    Op,
    RelateEntities,
    RestoreItem,
    SupersedeItem,
    UnlinkItems,
    UnrelateEntities,
    UpsertEntity,
)
from secondmind.memory.records import WriteLogRecord

# Fields worth showing as before -> after (in this order); the rest is noise for the panel.
SHOWN_ITEM_FIELDS = (
    "status",
    "state",
    "title",
    "text",
    "kind",
    "subtype",
    "valid_from",
    "valid_to",
    "occurred_start",
    "occurred_end",
    "due_at",
    "rrule",
    "time_precision",
    "predicate",
    "value",
    "tags",
    "attributes",
    "category_id",
    "sensitivity",
    "modality",
    "in_core",
    "in_quick",
    "quick_reason",
)
SHOWN_ENTITY_FIELDS = ("status", "name", "kind", "aliases", "labels", "is_key", "attributes")
SHOWN_TRIGGER_FIELDS = ("state", "fires_at", "spec")


def field_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None, fields: tuple[str, ...]
) -> list[FieldChange]:
    b, a = before or {}, after or {}
    return [
        FieldChange(field=name, before=b.get(name), after=a.get(name))
        for name in fields
        if b.get(name) != a.get(name)
    ]


def _layer_of(snapshot: dict[str, Any] | None, fallback: Layer) -> Layer:
    if not snapshot:
        return fallback
    if snapshot.get("in_core"):
        return Layer.CORE
    if snapshot.get("in_quick"):
        return Layer.QUICK
    if "in_core" in snapshot:
        return Layer.ARCHIVE
    return fallback


def _uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))


def diff_entries(  # noqa: PLR0911, PLR0912
    op: Op, label: WriteOp, rows: list[WriteLogRecord]
) -> list[DiffEntry]:
    if not rows:
        return []
    main = rows[0]
    layer = _layer_of(main.after or main.before, main.layer)
    title = op.title or _title(main)

    def entry(kind: DiffOp, changes: list[FieldChange] | None = None, **extra: Any) -> DiffEntry:
        return DiffEntry(
            op=kind,
            layer=extra.pop("layer", layer),
            item_id=extra.pop(
                "item_id", main.target_id if main.target_type is TargetType.ITEM else None
            ),
            entity_id=extra.pop("entity_id", None),
            title=title,
            changes=changes or [],
            reconcile=op.reconcile,
            **extra,
        )

    match op:
        case CreateItem():
            changes = field_changes(None, main.after, ("kind", "subtype", "state"))
            after = main.after or {}
            for name in ("occurred_start", "due_at", "valid_from", "rrule"):
                if after.get(name):
                    changes.append(FieldChange(field=name, after=after[name]))
            for row in rows[1:]:
                if row.target_type is TargetType.TRIGGER and row.after:
                    changes.append(FieldChange(field="reminder", after=row.after.get("fires_at")))
            return [entry("added", changes)]
        case SupersedeItem():
            return [
                entry("superseded", field_changes(main.before, main.after, ("state", "valid_to")))
            ]
        case FulfilIntention():
            return [entry("fulfilled", field_changes(main.before, main.after, ("state",)))]
        case DeleteItem():
            return [entry("removed", field_changes(main.before, main.after, ("status",)))]
        case RestoreItem():
            return [
                entry(
                    "added",
                    field_changes(main.before, main.after, ("status",)),
                    reason="restored",
                )
            ]
        case UpsertEntity():
            entity_id = main.target_id
            if main.before is None:
                return [
                    entry(
                        "added",
                        field_changes(None, main.after, ("kind", "name", "labels")),
                        entity_id=entity_id,
                        item_id=None,
                        layer=Layer.ARCHIVE,
                    )
                ]
            return [
                entry(
                    "updated",
                    field_changes(main.before, main.after, SHOWN_ENTITY_FIELDS),
                    entity_id=entity_id,
                    item_id=None,
                    layer=Layer.ARCHIVE,
                )
            ]
        case DeleteEntity():
            return [
                entry(
                    "removed",
                    field_changes(main.before, main.after, ("status",)),
                    entity_id=main.target_id,
                    item_id=None,
                    layer=Layer.ARCHIVE,
                )
            ]
        case RelateEntities() | UnrelateEntities():
            snapshot = main.after or main.before or {}
            return [
                entry(
                    "added" if main.after else "removed",
                    [FieldChange(field="relation", before=None, after=snapshot.get("relation"))],
                    entity_id=_uuid(snapshot.get("src_entity_id")),
                    item_id=_uuid(snapshot.get("evidence_item_id")),
                    layer=Layer.ARCHIVE,
                )
            ]
        case LinkItems() | UnlinkItems() | AttachEntity() | DetachEntity():
            snapshot = main.after or main.before or {}
            item_id = _uuid(snapshot.get("src_item_id") or snapshot.get("item_id"))
            what = snapshot.get("link_type") or snapshot.get("role")
            return [
                entry(
                    "updated",
                    [
                        FieldChange(
                            field="link",
                            before=what if main.before else None,
                            after=what if main.after else None,
                        )
                    ],
                    item_id=item_id,
                    layer=Layer.ARCHIVE,
                )
            ]
    if main.target_type is TargetType.TRIGGER:
        return [
            entry(
                "updated",
                field_changes(main.before, main.after, SHOWN_TRIGGER_FIELDS),
                item_id=_uuid((main.after or {}).get("item_id")),
                layer=Layer.QUICK,
            )
        ]
    if label is WriteOp.DELETE:
        return [entry("removed", field_changes(main.before, main.after, ("status",)))]
    return [entry("updated", field_changes(main.before, main.after, SHOWN_ITEM_FIELDS))]


def _title(row: WriteLogRecord) -> str:
    snapshot = row.after or row.before or {}
    return str(snapshot.get("title") or snapshot.get("name") or row.target_type.value)
