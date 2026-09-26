/**
 * One memory: what it is, and an editor (FR-10.3, S3.12). Saving runs the edit as its own turn
 * (source ui_edit) through the writer and policy, so it has a glass box and can be undone. A
 * delete is held for confirmation like any other.
 */
import { Undo2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  editItem,
  getItem,
  listEntities,
  type Entity,
  type ItemDetail,
  type ItemEdit,
  type Turn,
} from "../api/client";
import { formatInZone, formatValue } from "../lib/format";
import {
  Button,
  Chip,
  IconButton,
  Overline,
  Select,
  Sheet,
  Tag,
  TextField,
  type SheetMode,
} from "../ui";
import type { ItemField } from "./itemContext";

type Props = {
  itemId: string | null;
  focus?: ItemField;
  mode: SheetMode;
  timezone: string;
  onClose: () => void;
  onTurn: (turn: Turn) => void;
};

const KINDS = [
  "fact",
  "preference",
  "episode",
  "plan",
  "task",
  "intention",
  "resource",
  "note",
  "rule",
  "pattern",
];
const LAYERS = ["core", "quick", "archive"] as const;
const CLOCKS = ["occurred", "due", "valid"] as const;
const ROLES = ["about", "with", "for", "by", "at", "owner", "part_of"] as const;

export function ItemSheet({
  itemId,
  focus,
  mode,
  timezone,
  onClose,
  onTurn,
}: Props) {
  return (
    <Sheet
      open={itemId !== null}
      mode={mode === "docked" ? "overlay" : mode}
      onClose={onClose}
      label="Memory"
      testId="item-sheet"
    >
      {itemId && (
        <Body
          key={itemId}
          itemId={itemId}
          focus={focus}
          timezone={timezone}
          onClose={onClose}
          onTurn={onTurn}
        />
      )}
    </Sheet>
  );
}

function Body({
  itemId,
  focus,
  timezone,
  onClose,
  onTurn,
}: Omit<Props, "mode" | "itemId"> & { itemId: string }) {
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState("");
  const [subtype, setSubtype] = useState("");
  const [category, setCategory] = useState("");
  const [tags, setTags] = useState("");
  const [date, setDate] = useState("");
  const [clock, setClock] = useState<(typeof CLOCKS)[number]>("occurred");
  const [layer, setLayer] = useState("");
  const [state, setState] = useState("");
  const [entities, setEntities] = useState<Entity[]>([]);
  const [detach, setDetach] = useState<string[]>([]);
  const [linkTo, setLinkTo] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("about");
  const dateRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    getItem(itemId).then(
      (d) => {
        if (!live) return;
        setDetail(d);
        setKind(d.item.kind);
        setSubtype(d.item.subtype ?? "");
        setTags(d.item.tags.join(", "));
        setLayer(
          d.item.in_core ? "core" : d.item.in_quick ? "quick" : "archive",
        );
        setState(d.item.state);
        setClock(d.item.kind === "task" ? "due" : "occurred");
        listEntities(d.item.workspace_id).then(
          (list) => {
            if (live) setEntities(list);
          },
          () => undefined, // linking is optional; the rest of the editor still works
        );
      },
      (err: unknown) => {
        if (live)
          setError(err instanceof Error ? err.message : "Could not load it.");
      },
    );
    return () => {
      live = false;
    };
  }, [itemId]);

  useEffect(() => {
    if (detail && focus === "date") dateRef.current?.focus();
  }, [detail, focus]);

  async function run(body: ItemEdit) {
    setBusy(true);
    try {
      const turn = await editItem(itemId, body);
      onTurn(turn);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "That edit did not go through.",
      );
    } finally {
      setBusy(false);
    }
  }

  function changes(): ItemEdit {
    const item = detail?.item;
    const body: ItemEdit = { delete: false };
    if (!item) return body;
    if (kind !== item.kind) body.kind = kind;
    if (subtype.trim() !== (item.subtype ?? "")) body.subtype = subtype.trim();
    if (category.trim()) body.category = category.trim();
    const list = tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    if (list.join(",") !== item.tags.join(",")) body.tags = list;
    if (date.trim()) {
      body.date_expression = date.trim();
      body.date_clock = clock;
    }
    const current = item.in_core ? "core" : item.in_quick ? "quick" : "archive";
    if (layer !== current) body.layer = layer as (typeof LAYERS)[number];
    if (state.trim() && state.trim() !== item.state) body.state = state.trim();
    if (linkTo) body.attach = { entity_id: linkTo, role };
    if (detach.length > 0) body.detach = detach;
    return body;
  }

  const nameOf = (id: string) =>
    entities.find((e) => e.id === id)?.name ?? "someone";

  const item = detail?.item;
  const edit = changes();
  const changed = Object.keys(edit).length > 1;
  const when = item?.occurred_start ?? item?.due_at ?? null;

  return (
    <>
      <div className="flex items-start gap-3 border-b border-line px-5 pb-3.5 pt-4">
        <div className="min-w-0 flex-1">
          <Overline>Memory</Overline>
          <h2 className="m-0 mt-0.5 font-voice text-title-lg text-fg">
            {item?.title ?? "…"}
          </h2>
          {item && (
            <p className="m-0 flex flex-wrap items-center gap-2 font-machine text-mono-sm text-fg-3">
              {item.subtype ? `${item.kind} · ${item.subtype}` : item.kind} ·{" "}
              {item.state}
              <Tag>
                {item.in_core ? "core" : item.in_quick ? "quick" : "archive"}
              </Tag>
            </p>
          )}
        </div>
        <IconButton
          icon={X}
          label="Close"
          size="sm"
          onClick={onClose}
          tooltipSide="bottom"
        />
      </div>
      <div
        className="scroll-thin flex-1 overflow-y-auto px-5 pb-7 pt-4"
        data-testid="item-detail"
      >
        {error && (
          <p role="alert" className="m-0 mb-3 text-label text-bad">
            {error}
          </p>
        )}
        {item && (
          <>
            <p className="m-0 measure text-body text-fg">{item.text}</p>
            {when && (
              <p className="m-0 mt-1 font-machine text-mono-sm text-fg-3">
                {formatInZone(when, timezone)}
              </p>
            )}
            {Object.keys(item.attributes).length > 0 && (
              <p className="m-0 mt-1 font-machine text-mono-sm text-fg-3">
                {formatValue(item.attributes)}
              </p>
            )}
            <form
              className="mt-5 grid gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (changed) void run(edit);
              }}
              aria-label="Edit this memory"
            >
              <div className="grid gap-1">
                <span className="text-label text-fg-2">Kind</span>
                <Select
                  label="Kind"
                  triggerLabel={`Kind: ${kind}`}
                  value={kind}
                  align="left"
                  groups={[
                    {
                      label: "Kind",
                      options: KINDS.map((k) => ({ value: k, label: k })),
                    },
                  ]}
                  onChange={setKind}
                >
                  {kind}
                </Select>
              </div>
              <TextField
                label="Subtype"
                value={subtype}
                onChange={(e) => {
                  setSubtype(e.target.value);
                }}
                placeholder="watch, measurement…"
              />
              <TextField
                label="Category"
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                }}
                placeholder="entertainment/series"
                hint="Leave empty to keep it where it is."
              />
              <TextField
                label="Tags"
                value={tags}
                onChange={(e) => {
                  setTags(e.target.value);
                }}
                hint="Separated by commas."
              />
              <TextField
                ref={dateRef}
                label="Date"
                value={date}
                onChange={(e) => {
                  setDate(e.target.value);
                }}
                placeholder="Friday, 3 October, next Monday at 7pm"
                hint="Say it like you would; I work out the date."
                data-testid="edit-date"
              />
              <TextField
                type="date"
                label="Or pick a day"
                value={/^\d{4}-\d{2}-\d{2}$/.test(date) ? date : ""}
                onChange={(e) => {
                  setDate(e.target.value);
                }}
                data-testid="edit-date-picker"
              />
              <div className="flex flex-wrap gap-4">
                <div className="grid gap-1">
                  <span className="text-label text-fg-2">Date is when it</span>
                  <Select
                    label="Date clock"
                    triggerLabel={`Date is when it: ${clock}`}
                    value={clock}
                    align="left"
                    groups={[
                      {
                        label: "Clock",
                        options: CLOCKS.map((c) => ({ value: c, label: c })),
                      },
                    ]}
                    onChange={(v) => {
                      setClock(v as (typeof CLOCKS)[number]);
                    }}
                  >
                    {clock}
                  </Select>
                </div>
                <div className="grid gap-1">
                  <span className="text-label text-fg-2">Layer</span>
                  <Select
                    label="Layer"
                    triggerLabel={`Layer: ${layer}`}
                    value={layer}
                    align="left"
                    groups={[
                      {
                        label: "Layer",
                        options: LAYERS.map((l) => ({ value: l, label: l })),
                      },
                    ]}
                    onChange={setLayer}
                  >
                    {layer}
                  </Select>
                </div>
              </div>
              <fieldset className="m-0 grid gap-2 border-0 p-0">
                <legend className="p-0 text-label text-fg-2">Linked to</legend>
                {detail.entities.length === 0 && (
                  <p className="m-0 text-label font-normal text-fg-3">
                    No one yet.
                  </p>
                )}
                <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
                  {detail.entities.map((link) => {
                    const off = detach.includes(link.id);
                    return (
                      <li
                        key={link.id}
                        className="flex items-center gap-1"
                        data-testid="edit-link"
                      >
                        <Chip className={off ? "line-through" : undefined}>
                          {nameOf(link.entity_id)} · {link.role}
                        </Chip>
                        <IconButton
                          icon={off ? Undo2 : X}
                          label={`${off ? "Keep" : "Remove"} the link to ${nameOf(link.entity_id)}`}
                          size="sm"
                          onClick={() => {
                            setDetach((d) =>
                              off
                                ? d.filter((x) => x !== link.id)
                                : [...d, link.id],
                            );
                          }}
                        />
                      </li>
                    );
                  })}
                </ul>
                {entities.length > 0 && (
                  <div className="flex flex-wrap gap-4">
                    <div className="grid gap-1">
                      <span className="text-label text-fg-2">Link to</span>
                      <Select
                        label="Link to"
                        triggerLabel={`Link to: ${linkTo ? nameOf(linkTo) : "no one new"}`}
                        value={linkTo}
                        align="left"
                        groups={[
                          {
                            label: "People, places and things",
                            options: [
                              { value: "", label: "no one new" },
                              ...entities
                                .filter((e) => e.kind !== "self")
                                .map((e) => ({
                                  value: e.id,
                                  label: `${e.name} (${e.kind})`,
                                })),
                            ],
                          },
                        ]}
                        onChange={setLinkTo}
                      >
                        {linkTo ? nameOf(linkTo) : "no one new"}
                      </Select>
                    </div>
                    <div className="grid gap-1">
                      <span className="text-label text-fg-2">As</span>
                      <Select
                        label="Role"
                        triggerLabel={`Role: ${role}`}
                        value={role}
                        align="left"
                        groups={[
                          {
                            label: "Role",
                            options: ROLES.map((r) => ({ value: r, label: r })),
                          },
                        ]}
                        onChange={(v) => {
                          setRole(v as (typeof ROLES)[number]);
                        }}
                      >
                        {role}
                      </Select>
                    </div>
                  </div>
                )}
              </fieldset>
              <TextField
                label="State"
                value={state}
                onChange={(e) => {
                  setState(e.target.value);
                }}
                hint="open, done, scheduled, happened, wanted…"
              />
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!changed || busy}
                  data-testid="edit-save"
                >
                  {busy ? "Saving…" : "Save as a turn"}
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  disabled={busy}
                  onClick={() => void run({ delete: true })}
                  data-testid="edit-delete"
                >
                  Delete
                </Button>
              </div>
              <p className="m-0 text-label font-normal text-fg-3">
                Every edit is its own turn with a glass box, and can be undone.
                A delete waits for your OK.
              </p>
            </form>
          </>
        )}
      </div>
    </>
  );
}
