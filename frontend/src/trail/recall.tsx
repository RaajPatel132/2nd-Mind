/**
 * Recall's steps (plan, search, rank, triggers) and the Retrieval panel (FR-9, S3.13): labels,
 * the plain layer and the technical layer, all from the stored `retrieval` and `citations`
 * events, so a reloaded turn looks the same.
 */
import type { ReactNode } from "react";
import type { RetrievalCandidate, SubQueryTrace } from "../api/client";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { formatDay, formatMs, plural, truncate } from "../lib/format";
import { Chip, Dot, Tag, cx } from "../ui";
import { KV, MTable, ModelLines, Section } from "./parts";
import { candidates, fired, subs } from "./recallLabels";
import type { StepContext } from "./types";

type P = { ctx: StepContext };

function Plain({ children }: { children: ReactNode }) {
  return (
    <p className="m-0 measure text-pretty text-body text-fg">{children}</p>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return <p className="m-0 text-label font-normal text-fg-3">{children}</p>;
}

const SHAPE_WORDS: Record<string, string> = {
  exact: "one fact",
  list: "a list",
  latest: "the current value",
  history: "how it changed",
  time_window: "what falls in a time",
  order: "what came before or after",
  count: "a count",
  set: "a set of things",
  entity: "a person or thing",
  semantic: "something by meaning",
  why: "a reason",
  situational: "advice for a situation",
  conversation: "something said in the chat",
};

// ------------------------------------------------------------------ plan

export function PlanPlain({ ctx }: P) {
  const r = ctx.facts.retrieval;
  if (!r)
    return (
      <Plain>
        I worked out what kind of question this is before looking anything up.
      </Plain>
    );
  const words = r.sub_queries.map((s) => SHAPE_WORDS[s.shape] ?? s.shape);
  return (
    <Plain>
      {r.sub_queries.length > 1
        ? `You asked ${String(r.sub_queries.length)} things: `
        : "You asked about "}
      {words.join("; ")}.
      {r.plan_source === "fallback"
        ? " I searched by meaning, because the plan came back unusable."
        : ""}
    </Plain>
  );
}

export function PlanTech({ ctx }: P) {
  const r = ctx.facts.retrieval;
  return (
    <>
      {r && (
        <>
          <KV
            rows={[
              ["source", r.plan_source],
              ...(r.plan_note
                ? ([["note", truncate(r.plan_note, 160)]] as [string, string][])
                : []),
            ]}
          />
          {r.sub_queries.map((s) => (
            <PlanPart key={s.index} sub={s} />
          ))}
        </>
      )}
      <ModelLines calls={ctx.calls} />
    </>
  );
}

function PlanPart({ sub }: { sub: SubQueryTrace }) {
  return (
    <Section title={`Part ${String(sub.index)} · ${sub.shape}`}>
      <p className="m-0 mb-2 text-label font-normal text-fg-2">
        {sub.question}
      </p>
      <KV
        rows={[
          ...Object.entries(sub.filters).map(
            ([k, v]) => [k, v] as [string, ReactNode],
          ),
          ...sub.windows.map(
            (w) =>
              [
                "window",
                <span key={w.expression} data-testid="plan-window">
                  “{w.expression}” → {formatDay(w.value)}
                  {w.end ? ` – ${formatDay(w.end)}` : ""} · {w.clock} · {w.rule}
                  {w.anchor ? ` · from ${w.anchor}` : ""}
                </span>,
              ] as [string, ReactNode],
          ),
          ...sub.entities.map(
            (e) =>
              [
                "entity",
                `${e.mention} → ${e.path.length ? e.path.join(" → ") : e.names.join(", ") || e.outcome}`,
              ] as [string, ReactNode],
          ),
          ...sub.dropped.map((d) => ["dropped", d] as [string, ReactNode]),
          ...(sub.expansion
            ? ([
                [
                  "expansion",
                  `${sub.expansion.source}: ${sub.expansion.reason}`,
                ],
              ] as [string, ReactNode][])
            : []),
        ]}
      />
    </Section>
  );
}

// ------------------------------------------------------------------ search

export function SearchPlain({ ctx }: P) {
  const relaxed = subs(ctx).flatMap((s) =>
    s.relaxation.filter((r) => r.count > 0),
  );
  const soft = ctx.facts.retrieval?.soft_channel ?? true;
  return (
    <Plain>
      I looked with the tools the question needs
      {soft ? ", and searched every search key by meaning alongside them" : ""}.
      {relaxed.length > 0
        ? ` Nothing matched exactly, so I ${relaxed.map((r) => r.change).join("; ")}.`
        : ""}
    </Plain>
  );
}

export function SearchTech({ ctx }: P) {
  const all = subs(ctx);
  if (all.length === 0) return <Muted>No search ran.</Muted>;
  return (
    <>
      {all.map((s) => (
        <Section key={s.index} title={`Part ${String(s.index)} · tools`}>
          <MTable
            head={["tool", "found", "time", "arguments"]}
            rows={s.tools.map((t, i) => ({
              key: `${t.tool}-${String(i)}`,
              testId: "retrieval-tool",
              cells: [
                t.tool,
                t.error ?? String(t.count),
                formatMs(t.latency_ms),
                truncate(
                  Object.entries(t.arguments)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" "),
                  70,
                ),
              ],
            }))}
          />
          {s.soft_query && (
            <p className="m-0 mt-2 font-machine text-mono-sm text-fg-3">
              soft query: {truncate(s.soft_query, 160)}
            </p>
          )}
          {s.relaxation.map((r) => (
            <p
              key={r.step}
              className="m-0 mt-1 font-machine text-mono-sm text-fg-3"
              data-testid="relax-step"
            >
              relax {r.step}: {r.change} → {r.count}
            </p>
          ))}
        </Section>
      ))}
    </>
  );
}

// ------------------------------------------------------------------ rank

export function RankPlain({ ctx }: P) {
  const r = ctx.facts.retrieval;
  const kept = candidates(ctx).filter((c) => c.selected).length;
  const checks = subs(ctx).filter((s) => s.count_check?.note);
  return (
    <>
      <Plain>
        {r?.rerank === "model"
          ? `I scored what I found against your question and kept ${plural(kept, "item")}.`
          : `I kept ${plural(kept, "item")} in the order the searches agreed on.`}
      </Plain>
      {checks.map((s) => (
        <p
          key={s.index}
          className="m-0 mt-2 text-label font-normal text-fg-2"
          data-testid="count-check"
        >
          {s.count_check?.note}
        </p>
      ))}
    </>
  );
}

export function RankTech({ ctx }: P) {
  const r = ctx.facts.retrieval;
  return (
    <>
      {r && r.rerank !== "model" && (
        <p className="m-0 mb-2 font-machine text-mono-sm text-fg-3">
          rerank {r.rerank}
          {r.rerank_note ? `: ${r.rerank_note}` : ""}
        </p>
      )}
      {subs(ctx).map((s) => (
        <Section
          key={s.index}
          title={`Part ${String(s.index)} · ${plural(s.candidates.length, "candidate")}`}
        >
          <Candidates list={s.candidates} />
        </Section>
      ))}
      <ModelLines calls={ctx.calls} />
    </>
  );
}

function score(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(v < 0.01 && v > 0 ? 4 : 2);
}

function status(c: RetrievalCandidate): string {
  if (c.cited) return "cited";
  if (c.selected) return "selected";
  return c.demoted ? "demoted" : "—";
}

function FoundBy({ c }: { c: RetrievalCandidate }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {c.found_by.map((f) => (
        <Chip key={f.channel} kind="mono">
          {f.channel}#{f.rank}
        </Chip>
      ))}
      {c.soft_only && (
        <Chip kind="mono" dot="warn" className="text-fg-2">
          soft only
        </Chip>
      )}
    </span>
  );
}

/** The candidate table; at phone width it becomes a list (rulebook §7). */
export function Candidates({ list }: { list: RetrievalCandidate[] }) {
  const wide = useMediaQuery("(min-width: 640px)");
  if (list.length === 0) return <Muted>Nothing was found.</Muted>;
  if (!wide) {
    return (
      <ol className="m-0 grid list-none gap-2 p-0">
        {list.map((c, i) => (
          <li
            key={`${c.item_id ?? c.turn_id ?? c.title}-${String(i)}`}
            data-testid="candidate"
            data-selected={c.selected}
            data-soft-only={c.soft_only}
            className="rounded-sm bg-surface-2 p-3"
          >
            <p
              className={cx(
                "m-0 text-label",
                c.demoted ? "text-fg-3 line-through" : "text-fg",
              )}
            >
              {truncate(c.title, 60)}
            </p>
            <p className="m-0 mt-1 font-machine text-mono-sm text-fg-3">
              {c.kind ?? "turn"} · {c.state ?? "—"} · rrf {score(c.fused_score)}{" "}
              · rerank {score(c.rerank_score)} · {status(c)}
            </p>
            <div className="mt-1.5">
              <FoundBy c={c} />
            </div>
            {c.reason && (
              <p className="m-0 mt-1 text-label font-normal text-fg-3">
                {c.reason}
              </p>
            )}
          </li>
        ))}
      </ol>
    );
  }
  return (
    <MTable
      head={[
        "memory",
        "kind · state",
        "layer",
        "found by",
        "lex",
        "dense",
        "rrf",
        "rerank",
        "status",
        "why",
      ]}
      rows={list.map((c, i) => ({
        key: `${c.item_id ?? c.turn_id ?? c.title}-${String(i)}`,
        testId: "candidate",
        attrs: {
          "data-selected": String(c.selected),
          "data-soft-only": String(c.soft_only),
          "data-demoted": String(c.demoted),
        },
        cells: [
          <span key="t" className={cx(c.demoted && "text-fg-3 line-through")}>
            {truncate(c.title, 40)}
          </span>,
          `${c.kind ?? "turn"} · ${c.state ?? "—"}`,
          <Tag key="l">{c.layer}</Tag>,
          <FoundBy key="f" c={c} />,
          score(c.lexical_score),
          score(c.dense_score),
          score(c.fused_score),
          score(c.rerank_score),
          status(c),
          truncate(c.reason || c.rerank_reason, 48),
        ],
      }))}
    />
  );
}

// ------------------------------------------------------------------ triggers

export function TriggersPlain({ ctx }: P) {
  const f = fired(ctx);
  if (f.length === 0)
    return <Plain>Nothing you asked to be reminded of came up.</Plain>;
  return (
    <Plain>
      This came up, so I reminded you:{" "}
      {f.map((e) => e.title.replace(/^reminder: /, "")).join(", ")}. Undo puts
      the reminder back.
    </Plain>
  );
}

export function TriggersTech({ ctx }: P) {
  const f = fired(ctx);
  if (f.length === 0) return <Muted>No trigger matched.</Muted>;
  return (
    <KV
      rows={f.map((e) => [e.title, e.reason || "fired"] as [string, ReactNode])}
    />
  );
}

// ------------------------------------------------------------------ the Inspector's panel

export function RetrievalPanel({ ctx }: P) {
  const r = ctx.facts.retrieval;
  if (!r) return <Muted>Nothing was retrieved this turn.</Muted>;
  return (
    <div className="grid gap-2" data-testid="retrieval">
      {r.explanation && <Plain>{r.explanation}</Plain>}
      <PlanTech ctx={{ ...ctx, calls: [] }} />
      <SearchTech ctx={ctx} />
      {r.sub_queries.map((s) => (
        <Section key={s.index} title={`Part ${String(s.index)} · candidates`}>
          {s.aggregate && (
            <p
              className="m-0 mb-2 font-machine text-mono text-fg-2"
              data-testid="aggregate"
            >
              {s.aggregate.op}
              {s.aggregate.field ? `(${s.aggregate.field})` : ""} ={" "}
              {String(s.aggregate.value ?? "—")}
            </p>
          )}
          {s.count_check?.note && (
            <p
              className="m-0 mb-2 flex items-center gap-2 text-label font-normal text-fg-2"
              data-testid="count-check"
            >
              <Dot tone="warn" />
              {s.count_check.note}
            </p>
          )}
          <Candidates list={s.candidates} />
        </Section>
      ))}
    </div>
  );
}
