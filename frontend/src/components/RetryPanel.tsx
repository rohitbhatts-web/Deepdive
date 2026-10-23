import { Loader2, RotateCcw } from 'lucide-react'
import { useRef, useState } from 'react'
import { ApiError, post } from '@/api/client'
import type { RetryPlan } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useResource } from '@/hooks/useApi'
import { ErrorBox, Notice, Panel, SectionHeading, StatTile, num } from './bits'

/**
 * Explicit, confirmed continuation of a failed job from the stored evidence. Nothing here runs by itself: the button stays disabled until the
 * person ticks the confirmation, one click sends one request, and the plan (limits, lineage, why it may be unavailable) comes from the backend.
 */
export function RetryPanel({ runId, onStarted }: { runId: number; onStarted: () => void }) {
  const plan = useResource<RetryPlan>(`/runs/${runId}/retry`)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)

  if (plan.loading && !plan.data) return null
  if (plan.error) return <ErrorBox error={plan.error} what="the retry plan" />
  const p = plan.data
  if (!p) return null
  if (!p.allowed) {
    return (
      <Panel>
        <div className="space-y-1 p-4 text-sm">
          <SectionHeading level={3}>Retry from stored evidence is not available</SectionHeading>
          <ul className="list-disc pl-5 text-muted-foreground">{p.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      </Panel>
    )
  }

  async function retry() {
    if (!confirmed || inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError(null)
    try {
      await post(`/runs/${runId}/retry`, { confirm_paid_retry: true })
      onStarted()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  const prev = p.previous_job
  const used = prev?.budget_used_of_limit?.model_requests
  const limits = p.new_job_limits
  return (
    <Panel accent="neutral">
      <section aria-labelledby="retry-h" className="space-y-3 p-4">
        <SectionHeading id="retry-h" level={3} description={`Continues from the ${p.stored_evidence.sources} stored source(s) and ${p.stored_evidence.passages} passages. It makes no search and no page fetch; it makes paid model requests under a NEW budget. The failed job's budget record is kept, not reset.`}>
          Retry from stored evidence (attempt {p.attempt})
        </SectionHeading>
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile label="New job: model requests">{num(limits.model_requests)}</StatTile>
          <StatTile label="New job: output tokens">{num(limits.output_tokens)}</StatTile>
          <StatTile label="New job: seconds">{num(limits.elapsed_seconds)}</StatTile>
          <StatTile label="Searches and fetches">{num(limits.search_queries)} and {num(limits.page_fetches)}</StatTile>
        </dl>
        {prev && (
          <p className="text-sm text-muted-foreground">
            Previous job: {used ? `${num(used[0])} of ${num(used[1])} model requests used` : 'budget record unavailable'}; stopped because: <span className="break-words">{prev.stop_reason ?? 'no reason recorded'}</span>
          </p>
        )}
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 size-4" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} disabled={pending} />
          <span>I understand this makes paid model requests (up to {num(limits.model_requests)}) and that nothing will be retried unless I start it.</span>
        </label>
        {error && <Notice tone="bad" title="The retry did not start">{error}</Notice>}
        <Button onClick={retry} disabled={!confirmed || pending}>
          {pending ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : <RotateCcw aria-hidden />}
          {pending ? 'Starting…' : 'Retry from stored evidence'}
        </Button>
      </section>
    </Panel>
  )
}
