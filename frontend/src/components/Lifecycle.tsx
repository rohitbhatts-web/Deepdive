import { AlertTriangle, ChevronDown, Circle, CircleDashed, Clock, Loader2, Square } from 'lucide-react'
import { Fragment } from 'react'
import type { Completion, RunEvent, RunSummary } from '@/api/types'
import { lifecycle, type LifeState } from '@/lib/overview'
import { cn } from '@/lib/utils'
import { Panel, SectionHeading, when } from './bits'

const LIFE_TEXT: Record<LifeState, string> = { recorded: 'Recorded', running: 'Running', stopped: 'Stopped', failed: 'Failed', not_recorded: 'Not recorded', waiting: 'Waiting' }
const ICON = { recorded: Circle, failed: AlertTriangle, stopped: Square, running: Loader2, waiting: Clock, not_recorded: CircleDashed } as const

function Dot({ state, size = 'md' }: { state: LifeState; size?: 'sm' | 'md' }) {
  const Icon = ICON[state]
  return (
    <span aria-hidden className={cn('flex shrink-0 items-center justify-center rounded-full border', size === 'md' ? 'size-8' : 'size-6',
      state === 'recorded' && 'border-teal-600/50 bg-teal-50 text-teal-600', state === 'failed' && 'border-bad/40 bg-bad-bg text-bad', state === 'stopped' && 'border-gap/40 bg-gap-bg text-gap',
      (state === 'not_recorded' || state === 'waiting') && 'bg-muted text-muted-foreground', state === 'running' && 'border-primary/40 bg-accent text-primary')}>
      <Icon className={cn(size === 'md' ? 'size-4' : 'size-3.5', state === 'recorded' && 'size-2 fill-current', state === 'running' && 'motion-safe:animate-spin')} />
    </span>
  )
}

const STRIP_NOTE = 'A recorded stage ran and stored an event. It does not mean the answer is complete.'

function StageItems({ stages }: { stages: ReturnType<typeof lifecycle> }) {
  return (
    <>
      {stages.map((s, i) => (
        <Fragment key={s.key}>
          <li className="flex min-w-0 items-center gap-2">
            <Dot state={s.state} size="sm" />
            <span className="min-w-0 text-sm leading-tight">
              <span className="font-semibold">{s.label}</span>
              {s.state === 'recorded' ? <span className="sr-only">: {LIFE_TEXT[s.state]}</span> : <span className="block text-muted-foreground">{LIFE_TEXT[s.state]}</span>}
            </span>
          </li>
          {i < stages.length - 1 && <li aria-hidden className="hidden h-px min-w-2 flex-1 bg-border/80 @xl:block" />}
        </Fragment>
      ))}
    </>
  )
}

/**
 * The compact stage strip in the Overview header: stage names, and a state word only when a stage is not simply Recorded. Timestamps live in Run history.
 * On narrow screens it collapses into an expandable “Recorded stages” row (a stage that is not Recorded is named in the row itself).
 */
export function LifecycleStrip({ run, events, completion }: { run: RunSummary; events: RunEvent[]; completion: Completion | null }) {
  const stages = lifecycle(run, events, completion)
  const recorded = stages.filter((s) => s.state === 'recorded').length
  const notable = stages.filter((s) => s.state !== 'recorded' && s.state !== 'waiting')
  return (
    <>
      <ol aria-label="Research lifecycle" title={STRIP_NOTE} className="hidden items-center gap-2 @xl:flex">
        <StageItems stages={stages} />
      </ol>
      <details className="group @xl:hidden">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
          <ChevronDown aria-hidden className="size-4 transition-transform group-open:rotate-180" />
          Recorded stages
          <span className="font-normal text-muted-foreground">{recorded} of {stages.length}{notable.length ? ` · ${notable.map((s) => `${s.label} ${LIFE_TEXT[s.state].toLowerCase()}`).join(', ')}` : ''}</span>
        </summary>
        <ol aria-label="Recorded stages" className="mt-2.5 grid grid-cols-3 gap-x-3 gap-y-2.5">
          <StageItems stages={stages} />
        </ol>
        <p className="mt-2 text-sm text-muted-foreground">{STRIP_NOTE}</p>
      </details>
    </>
  )
}

/** The same stages with the stored time of each event, for Run history. A stage with no stored event is “Not recorded”, never assumed. */
export function LifecycleDetail({ run, events, completion }: { run: RunSummary; events: RunEvent[]; completion: Completion | null }) {
  const stages = lifecycle(run, events, completion)
  return (
    <section aria-labelledby="life-h" className="space-y-3">
      <SectionHeading id="life-h" description="Each stage is shown only if the run stored an event for it.">Research lifecycle</SectionHeading>
      <Panel>
        <ol aria-label="Lifecycle stages with times" className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {stages.map((s) => (
            <li key={s.key} className="flex items-start gap-3">
              <Dot state={s.state} size="sm" />
              <span>
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="block text-sm text-muted-foreground">{LIFE_TEXT[s.state]}{s.at ? <> · {when(s.at)}</> : null}</span>
              </span>
            </li>
          ))}
        </ol>
      </Panel>
    </section>
  )
}
