/** Recall's done labels and chips (plan, search, rank, triggers), from the stored events. */
import type { RetrievalCandidate, SubQueryTrace } from "../api/client";
import { plural } from "../lib/format";
import type { StepContext } from "./types";

export function subs(ctx: StepContext): SubQueryTrace[] {
  return ctx.facts.retrieval?.sub_queries ?? [];
}

export function candidates(ctx: StepContext): RetrievalCandidate[] {
  return subs(ctx).flatMap((s) => s.candidates);
}

export function channels(ctx: StepContext): Set<string> {
  return new Set(
    candidates(ctx).flatMap((c) =>
      c.found_by.map((f) => f.channel.split(":")[0] ?? f.channel),
    ),
  );
}

export function fired(ctx: StepContext) {
  return (ctx.facts.diff?.entries ?? []).filter((e) =>
    e.title.startsWith("reminder:"),
  );
}

export function planDone(ctx: StepContext): string {
  const s = subs(ctx);
  if (s.length === 0) return "Worked out what you're asking";
  const [first] = s;
  if (s.length === 1 && first) return `1 question · ${first.shape}`;
  return `${String(s.length)} questions`;
}
export function planChips(ctx: StepContext): string[] {
  const r = ctx.facts.retrieval;
  const out: string[] = subs(ctx)
    .slice(0, 2)
    .map((s) => s.shape);
  if (r && r.plan_source !== "model") out.push(r.plan_source);
  return out;
}

export function searchDone(ctx: StepContext): string {
  const n = new Set(candidates(ctx).map((c) => c.item_id ?? c.turn_id)).size;
  if (n === 0) return "Found nothing that fits";
  return `${String(n)} found in ${plural(channels(ctx).size, "way")}`;
}
export function searchChips(ctx: StepContext): string[] {
  const out: string[] = [];
  const relaxed = subs(ctx).some((s) => s.relaxation.some((r) => r.count > 0));
  if (relaxed) out.push("relaxed");
  const soft = candidates(ctx).filter((c) => c.soft_only && c.selected).length;
  if (soft) out.push(`${String(soft)} soft-only`);
  return out;
}

export function rankDone(ctx: StepContext): string {
  const kept = candidates(ctx).filter((c) => c.selected).length;
  const count = subs(ctx).find((s) => s.aggregate?.value != null);
  if (count?.aggregate)
    return `Counted ${String(count.aggregate.value)} exactly`;
  return kept ? `Kept the top ${String(kept)}` : "Nothing relevant enough";
}
export function rankChips(ctx: StepContext): string[] {
  const r = ctx.facts.retrieval;
  return r && r.rerank !== "model" ? [`rerank ${r.rerank}`] : [];
}

export function triggersDone(ctx: StepContext): string {
  const n = fired(ctx).length;
  return n ? `${plural(n, "reminder")} fired` : "No reminders tied to this";
}
export function triggersChips(ctx: StepContext): string[] {
  const n = fired(ctx).length;
  return n ? [`~${String(n)}`] : [];
}
