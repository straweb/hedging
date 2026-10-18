/**
 * The ghost overlay's maths, without a chart: aggregation into session buckets,
 * the live tail fold, the bucket lookup the legend reads, and the ghost row of
 * the OHLC readout.
 */

import { describe, expect, it } from 'vitest'

import {
  aggregateGhostBars,
  foldGhostBar,
  type GhostFoldState,
  ghostBarAt,
  isGhostInterval,
} from './ghostCandles'
import { buildGhostLegend } from './legend'

const fmt = (n: number) => n.toFixed(2)
const fmtVolume = (n: number) => `${n / 1000}K`

/**
 * Arithmetic buckets from an anchor at 3300 (55 minutes), standing in for the
 * session calendar the terminal hands the overlay. Buckets span 180s:
 * [3300,3480), [3480,3660), [3660,3840)...
 */
const bucket3m = (interval: string, time: number): number => {
  if (interval !== '3m') throw new Error(`unexpected interval ${interval}`)
  return 3300 + Math.floor((time - 3300) / 180) * 180
}

const bar = (
  time: number,
  open: number,
  close: number,
  volume = 100,
  high = Math.max(open, close) + 1,
  low = Math.min(open, close) - 1
) => ({
  time,
  open,
  high,
  low,
  close,
  volume,
})

describe('isGhostInterval', () => {
  it('accepts exactly the offered higher timeframes', () => {
    for (const tf of ['3m', '5m', '10m', '15m', '30m', '1h']) {
      expect(isGhostInterval(tf)).toBe(true)
    }
    for (const tf of ['', '2m', '5M', '1h ', null, undefined, 5]) {
      expect(isGhostInterval(tf)).toBe(false)
    }
  })
})

describe('aggregateGhostBars', () => {
  it('folds consecutive bars into one bucket and keeps the extremes', () => {
    const bars = [
      bar(3300, 100, 101, 10), // 55:00 -> bucket 3300
      bar(3360, 101, 99, 20, 103, 98), // 56:00 -> same bucket
      bar(3420, 99, 102, 30), // 57:00 -> still the same bucket
      bar(3480, 99, 102, 30), // 58:00 -> the next bucket
    ]
    const out = aggregateGhostBars(bars, '3m', bucket3m)
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({ time: 3300, open: 100, high: 103, low: 98, close: 102, volume: 60 })
    expect(out[1]).toEqual({ time: 3480, open: 99, high: 103, low: 98, close: 102, volume: 30 })
  })

  it('splits on the bucket boundary the calendar reports, not arithmetic alone', () => {
    // 3479 is the last second of the 3300 bucket; 3480 opens the next one.
    const bars = [bar(3479, 5, 6), bar(3480, 6, 7)]
    const out = aggregateGhostBars(bars, '3m', bucket3m)
    expect(out.map((b) => b.time)).toEqual([3300, 3480])
  })

  it('skips bars the calendar cannot place instead of inventing a bucket', () => {
    const out = aggregateGhostBars([bar(10, 1, 2)], '3m', () => {
      throw new Error('outside session')
    })
    expect(out).toEqual([])
  })

  it('sums volume and tolerates bars without one', () => {
    const bars = [{ time: 3300, open: 1, high: 2, low: 0.5, close: 1.5 }, bar(3360, 1.5, 1.2, 40)]
    const out = aggregateGhostBars(bars, '3m', bucket3m)
    expect(out).toHaveLength(1)
    expect(out[0].volume).toBe(40)
  })
})

describe('foldGhostBar', () => {
  const state = (): GhostFoldState => ({ bar: null, lastTime: null, lastVolume: 0 })

  it('opens a bucket on the first bar and folds the same bar once per tick', () => {
    const s = state()
    expect(foldGhostBar(s, bar(3300, 100, 100.5, 10), '3m', bucket3m)).toBe('append')
    expect(s.bar?.volume).toBe(10)
    // The same forming bar, grown: the delta, not the whole volume again.
    expect(foldGhostBar(s, bar(3300, 100, 100.6, 16), '3m', bucket3m)).toBe('update')
    expect(s.bar).toMatchObject({ close: 100.6, volume: 16 })
    // A second bar inside the bucket adds its whole volume.
    expect(foldGhostBar(s, bar(3360, 100.6, 101, 5, 102, 100), '3m', bucket3m)).toBe('update')
    expect(s.bar).toMatchObject({ high: 102, volume: 21 })
  })

  it('opens the next bucket when the bar crosses the boundary', () => {
    const s = state()
    foldGhostBar(s, bar(3300, 100, 101, 10), '3m', bucket3m)
    expect(foldGhostBar(s, bar(3480, 101, 102, 7), '3m', bucket3m)).toBe('append')
    expect(s.bar?.time).toBe(3480)
    expect(s.bar?.volume).toBe(7)
  })

  it('ignores a bar for a bucket already closed', () => {
    const s = state()
    foldGhostBar(s, bar(3480, 100, 101, 10), '3m', bucket3m)
    expect(foldGhostBar(s, bar(3300, 1, 2, 3), '3m', bucket3m)).toBe('none')
    expect(s.bar?.time).toBe(3480)
  })

  it('says none when the calendar cannot place the bar', () => {
    const s = state()
    expect(
      foldGhostBar(s, bar(55 * 60, 1, 2, 3), '3m', () => {
        throw new Error('outside session')
      })
    ).toBe('none')
    expect(s.bar).toBeNull()
  })
})

describe('ghostBarAt', () => {
  // Buckets 3300..3480 (two source bars) and 3480..3660 (one source bar).
  const bars = aggregateGhostBars(
    [bar(3300, 1, 1), bar(3420, 2, 2), bar(3600, 3, 3)],
    '3m',
    bucket3m
  )

  it('finds the bucket containing the hovered bar', () => {
    expect(ghostBarAt(bars, 3300)?.time).toBe(3300)
    expect(ghostBarAt(bars, 3360)?.time).toBe(3300)
    expect(ghostBarAt(bars, 3720)?.time).toBe(3480)
  })

  it('answers null for a time before every bucket', () => {
    expect(ghostBarAt(bars, 0)).toBeNull()
    expect(ghostBarAt(bars, null)).toBeNull()
    expect(ghostBarAt([], 3300)).toBeNull()
  })
})

describe('buildGhostLegend', () => {
  it('labels the row with the ghost interval and reads like the main line', () => {
    const runs = buildGhostLegend({
      interval: '5m',
      bar: { open: 100, high: 102, low: 99, close: 101, volume: 12000 },
      prevClose: 99,
      fmt,
      fmtVolume,
    })
    const flat = runs.map((r) => r.text).join(' ')
    expect(flat).toContain('5m')
    expect(flat).toContain('O 100.00 H 102.00 L 99.00 C 101.00')
    expect(flat).toContain('V 12K')
    expect(flat).toContain('+2.00 (+2.02%)')
  })

  it('drops the change when there is no earlier ghost candle', () => {
    const runs = buildGhostLegend({
      interval: '5m',
      bar: { open: 100, high: 102, low: 99, close: 101 },
      prevClose: null,
      fmt,
      fmtVolume,
    })
    expect(runs.map((r) => r.text).join(' ')).not.toContain('%')
  })

  it('says nothing at all without a candle to describe', () => {
    expect(
      buildGhostLegend({ interval: '5m', bar: null, prevClose: null, fmt, fmtVolume })
    ).toEqual([])
  })
})
