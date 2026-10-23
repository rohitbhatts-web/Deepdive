import { FileText, Link2, Network, Quote } from 'lucide-react'
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { hrefFor } from '@/lib/route'
import type { EvidenceMapData } from '@/lib/overview'
import { cn } from '@/lib/utils'
import { Panel, SectionHeading } from './bits'

export type MapSelection = { kind: 'claim'; id: number } | { kind: 'passage'; id: string; claimId?: number } | { kind: 'source'; id: number }

const nodeId = (s: MapSelection) => (s.kind === 'claim' ? `clm-${s.id}` : s.kind === 'passage' ? `psg-${s.id}` : `src-${s.id}`)

/** Nodes connected to `from` through stored edges, up to two steps (source - passage - claim), in both directions. */
function connected(edges: { from: string; to: string }[], from: string): Set<string> {
  const out = new Set<string>([from])
  for (let step = 0; step < 2; step++) {
    for (const e of edges) {
      if (out.has(e.from)) out.add(e.to)
      if (out.has(e.to)) out.add(e.from)
    }
  }
  return out
}

const claimStatus = (c: { inReport: boolean; eligible: boolean }) => (c.inReport ? 'In report' : c.eligible ? 'Verified, not in report' : 'Not reported')

/**
 * Sources -> passages -> claims, drawn from the stored citations of a selected subset (the counts say how much is left out). Every node is a button; choosing one
 * opens the existing evidence inspector. The connecting lines are decoration over the same facts the list below states in words, so nothing depends on seeing them.
 */
export function EvidenceMap({ runId, data, selected, onSelect }: { runId: number; data: EvidenceMapData; selected: MapSelection | null; onSelect: (s: MapSelection) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [paths, setPaths] = useState<{ key: string; d: string; kind: string; on: boolean }[]>([])
  const lit = useMemo(() => (selected ? connected(data.edges, nodeId(selected)) : null), [selected, data.edges])

  const measure = useCallback(() => {
    const root = box.current
    if (!root) return
    const base = root.getBoundingClientRect()
    const at = (id: string, side: 'l' | 'r') => {
      const el = root.querySelector<HTMLElement>(`[data-node="${id}"]`)
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: (side === 'r' ? r.right : r.left) - base.left, y: r.top + r.height / 2 - base.top }
    }
    setPaths(data.edges.flatMap((e) => {
      const a = at(e.from, 'r')
      const b = at(e.to, 'l')
      if (!a || !b) return []
      const mid = (a.x + b.x) / 2
      return [{ key: `${e.from}>${e.to}`, d: `M${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`, kind: e.kind, on: !lit || (lit.has(e.from) && lit.has(e.to)) }]
    }))
  }, [data.edges, lit])
  useLayoutEffect(() => {
    measure()
    if (typeof ResizeObserver === 'undefined' || !box.current) return
    const ro = new ResizeObserver(measure)
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [measure])

  const pick = (s: MapSelection) => () => onSelect(s)
  const dim = (id: string) => (lit && !lit.has(id) ? 'bg-muted/50 text-muted-foreground' : '') // unrelated nodes recede without lowering text contrast
  const c = data.counts

  return (
    <Panel className="rounded-2xl shadow-sm">
      <div className="space-y-4 p-5">
        <div className="flex items-start gap-3">
          <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700"><Network className="size-5" /></span>
          <SectionHeading id="map-h" description="How stored sources connect to the passages and claims that cite them.">Evidence map</SectionHeading>
        </div>
        {data.claims.length === 0 ? (
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">No claim cites a stored passage yet, so there is nothing to connect.</p>
        ) : (
          <div ref={box} className="@container relative">
            <svg aria-hidden="true" focusable="false" className="pointer-events-none absolute inset-0 hidden size-full @lg:block">
              {paths.map((p) => (
                <path key={p.key} d={p.d} fill="none" strokeLinecap="round" strokeWidth={p.on ? 2 : 1} strokeDasharray={p.kind === 'context' ? '5 4' : undefined}
                  className={p.kind === 'contains' ? 'stroke-slate-300' : p.kind === 'cites' ? 'stroke-teal-600' : 'stroke-blue-500'} opacity={p.on ? 0.95 : 0.12} />
              ))}
            </svg>
            <div className="relative grid gap-4 @lg:grid-cols-[0.95fr_0.8fr_1.15fr] @lg:gap-7">
              <ul aria-label="Sources" className="space-y-2">
                <li aria-hidden className="pb-1"><span className="block text-sm font-semibold">Sources</span><span className="block text-sm text-muted-foreground">{c.sourcesShown} of {c.sourcesTotal} shown</span></li>
                {data.sources.map((s) => (
                  <li key={s.id}>
                    <button type="button" data-node={`src-${s.id}`} id={`map-src-${s.id}`} aria-pressed={selected?.kind === 'source' && selected.id === s.id} onClick={pick({ kind: 'source', id: s.id })}
                      className={cn('flex w-full items-start gap-2 rounded-xl border bg-card p-2.5 text-left text-sm shadow-sm transition-colors hover:border-primary/50', dim(`src-${s.id}`), selected?.kind === 'source' && selected.id === s.id && 'border-primary ring-2 ring-primary/30')}>
                      <FileText aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
                      <span className="min-w-0">
                        <span className="line-clamp-2 break-words font-semibold" title={s.title}>{s.title}</span>
                        <span className="block text-sm text-muted-foreground">{s.cited} of {s.passages} passages</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <ul aria-label="Passages" className="space-y-2">
                <li aria-hidden className="pb-1"><span className="block text-sm font-semibold">Passages</span><span className="block text-sm text-muted-foreground">{c.passagesShown} of {c.passagesCitedTotal} cited shown</span></li>
                {data.passages.map((p) => (
                  <li key={p.id}>
                    <button type="button" data-node={`psg-${p.id}`} id={`map-psg-${p.id}`} aria-pressed={selected?.kind === 'passage' && selected.id === p.id} onClick={pick({ kind: 'passage', id: p.id })}
                      className={cn('flex w-full items-center gap-2 rounded-xl border bg-card p-2.5 text-left text-sm shadow-sm transition-colors hover:border-primary/50', dim(`psg-${p.id}`), selected?.kind === 'passage' && selected.id === p.id && 'border-primary ring-2 ring-primary/30')}>
                      <Quote aria-hidden className="size-4 shrink-0 text-blue-600" />
                      <span className="min-w-0 whitespace-nowrap text-sm font-semibold">{p.id}</span>
                      <span className="ml-auto shrink-0 text-sm text-muted-foreground">{p.claimIds.length + p.contextOnlyFor.length} claim{p.claimIds.length + p.contextOnlyFor.length === 1 ? '' : 's'}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <ul aria-label="Claims" className="space-y-2">
                <li aria-hidden className="pb-1"><span className="block text-sm font-semibold">Claims</span><span className="block text-sm text-muted-foreground">{c.claimsShown} of {c.claimsTotal} shown</span></li>
                {data.claims.map((cl) => (
                  <li key={cl.id}>
                    <button type="button" data-node={`clm-${cl.id}`} id={`map-clm-${cl.id}`} aria-pressed={selected?.kind === 'claim' && selected.id === cl.id} onClick={pick({ kind: 'claim', id: cl.id })}
                      className={cn('flex w-full items-start gap-2 rounded-xl border bg-card p-2.5 text-left text-sm shadow-sm transition-colors hover:border-primary/50', dim(`clm-${cl.id}`), selected?.kind === 'claim' && selected.id === cl.id && 'border-primary ring-2 ring-primary/30')}>
                      <Link2 aria-hidden className="mt-0.5 size-4 shrink-0 text-teal-700" />
                      <span className="min-w-0">
                        <span className="line-clamp-3 break-words" title={cl.text}>{cl.text}</span>
                        <span className="block text-sm text-muted-foreground">Claim {cl.id} · {claimStatus(cl)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <p className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted-foreground" aria-label="Legend">
              <span><span aria-hidden className="mr-1.5 inline-block h-0.5 w-5 bg-teal-600 align-middle" />Cites</span>
              <span><span aria-hidden className="mr-1.5 inline-block h-0.5 w-5 border-t-2 border-dashed border-blue-500 align-middle" />Neighbouring passage the verdict relied on</span>
              <span><span aria-hidden className="mr-1.5 inline-block h-0.5 w-5 bg-slate-300 align-middle" />Belongs to source</span>
            </p>
          </div>
        )}
        <details className="rounded-xl border bg-muted/30 px-4 py-2.5 text-sm">
          <summary className="cursor-pointer font-medium">List of all {data.list.length} connected claim{data.list.length === 1 ? '' : 's'}</summary>
          <ul className="mt-3 space-y-2" aria-label="Evidence connections">
            {data.list.map((cl) => (
              <li key={cl.id} className="rounded-lg bg-card p-3">
                <button type="button" id={`list-clm-${cl.id}`} className="text-left font-medium underline-offset-2 hover:underline" onClick={pick({ kind: 'claim', id: cl.id })}>Claim {cl.id}: {cl.text}</button>
                <span className="block text-muted-foreground">{claimStatus(cl)} · <a className="underline" href={hrefFor(runId, 'evidence', cl.id)}>open in the Evidence tab</a></span>
                <ul className="mt-1.5 space-y-1 pl-3">
                  {cl.cites.map((ct) => (
                    <li key={`${ct.passageId}-${ct.context}`}>
                      <button type="button" className="font-semibold underline-offset-2 hover:underline" onClick={pick({ kind: 'passage', id: ct.passageId, claimId: cl.id })}>{ct.passageId}</button>
                      {' '}from {ct.sourceTitle}{ct.context ? ' (neighbouring passage the verdict relied on)' : ''}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </Panel>
  )
}
