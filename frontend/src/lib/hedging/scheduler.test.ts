import { describe, expect, it } from 'vitest'
import type { ScheduledOrder } from '@/types/hedging'
import {
  bandOf,
  hasSeconds,
  isDue,
  istDateKey,
  istMinutes,
  istSeconds,
  newScheduleId,
  parseClock,
  priceDue,
  scheduleActionLabel,
  scheduleTriggerLabel,
  scheduleVocab,
  timeDue,
} from './scheduler'

/** A UTC instant whose IST wall time is the given HH:MM(:SS). */
function istInstant(hhmmss: string, date = '2025-06-10'): Date {
  const [h, m, s] = hhmmss.split(':').map((part) => Number(part) || 0)
  const totalIstMinutes = h * 60 + m
  // IST = UTC+5:30, so the UTC instant is 5:30 behind the IST wall time.
  const utcMs =
    Date.parse(`${date}T00:00:00Z`) + (totalIstMinutes - 330) * 60 * 1000 + (s ?? 0) * 1000
  return new Date(utcMs)
}

const ltpOf = () => undefined

describe('istMinutes / istSeconds / istDateKey', () => {
  it('maps a UTC instant to the IST wall clock', () => {
    expect(istMinutes(istInstant('09:20'))).toBe(9 * 60 + 20)
    expect(istSeconds(istInstant('09:20:30'))).toBe(9 * 3600 + 20 * 60 + 30)
  })

  it('wraps past midnight IST', () => {
    // 00:10 IST is 18:40 the previous UTC day.
    expect(istMinutes(istInstant('00:10'))).toBe(10)
  })

  it('keys the IST calendar date, not the UTC one', () => {
    // 01:00 IST on the 11th is 19:30 UTC on the 10th.
    const instant = istInstant('01:00', '2025-06-11')
    expect(istDateKey(instant)).toBe('2025-06-11')
  })
})

describe('parseClock', () => {
  it('accepts HH:MM and HH:MM:SS and rejects junk', () => {
    expect(parseClock('09:20')).toBe(9 * 3600 + 20 * 60)
    expect(parseClock('9:05')).toBe(9 * 3600 + 5 * 60)
    expect(parseClock('09:20:30')).toBe(9 * 3600 + 20 * 60 + 30)
    expect(parseClock('24:00')).toBeNull()
    expect(parseClock('09:60')).toBeNull()
    expect(parseClock('09:20:60')).toBeNull()
    expect(parseClock('nine')).toBeNull()
    expect(parseClock('')).toBeNull()
  })

  it('reports whether an entry carries seconds', () => {
    expect(hasSeconds('09:20:30')).toBe(true)
    expect(hasSeconds('09:20')).toBe(false)
  })
})

describe('timeDue', () => {
  const trigger = (at: string, repeat: 'once' | 'daily' = 'once') =>
    ({ kind: 'time', at, repeat }) as const

  it('fires when the clock reaches the target', () => {
    expect(timeDue(trigger('09:20'), istInstant('09:20'))).toBe(true)
    expect(timeDue(trigger('09:20'), istInstant('09:21'))).toBe(true)
  })

  it('honours seconds precision', () => {
    expect(timeDue(trigger('09:20:30'), istInstant('09:20:29'))).toBe(false)
    expect(timeDue(trigger('09:20:30'), istInstant('09:20:30'))).toBe(true)
    // A minute entry still fires anywhere inside its minute — the clock has
    // reached it (the pre-seconds behavior, preserved for HH:MM rows).
    expect(timeDue(trigger('09:20'), istInstant('09:20:29'))).toBe(true)
  })

  it('does not fire before the target', () => {
    expect(timeDue(trigger('09:20'), istInstant('09:19'))).toBe(false)
  })

  it('fires only once per IST day, then re-arms the next day', () => {
    const at = trigger('09:20', 'daily')
    const firedDay = istDateKey(istInstant('09:20'))
    expect(timeDue(at, istInstant('10:00'), firedDay)).toBe(false)
    const nextDay = istInstant('09:20').getTime() + 24 * 60 * 60 * 1000
    expect(timeDue(at, new Date(nextDay), firedDay)).toBe(true)
  })

  it('rejects malformed times', () => {
    expect(timeDue(trigger('25:00'), istInstant('10:00'))).toBe(false)
  })
})

describe('priceDue', () => {
  const above = {
    kind: 'price',
    symbol: 'NIFTY',
    exchange: 'NFO',
    condition: 'above',
    price: 24500,
  } as const
  const below = {
    kind: 'price',
    symbol: 'NIFTY',
    exchange: 'NFO',
    condition: 'below',
    price: 24400,
  } as const
  const band = (condition: 'inside' | 'outside', price: number, price2: number) =>
    ({ kind: 'price', symbol: 'NIFTY', exchange: 'NFO', condition, price, price2 }) as const

  it('fires an above trigger at or over the level', () => {
    expect(priceDue(above, 24_500)).toBe(true)
    expect(priceDue(above, 24_501.5)).toBe(true)
    expect(priceDue(above, 24_499)).toBe(false)
  })

  it('fires a below trigger at or under the level', () => {
    expect(priceDue(below, 24_400)).toBe(true)
    expect(priceDue(below, 24_399)).toBe(true)
    expect(priceDue(below, 24_401)).toBe(false)
  })

  it('fires inside the band while the quote holds it', () => {
    const inside = band('inside', 24_400, 24_600)
    expect(priceDue(inside, 24_400)).toBe(true)
    expect(priceDue(inside, 24_512.5)).toBe(true)
    expect(priceDue(inside, 24_600)).toBe(true)
    expect(priceDue(inside, 24_399.9)).toBe(false)
    expect(priceDue(inside, 24_600.1)).toBe(false)
    // Endpoints given in either order mean the same band.
    expect(priceDue(band('inside', 24_600, 24_400), 24_500)).toBe(true)
  })

  it('fires outside the band the moment the quote escapes it', () => {
    const outside = band('outside', 24_400, 24_600)
    expect(priceDue(outside, 24_399.9)).toBe(true)
    expect(priceDue(outside, 24_600.1)).toBe(true)
    expect(priceDue(outside, 24_500)).toBe(false)
    expect(priceDue(outside, 24_400)).toBe(false)
  })

  it('waits for a live quote and a complete band', () => {
    expect(priceDue(above, undefined)).toBe(false)
    expect(priceDue(above, NaN)).toBe(false)
    expect(priceDue(band('inside', 24_400, Number.NaN), 24_500)).toBe(false)
    expect(bandOf({ ...band('inside', 24_600, 24_400) })).toEqual([24_400, 24_600])
  })
})

describe('isDue', () => {
  const entry = (over: Partial<ScheduledOrder>): ScheduledOrder => ({
    id: 'sch-1',
    action: 'deployA',
    trigger: { kind: 'time', at: '09:20', repeat: 'once' },
    status: 'pending',
    createdAt: 0,
    modeAtCreation: 'fno',
    ...over,
  })

  it('skips entries that already ran or were missed', () => {
    expect(isDue(entry({ status: 'done' }), { now: istInstant('10:00'), ltpOf })).toBe(false)
    expect(isDue(entry({ status: 'missed' }), { now: istInstant('10:00'), ltpOf })).toBe(false)
  })

  it('dispatches price triggers with the ltp reader', () => {
    const price = entry({
      trigger: { kind: 'price', symbol: 'X', exchange: 'NSE', condition: 'above', price: 100 },
    })
    expect(isDue(price, { now: new Date(), ltpOf: () => 101 })).toBe(true)
    expect(isDue(price, { now: new Date(), ltpOf: () => 99 })).toBe(false)
  })

  it('dispatches band triggers through the same reader', () => {
    const range = entry({
      trigger: {
        kind: 'price',
        symbol: 'X',
        exchange: 'NSE',
        condition: 'outside',
        price: 100,
        price2: 110,
      },
    })
    expect(isDue(range, { now: new Date(), ltpOf: () => 105 })).toBe(false)
    expect(isDue(range, { now: new Date(), ltpOf: () => 111 })).toBe(true)
  })
})

describe('labels + vocabulary', () => {
  it('labels actions in both market vocabularies', () => {
    expect(scheduleActionLabel('deployA', 'fno')).toBe('Deploy CE')
    expect(scheduleActionLabel('deployB', 'fno')).toBe('Deploy PE')
    expect(scheduleActionLabel('deployA', 'equity')).toBe('Deploy Buy')
    expect(scheduleActionLabel('closeB', 'equity')).toBe('Close Sell')
    expect(scheduleActionLabel('exitAll', 'fno')).toBe('Exit All')
  })

  it('switches to the Buy/Sell vocabulary when the target is the stock', () => {
    expect(scheduleActionLabel('deployA', 'fno', 'stock')).toBe('Deploy Buy')
    expect(scheduleActionLabel('deployB', 'fno', 'stock')).toBe('Deploy Sell')
    expect(scheduleActionLabel('closeA', 'fno', 'stock')).toBe('Close Buy')
    expect(scheduleActionLabel('deployA', 'fno', 'options')).toBe('Deploy CE')
    expect(scheduleVocab('fno', 'stock')).toBe('bs')
    expect(scheduleVocab('fno', 'options')).toBe('cepe')
    expect(scheduleVocab('equity')).toBe('bs')
  })

  it('labels triggers readably', () => {
    expect(scheduleTriggerLabel({ kind: 'time', at: '09:20', repeat: 'once' })).toBe('09:20')
    expect(scheduleTriggerLabel({ kind: 'time', at: '09:20:30', repeat: 'daily' })).toBe(
      '09:20:30 · daily'
    )
    expect(
      scheduleTriggerLabel({
        kind: 'price',
        symbol: 'NIFTY',
        exchange: 'NFO',
        condition: 'above',
        price: 24500,
      })
    ).toBe('NIFTY ≥ 24,500')
    expect(
      scheduleTriggerLabel({
        kind: 'price',
        symbol: 'NIFTY',
        exchange: 'NFO',
        condition: 'inside',
        price: 24600,
        price2: 24400,
      })
    ).toBe('NIFTY in 24,400–24,600')
    expect(
      scheduleTriggerLabel({
        kind: 'price',
        symbol: 'NIFTY',
        exchange: 'NFO',
        condition: 'outside',
        price: 24400,
        price2: 24600,
      })
    ).toBe('NIFTY out 24,400–24,600')
  })
})

describe('newScheduleId', () => {
  it('is unique and prefixed', () => {
    const a = newScheduleId()
    const b = newScheduleId()
    expect(a).toMatch(/^sch-/)
    expect(a).not.toBe(b)
  })
})
