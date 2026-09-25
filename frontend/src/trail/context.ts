/** Shared, non-component pieces of the Trail: held-write actions and the model line facts. */
import { createContext, type ReactNode } from 'react'
import type { HeldWrite, ModelCallEvent } from '../api/client'
import { formatMs, formatTokens } from '../lib/format'

export type HeldActions = {
  held: Map<string, HeldWrite>
  busy: string | null
  confirm: (heldId: string) => void
  reject: (heldId: string) => void
}

export const HeldContext = createContext<HeldActions | null>(null)

export function modelFacts(call: ModelCallEvent): ReactNode[] {
  const u = call.usage
  const facts: ReactNode[] = [`${call.provider}:${call.model}`]
  if (call.prompt) facts.push(`prompt ${call.prompt}`)
  facts.push(`${formatTokens(u.input_tokens + u.cached_input_tokens)} → ${formatTokens(u.output_tokens)} tok`)
  if (call.time_to_first_token_ms != null) facts.push(`ttft ${formatMs(call.time_to_first_token_ms)}`)
  facts.push(formatMs(call.latency_ms))
  if (call.cache_hits != null) facts.push(`${String(call.cache_hits)} from cache`)
  return facts
}
