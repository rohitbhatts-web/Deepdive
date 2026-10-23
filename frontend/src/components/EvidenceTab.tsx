import { useState } from 'react'
import type { Claim, RunSummary } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import { claimState } from '@/lib/claimState'
import { hrefFor } from '@/lib/route'
import { cn } from '@/lib/utils'
import { Empty, ErrorBox, Loading, Panel, SafeLink, SectionHeading, when } from './bits'
import { ClaimPill, Inspector } from './Inspector'

const FILTERS: { key: string; label: string; test: (c: Claim) => boolean }[] = [
  { key: 'all', label: 'All', test: () => true },
  { key: 'eligible', label: 'Report-eligible', test: (c) => c.eligible_for_report },
  { key: 'rejected', label: 'Rejected or excluded', test: (c) => ['unsupported', 'contradicted', 'integrity_failed', 'excluded'].includes(claimState(c).key) },
  { key: 'attention', label: 'Not judged / error', test: (c) => ['not_judged', 'unchecked', 'verification_error', 'needs_rejudge'].includes(claimState(c).key) },
  { key: 'test', label: 'Test inputs', test: (c) => c.origin === 'controlled_challenge' },
]

interface SourceRow { id: number; final_url: string; title: string | null; retrieved_at: string; content_type: string; passage_count: number }

export function EvidenceTab({ run, claimId, version }: { run: RunSummary; claimId: number | null; version: number }) {
  const claims = useResource<Claim[]>(`/runs/${run.id}/claims`, version)
  const sources = useResource<SourceRow[]>(`/runs/${run.id}/sources`, version)
  const [filter, setFilter] = useState('all')
  const test = FILTERS.find((f) => f.key === filter)!.test
  if (claims.loading && !claims.data) return <Loading label="Loading claims" blocks={3} />
  if (claims.error) return <ErrorBox error={claims.error} what="claims" />
  const all = claims.data ?? []
  const shown = all.filter(test)
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_28rem]">
      <div className="min-w-0 space-y-6">
        <section aria-labelledby="claims-h" className="space-y-3">
          <SectionHeading id="claims-h" description="Every stored claim version, including rejected ones. “Not judged” and “Verification error” are not support, and are not rejections either.">Claims</SectionHeading>
          <div role="group" aria-label="Filter claims" className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}
                className={cn('rounded-full border px-3 py-1 text-sm transition-colors', filter === f.key ? 'border-primary bg-accent font-medium text-accent-foreground' : 'bg-card hover:bg-muted')}>
                {f.label} <span className="tabular-nums text-muted-foreground">({all.filter(f.test).length})</span>
              </button>
            ))}
          </div>
          {all.length === 0 ? <Empty title="No claims yet">Claims appear after the drafter and verifier have run.</Empty> : shown.length === 0 ? <Empty title="No claims match this filter" /> : (
            <ul className="space-y-2.5">
              {shown.map((c) => (
                <li key={c.id}>
                  <Panel accent={claimState(c).tone} className={cn(claimId === c.id && 'ring-2 ring-primary/40')}>
                    <a id={`claim-link-${c.id}`} href={hrefFor(run.id, 'evidence', c.id)} aria-current={claimId === c.id ? 'true' : undefined}
                      className="block rounded-lg p-3.5 transition-colors hover:bg-muted/40">
                      <span className="whitespace-pre-wrap break-words leading-relaxed">{c.text}</span>
                      <span className="mt-2.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span>Claim {c.id} v{c.version}</span>
                        <ClaimPill claim={c} />
                        <span>{c.citations.length} cited passage(s)</span>
                      </span>
                    </a>
                  </Panel>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section aria-labelledby="src-h" className="space-y-3">
          <SectionHeading id="src-h" description="Pages stored for this run, with the date each was retrieved.">Sources collected</SectionHeading>
          {sources.loading && !sources.data && <Loading label="Loading sources" blocks={1} />}
          {sources.error && <ErrorBox error={sources.error} what="sources" />}
          {sources.data?.length === 0 && <p className="text-sm text-muted-foreground">No source was stored for this run.</p>}
          <ul className="space-y-2">
            {sources.data?.map((s) => (
              <li key={s.id}>
                <Panel>
                  <div className="space-y-0.5 p-3 text-sm">
                    <p className="font-medium">S{s.id} · {s.title ?? 'Untitled'}</p>
                    <p><SafeLink url={s.final_url} /></p>
                    <p className="text-xs text-muted-foreground">Retrieved {when(s.retrieved_at)} · {s.content_type} · {s.passage_count} passages</p>
                  </div>
                </Panel>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
        {claimId ? <Inspector runId={run.id} claimId={claimId} highlight={null} onClose={() => { window.location.hash = hrefFor(run.id, 'evidence').slice(1); setTimeout(() => document.getElementById(`claim-link-${claimId}`)?.focus(), 0) }} /> : (
          <Empty title="Select a claim">Its exact stored evidence opens here.</Empty>
        )}
      </div>
    </div>
  )
}
