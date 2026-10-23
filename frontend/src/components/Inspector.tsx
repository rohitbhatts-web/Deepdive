import { X } from 'lucide-react'
import { Fragment, useEffect, useRef } from 'react'
import type { Claim, Evidence, ResultOut } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useResource } from '@/hooks/useApi'
import { claimState } from '@/lib/claimState'
import { hrefFor } from '@/lib/route'
import { ErrorBox, Loading, Notice, Panel, Pill, SafeLink, when } from './bits'

export function ClaimPill({ claim }: { claim: Parameters<typeof claimState>[0] }) {
  const s = claimState(claim)
  return <Pill tone={s.tone} icon={s.icon}>{s.label}</Pill>
}

/** Splits `text` around the first exact occurrence of `quote`; no occurrence means no highlight (never a fuzzy match). */
function QuotedPassage({ text, quote }: { text: string; quote: string }) {
  const at = quote ? text.indexOf(quote) : -1
  return (
    <blockquote className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-md border-l-4 border-primary/40 bg-muted/60 p-3 text-sm leading-relaxed" tabIndex={0}>
      {at < 0 ? text : (
        <>
          {text.slice(0, at)}
          <mark className="rounded bg-gap-bg px-0.5 text-foreground">{quote}</mark>
          {text.slice(at + quote.length)}
        </>
      )}
    </blockquote>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{children}</p>
}

function EvidenceItem({ ev, highlighted }: { ev: Evidence; highlighted: boolean }) {
  return (
    <li id={ev.passage_id ? `passage-${ev.passage_id}` : undefined} className={`space-y-2 rounded-lg border bg-card p-3 ${highlighted ? 'border-primary ring-2 ring-primary/30' : ''}`}>
      <p className="text-sm font-medium leading-snug">
        {ev.passage?.title ?? 'Untitled source'} <span className="font-normal text-muted-foreground">· passage {ev.passage_id ?? ev.passage_ref}</span>
      </p>
      {ev.passage && <p className="text-xs"><SafeLink url={ev.passage.final_url} /></p>}
      <Label>Quote the claim cites</Label>
      <p className="whitespace-pre-wrap break-words text-sm">“{ev.quote}”</p>
      {ev.resolution !== 'resolved' && <Notice tone="bad" title="Passage did not resolve">{ev.resolution}: this reference is not a passage of this run.</Notice>}
      {ev.passage && (
        <>
          <Label>Stored passage text (exact)</Label>
          <QuotedPassage text={ev.passage.text} quote={ev.quote} />
        </>
      )}
    </li>
  )
}

function Result({ title, r }: { title: string; r: ResultOut | null }) {
  if (!r) return null
  return (
    <div className="space-y-1 rounded-lg border bg-card p-3">
      <p className="text-sm font-semibold">{title}: <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs font-normal">{r.verdict}</span></p>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{r.reason}</p>
      <p className="text-xs text-muted-foreground">{r.model ? `Model ${r.model} · ` : 'Deterministic check · '}{when(r.created_at)}</p>
    </div>
  )
}

export function Inspector({ runId, claimId, highlight, onClose }: { runId: number; claimId: number; highlight: string | null; onClose: () => void }) {
  const res = useResource<Claim>(`/runs/${runId}/claims/${claimId}`)
  const claim = res.data
  const heading = useRef<HTMLHeadingElement>(null)
  const loadedId = claim?.id
  useEffect(() => { if (loadedId === claimId) heading.current?.focus() }, [loadedId, claimId]) // keyboard users land on the opened claim
  const state = claim ? claimState(claim) : null
  const relied = claim?.semantic?.passages.filter((p) => p.role === 'neighbour_context') ?? []
  return (
    <aside id="inspector" aria-label="Claim and evidence inspector" className="lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
      <Panel accent={state?.tone}>
        <div className="space-y-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold tracking-tight outline-offset-4">Claim {claimId} inspector</h2>
            <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close inspector"><X aria-hidden /></Button>
          </div>
          {res.loading && !claim && <Loading label="Loading claim" blocks={2} />}
          {res.error && <ErrorBox error={res.error} what={`claim ${claimId}`} />}
          {claim && state && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <ClaimPill claim={claim} />
                <span className="text-xs text-muted-foreground">version {claim.version}{claim.parent_claim_id ? <> of <a className="underline" href={hrefFor(runId, 'evidence', claim.parent_claim_id)}>claim {claim.parent_claim_id}</a></> : ''}</span>
                {claim.eligible_for_report ? <Pill tone="ok" icon="check">Currently report-eligible</Pill> : <Pill tone="neutral">Not currently report-eligible</Pill>}
              </div>
              <p className="text-sm text-muted-foreground">{state.explain}</p>
              {claim.superseded_by && <Notice tone="gap" title="Superseded">A newer version exists: <a className="underline" href={hrefFor(runId, 'evidence', claim.superseded_by)}>claim {claim.superseded_by}</a>.</Notice>}
              <p className="whitespace-pre-wrap break-words text-base font-medium leading-snug">{claim.text}</p>
              <Result title="Integrity check" r={claim.integrity} />
              <Result title="Semantic verdict" r={claim.semantic} />
              {claim.semantic_verdict === null && claim.integrity_verdict === 'passed' && <Notice title="No semantic verdict stored">The verifier has not judged this claim.</Notice>}
              {relied.length > 0 && <p className="text-sm">Also relied on (neighbouring passages, not cited by the claim): {relied.map((p) => p.passage_id).join(', ')}</p>}
              <h3 className="pt-1 text-sm font-semibold">Cited evidence</h3>
              {claim.evidence.length === 0 ? <p className="text-sm text-muted-foreground">This claim cites no evidence.</p> : (
                <ul className="space-y-2">{claim.evidence.map((ev) => <EvidenceItem key={ev.id} ev={ev} highlighted={ev.passage_id === highlight} />)}</ul>
              )}
              <details className="rounded-lg border bg-card px-3 py-2 text-sm">
                <summary className="cursor-pointer font-semibold">Provenance and result history</summary>
                <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                  <dt className="text-muted-foreground">Origin</dt><dd>{claim.origin}</dd>
                  <dt className="text-muted-foreground">Created</dt><dd>{when(claim.created_at)}</dd>
                  {Object.entries(claim.provenance).map(([k, v]) => (<Fragment key={k}><dt className="text-muted-foreground">{k}</dt><dd className="break-words">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></Fragment>))}
                </dl>
                <ol className="mt-2 space-y-1">
                  {claim.history?.map((h) => (<li key={h.id}>{when(h.created_at)} · {h.stage} · <span className="font-mono text-xs">{h.verdict}</span></li>))}
                </ol>
              </details>
            </div>
          )}
        </div>
      </Panel>
    </aside>
  )
}
