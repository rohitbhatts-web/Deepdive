import type { RunEvent } from '@/api/types'

/** What the run's recorded events say about collection. Counts only: nothing here is inferred about the world outside the run. */
export interface CollectionContext { searches: number; pages: number; subquestionsWithSelection: number; noUrlSelected: number; pdfSkipped: number; fetchFailed: number }

export function collectionContext(events: RunEvent[]): CollectionContext {
  const of = (t: string) => events.filter((e) => e.event_type === t)
  // One selection step is recorded per research call, and a correction round can research the same subquestion again: count SUBQUESTIONS, not steps.
  // A subquestion "had no URL selected" when none of its steps selected one. (An event without a subquestion id counts as its own.)
  const picked = new Map<string, boolean>()
  for (const e of of('urls_selected')) {
    const key = e.payload.subquestion_id === undefined ? `event-${e.id}` : String(e.payload.subquestion_id)
    picked.set(key, (picked.get(key) ?? false) || (Array.isArray(e.payload.selected) && e.payload.selected.length > 0))
  }
  return {
    searches: of('search_completed').length + of('search_failed').length,
    pages: of('source_ingested').length,
    subquestionsWithSelection: picked.size,
    noUrlSelected: [...picked.values()].filter((any) => !any).length,
    pdfSkipped: of('result_skipped').filter((e) => e.payload.reason === 'pdf_not_supported').length,
    fetchFailed: of('fetch_failed').length,
  }
}

/** Plain-language context for a report that has no claims. It states what was done and what that does NOT show. */
export function emptyReportContext(c: CollectionContext): string {
  const parts = [`The run sent ${c.searches} search ${c.searches === 1 ? 'query' : 'queries'} and stored ${c.pages} ${c.pages === 1 ? 'page' : 'pages'}.`]
  if (c.noUrlSelected > 0) parts.push(`For ${c.noUrlSelected} of ${c.subquestionsWithSelection} subquestion(s) no URL was selected to fetch at any research step.`)
  if (c.pdfSkipped > 0) parts.push(`${c.pdfSkipped} search result(s) were skipped because PDF documents are not supported.`)
  if (c.fetchFailed > 0) parts.push(`${c.fetchFailed} page fetch(es) failed.`)
  parts.push('This report only means that no claim could be verified from the pages collected. It does not show that the search was exhaustive, or that the information does not exist.')
  return parts.join(' ')
}
