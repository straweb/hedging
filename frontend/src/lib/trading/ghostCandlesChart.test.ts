/**
 * The ghost overlay against the real chart engine: the series it adds, the
 * scale and style it carries, and the rows the engine ends up holding.
 */

import { type Bar, type Chart, createChart } from 'openalgo-charts'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GhostCandles, isGhostInterval } from './ghostCandles'

const IST = 19_800
/** One-minute bars for one IST date, 09:15 to 09:29, wandering around 100. */
function session(date: string, count = 15): Bar[] {
  const [y, m, d] = date.split('-').map(Number)
  const open = Date.UTC(y, m - 1, d, 9, 15) / 1000 - IST
  const out: Bar[] = []
  let price = 100
  for (let i = 0; i < count; i++) {
    price += Math.sin(i * 0.9) * 0.4
    out.push({
      time: open + i * 60,
      open: price,
      high: price + 0.8,
      low: price - 0.8,
      close: price + 0.1,
      volume: 1000 + i * 10,
    })
  }
  return out
}

const charts: Chart[] = []
const ghostOf = (chart: Chart, bars: () => readonly Bar[]): GhostCandles => {
  const ghost = new GhostCandles()
  // 5m/15m/1h arithmetic buckets anchored at the session's first bar, standing
  // in for the session calendar the terminal hands the overlay.
  const anchor = bars()[0]?.time ?? 0
  ghost.attach(
    chart,
    bars,
    (interval, time) => {
      if (!isGhostInterval(interval)) throw new Error(`unexpected interval ${interval}`)
      const sec = interval === '1h' ? 3600 : Number(interval.slice(0, -1)) * 60
      return anchor + Math.floor((time - anchor) / sec) * sec
    },
    (v) => v.toFixed(2)
  )
  return ghost
}

beforeEach(() => {
  const context = new Proxy(
    {
      measureText: (text: string) => ({ width: text.length * 7 }),
      createLinearGradient: () => ({ addColorStop() {} }),
    },
    { get: (target, key) => target[key as keyof typeof target] ?? (() => {}) }
  )
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D
  )
})
afterEach(() => {
  for (const chart of charts.splice(0)) chart.destroy()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

function chartWith(bars: readonly Bar[]): Chart {
  const chart = createChart(document.createElement('div'), {
    shortcuts: false,
    timeNavigator: false,
    raf: {
      schedule: (cb) => {
        cb()
        return 1
      },
      cancel() {},
    },
  })
  chart.applySize(900, 500)
  chart.addSeries('candlestick').setData(bars)
  charts.push(chart)
  return chart
}

describe('GhostCandles on the engine', () => {
  it('adds one hollow series on the chart scale, and removes it when taken off', () => {
    const bars = session('2026-09-30')
    const chart = chartWith(bars)
    const ghost = ghostOf(chart, () => bars)
    expect(chart.panes()[0].series()).toHaveLength(1)

    ghost.setInterval('5m')
    expect(ghost.attached()).toBe(true)
    const records = chart.panes()[0].series()
    expect(records).toHaveLength(2)
    const added = records[1]
    expect(added.type).toBe('candlestick')
    expect(added.scaleId).toBe('right')
    expect(added.style.bodyVisible).toBe(false)
    expect(added.style.priceLineVisible).toBe(false)
    expect(added.style.lastValueVisible).toBe(false)

    ghost.setInterval(null)
    expect(ghost.attached()).toBe(false)
    expect(chart.panes()[0].series()).toHaveLength(1)
  })

  it('aggregates the session into 5m candles and folds live ticks into the tail', () => {
    const bars = session('2026-09-30')
    const chart = chartWith(bars)
    const ghost = ghostOf(chart, () => bars)
    ghost.setInterval('5m')

    const aggregated = ghost.ghostBars()
    // 15 one-minute bars -> three 5-minute candles, stamped at bucket opens.
    expect(aggregated).toHaveLength(3)
    expect(aggregated[0].time).toBe(bars[0].time)
    expect(aggregated[1].time).toBe(bars[0].time + 300)
    expect(aggregated[2].volume).toBe(bars.slice(10).reduce((sum, b) => sum + (b.volume ?? 0), 0))

    // A live tick on the last one-minute bar moves the tail candle only.
    const last = bars[bars.length - 1]
    const moved = ghost.foldLive({ ...last, close: last.close + 2, high: last.high + 2 })
    expect(moved?.close).toBe(last.close + 2)
    expect(ghost.ghostBars()).toHaveLength(3)

    // History replaced: the fold state is rebuilt, not appended to.
    ghost.syncAll()
    expect(ghost.ghostBars()[2].close).toBe(bars[bars.length - 1].close)
  })

  it('re-aggregates when the timeframe changes without touching the chart', () => {
    const bars = session('2026-09-30')
    const chart = chartWith(bars)
    const ghost = ghostOf(chart, () => bars)
    ghost.setInterval('5m')
    expect(ghost.ghostBars()).toHaveLength(3)
    ghost.setInterval('15m')
    expect(ghost.ghostBars()).toHaveLength(1)
    expect(ghost.ghostBars()[0].time).toBe(bars[0].time)
    expect(chart.panes()[0].series()).toHaveLength(2)
  })
})
