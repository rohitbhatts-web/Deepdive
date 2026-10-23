import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Inspector } from '@/components/Inspector'
import { NewResearch } from '@/components/NewResearch'
import { ReportTab } from '@/components/ReportTab'
import { usePolling } from '@/hooks/useApi'
import { claimState } from '@/lib/claimState'
import { CLAIM_A, CLAIM_B, PASSAGE_A, REPORT, RUN, claim, stubFetch } from './fixtures'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  window.location.hash = ''
})

const reportRoutes = (extra = {}) => ({
  '/runs/7/report': REPORT,
  '/runs/7/claims': [CLAIM_A, CLAIM_B],
  '/runs/7/claims/1': CLAIM_A,
  '/runs/7/claims/2': CLAIM_B,
  ...extra,
})
const LIMITS = { limits: { model_requests: 40 }, question_min_chars: 3, question_max_chars: 2000, max_correction_rounds: 2 }

describe('report and inspector', () => {
  it('renders the snapshot text exactly, as plain text', async () => {
    stubFetch(reportRoutes())
    render(<ReportTab run={RUN} claimId={null} version={1} active={false} />)
    const button = await screen.findByRole('button', { name: /The cache holds/ })
    expect(button.textContent).toBe(CLAIM_A.text) // double spaces and <i> markup preserved verbatim
    expect(button.querySelector('i')).toBeNull() // never interpreted as HTML
  })

  it('a citation opens the evidence of its own claim', async () => {
    const calls = stubFetch(reportRoutes())
    const { rerender } = render(<ReportTab run={RUN} claimId={null} version={1} active={false} />)
    await userEvent.click(await screen.findByRole('button', { name: /\[1\] Source B · S2-P3/ }))
    expect(window.location.hash).toBe('#/runs/7/report?claim=2')
    rerender(<ReportTab run={RUN} claimId={2} version={1} active={false} />)
    expect(await screen.findByRole('heading', { name: 'Claim 2 inspector' })).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/runs/7/claims/2')).toBe(true)
    expect(calls.some((c) => c.path === '/runs/7/claims/1')).toBe(false)
    expect(await screen.findByText(/Beta passage says/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /example\.test\/b/ })
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('shows the exact stored passage and highlights only an exact quote match', async () => {
    stubFetch(reportRoutes())
    render(<Inspector runId={7} claimId={1} highlight="S1-P0" onClose={() => {}} />)
    const mark = await screen.findByText('The cache holds 512 entries.', { selector: 'mark' })
    expect(mark.parentElement?.textContent).toBe(PASSAGE_A)
  })

  it('does not present a missing or errored verdict as supported', async () => {
    expect(claimState(claim({ id: 9, semantic_verdict: null, semantic: null })).label).toBe('Not judged')
    expect(claimState(claim({ id: 9, semantic_verdict: 'error' })).label).toBe('Verification error')
    expect(claimState(claim({ id: 9, integrity_verdict: null, semantic_verdict: null })).label).toBe('Not checked')
    expect(claimState(claim({ id: 9, semantic_verdict: 'unsupported' })).label).toBe('Unsupported')
    expect(claimState(claim({ id: 9, origin: 'controlled_challenge' })).label).toBe('Test input')
    expect(claimState(claim({ id: 9, needs_rejudge: true })).label).toBe('Needs re-judge')
    stubFetch({ '/runs/7/claims/9': claim({ id: 9, semantic_verdict: 'error', eligible_for_report: false, semantic: null }) })
    render(<Inspector runId={7} claimId={9} highlight={null} onClose={() => {}} />)
    expect(await screen.findByText('Verification error')).toBeInTheDocument()
    expect(screen.queryByText('Supported')).toBeNull()
    expect(screen.getByText('Not currently report-eligible')).toBeInTheDocument()
  })

  it('marks a report claim whose current state changed since the snapshot', async () => {
    const excluded = claim({ ...CLAIM_B, excluded: { reason: 'fixture exclusion', planned_round: 1 }, eligible_for_report: false })
    stubFetch(reportRoutes({ '/runs/7/claims': [CLAIM_A, excluded] }))
    render(<ReportTab run={RUN} claimId={null} version={1} active={false} />)
    expect(await screen.findByText(/Changed since this snapshot: Excluded/)).toBeInTheDocument()
  })

  it('shows partial coverage and the unresolved requirement with its gap', async () => {
    stubFetch(reportRoutes())
    render(<ReportTab run={RUN} claimId={null} version={1} active={false} />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(screen.getByText('Unresolved')).toBeInTheDocument()
    expect(screen.getByText(/Gap: no evidence on writers/)).toBeInTheDocument()
  })

  it('shows an empty state when there is no report, and a failed state on a server error', async () => {
    stubFetch({ '/runs/7/claims': [] })
    const { unmount } = render(<ReportTab run={{ ...RUN, complete_status: 'stopped' }} claimId={null} version={1} active={false} />)
    expect(await screen.findByText('This run has no report')).toBeInTheDocument()
    unmount()
    stubFetch({ '/runs/7/report': { __status: 500, detail: 'database is locked' }, '/runs/7/claims': [] })
    render(<ReportTab run={RUN} claimId={null} version={1} active={false} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('database is locked')
  })
})

describe('starting research', () => {
  it('validates the question and shows the configured limits', async () => {
    stubFetch({ '/limits': LIMITS })
    render(<NewResearch onStarted={() => {}} />)
    expect(await screen.findByText('Model requests')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Research question'), 'ab')
    await userEvent.click(screen.getByRole('button', { name: /Start research/ }))
    expect(screen.getByText('Enter at least 3 characters.')).toBeInTheDocument()
  })

  it('sends one start even if the button is clicked repeatedly, and disables it while pending', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => { release = r })
    const calls = stubFetch({
      '/limits': LIMITS,
      '/runs': () => ({ ...RUN, id: 11 }),
      '/runs/11/research?complete=true': () => ({ run_id: 11, status: 'researching' }),
    })
    const base = globalThis.fetch
    globalThis.fetch = vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
      if (String(i).endsWith('/runs') && init?.method === 'POST') await gate
      return base(i, init)
    }) as typeof fetch
    const started = vi.fn()
    render(<NewResearch onStarted={started} />)
    await userEvent.type(screen.getByLabelText('Research question'), 'a valid question')
    const button = screen.getByRole('button', { name: /Start research/ })
    fireEvent.click(button)
    fireEvent.click(button)
    fireEvent.click(button)
    expect(await screen.findByRole('button', { name: /Starting/ })).toBeDisabled()
    release()
    await waitFor(() => expect(started).toHaveBeenCalledWith(11))
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/runs')).toHaveLength(1)
    expect(calls.filter((c) => c.path.startsWith('/runs/11/research'))).toHaveLength(1)
  })

  it('displays a backend error clearly and links the saved run', async () => {
    stubFetch({
      '/limits': LIMITS,
      '/runs': () => ({ ...RUN, id: 12 }),
      '/runs/12/research?complete=true': { __status: 503, detail: { error: 'not_configured', message: 'Mesh is not configured', missing: ['MESH_API_KEY'] } },
    })
    render(<NewResearch onStarted={() => {}} />)
    await userEvent.type(screen.getByLabelText('Research question'), 'a valid question')
    await userEvent.click(screen.getByRole('button', { name: /Start research/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Mesh is not configured (missing: MESH_API_KEY)')
    expect(screen.getByRole('link', { name: 'run 12' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Start research/ })).toBeEnabled()
  })
})

describe('polling', () => {
  it('stops requesting once a terminal state arrives', async () => {
    vi.useFakeTimers()
    let calls = 0
    function Probe() {
      const s = usePolling<string>('k', async () => { calls += 1; return calls >= 3 ? 'done' : 'running' }, (d) => d === 'done', 1000)
      return <p>{s.data}|{s.polling ? 'polling' : 'stopped'}</p>
    }
    render(<Probe />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(calls).toBe(3)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(calls).toBe(3)
    expect(screen.getByText('done|stopped')).toBeInTheDocument()
  })

  it('cancels the timer after unmount (navigation away)', async () => {
    vi.useFakeTimers()
    let calls = 0
    function Probe() {
      usePolling<string>('k', async () => { calls += 1; return 'running' }, () => false, 1000)
      return null
    }
    const { unmount } = render(<Probe />)
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    const before = calls
    expect(before).toBeGreaterThanOrEqual(2)
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(calls).toBe(before)
  })
})
