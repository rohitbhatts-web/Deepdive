import { Loader2, Play } from 'lucide-react'
import { useRef, useState } from 'react'
import { ApiError, post } from '@/api/client'
import type { Limits, RunSummary } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useResource } from '@/hooks/useApi'
import { hrefFor } from '@/lib/route'
import { ErrorBox, Notice, Panel, SectionHeading, StatTile, num, Skeleton } from './bits'

export const LIMIT_LABELS: Record<string, string> = {
  model_requests: 'Model requests',
  search_queries: 'Web searches',
  page_fetches: 'Page fetches',
  candidate_claims: 'Corrective candidate claims',
  output_tokens: 'Output tokens',
  elapsed_seconds: 'Elapsed seconds',
}

const STEPS = ['Plan sub-questions', 'Search and read sources', 'Draft claims with exact quotes', 'Verify each claim', 'Correct gaps within the limits', 'Write the report']

export function NewResearch({ onStarted }: { onStarted: (runId: number) => void }) {
  const limits = useResource<Limits>('/limits')
  const [question, setQuestion] = useState('')
  const [touched, setTouched] = useState(false)
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<{ message: string; runId: number | null } | null>(null)
  const inFlight = useRef(false) // blocks a second start even before React re-renders the disabled button

  const min = limits.data?.question_min_chars ?? 3
  const max = limits.data?.question_max_chars ?? 2000
  const trimmed = question.trim()
  const problem = trimmed.length < min ? `Enter at least ${min} characters.` : trimmed.length > max ? `At most ${max} characters (now ${trimmed.length}).` : null

  async function start(e: React.FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (problem || inFlight.current) return
    inFlight.current = true
    setPending(true)
    setFailure(null)
    let runId: number | null = null
    try {
      const run = await post<RunSummary>('/runs', { question: trimmed })
      runId = run.id
      await post(`/runs/${run.id}/research?complete=true`)
      onStarted(run.id)
    } catch (err) {
      setFailure({ message: err instanceof ApiError ? err.message : String(err), runId })
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-8">
      <header className="space-y-1.5">
        <h1 className="text-3xl font-semibold tracking-tight">New research</h1>
        <p className="text-muted-foreground">Ask a question. Every claim in the report is stored with an exact quote from a fetched source and checked before it is reported.</p>
      </header>

      <Panel>
        <form onSubmit={start} className="space-y-3 p-5" noValidate>
          <label htmlFor="question" className="block text-sm font-medium">Research question</label>
          <textarea
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onBlur={() => setTouched(true)}
            rows={4}
            disabled={pending}
            aria-invalid={touched && !!problem}
            aria-describedby="question-help"
            className="w-full rounded-lg border border-input bg-background p-3 text-base leading-relaxed disabled:opacity-60"
            placeholder="For example: How do SQLite and PostgreSQL differ for a small web application?"
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p id="question-help" className={touched && problem ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>
              {touched && problem ? problem : `${min}–${max} characters.`}
            </p>
            <Button type="submit" disabled={pending || (touched && !!problem)}>
              {pending ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : <Play aria-hidden />}
              {pending ? 'Starting…' : 'Start research'}
            </Button>
          </div>
        </form>
      </Panel>
      {failure && (
        <Notice tone="bad" title="The research did not start">
          {failure.message}
          {failure.runId !== null && (
            <>
              {' '}
              The question was saved as <a className="underline" href={hrefFor(failure.runId)}>run {failure.runId}</a>, but research was not started.
            </>
          )}
        </Notice>
      )}

      <section aria-labelledby="limits-h" className="space-y-3">
        <SectionHeading id="limits-h" description="Every run stops at the first limit it reaches; the stop reason is recorded and shown.">Configured limits for each run</SectionHeading>
        {limits.loading && !limits.data && <div role="status"><span className="sr-only">Loading limits</span><Skeleton className="h-16 w-full" /></div>}
        {limits.error && <ErrorBox error={limits.error} what="the configured limits" />}
        {limits.data && (
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(limits.data.limits).map(([k, v]) => (
              <StatTile key={k} label={LIMIT_LABELS[k] ?? k}>{num(v)}</StatTile>
            ))}
            <StatTile label="Correction rounds">{num(limits.data.max_correction_rounds)}</StatTile>
          </dl>
        )}
      </section>

      <section aria-labelledby="steps-h" className="space-y-3">
        <SectionHeading id="steps-h" description="The stages a run goes through, in order. Progress is shown from recorded events as they happen.">What happens next</SectionHeading>
        <ol className="grid gap-2 sm:grid-cols-2">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 text-sm">
              <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">{i + 1}</span>
              {s}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}
