/**
 * `/evals` until S5 (FR-15.2, S4.13): the runs behind the landing page's numbers, so no number is a
 * dead end. Each row is a committed run file in the repository: its id, date, commit and models.
 * S5 replaces this page with the full one.
 */
import { BrandMark, Overline, Wordmark } from '../ui'
import { useHeadline } from '../hooks/useHeadline'

export function EvalsPage() {
  const state = useHeadline()
  return (
    <main id="main" className="mx-auto max-w-180 px-4 pb-24 pt-8 md:px-6" data-testid="evals-page">
      <a href="/" className="inline-flex items-center gap-2.5 rounded-sm text-fg no-underline" aria-label="2nd Mind, home">
        <BrandMark />
        <Wordmark />
      </a>
      <h1 className="m-0 mt-10 font-voice text-title-lg text-fg">How it is measured</h1>
      <p className="m-0 mt-2 measure text-body text-fg-2">
        Every number on the front page comes from a recorded run of the evaluation set, stamped with the commit and the models it ran on. Nothing here is typed in by hand, and a number with no run says so.
      </p>
      {state.status === 'loading' && <p className="m-0 mt-8 text-label text-fg-3">Loading the runs…</p>}
      {state.status === 'unavailable' && (
        <p className="m-0 mt-8 text-label text-fg-3" role="status">
          The runs couldn't be loaded.
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <section className="mt-10" aria-labelledby="evals-numbers">
            <Overline id="evals-numbers">The numbers</Overline>
            <ul className="m-0 mt-3 grid list-none gap-3 p-0">
              {state.headline.metrics.map((m) => (
                <li key={m.id} className="rounded-md border border-line bg-surface px-4 py-3.5 shadow-edge" data-testid="evals-metric">
                  <p className="m-0 flex items-baseline justify-between gap-3">
                    <span className="text-label text-fg-2">{m.label}</span>
                    <span className="font-voice text-title-lg text-fg tnum">{m.display}</span>
                  </p>
                  <p className="m-0 mt-1 text-label font-normal text-fg-3">{m.run ? m.detail : 'No recorded run has measured this yet.'}</p>
                </li>
              ))}
            </ul>
          </section>
          <section className="mt-10" aria-labelledby="evals-runs">
            <Overline id="evals-runs">The runs behind them</Overline>
            {state.headline.runs.length === 0 ? (
              <p className="m-0 mt-3 text-label text-fg-3">No run yet.</p>
            ) : (
              <div className="scroll-thin mt-3 overflow-x-auto" tabIndex={0} role="region" aria-label="Runs">
                <table className="min-w-full border-collapse font-machine text-mono-sm text-fg-2">
                  <thead>
                    <tr>
                      {['run', 'suite', 'date', 'commit', 'cases', 'models'].map((h) => (
                        <th key={h} scope="col" className="whitespace-nowrap border-b border-line pb-1.5 pr-4 text-left font-medium text-fg-3">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {state.headline.runs.map((r) => (
                      <tr key={r.id} className="border-b border-line last:border-b-0" data-testid="evals-run">
                        <td className="whitespace-nowrap py-2 pr-4">{r.id}</td>
                        <td className="whitespace-nowrap py-2 pr-4">{r.suite}</td>
                        <td className="whitespace-nowrap py-2 pr-4">{r.date}</td>
                        <td className="whitespace-nowrap py-2 pr-4">{r.sha}</td>
                        <td className="whitespace-nowrap py-2 pr-4 tnum">
                          {r.passed}/{r.cases} passed
                        </td>
                        <td className="py-2 pr-4">{r.models.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  )
}
