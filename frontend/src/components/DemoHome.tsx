import type { DemoInfo } from '@/api/types'
import { hrefFor } from '@/lib/route'
import { Notice, Panel, SectionHeading } from './bits'

/** A thin, always-visible strip: this deployment shows recorded runs and starts nothing. */
export function DemoBanner({ info }: { info: DemoInfo }) {
  return (
    <aside aria-label="About this demo" className="border-b bg-amber-50 px-4 py-2 text-sm text-amber-950 md:px-6">
      <strong className="font-semibold">{info.label}.</strong> These are recorded development runs, not live research. Nothing on this site starts a run or calls a model, search or page fetch.{' '}
      <a className="underline" href="#/">About this demo</a>
    </aside>
  )
}

/** Replaces the "New research" form in the hosted demo: what this is, what is withheld, the limits, and the recorded runs. */
export function DemoHome({ info }: { info: DemoInfo }) {
  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <h1 className="text-2xl font-semibold tracking-tight">DeepDive: recorded demo</h1>
      <Notice tone="neutral" title={info.label}>{info.notice}</Notice>
      <Panel>
        <SectionHeading level={2}>Recorded runs</SectionHeading>
        <ul className="mt-2 space-y-3">
          {info.runs.map((r) => (
            <li key={r.run_id} className="text-sm">
              <a className="font-medium text-primary underline" href={hrefFor(r.run_id)}>Open run {r.run_id}</a>{r.title && <span className="ml-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{r.title}</span>}
              <p className="mt-1 text-muted-foreground">{r.note}</p>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel>
        <SectionHeading level={2}>What is withheld</SectionHeading>
        <p className="mt-2 text-sm">{info.withholding}</p>
      </Panel>
      <Panel accent="gap">
        <SectionHeading level={2}>Limitations that apply to everything here</SectionHeading>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
          {info.limitations.map((l) => <li key={l}>{l}</li>)}
        </ul>
      </Panel>
    </div>
  )
}
