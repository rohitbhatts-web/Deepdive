import type { ModelCall, RunSummary } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import { ErrorBox, Loading, Notice, Panel, SectionHeading, StatTile, UNAVAILABLE, num } from './bits'
import { latestJobRequests, type Live } from './RunPage'

interface Row { agent: string; calls: number; failed: number; prompt: number | null; completion: number | null }

/** Sum only what the provider reported: if no call of an agent reported a count, the total is Unavailable, not zero. */
function byAgent(calls: ModelCall[]): Row[] {
  const rows = new Map<string, Row>()
  for (const c of calls) {
    const r = rows.get(c.agent) ?? { agent: c.agent, calls: 0, failed: 0, prompt: null, completion: null }
    r.calls += 1
    if (!c.ok) r.failed += 1
    if (c.prompt_tokens !== null) r.prompt = (r.prompt ?? 0) + c.prompt_tokens
    if (c.completion_tokens !== null) r.completion = (r.completion ?? 0) + c.completion_tokens
    rows.set(c.agent, r)
  }
  return [...rows.values()]
}

const BUDGET_LABEL: Record<string, string> = {
  model_requests: 'Model requests', search_queries: 'Web searches', page_fetches: 'Page fetches',
  candidate_claims: 'Corrective candidate claims', output_tokens: 'Output tokens', elapsed_seconds: 'Elapsed seconds',
}

export function UsageTab({ run, live, version }: { run: RunSummary; live: Live; version: number }) {
  const calls = useResource<ModelCall[]>(`/runs/${run.id}/model-calls`, version)
  const used = live.research.model_usage
  const budget = live.completion.budget_used_of_limit
  const rows = calls.data ? byAgent(calls.data) : []
  return (
    <div className="space-y-8">
      <section aria-labelledby="tok-h" className="space-y-3">
        <SectionHeading id="tok-h" description="Every call recorded for the run, across all jobs (collection, verification, completion). Token counts are as returned by the provider.">Total for this run: all stored model calls</SectionHeading>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <StatTile label="Total model calls for this run">{num(used.model_calls)}</StatTile>
          <StatTile label="Failed requests">{num(used.failed_calls)}</StatTile>
          <StatTile label="Prompt tokens">{num(used.prompt_tokens)}</StatTile>
          <StatTile label="Completion tokens">{num(used.completion_tokens)}</StatTile>
          <StatTile label="Total tokens">{num(used.total_tokens)}</StatTile>
        </dl>
        {calls.loading && !calls.data && <Loading label="Loading per-agent usage" blocks={1} />}
        {calls.error && <ErrorBox error={calls.error} what="model calls" />}
        {rows.length > 0 && (
          <Panel>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[30rem] text-left text-sm">
                <caption className="sr-only">Model usage by agent</caption>
                <thead>
                  <tr className="border-b bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
                    <th scope="col" className="px-3 py-2 font-semibold">Agent</th><th scope="col" className="px-3 py-2 text-right font-semibold">Requests</th><th scope="col" className="px-3 py-2 text-right font-semibold">Failed</th>
                    <th scope="col" className="px-3 py-2 text-right font-semibold">Prompt tokens</th><th scope="col" className="px-3 py-2 text-right font-semibold">Completion tokens</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {rows.map((r) => (
                    <tr key={r.agent} className="border-b last:border-0">
                      <th scope="row" className="px-3 py-2 font-medium">{r.agent}</th>
                      <td className="px-3 py-2 text-right">{r.calls}</td><td className="px-3 py-2 text-right">{r.failed}</td>
                      <td className="px-3 py-2 text-right">{num(r.prompt)}</td><td className="px-3 py-2 text-right">{num(r.completion)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </section>

      <section aria-labelledby="bud-h" className="space-y-3">
        <SectionHeading id="bud-h" description="Recorded when the latest completion job finished. It counts that job only, so its request total can be lower than the run total above.">Latest job: budget used / limit</SectionHeading>
        <p className="text-sm">Latest job requests used / limit: <strong>{latestJobRequests(live.completion)}</strong></p>
        {budget ? (
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(budget).map(([k, v]) => (<StatTile key={k} label={BUDGET_LABEL[k] ?? k}>{num(v[0])} of {num(v[1])}</StatTile>))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">{UNAVAILABLE}: the final budget is recorded only when the completion step finishes.</p>
        )}
      </section>

      <section aria-labelledby="cost-h" className="space-y-3">
        <SectionHeading id="cost-h" description="Three different quantities. None is stored with the run, so none is shown.">Cost</SectionHeading>
        <dl className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-lg border bg-card p-3 text-sm"><dt className="font-medium">Estimated before running</dt><dd className="text-muted-foreground">{UNAVAILABLE} – no estimate is made or stored.</dd></div>
          <div className="rounded-lg border bg-card p-3 text-sm"><dt className="font-medium">Calculated from tokens and prices</dt><dd className="text-muted-foreground">{UNAVAILABLE} – the backend stores no price table with a run.</dd></div>
          <div className="rounded-lg border bg-card p-3 text-sm"><dt className="font-medium">Reported by the provider</dt><dd className="text-muted-foreground">{UNAVAILABLE} – charges are not stored per run.</dd></div>
        </dl>
        <Notice title="No cost is shown on purpose">These three amounts are different things. None of them is stored with the run, so none is displayed.</Notice>
      </section>
    </div>
  )
}
