import type {
  ScheduleAction,
  ScheduledOrder,
  ScheduleTarget,
  ScheduleTrigger,
} from '@/types/hedging'

/**
 * The scheduler's pure side: deciding whether a scheduled batch is due.
 *
 * Two triggers exist. A time trigger arms a clock time in IST (the market's
 * wall) — minute or second precision, "09:20" and "09:20:00" are the same
 * instant — and can repeat daily. A price trigger watches an instrument's LTP
 * against a level (above/below) or a band (inside/outside, either endpoint
 * order). Both answer one question, `isDue`, and the engine in the page pairs
 * the answer with the batch actions the quick actions strip runs.
 *
 * Time is handled in IST regardless of where the browser sits: the terminal
 * trades Indian markets, so "09:20" means 09:20 Asia/Kolkata.
 */

export const IST_OFFSET_MINUTES = 330 // UTC+5:30

/** Minutes since midnight IST for a wall-clock instant. */
export function istMinutes(now: Date = new Date()): number {
  const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes()
  return (utcMinutes + IST_OFFSET_MINUTES) % (24 * 60)
}

/** Seconds since midnight IST for a wall-clock instant — second-precision triggers. */
export function istSeconds(now: Date = new Date()): number {
  return istMinutes(now) * 60 + now.getUTCSeconds()
}

/** The IST calendar date (YYYY-MM-DD) of an instant — the daily re-arm key. */
export function istDateKey(now: Date = new Date()): string {
  const shifted = new Date(now.getTime() + IST_OFFSET_MINUTES * 60 * 1000)
  return shifted.toISOString().slice(0, 10)
}

/**
 * Parse "HH:MM" or "HH:MM:SS" into seconds since midnight; null when
 * malformed. A bare HH:MM means :00, so minute and second entries compare
 * on one scale.
 */
export function parseClock(at: string): number | null {
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(at.trim())
  if (!match) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  const seconds = match[3] ? Number(match[3]) : 0
  if (hours > 23 || minutes > 59 || seconds > 59) return null
  return hours * 3600 + minutes * 60 + seconds
}

/**
 * True when the entry's stored time carries a seconds field. The dialog uses
 * it to keep an "HH:MM" row reading exactly that, not ":00".
 */
export function hasSeconds(at: string): boolean {
  return /^\d{1,2}:\d{2}:\d{2}$/.test(at.trim())
}

/** Whether a pending time trigger is due at `now`. */
export function timeDue(
  trigger: Extract<ScheduleTrigger, { kind: 'time' }>,
  now: Date,
  lastFiredDateKey?: string
): boolean {
  const target = parseClock(trigger.at)
  if (target == null) return false
  // A daily entry fires once per IST calendar day; a once entry fires the
  // first time the clock reaches (or passes) the target after it was armed.
  if (lastFiredDateKey === istDateKey(now)) return false
  return istSeconds(now) >= target
}

/** Low/high of a band trigger, tolerant of the endpoints being given in either order. */
export function bandOf(
  trigger: Extract<ScheduleTrigger, { kind: 'price' }>
): [number, number] | null {
  if (trigger.price2 == null || !Number.isFinite(trigger.price2)) return null
  return trigger.price <= trigger.price2
    ? [trigger.price, trigger.price2]
    : [trigger.price2, trigger.price]
}

/** Whether a pending price trigger is due at the given LTP. */
export function priceDue(
  trigger: Extract<ScheduleTrigger, { kind: 'price' }>,
  ltp: number | undefined
): boolean {
  if (ltp == null || !Number.isFinite(ltp) || !Number.isFinite(trigger.price)) return false
  if (trigger.condition === 'above') return ltp >= trigger.price
  if (trigger.condition === 'below') return ltp <= trigger.price
  const band = bandOf(trigger)
  if (!band) return false
  const [low, high] = band
  // Inside fires while the quote holds the band; outside the moment it
  // escapes it — a breakout arm and a range arm on the same four words.
  return trigger.condition === 'inside' ? ltp >= low && ltp <= high : ltp < low || ltp > high
}

/** Is this pending entry due right now? (Dispatch by trigger kind.) */
export function isDue(
  entry: ScheduledOrder,
  context: { now: Date; ltpOf(symbol: string, exchange: string): number | undefined }
): boolean {
  if (entry.status !== 'pending') return false
  if (entry.trigger.kind === 'time') {
    // A daily entry that already fired today (firedAt pins the day) waits.
    const lastFiredDateKey = entry.firedAt ? istDateKey(new Date(entry.firedAt)) : undefined
    return timeDue(entry.trigger, context.now, lastFiredDateKey)
  }
  return priceDue(entry.trigger, context.ltpOf(entry.trigger.symbol, entry.trigger.exchange))
}

/**
 * The vocabulary a scheduled row reads in: CE/PE for the option panels,
 * Buy/Sell for equity stocks and for F&O underlyings scheduled against the
 * stock itself (the `target` toggle).
 */
export function scheduleVocab(mode: 'fno' | 'equity', target?: ScheduleTarget): 'cepe' | 'bs' {
  if (mode === 'equity') return 'bs'
  return target === 'stock' ? 'bs' : 'cepe'
}

/** Human label for a scheduled action, aware of the F&O / equity vocabulary. */
export function scheduleActionLabel(
  action: ScheduleAction,
  mode: 'fno' | 'equity',
  target?: ScheduleTarget
): string {
  const vocab = scheduleVocab(mode, target)
  const a = vocab === 'cepe' ? 'CE' : 'Buy'
  const b = vocab === 'cepe' ? 'PE' : 'Sell'
  switch (action) {
    case 'deployA':
      return `Deploy ${a}`
    case 'deployB':
      return `Deploy ${b}`
    case 'closeA':
      return `Close ${a}`
    case 'closeB':
      return `Close ${b}`
    case 'exitAll':
      return 'Exit All'
  }
}

/** Human label for a trigger: "09:20:30 daily" or "NIFTY ≥ 24,500" / "NIFTY 24,4–24,6k". */
export function scheduleTriggerLabel(trigger: ScheduleTrigger): string {
  if (trigger.kind === 'time') {
    return `${trigger.at}${trigger.repeat === 'daily' ? ' · daily' : ''}`
  }
  const fmt = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
  if (trigger.condition === 'above') return `${trigger.symbol} ≥ ${fmt(trigger.price)}`
  if (trigger.condition === 'below') return `${trigger.symbol} ≤ ${fmt(trigger.price)}`
  const band = bandOf(trigger)
  if (!band) return `${trigger.symbol} ${trigger.condition} ?`
  const word = trigger.condition === 'inside' ? 'in' : 'out'
  return `${trigger.symbol} ${word} ${fmt(band[0])}–${fmt(band[1])}`
}

/** A fresh scheduler row id — sortable by creation, unique enough for a tab. */
export function newScheduleId(): string {
  return `sch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}
