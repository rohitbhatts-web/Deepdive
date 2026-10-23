import { AlertCircle, ArrowLeft, ArrowRight, BarChart3, ChevronRight, Clock, Cpu, FileText, ListChecks, Target } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Claim, Coverage, ModelCall, Report, RunSummary } from '@/api/types'
import { useResource } from '@/hooks/useApi'
import { hrefFor } from '@/lib/route'
import { coverageBySubquestion, coverageTotals, evidenceMap, outcomeSummary, requestsByStage } from '@/lib/overview'
import { cn } from '@/lib/utils'
import { Empty, ErrorBox, Loading, Panel, Pill, SafeLink, SectionHeading, num } from './bits'
import { EvidenceMap, type MapSelection } from './EvidenceMap'
import { ClaimPill, Inspector } from './Inspector'
import { latestJobRequests, type Live } from './RunPage'

interface SourceRow { id: number; final_url: string; title: string | null; retrieved_at: string; content_type: string; passage_count: number }

const PREVIEW = 3 // answer cards and gaps shown before "View full report" / "View all gaps"
const WIDE_PX = 1024 // content width at which the answer column and the map sit side by side (the @5xl container breakpoint)

/** True once the observed element is at least `px` wide. Without ResizeObserver (tests) it stays false, which is the stacked layout. */
function useWide(ref: React.RefObject<HTMLElement | null>, px: number): boolean {
  const [wide, setWide] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setWide(el.getBoundingClientRect().width >= px))
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, px])
  return wide
}

function IconTile({ icon: Icon, tone = 'teal' }: { icon: ComponentType<{ className?: string }>; tone?: 'teal' | 'amber' }) {
  return <span aria-hidden className={cn('flex size-10 shrink-0 items-center justify-center rounded-xl', tone === 'teal' ? 'bg-teal-50 text-teal-700' : 'bg-amber-100 text-amber-700')}><Icon className="size-5" /></span>
}

function Metric({ href, icon, label, value, note }: { href: string; icon: ComponentType<{ className?: string }>; label: string; value: ReactNode; note?: string }) {
  return (
    <li className="min-w-0">
      <a href={href} className="group flex h-full min-w-0 flex-col items-start gap-2 rounded-2xl border bg-card p-4 shadow-sm @md:flex-row @md:items-center @md:gap-3 transition hover:border-primary/50 hover:shadow-md">
        <IconTile icon={icon} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-muted-foreground">{label}</span>
          <span className="block whitespace-nowrap text-2xl font-semibold leading-tight tabular-nums">{value}</span>
          {note && <span className="block truncate text-sm text-muted-foreground" title={note}>{note}</span>}
        </span>
        <ChevronRight aria-hidden className="hidden size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 @md:block" />
      </a>
    </li>
  )
}

function CoverageBars({ runId, historical, rows, totals }: { runId: number; historical: boolean; rows: ReturnType<typeof coverageBySubquestion>; totals: ReturnType<typeof coverageTotals> }) {
  return (
    <Panel className="rounded-2xl shadow-sm">
      <div className="space-y-4 p-5">
        <div className="flex items-start gap-3">
          <IconTile icon={BarChart3} />
          <SectionHeading id="cov-h" description={historical ? "Stored with the report: it was assessed from claims that now need re-judging." : "Requirements judged Addressed, Partial or Open for each question."}>Coverage by question{historical && <Pill tone="gap" icon="clock" className="ml-2 align-middle">Historical</Pill>}</SectionHeading>
        </div>
        {rows.length === 0 ? <p className="text-sm text-muted-foreground">No coverage assessment is stored for this run.</p> : (
          <ul className="space-y-3.5">
            {rows.map((r) => (
              <li key={r.subquestionId} className="space-y-1.5">
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 break-words font-medium">{r.label}</span>
                  <span className="shrink-0 tabular-nums text-muted-foreground">{r.addressed} of {r.total}</span>
                </div>
                <div role="img" aria-label={`${r.label}: ${r.addressed} addressed, ${r.partial} partial, ${r.open} open, of ${r.total} requirements`} className="flex h-3 overflow-hidden rounded-full bg-muted ring-1 ring-border">
                  {r.addressed > 0 && <span style={{ width: `${(r.addressed / r.total) * 100}%` }} className="bg-teal-600" />}
                  {r.partial > 0 && <span style={{ width: `${(r.partial / r.total) * 100}%`, backgroundImage: 'repeating-linear-gradient(45deg,#f59e0b 0 4px,#fcd34d 4px 8px)' }} />}
                  {r.open > 0 && <span style={{ width: `${(r.open / r.total) * 100}%` }} className="bg-slate-300" />}
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm">
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground" aria-label="Legend">
            <span><span aria-hidden className="mr-1.5 inline-block size-2.5 rounded-full bg-teal-600 align-middle" />Addressed ({totals.addressed})</span>
            <span><span aria-hidden className="mr-1.5 inline-block size-2.5 rounded-full align-middle" style={{ backgroundImage: 'repeating-linear-gradient(45deg,#f59e0b 0 2px,#fcd34d 2px 4px)' }} />Partial ({totals.partial})</span>
            <span><span aria-hidden className="mr-1.5 inline-block size-2.5 rounded-full bg-slate-300 align-middle" />Open ({totals.open})</span>
          </p>
          <a className="inline-flex items-center gap-1 font-medium text-primary hover:underline" href={hrefFor(runId, 'coverage')}>Coverage details<ArrowRight aria-hidden className="size-4" /></a>
        </div>
      </div>
    </Panel>
  )
}

function RequestBars({ runId, calls }: { runId: number; calls: ModelCall[] | null }) {
  const rows = calls ? requestsByStage(calls) : []
  const max = Math.max(1, ...rows.map((r) => r.calls))
  return (
    <Panel className="rounded-2xl shadow-sm">
      <div className="space-y-4 p-5">
        <div className="flex items-start gap-3">
          <IconTile icon={Cpu} />
          <SectionHeading id="req-h" description="Recorded model requests for each stage of the run.">Model requests by stage</SectionHeading>
        </div>
        {calls === null ? <Loading label="Loading model requests" blocks={1} /> : rows.length === 0 ? <p className="text-sm text-muted-foreground">No model request was recorded.</p> : (
          <ul className="space-y-2.5">
            {rows.map((r) => (
              <li key={r.agent} className="grid grid-cols-[7.5rem_minmax(0,1fr)_auto] items-center gap-3 text-sm">
                <span className="truncate" title={r.label}>{r.label}</span>
                <span aria-hidden className="h-3 rounded-full bg-muted"><span className="block h-full rounded-full bg-gradient-to-r from-sky-400 to-blue-600" style={{ width: `${(r.calls / max) * 100}%` }} /></span>
                <span className="tabular-nums">{r.calls}{r.failed > 0 && <span className="text-bad"> · {r.failed} failed</span>}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-muted-foreground">{calls ? `${calls.length} recorded request${calls.length === 1 ? '' : 's'} in total` : ''}</span>
          <a className="inline-flex items-center gap-1 font-medium text-primary hover:underline" href={hrefFor(runId, 'usage')}>Usage details<ArrowRight aria-hidden className="size-4" /></a>
        </div>
      </div>
    </Panel>
  )
}

function SourcePanel({ runId, id, map, onSelect, onClose }: { runId: number; id: number; map: ReturnType<typeof evidenceMap>; onSelect: (s: MapSelection) => void; onClose: () => void }) {
  const s = map.sources.find((x) => x.id === id)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [id])
  if (!s) return null
  return (
    <aside aria-label="Source inspector">
      <Panel className="rounded-2xl shadow-sm">
        <div className="space-y-3 p-5 text-sm">
          <div className="flex items-start justify-between gap-2">
            <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold break-words outline-offset-4">{s.title}</h2>
            <button type="button" className="rounded-md border px-2.5 py-1 text-sm hover:bg-muted" onClick={onClose}>Close</button>
          </div>
          <p>S{s.id}{s.url && <> · <SafeLink url={s.url} /></>}</p>
          <p className="text-muted-foreground">{s.passages} stored passages; {s.cited} cited by the claims shown. Choose one to inspect it:</p>
          <ul className="flex flex-wrap gap-2">
            {map.passages.filter((p) => p.sourceId === id).map((p) => (
              <li key={p.id}><button type="button" className="rounded-lg border bg-muted/70 px-2.5 py-1 font-medium hover:border-primary/50" onClick={() => onSelect({ kind: 'passage', id: p.id })}>{p.id}</button></li>
            ))}
          </ul>
          <a className="inline-flex items-center gap-1 font-medium text-primary hover:underline" href={hrefFor(runId, 'evidence')}>All sources in the Evidence tab<ArrowRight aria-hidden className="size-4" /></a>
        </div>
      </Panel>
    </aside>
  )
}

/**
 * The open claim lives in the page's own deep link (`#/runs/N/overview?claim=ID`, the mechanism the Report and Evidence tabs use), so it survives a refresh, can be shared,
 * follows Back and Forward, and closing clears it. A highlighted passage belongs to the claim it was opened from; a source panel has no claim and is not part of the link.
 */
export function OverviewTab({ run, live, version, claimId }: { run: RunSummary; live: Live; version: number; claimId: number | null }) {
  const report = useResource<Report>(run.report_outcome === null ? null : `/runs/${run.id}/report`, version)
  const claims = useResource<Claim[]>(`/runs/${run.id}/claims`, version)
  const sources = useResource<SourceRow[]>(`/runs/${run.id}/sources`, version)
  const coverage = useResource<Coverage>(`/runs/${run.id}/coverage`, version)
  const questions = useResource<{ id: number; question: string }[]>(`/runs/${run.id}/subquestions`, version)
  const calls = useResource<ModelCall[]>(`/runs/${run.id}/model-calls`, version)
  const [passage, setPassage] = useState<{ claimId: number; id: string } | null>(null)
  const [source, setSource] = useState<number | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const wide = useWide(root, WIDE_PX)
  const trigger = useRef<string | null>(null)

  const rep = report.data
  const requirements = coverage.data?.requirements ?? []
  const rows = useMemo(() => coverageBySubquestion(requirements, questions.data ?? []), [requirements, questions.data])
  const totals = coverageTotals(rows)
  const answers = useMemo(() => {
    const seen = new Set<number>()
    return (rep?.sections ?? []).flatMap((s) => s.claims).filter((c) => (seen.has(c.claim_id) ? false : (seen.add(c.claim_id), true)))
  }, [rep])
  const map = useMemo(() => evidenceMap(claims.data ?? [], sources.data ?? [], new Set(answers.map((a) => a.claim_id))), [claims.data, sources.data, answers])
  const current = useMemo(() => new Map((claims.data ?? []).map((c) => [c.id, c])), [claims.data])
  const stale = answers.filter((a) => { const c = current.get(a.claim_id); return c !== undefined && !c.eligible_for_report }).length
  // while the stored report or claims are still being read, say so: "no report" or a claim count would be a statement the page cannot yet make
  const pending = (run.report_outcome !== null && report.loading && !rep) || (claims.loading && !claims.data)
  const outcome = pending ? { headline: 'Loading the stored report…', detail: 'The answer, evidence and coverage appear as soon as they are read.', tone: 'neutral' as const }
    : outcomeSummary(run, rep, live.completion, rep ? rep.requirements : requirements, stale)
  const open = rep ? rep.unresolved_requirements : requirements.filter((r) => r.status !== 'addressed')
  const active = live.research.status === 'researching' || run.complete_status === 'completing' || run.verify_status === 'verifying'
  const stop = live.completion.stop_reason ?? run.stop_reason
  const stoppedEarly = !active && (run.complete_status === 'stopped' || run.status === 'stopped')

  const setLink = useCallback((id: number | null) => { window.location.hash = hrefFor(run.id, 'overview', id).slice(1) }, [run.id])
  const pick = useCallback((s: MapSelection) => {
    trigger.current = (document.activeElement as HTMLElement | null)?.id || trigger.current
    if (s.kind === 'source') { setSource(s.id); setPassage(null); setLink(null); return }
    const p = s.kind === 'passage' ? map.passages.find((x) => x.id === s.id) : undefined
    const owner = s.kind === 'claim' ? s.id : (s.claimId ?? p?.claimIds[0] ?? p?.contextOnlyFor[0] ?? null)
    if (owner === null) return
    setSource(null)
    setPassage(s.kind === 'passage' ? { claimId: owner, id: s.id } : null)
    setLink(owner)
  }, [map, setLink])
  const close = useCallback(() => {
    setPassage(null)
    setSource(null)
    setLink(null)
    const id = trigger.current
    if (id) setTimeout(() => document.getElementById(id)?.focus(), 0) // keyboard users return to the item that opened the inspector
  }, [setLink])
  const selected = claimId !== null || source !== null
  useEffect(() => {
    if (!selected) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) close() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [selected, close])

  if (claims.error && claims.error.status !== 404) return <ErrorBox error={claims.error} what="claims" />
  const highlight = passage && passage.claimId === claimId ? passage.id : null // a highlight only applies to the claim it was opened from (Back or a new link drops it)
  const mapSel: MapSelection | null = claimId !== null ? (highlight && map.passages.some((p) => p.id === highlight) ? { kind: 'passage', id: highlight, claimId } : { kind: 'claim', id: claimId })
    : source !== null ? { kind: 'source', id: source } : null
  const inspector = claimId !== null ? <Inspector key={`${claimId}-${highlight}`} runId={run.id} claimId={claimId} highlight={highlight} onClose={close} />
    : source !== null ? <SourcePanel runId={run.id} id={source} map={map} onSelect={pick} onClose={close} /> : null

  const shownAnswers = answers.slice(0, PREVIEW)
  const moreAnswers = answers.length - shownAnswers.length
  const shownGaps = open.slice(0, PREVIEW)
  const technical = stoppedEarly || Boolean(stop)
  const tone = outcome.tone

  // shown wherever the answer is shown, including above the inspector that replaces the answer column on wide screens
  const historicalNote = stale > 0 ? (
    <p className="flex items-start gap-2 rounded-lg border border-gap/30 bg-gap-bg px-3 py-2 text-sm font-medium text-gap"><Clock aria-hidden className="mt-0.5 size-4 shrink-0" />Historical answer: {stale} of {answers.length} claim{answers.length === 1 ? "" : "s"} need{stale === 1 ? "s" : ""} re-judging.</p>
  ) : null

  const answerCard = (
    <Panel className="rounded-2xl shadow-sm">
      <div className="space-y-4 p-5">
        <div className="flex items-start gap-3">
          <IconTile icon={FileText} />
          <SectionHeading id="answer-h" description="Claims the report lists as answers, exactly as stored.">Research answer</SectionHeading>
        </div>
        {historicalNote}
        {report.loading && !rep && <Loading label="Loading answer" blocks={2} />}
        {!rep && !report.loading && <Empty title={active ? 'The answer is not written yet' : 'No answer was produced'}>{active ? 'It appears here when the run finishes.' : 'This run stored no report, so there is nothing to preview.'}</Empty>}
        {rep && answers.length === 0 && <p className="text-sm text-muted-foreground">No verified claim directly answers a requirement of this question.</p>}
        <ul className="space-y-3">
          {shownAnswers.map((c) => {
            const cur = current.get(c.claim_id)
            return (
              <li key={c.claim_id} className="rounded-xl border bg-card p-4 shadow-sm">
                <div className="flex flex-col items-start gap-2 @md:flex-row @md:justify-between @md:gap-3">
                  <p className="min-w-0 whitespace-pre-wrap break-words text-base font-medium leading-snug">{c.text}</p>
                  {cur && <span className="shrink-0"><ClaimPill claim={cur} /></span>}
                </div>
                {(c.requirement_texts?.length ?? 0) > 0 && <p className="mt-2 line-clamp-2 text-sm text-muted-foreground" title={c.requirement_texts!.join('; ')}>Answers: {c.requirement_texts!.join('; ')}</p>}
                <button type="button" id={`ans-clm-${c.claim_id}`} aria-label={`Inspect evidence for claim ${c.claim_id}`} onClick={() => pick({ kind: 'claim', id: c.claim_id })}
                  className="mt-3 inline-flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-1.5 text-sm font-medium transition-colors hover:border-primary/50 hover:bg-accent">
                  <FileText aria-hidden className="size-4 text-teal-700" />
                  Inspect evidence · {c.citations.length} passage{c.citations.length === 1 ? '' : 's'}
                  <ChevronRight aria-hidden className="size-4 text-muted-foreground" />
                </button>
              </li>
            )
          })}
        </ul>
        {rep && <a className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline" href={hrefFor(run.id, 'report')}>View full report{moreAnswers > 0 ? ` (${moreAnswers} more claim${moreAnswers === 1 ? '' : 's'})` : ''}<ArrowRight aria-hidden className="size-4" /></a>}
      </div>
    </Panel>
  )

  const gapsCard = (
    <section aria-labelledby="open-h" className={cn('rounded-2xl border p-5 shadow-sm', open.length ? 'border-gap/30 bg-gap-bg/70' : 'bg-card')}>
      <div className="space-y-3">
        <div className="flex items-start gap-3">
          <IconTile icon={AlertCircle} tone="amber" />
          <SectionHeading id="open-h" description={open.length ? 'Requirements the stored claims do not fully answer.' : undefined}>Still unanswered{open.length ? ` (${open.length})` : ''}</SectionHeading>
        </div>
        {open.length === 0 ? (
          <p className="text-sm">{rep || requirements.length ? 'Every requirement was assessed as addressed. That is a model assessment, not a guarantee of truth.' : 'No coverage assessment is stored, so open requirements are unknown.'}</p>
        ) : (
          <ul className="space-y-2.5">
            {shownGaps.map((r) => {
              const x = r as { requirement_key?: string; requirement_text?: string; gap?: string; status?: string }
              return (
                <li key={x.requirement_key} className="rounded-xl border border-gap/25 bg-card/80 px-4 py-3">
                  <p className="font-medium">{x.requirement_text}</p>
                  <p className="line-clamp-2 text-sm text-muted-foreground" title={x.gap}><span className="font-medium text-gap">{x.status === 'partially_addressed' ? 'Partial' : 'Open'}</span>{x.gap ? `: ${x.gap}` : ''}</p>
                </li>
              )
            })}
          </ul>
        )}
        {(open.length > 0 || rep || requirements.length > 0) && <a className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline" href={hrefFor(run.id, 'coverage')}>View all gaps{open.length > PREVIEW ? ` (${open.length - PREVIEW} more)` : ''}<ArrowRight aria-hidden className="size-4" /></a>}
      </div>
    </section>
  )

  return (
    <div ref={root} className={cn('@container flex flex-col gap-5', selected && !wide && 'pb-[55vh]')}>
      <Panel className="rounded-2xl shadow-sm">
        <div role="status" className="p-5">
          <div>
            <p className={cn('text-lg font-semibold leading-snug', tone === 'bad' && 'text-bad', tone === 'gap' && 'text-gap')}>{outcome.headline}</p>
            <p className="text-sm text-muted-foreground">{outcome.detail}</p>
            {technical && (
              <details className="mt-1.5 text-sm">
                <summary className="cursor-pointer font-medium text-primary">Technical stop details</summary>
                <div className="mt-1.5 space-y-1 text-muted-foreground">
                  {stoppedEarly && <p>Stopped early, so results are partial. Whatever was verified before the stop is kept; requirements that were not addressed are listed as gaps.</p>}
                  {stop && <p>Reason: {stop}</p>}
                </div>
              </details>
            )}
          </div>
        </div>
      </Panel>

      <ul className="order-[20] grid grid-cols-2 gap-4 @3xl:grid-cols-4 @5xl:order-1" aria-label="Run at a glance">
        <Metric href={hrefFor(run.id, 'evidence')} icon={FileText} label="Sources collected" value={num(live.research.counts.sources)} note={`${map.counts.sourcesCited} cited by a claim`} />
        <Metric href={hrefFor(run.id, 'report')} icon={ListChecks} label="Claims in report" value={rep ? answers.length : pending ? '…' : 'None yet'} note={stale ? `${stale} not eligible now` : 'verified answers'} />
        <Metric href={hrefFor(run.id, 'coverage')} icon={Target} label="Requirements addressed" value={totals.total ? `${totals.addressed} of ${totals.total}` : coverage.loading ? '…' : 'Unavailable'} note={totals.total ? `${totals.partial} partial · ${totals.open} open` : 'not assessed'} />
        <Metric href={hrefFor(run.id, 'usage')} icon={Cpu} label="Model requests" value={latestJobRequests(live.completion)} note={`latest job · ${num(live.research.model_usage.model_calls)} in total`} />
      </ul>

      <div className="contents @5xl:order-2 @5xl:grid @5xl:items-start @5xl:gap-5 @5xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="order-[10] min-w-0 space-y-5 @5xl:order-none">
          {wide && inspector ? (
            <div aria-live="polite" className="space-y-3">
              {historicalNote}
              <button type="button" onClick={close} className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-sm hover:border-primary/50"><ArrowLeft aria-hidden className="size-4" />Back to answer</button>
              {inspector}
            </div>
          ) : (<>{answerCard}{gapsCard}</>)}
        </div>
        <div className="order-[30] min-w-0 @5xl:order-none">
          {claims.loading && !claims.data ? <Loading label="Loading evidence map" blocks={3} /> : <EvidenceMap runId={run.id} data={map} selected={mapSel} onSelect={pick} />}
        </div>
      </div>

      <div className="order-[40] grid items-start gap-5 @5xl:order-3 @5xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <CoverageBars runId={run.id} historical={stale > 0} rows={rows} totals={totals} />
        <RequestBars runId={run.id} calls={calls.data ?? (calls.error ? [] : null)} />
      </div>

      {!wide && inspector && createPortal(
        <div aria-live="polite" className="fixed inset-x-0 bottom-0 z-50 max-h-[55vh] overflow-y-auto rounded-t-2xl border-t bg-background p-3 shadow-2xl sm:mx-auto sm:max-w-3xl">{inspector}</div>,
        document.body,
      )}
    </div>
  )
}
