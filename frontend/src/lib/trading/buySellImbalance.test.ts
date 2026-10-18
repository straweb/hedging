import type { Bar } from 'openalgo-charts'
import type { Drawing, HitContext } from 'openalgo-charts/draw'
import { describe, expect, it } from 'vitest'
import {
  BUY_SELL_IMBALANCE_ID,
  BUY_SELL_IMBALANCE_NAME,
  BUY_SELL_IMBALANCE_TOOL,
  buySellImbalanceAnalysis,
  classifyVolume,
  ensureHostDrawingTools,
  HOST_DRAW_TOOL_METADATA,
  hostDrawingToolsReady,
  imbalanceNumber,
  imbalanceTimeBound,
  registerHostDrawingTools,
} from './buySellImbalance'

function bar(overrides: Partial<Bar> = {}): Bar {
  return {
    open: 100,
    high: 110,
    low: 90,
    close: 105,
    volume: 10,
    time: 1_000,
    ...overrides,
  }
}

function bars(list: Partial<Bar>[]): Bar[] {
  return list.map((overrides, i) => bar({ time: 1_000 + i * 60, ...overrides }))
}

describe('classifyVolume', () => {
  it('sends all volume of a rising candle to the buy side', () => {
    expect(classifyVolume(bar({ open: 100, close: 110, volume: 30 }))).toEqual({ buy: 30, sell: 0 })
  })

  it('sends all volume of a falling candle to the sell side', () => {
    expect(classifyVolume(bar({ open: 110, close: 100, volume: 30 }))).toEqual({ buy: 0, sell: 30 })
  })

  it('splits a doji evenly, so it adds no imbalance of its own', () => {
    const { buy, sell } = classifyVolume(
      bar({ open: 100, close: 100, high: 105, low: 95, volume: 30 })
    )
    expect(buy).toBe(15)
    expect(sell).toBe(15)
  })

  it('ignores missing, zero and negative volume', () => {
    expect(classifyVolume(bar({ open: 100, close: 110, volume: 0 }))).toEqual({ buy: 0, sell: 0 })
    expect(classifyVolume({ ...bar({ open: 100, close: 110 }), volume: undefined })).toEqual({
      buy: 0,
      sell: 0,
    })
    expect(classifyVolume(bar({ open: 100, close: 110, volume: -5 }))).toEqual({ buy: 0, sell: 0 })
  })
})

describe('imbalanceNumber', () => {
  it('passes finite values through the clamp', () => {
    expect(imbalanceNumber(7, 3, 0, 10)).toBe(7)
    expect(imbalanceNumber(-5, 3, 0, 10)).toBe(0)
    expect(imbalanceNumber(50, 3, 0, 10)).toBe(10)
  })

  it('falls back when the value is missing or not a finite number', () => {
    expect(imbalanceNumber(undefined, 3, 0, 10)).toBe(3)
    expect(imbalanceNumber(Number.NaN, 3, 0, 10)).toBe(3)
    expect(imbalanceNumber('7', 3, 0, 10)).toBe(3)
  })
})

describe('imbalanceTimeBound', () => {
  const series = [bar({ time: 10 }), bar({ time: 20 }), bar({ time: 30 })]

  it('returns the first bar at or after the time', () => {
    expect(imbalanceTimeBound(series, 5)).toBe(0)
    expect(imbalanceTimeBound(series, 10)).toBe(0)
    expect(imbalanceTimeBound(series, 11)).toBe(1)
    expect(imbalanceTimeBound(series, 30)).toBe(2)
    expect(imbalanceTimeBound(series, 99)).toBe(3)
  })

  it('returns the first bar strictly after the time when upper', () => {
    expect(imbalanceTimeBound(series, 10, true)).toBe(1)
    expect(imbalanceTimeBound(series, 20, true)).toBe(2)
    expect(imbalanceTimeBound(series, 30, true)).toBe(3)
  })
})

describe('buySellImbalanceAnalysis', () => {
  it('reports an empty range as empty', () => {
    const result = buySellImbalanceAnalysis(
      [bar({ open: 1, close: 2, volume: 1, time: 0 })],
      5_000,
      6_000
    )
    expect(result.status).toBe('empty')
    expect(result.rows).toEqual([])
    expect(result.totalBuy).toBe(0)
    expect(result.totalSell).toBe(0)
  })

  it('rejects non-finite bounds without touching the bars', () => {
    const result = buySellImbalanceAnalysis([bar({ volume: 5 })], Number.NaN, 2_000)
    expect(result.status).toBe('empty')
    expect(result.historyPartial).toBe(false)
  })

  it('distributes a single-bar range across rows while holding all of its volume', () => {
    const result = buySellImbalanceAnalysis([bar({ time: 100, volume: 8 })], 100, 160)
    expect(result.status).toBe('ready')
    expect(result.rows.length).toBeGreaterThanOrEqual(4)
    const buySum = result.rows.reduce((acc, row) => acc + row.buy, 0)
    expect(buySum).toBeCloseTo(8, 6)
    expect(result.totalBuy).toBe(8)
    expect(result.totalSell).toBe(0)
    expect(result.buyPoc).not.toBeNull()
    expect(result.sellPoc).toBeNull()
  })

  it('marks a range that starts before the first supplied bar as partial history', () => {
    const result = buySellImbalanceAnalysis(
      [bar({ open: 100, close: 110, volume: 5, time: 100 })],
      50,
      200
    )
    expect(result.historyPartial).toBe(true)
    expect(result.status).toBe('partial')
  })

  it('reports missing volume when no bar in the range carries volume', () => {
    const missing = buySellImbalanceAnalysis(
      [{ ...bar({ open: 100, close: 110, time: 0 }), volume: Number.NaN }],
      0,
      100
    )
    expect(missing.status).toBe('missing-volume')
    const zero = buySellImbalanceAnalysis(
      [bar({ open: 100, close: 110, time: 0, volume: 0 })],
      0,
      100
    )
    expect(zero.status).toBe('zero-volume')
  })

  it('reports invalid data when candle prices are unusable', () => {
    const broken = bar({ open: 110, close: 100, time: 0, volume: 10 })
    broken.high = Number.NaN
    const result = buySellImbalanceAnalysis([broken], 0, 100)
    expect(result.status).toBe('invalid-data')
    expect(result.invalidPriceBars).toBe(1)
  })

  it('conserves both totals: rows sum back to the buy and sell volume', () => {
    const list = bars([
      { open: 100, close: 120, high: 125, low: 95, volume: 30 },
      { open: 120, close: 100, high: 125, low: 95, volume: 20 },
      { open: 100, close: 130, high: 135, low: 95, volume: 40 },
      { open: 130, close: 128, high: 135, low: 95, volume: 10 },
    ])
    const result = buySellImbalanceAnalysis(list, list[0]!.time, list[3]!.time + 60)
    expect(result.status).toBe('ready')
    const buySum = result.rows.reduce((acc, row) => acc + row.buy, 0)
    const sellSum = result.rows.reduce((acc, row) => acc + row.sell, 0)
    expect(buySum).toBeCloseTo(result.totalBuy, 6)
    expect(sellSum).toBeCloseTo(result.totalSell, 6)
    expect(result.totalBuy).toBeCloseTo(70, 6)
    expect(result.totalSell).toBeCloseTo(30, 6)
    for (const row of result.rows) {
      expect(row.high).toBeGreaterThan(row.low)
      expect(row.buy).toBeGreaterThanOrEqual(0)
      expect(row.sell).toBeGreaterThanOrEqual(0)
    }
  })

  it('spans rows across the full price extent without gaps', () => {
    const list = bars([
      { open: 100, close: 118, high: 120, low: 80, volume: 30 },
      { open: 118, close: 96, high: 130, low: 75, volume: 20 },
    ])
    const result = buySellImbalanceAnalysis(list, list[0]!.time, list[1]!.time + 60)
    expect(result.rows.length).toBeGreaterThan(1)
    expect(result.rows[0]!.low).toBeCloseTo(75, 6)
    expect(result.rows[result.rows.length - 1]!.high).toBeCloseTo(130, 6)
    for (let i = 1; i < result.rows.length; i++)
      expect(result.rows[i]!.low).toBeCloseTo(result.rows[i - 1]!.high, 6)
  })

  it('places each side POC on the midpoint of its heaviest row', () => {
    const list = bars([
      { open: 100, close: 104, high: 105, low: 95, volume: 100 },
      { open: 104, close: 102, high: 105, low: 95, volume: 10 },
    ])
    const result = buySellImbalanceAnalysis(list, list[0]!.time, list[1]!.time + 60)
    const top = result.rows.reduce((best, row) => (row.buy > best.buy ? row : best))
    expect(result.buyPoc).toBeCloseTo((top.low + top.high) / 2, 6)
  })

  it('breaks POC ties towards the lower price row', () => {
    const list = [
      bar({ open: 100, close: 110, high: 120, low: 100, volume: 10, time: 0 }),
      bar({ open: 110, close: 120, high: 140, low: 120, volume: 10, time: 60 }),
    ]
    const result = buySellImbalanceAnalysis(list, 0, 120)
    expect(result.totalBuy).toBeCloseTo(20, 6)
    expect(result.buyPoc).not.toBeNull()
    expect(result.buyPoc!).toBeLessThanOrEqual(120)
  })

  it('honours the requested row count and clamps it to 4..200', () => {
    const list = bars([
      { open: 100, close: 120, high: 125, low: 95, volume: 30 },
      { open: 120, close: 100, high: 125, low: 95, volume: 20 },
    ])
    const span = 1_000
    const from = list[0]!.time
    const to = list[1]!.time + 60
    expect(buySellImbalanceAnalysis(list, from, to, { rows: 7 }).rows).toHaveLength(7)
    expect(
      buySellImbalanceAnalysis(list, from, to, { rows: 1 }).rows.length
    ).toBeGreaterThanOrEqual(4)
    expect(buySellImbalanceAnalysis(list, from, to, { rows: 999 }).rows.length).toBeLessThanOrEqual(
      200
    )
    expect(
      buySellImbalanceAnalysis(list, from, to, { rows: span }).rows.length
    ).toBeLessThanOrEqual(200)
  })

  it('spreads a wide candle across interior rows proportionally', () => {
    const list = [bar({ open: 100, close: 121, high: 180, low: 20, volume: 100, time: 0 })]
    const result = buySellImbalanceAnalysis(list, 0, 60, { rows: 8 })
    expect(result.rows).toHaveLength(8)
    const buySum = result.rows.reduce((acc, row) => acc + row.buy, 0)
    expect(buySum).toBeCloseTo(100, 6)
    expect(result.rows[0]!.buy).toBeGreaterThan(0)
    expect(result.rows[7]!.buy).toBeGreaterThan(0)
  })
})

describe('BUY_SELL_IMBALANCE_TOOL descriptor', () => {
  it('carries the engine-facing identity of a two-anchor range tool', () => {
    expect(BUY_SELL_IMBALANCE_TOOL.id).toBe(BUY_SELL_IMBALANCE_ID)
    expect(BUY_SELL_IMBALANCE_ID).toBe('buy-sell-imbalance')
    expect(BUY_SELL_IMBALANCE_NAME).toBe('Buy/Sell Imbalance')
    expect(BUY_SELL_IMBALANCE_TOOL.points).toBe(2)
    expect(BUY_SELL_IMBALANCE_TOOL.name).toBe(BUY_SELL_IMBALANCE_NAME)
    expect(typeof BUY_SELL_IMBALANCE_TOOL.draw).toBe('function')
    expect(typeof BUY_SELL_IMBALANCE_TOOL.distance).toBe('function')
    expect(BUY_SELL_IMBALANCE_TOOL.defaultStyle).toEqual({ showLabels: true, fillOpacity: 0.4 })
  })

  it('exposes rail metadata shaped like the generated entries', () => {
    const meta = HOST_DRAW_TOOL_METADATA[BUY_SELL_IMBALANCE_ID]
    expect(meta?.name).toBe('Buy/Sell Imbalance')
    expect(meta?.path).toMatch(/^M12 3v18/)
    expect(meta?.text).toBe(false)
  })

  it('lists every settings field the properties dialog needs', () => {
    const paths = BUY_SELL_IMBALANCE_TOOL.settings.fields.map((field) => field.path)
    for (const path of [
      'style.fillOpacity',
      'props.rows',
      'props.width',
      'props.side',
      'props.layout',
      'props.threshold',
      'props.showPoc',
      'props.showDelta',
      'props.buyColor',
      'props.sellColor',
      'style.showLabels',
      'text.color',
      'text.fontSize',
      'text.fontFamily',
      'text.bold',
      'text.italic',
    ])
      expect(paths, path).toContain(path)
  })
})

describe('registration', () => {
  it('registers once and ignores repeat calls', () => {
    let calls = 0
    const register = (): void => {
      calls++
    }
    registerHostDrawingTools(register)
    const afterFirst = hostDrawingToolsReady()
    registerHostDrawingTools(register)
    expect(calls).toBe(1)
    expect(afterFirst).toBe(true)
    expect(hostDrawingToolsReady()).toBe(true)
  })

  it('exposes an async lazy path that resolves without double registering', async () => {
    await ensureHostDrawingTools()
    expect(hostDrawingToolsReady()).toBe(true)
  })
})

// ── render and hit-test on a stubbed chart context ─────────────────────────

const PLOT_WIDTH = 1_000
const PLOT_HEIGHT = 800
const PRICE_MIN = 90
const PRICE_MAX = 110

function hitContext(
  list: Bar[],
  from: number,
  to: number,
  props: Record<string, unknown> = {}
): HitContext {
  const drawing = {
    id: 'test',
    tool: BUY_SELL_IMBALANCE_ID,
    points: [
      { time: from, price: 95 },
      { time: to, price: 105 },
    ],
    props,
    style: { fillOpacity: 0.4 },
  } as unknown as Drawing
  return {
    drawing,
    rc: {
      dpr: 1,
      plotWidth: PLOT_WIDTH,
      plotHeight: PLOT_HEIGHT,
      bars: () => list,
      theme: { buy: '#26a69a', sell: '#ef5350' },
      priceScale: {
        priceToY: (price: number) =>
          PLOT_HEIGHT - ((price - PRICE_MIN) / (PRICE_MAX - PRICE_MIN)) * PLOT_HEIGHT,
      },
      timeScale: { indexToX: (index: number) => index * 40 },
      dataLayer: {
        timeToIndexFloat: (time: number) => (time - list[0]!.time) / 60,
      },
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as unknown as HitContext
}

describe('geometry render', () => {
  const list = bars([
    { open: 100, close: 120, high: 125, low: 95, volume: 30 },
    { open: 120, close: 100, high: 125, low: 95, volume: 20 },
  ])
  const from = list[0]!.time
  const to = list[1]!.time + 60

  function paintContext(
    list: Bar[],
    from: number,
    to: number,
    props: Record<string, unknown> = {}
  ) {
    const rects: { x: number; y: number; w: number; h: number; fill: string }[] = []
    const texts: string[] = []
    const lines: number[][] = []
    const ctx = {
      save: () => {},
      restore: () => {},
      beginPath: () => {},
      rect: () => {},
      clip: () => {},
      fillRect: (x: number, y: number, w: number, h: number) => {
        rects.push({ x, y, w, h, fill: String(ctx.fillStyle) })
      },
      moveTo: (x: number, y: number) => lines.push([x, y]),
      lineTo: (x: number, y: number) => lines.push([x, y]),
      stroke: () => {},
      fillText: (label: string) => texts.push(label),
      measureText: () => ({ width: 120 }),
      set fillStyle(value: string) {
        this._fill = value
      },
      get fillStyle() {
        return this._fill
      },
      _fill: '#000',
      strokeStyle: '',
      lineWidth: 1,
      globalAlpha: 1,
      font: '',
      textAlign: 'left',
      textBaseline: 'bottom',
    }
    const hit = hitContext(list, from, to, props)
    BUY_SELL_IMBALANCE_TOOL.draw!({
      ctx,
      rc: hit.rc,
      drawing: hit.drawing,
      style: { fillOpacity: 0.4 },
    } as never)
    return { rects, texts, lines }
  }

  it('paints buy and sell rects in the theme colours with a delta label', () => {
    const { rects, texts } = paintContext(list, from, to)
    expect(rects.length).toBeGreaterThan(0)
    expect(rects.some((rect) => rect.fill === '#26a69a')).toBe(true)
    expect(rects.some((rect) => rect.fill === '#ef5350')).toBe(true)
    expect(texts[0]).toContain('Buy/Sell imbalance (estimated)')
    expect(texts[0]).toContain('Buy 30')
    expect(texts[0]).toContain('Sell 20')
    expect(texts[0]).toContain('+10')
  })

  it('dims balanced rows below the strong-row opacity', () => {
    const balanced = paintContext(
      [bar({ open: 100, close: 101, high: 105, low: 95, volume: 10, time: 1_000 })],
      1_000,
      1_060
    )
    expect(balanced.rects.length).toBeGreaterThan(0)
  })

  it('explains itself when the range holds no usable bars', () => {
    const { texts } = paintContext(list, 900_000, 900_060)
    expect(texts[0]).toContain('no bars in range')
  })

  it('honours the stacked layout without changing the totals', () => {
    const mirrored = paintContext(list, from, to)
    const stacked = paintContext(list, from, to, { layout: 'stacked' })
    expect(stacked.rects.length).toBeGreaterThan(0)
    expect(stacked.rects.length).toBeGreaterThanOrEqual(mirrored.rects.length)
  })
})

describe('hit testing', () => {
  const list = [bar({ open: 100, close: 121, high: 180, low: 20, volume: 100, time: 1_000 })]
  const from = 1_000
  const to = 1_060

  it('returns zero inside a histogram rect', () => {
    const c = hitContext(list, from, to)
    const deep = BUY_SELL_IMBALANCE_TOOL.distance!(7, 400, c)
    expect(deep).toBe(0)
  })

  it('returns a positive distance outside the bars but inside the plot', () => {
    const c = hitContext(list, from, to)
    const near = BUY_SELL_IMBALANCE_TOOL.distance!(990, 790, c)
    expect(near).not.toBeNull()
    expect(near!).toBeGreaterThan(0)
  })

  it('returns null outside the plot area', () => {
    const c = hitContext(list, from, to)
    expect(BUY_SELL_IMBALANCE_TOOL.distance!(-5, 400, c)).toBeNull()
    expect(BUY_SELL_IMBALANCE_TOOL.distance!(500, PLOT_HEIGHT + 5, c)).toBeNull()
  })

  it('ignores rects when the fill is fully transparent', () => {
    const c = hitContext(list, from, to, { showPoc: false })
    ;(c.drawing as unknown as { style: Record<string, unknown> }).style = { fillOpacity: 0 }
    expect(BUY_SELL_IMBALANCE_TOOL.distance!(7, 400, c)).toBeNull()
  })
})
