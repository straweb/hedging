import { rangePct } from '@/lib/hedging/range'
import { cn } from '@/lib/utils'

/**
 * The session low — current — high rail: one thin track per row, the needle
 * at today's last traded price between the session's extremes. Every strike
 * and stock row carries one, so "how far into the day's range am I" reads
 * without opening a chart. The exact marks live in the hover title.
 */
export function SessionRangeBar(props: {
  low: number | undefined
  high: number | undefined
  ltp: number | undefined
  className?: string
}) {
  const pct =
    props.low != null && props.high != null && props.ltp != null
      ? rangePct(props.low, props.high, props.ltp)
      : null
  if (pct == null) {
    // No session marks yet (pre-open, or the quote feed has not said) — keep
    // the row's rhythm with an empty rail rather than jumping the layout.
    return (
      <span
        className={cn('block h-1 rounded-full bg-muted/60', props.className)}
        title="Session range unavailable"
      />
    )
  }
  // The needle's tint follows the needle's side of the range's midpoint: a
  // quote near the high reads green (strength), near the low red (weakness).
  const strong = pct >= 0.5
  return (
    <span
      className={cn('relative block h-1 rounded-full bg-muted/60', props.className)}
      title={`Session L ${props.low?.toFixed(2)} · C ${props.ltp?.toFixed(2)} · H ${props.high?.toFixed(2)}`}
    >
      <span
        className={cn(
          'absolute top-1/2 h-2 w-[3px] -translate-y-1/2 rounded-full',
          strong ? 'bg-emerald-500' : 'bg-red-500'
        )}
        style={{ left: `calc(${(pct * 100).toFixed(1)}% - 1.5px)` }}
      />
    </span>
  )
}
