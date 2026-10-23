import { AlertTriangle, Ban, Check, Clock, ExternalLink, FlaskConical, HelpCircle, Inbox, Loader2, X } from 'lucide-react'
import type { ReactNode } from 'react'
import type { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import type { StateIcon, Tone } from '@/lib/claimState'
import { cn } from '@/lib/utils'

const TONE: Record<Tone, string> = {
  ok: 'bg-ok-bg text-ok border-ok/30',
  gap: 'bg-gap-bg text-gap border-gap/30',
  bad: 'bg-bad-bg text-bad border-bad/30',
  neutral: 'bg-neutral-bg text-neutral border-neutral/25',
}
const ACCENT: Record<Tone, string> = { ok: 'border-l-ok', gap: 'border-l-gap', bad: 'border-l-bad', neutral: 'border-l-neutral/40' }
const ICON = { check: Check, alert: AlertTriangle, x: X, help: HelpCircle, flask: FlaskConical, ban: Ban, clock: Clock } as const

/** Status chip (shadcn Badge): always a text label plus an icon, never colour alone. */
export function Pill({ tone, icon, children, className }: { tone: Tone; icon?: StateIcon; children: ReactNode; className?: string }) {
  const Icon = icon ? ICON[icon] : null
  return (
    <Badge variant="outline" className={cn('gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium whitespace-nowrap', TONE[tone], className)}>
      {Icon && <Icon aria-hidden className="size-3.5" />}
      {children}
    </Badge>
  )
}

/** A restrained bordered card (shadcn Card without its default large padding and shadow). */
export function Panel({ children, className, accent }: { children: ReactNode; className?: string; accent?: Tone }) {
  return <Card className={cn('gap-0 rounded-lg py-0 shadow-none', accent && cn('border-l-4', ACCENT[accent]), className)}>{children}</Card>
}

/** Section heading with an optional one-line description; `level` keeps the heading order of the page. */
export function SectionHeading({ id, level = 2, children, description, className }: { id?: string; level?: 2 | 3; children: ReactNode; description?: ReactNode; className?: string }) {
  const H = level === 2 ? 'h2' : 'h3'
  return (
    <div className={cn('space-y-0.5', className)}>
      <H id={id} className={cn('font-semibold tracking-tight', level === 2 ? 'text-lg' : 'text-base')}>{children}</H>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  )
}

/** One label and one real value (a `dt`/`dd` pair; place inside a `dl`). */
export function StatTile({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-col justify-between gap-1 rounded-lg border bg-card px-3 py-2">
      <dt className="text-xs leading-tight text-muted-foreground">{label}</dt>
      <dd className="truncate text-base font-semibold tabular-nums" title={hint}>{children}</dd>
    </div>
  )
}

export function Notice({ tone = 'neutral', title, children }: { tone?: Tone; title?: string; children?: ReactNode }) {
  const Icon = tone === 'ok' ? Check : tone === 'neutral' ? HelpCircle : AlertTriangle
  return (
    <div role={tone === 'bad' ? 'alert' : 'status'} className={cn('flex gap-2.5 rounded-lg border border-l-4 p-3 text-sm', TONE[tone], ACCENT[tone])}>
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="break-words">{children}</div>}
      </div>
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn('rounded-md bg-muted motion-safe:animate-pulse', className)} />
}

/** Loading state: a status message plus placeholder blocks (no invented content). */
export function Loading({ label = 'Loading', blocks = 2 }: { label?: string; blocks?: number }) {
  return (
    <div className="space-y-3 py-4">
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" /> {label}…
      </p>
      {Array.from({ length: blocks }, (_, i) => <Skeleton key={i} className="h-16 w-full" />)}
    </div>
  )
}

export function ErrorBox({ error, what }: { error: ApiError; what: string }) {
  return (
    <Notice tone="bad" title={`Could not load ${what}`}>
      {error.message}
    </Notice>
  )
}

/** External links open in a new tab without giving the target window access to this one; only http(s) URLs become links. */
export function SafeLink({ url, children }: { url: string; children?: ReactNode }) {
  let ok = false
  try {
    ok = ['http:', 'https:'].includes(new URL(url).protocol)
  } catch {
    ok = false
  }
  if (!ok) return <span className="break-all">{children ?? url}</span>
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 break-all text-primary underline underline-offset-2">
      {children ?? url}
      <ExternalLink aria-hidden className="size-3 shrink-0" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  )
}

export const UNAVAILABLE = 'Unavailable'
export const num = (n: number | null | undefined): string => (n === null || n === undefined ? UNAVAILABLE : n.toLocaleString('en-US'))

export function when(iso: string | null | undefined): string {
  if (!iso) return UNAVAILABLE
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed bg-card/50 px-6 py-10 text-center">
      <Inbox aria-hidden className="size-6 text-muted-foreground" />
      <p className="font-medium">{title}</p>
      {children && <p className="max-w-prose text-sm text-muted-foreground">{children}</p>}
    </div>
  )
}

/** Count chips for a set of statuses (real counts only). */
export function CountChips({ items }: { items: { label: string; count: number; tone: Tone; icon?: StateIcon }[] }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((i) => (
        <li key={i.label}><Pill tone={i.tone} icon={i.icon} className="px-2 py-1 text-sm"><span className="font-semibold tabular-nums">{i.count}</span> {i.label}</Pill></li>
      ))}
    </ul>
  )
}
