/**
 * Upcoming (§7.7, S3.14): plans with routine occurrences, reminders, tasks due and dated
 * intentions, grouped by day in the workspace timezone, then undated open tasks. Done, Snooze and
 * Edit each run as a turn through the writer and policy, so each has a glass box and an undo.
 */
import { useCallback, useEffect, useState } from "react";
import {
  editItem,
  getUpcoming,
  type ItemEdit,
  type Turn,
  type Upcoming,
  type UpcomingEntry,
} from "../api/client";
import { formatDay, plural } from "../lib/format";
import {
  Button,
  Chip,
  Overline,
  Select,
  Skeleton,
  Tag,
  TextField,
  useToast,
} from "../ui";
import { useItemActions } from "./itemContext";

type Props = {
  workspaceId: string;
  timezone: string;
  onTurn: (turn: Turn) => void;
};

const DONE: Record<string, string> = {
  task: "done",
  plan: "happened",
  intention: "fulfilled",
};

function localParts(iso: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) =>
    parts.find((p) => p.type === type)?.value ?? "00";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    time: `${get("hour")}:${get("minute")}`,
  };
}

/** The entry's time moved by whole days, as the resolver reads it ("2026-10-12 19:00"). */
function moved(entry: UpcomingEntry, days: number, timeZone: string): string {
  const next = new Date(Date.parse(entry.at) + days * 86_400_000).toISOString();
  const { date, time } = localParts(next, timeZone);
  return time === "00:00" ? date : `${date} ${time}`;
}

function timeOf(entry: UpcomingEntry, timeZone: string): string {
  const { time } = localParts(entry.at, timeZone);
  return time === "00:00" ? "all day" : time;
}

const VIA: Record<string, string> = {
  occurred: "",
  routine: "routine",
  due: "due",
  trigger: "reminder",
};

export function UpcomingPage({ workspaceId, timezone, onTurn }: Props) {
  const [data, setData] = useState<Upcoming | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();

  const load = useCallback(() => {
    getUpcoming(workspaceId).then(setData, (err: unknown) => {
      setError(
        err instanceof Error
          ? err.message
          : "Could not load what is coming up.",
      );
    });
  }, [workspaceId]);
  useEffect(load, [load]);

  const act = useCallback(
    async (key: string, itemId: string, body: ItemEdit, done: string) => {
      setBusy(key);
      try {
        onTurn(await editItem(itemId, body));
        toast(`${done}. Undo it from its turn in the chat.`);
        load();
      } catch (err) {
        toast(err instanceof Error ? err.message : "That did not go through.");
      } finally {
        setBusy(null);
      }
    },
    [onTurn, toast, load],
  );

  return (
    <section
      className="mx-auto w-full max-w-180"
      aria-labelledby="upcoming-title"
      data-testid="upcoming"
    >
      <Overline>Upcoming</Overline>
      <h1
        id="upcoming-title"
        className="m-0 mt-1 font-voice text-title-lg text-fg"
      >
        What's ahead
      </h1>
      {data && (
        <p className="m-0 mt-1 text-label font-normal text-fg-3">
          The next{" "}
          {plural(
            Math.round(
              (Date.parse(data.end) - Date.parse(data.start)) / 86_400_000,
            ),
            "day",
          )}
          , in {data.timezone}.
        </p>
      )}
      {error && (
        <p role="alert" className="m-0 mt-4 text-label text-bad">
          {error}
        </p>
      )}
      {!data && !error && (
        <div className="mt-6 grid gap-3">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      )}
      {data && data.days.length === 0 && data.undated.length === 0 && (
        <p className="m-0 mt-6 font-voice text-title text-fg-2">
          Nothing coming up.
        </p>
      )}
      {data?.days.map((d) => (
        <section key={d.day} className="mt-7" aria-label={formatDay(d.day)}>
          <h2 className="m-0 mb-2 text-title text-fg">{formatDay(d.day)}</h2>
          <ul className="m-0 grid list-none gap-2 p-0">
            {d.entries.map((e) => (
              <Entry
                key={`${e.item_id}-${e.at}-${e.via}`}
                entry={e}
                timezone={timezone}
                busy={busy}
                act={act}
              />
            ))}
          </ul>
        </section>
      ))}
      {data && data.undated.length > 0 && (
        <section className="mt-7" aria-label="No date yet">
          <h2 className="m-0 mb-2 text-title text-fg">No date yet</h2>
          <ul className="m-0 grid list-none gap-2 p-0">
            {data.undated.map((t) => (
              <li
                key={t.item_id}
                className="flex flex-wrap items-center gap-3 rounded-sm bg-surface p-3 ring-1 ring-inset ring-line"
                data-testid="upcoming-undated"
              >
                <span className="min-w-0 flex-1 text-body text-fg">
                  {t.title}
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void act(
                      `${t.item_id}-done`,
                      t.item_id,
                      { delete: false, state: "done" },
                      `Marked “${t.title}” done`,
                    )
                  }
                >
                  Done
                </Button>
                <EditButton itemId={t.item_id} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

type Act = (
  key: string,
  itemId: string,
  body: ItemEdit,
  done: string,
) => Promise<void>;

function Entry({
  entry,
  timezone,
  busy,
  act,
}: {
  entry: UpcomingEntry;
  timezone: string;
  busy: string | null;
  act: Act;
}) {
  const [picking, setPicking] = useState(false);
  const [date, setDate] = useState("");
  const key = `${entry.item_id}-${entry.at}`;
  const clock = entry.via === "due" ? "due" : "occurred";
  const canSnooze = entry.via === "occurred" || entry.via === "due";
  const snooze = (expression: string, label: string) =>
    void act(
      key,
      entry.item_id,
      { delete: false, date_expression: expression, date_clock: clock },
      `Moved “${entry.title}” to ${label}`,
    );

  return (
    <li
      className="rounded-sm bg-surface p-3 ring-1 ring-inset ring-line"
      data-testid="upcoming-entry"
      data-via={entry.via}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="w-14 shrink-0 font-machine text-mono-sm text-fg-3 tnum">
          {timeOf(entry, timezone)}
        </span>
        <span className="min-w-0 flex-1 text-body text-fg">{entry.title}</span>
        {VIA[entry.via] && <Chip kind="mono">{VIA[entry.via]}</Chip>}
        <Tag>{entry.kind}</Tag>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {DONE[entry.kind] && !entry.routine && (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy !== null}
            onClick={() =>
              void act(
                key,
                entry.item_id,
                { delete: false, state: DONE[entry.kind] },
                `Marked “${entry.title}” ${DONE[entry.kind] ?? "done"}`,
              )
            }
            data-testid="upcoming-done"
          >
            Done
          </Button>
        )}
        {canSnooze && (
          <Select
            label="Snooze"
            value=""
            align="left"
            groups={[
              {
                label: "Snooze",
                options: [
                  { value: "1", label: "1 day" },
                  { value: "7", label: "1 week" },
                  { value: "pick", label: "Choose a date" },
                ],
              },
            ]}
            onChange={(v) => {
              if (v === "pick") setPicking(true);
              else
                snooze(
                  moved(entry, Number(v), timezone),
                  v === "1" ? "a day later" : "a week later",
                );
            }}
          >
            Snooze
          </Select>
        )}
        <EditButton itemId={entry.item_id} />
      </div>
      {picking && (
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (date.trim()) snooze(date.trim(), date.trim());
            setPicking(false);
          }}
        >
          <TextField
            label="New date"
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
            }}
            placeholder="Friday, 3 November at 7pm"
            className="min-w-0 flex-1"
            autoFocus
          />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            disabled={!date.trim() || busy !== null}
          >
            Move it
          </Button>
        </form>
      )}
    </li>
  );
}

function EditButton({ itemId }: { itemId: string }) {
  const items = useItemActions();
  if (!items) return null;
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        items.open(itemId);
      }}
    >
      Edit
    </Button>
  );
}
