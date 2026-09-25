import type { DecisionEvent, IntentEvent } from '../../api/client'
import { formatInZone } from '../../lib/format'
import { Chip, Subhead } from './Panel'

type Props = { intent: IntentEvent | undefined; decision: DecisionEvent | undefined }

/** What was decided about the message: intent, memories, dates, entities, reconciliation. */
export function DecisionBody({ intent, decision }: Props) {
  return (
    <div className="space-y-4 text-sm text-slate-800">
      {intent && (
        <p data-testid="decision-intent">
          Intent <strong>{intent.intent.replaceAll('_', '-')}</strong>{' '}
          <span className="text-slate-600">
            ({intent.source}, {Math.round(intent.confidence * 100)}%)
          </span>
          : {intent.reason}
        </p>
      )}
      {decision && (
        <>
          <p className="font-medium text-slate-900">{decision.summary}</p>
          <Memories decision={decision} />
          <Dates decision={decision} />
          <Entities decision={decision} />
          <Reconciliations decision={decision} />
          <Vocabulary decision={decision} />
          {decision.not_written.length > 0 && (
            <section>
              <Subhead>Chose not to write</Subhead>
              <ul className="list-disc space-y-0.5 pl-5">
                {decision.not_written.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </section>
          )}
          {Object.keys(decision.decided_by).length > 0 && (
            <p className="text-xs text-slate-600" data-testid="decided-by">
              Decided by{' '}
              {Object.entries(decision.decided_by).map(([step, model], i) => (
                <span key={step}>
                  {i > 0 && ' · '}
                  {step}: <span className="font-mono">{model}</span>
                </span>
              ))}
            </p>
          )}
        </>
      )}
    </div>
  )
}

function Memories({ decision }: { decision: DecisionEvent }) {
  if (decision.classifications.length === 0) return null
  return (
    <section>
      <Subhead>Memories</Subhead>
      <ul className="space-y-2">
        {decision.classifications.map((c, i) => (
          <li key={`${c.label}-${String(i)}`} data-testid="decision-memory" className="space-y-1">
            <p className="font-medium text-slate-900">{c.label}</p>
            <div className="flex flex-wrap gap-1">
              <Chip tone="indigo">{c.subtype ? `${c.kind} · ${c.subtype}` : c.kind}</Chip>
              {c.state && <Chip>{c.state}</Chip>}
              {c.format && <Chip>{c.format}</Chip>}
              <Chip>{c.layer}</Chip>
              {c.category && <Chip>{c.category}</Chip>}
              {c.modality !== 'asserted' && <Chip tone="amber">{c.modality}</Chip>}
              {c.sensitivity !== 'normal' && <Chip tone="amber">{c.sensitivity}</Chip>}
            </div>
            {c.rationale && <p className="text-xs text-slate-600">{c.rationale}</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Dates({ decision }: { decision: DecisionEvent }) {
  if (decision.time_resolutions.length === 0) return null
  return (
    <section>
      <Subhead>Dates</Subhead>
      <ul className="space-y-1.5">
        {decision.time_resolutions.map((t, i) => (
          <li key={`${t.expression}-${t.clock}-${String(i)}`} data-testid="decision-date">
            <span className="font-medium">“{t.expression}”</span> → <span className="font-mono">{t.value}</span>
            {t.rrule && <span className="font-mono text-slate-600"> ({t.rrule})</span>}
            <span className="block text-xs text-slate-600">
              {t.precision} · {t.clock} clock · rule {t.rule} · now {formatInZone(t.now, t.timezone)} {t.timezone}
            </span>
            {t.assumed && (
              <span className="block text-xs text-amber-800">
                Assumed{t.alternative ? `; the other reading was ${t.alternative}` : ''}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Entities({ decision }: { decision: DecisionEvent }) {
  if (decision.entity_resolutions.length === 0) return null
  const tones = { matched: 'green', new: 'indigo', ambiguous: 'amber', updated: 'slate' } as const
  return (
    <section>
      <Subhead>Entities</Subhead>
      <ul className="space-y-1.5">
        {decision.entity_resolutions.map((e, i) => (
          <li key={`${e.mention}-${String(i)}`} data-testid="decision-entity" data-outcome={e.outcome}>
            <span className="font-medium">“{e.mention}”</span> →{' '}
            <Chip tone={tones[e.outcome]}>{e.outcome}</Chip> {e.display_name}{' '}
            <span className="text-slate-600">({e.entity_kind})</span>
            {e.candidates.length > 0 && (
              <span className="block text-xs text-slate-600">Other candidates: {e.candidates.join(', ')}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Reconciliations({ decision }: { decision: DecisionEvent }) {
  if (decision.reconciliations.length === 0) return null
  return (
    <section>
      <Subhead>Reconciliation</Subhead>
      <ul className="space-y-1.5">
        {decision.reconciliations.map((r, i) => (
          <li key={`${r.label}-${String(i)}`} data-testid="decision-reconcile">
            {r.label} → <Chip tone={r.info.decision === 'new' ? 'slate' : 'indigo'}>{r.info.decision.replace('_', ' ')}</Chip>
            {r.info.candidate_title && <> “{r.info.candidate_title}”</>}
            {r.info.score != null && <span className="text-slate-600"> (score {r.info.score.toFixed(2)})</span>}
            {r.info.rule && <span className="block text-xs text-slate-600">{r.info.rule}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Vocabulary({ decision }: { decision: DecisionEvent }) {
  if (decision.normalisations.length === 0) return null
  return (
    <section>
      <Subhead>Categories and vocabulary</Subhead>
      <ul className="space-y-1 text-xs">
        {decision.normalisations.map((n, i) => (
          <li key={`${n.vocab}-${n.proposed}-${String(i)}`}>
            {n.vocab}: {n.reused ? 'reused' : 'new'} <span className="font-mono">{n.chosen}</span>
            {n.proposed !== n.chosen && (
              <>
                {' '}
                instead of <span className="font-mono">{n.proposed}</span>
              </>
            )}
            {n.how && <span className="text-slate-600"> ({n.how})</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
