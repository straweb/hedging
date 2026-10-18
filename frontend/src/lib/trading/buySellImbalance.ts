/**
 * Buy/Sell Imbalance, a host-registered range study beside the engine's Fixed
 * Range Volume Profile.
 *
 * The trader picks a range with two anchors, exactly like the volume profile,
 * and every price row inside it shows buying volume against selling volume,
 * estimated from the candles: a bar that closes above its open contributes all
 * of its volume to the buy side, one that closes below to the sell side, and a
 * doji splits evenly. This is the candle-based approximation the platform's
 * own profile uses — it is not trade-by-trade order flow, so the label says
 * "estimated" and the tool never claims tick accuracy.
 *
 * The module is host-owned and loads no chart engine code at import time: the
 * descriptor below types itself through type-only imports, the settings
 * schema is plain data, and `ensureHostDrawingTools` pulls the draw tier
 * lazily the one moment registration is needed. That keeps the drawing rail's
 * import as light as the generated metadata it reads.
 */
import type { Bar } from 'openalgo-charts'
import type {
  DrawContext,
  Drawing,
  DrawingTool,
  HitContext,
  SettingsField,
  SettingsSchema,
} from 'openalgo-charts/draw'

/** Partial results have unavailable anchor history, missing volume or unusable prices. */
export type ImbalanceStatus =
  | 'ready'
  | 'partial'
  | 'missing-volume'
  | 'zero-volume'
  | 'empty'
  | 'invalid-data'

export interface ImbalanceRow {
  low: number
  high: number
  buy: number
  sell: number
}

export interface BuySellImbalanceResult {
  status: ImbalanceStatus
  /** The requested range begins before the earliest supplied bar. */
  historyPartial: boolean
  rows: ImbalanceRow[]
  totalBuy: number
  totalSell: number
  /** Midpoint of the row with the most buying volume. Ties choose the lower price. */
  buyPoc: number | null
  /** Midpoint of the row with the most selling volume. Ties choose the lower price. */
  sellPoc: number | null
  missingVolumeBars: number
  invalidPriceBars: number
}

export interface BuySellImbalanceOptions {
  /** Requested price rows, rounded and clamped to 4..200. Default 48. */
  rows?: number
}

export function imbalanceNumber(
  value: unknown,
  fallback: number,
  min: number,
  max: number
): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.max(min, Math.min(max, value))
    : fallback
}

/** The engine's binary window bounds: first bar at or after `time`, optionally exclusive. */
export function imbalanceTimeBound(
  bars: readonly { time: number }[],
  time: number,
  upper = false
): number {
  let lo = 0,
    hi = bars.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (upper ? bars[mid]!.time <= time : bars[mid]!.time < time)
      lo = mid + 1 // mid < hi <= length
    else hi = mid
  }
  return lo
}

/**
 * Split one bar's volume between the sides. Close above open is buying, below
 * is selling, and a doji has no side of its own, so it splits evenly — the
 * candle-based estimate used across the platform.
 */
export function classifyVolume(bar: Bar): { buy: number; sell: number } {
  const volume = bar.volume
  if (volume === undefined || !Number.isFinite(volume) || volume <= 0) return { buy: 0, sell: 0 }
  if (bar.close > bar.open) return { buy: volume, sell: 0 }
  if (bar.close < bar.open) return { buy: 0, sell: volume }
  return { buy: volume / 2, sell: volume / 2 }
}

/**
 * Estimate the buy and sell histograms over the inclusive timestamp range.
 * Each candle's classified volume is uniform over low..high; difference arrays
 * distribute interior rows without visiting each row per candle, the same
 * O(bars log(rows) + rows) shape the engine's profile uses.
 */
export function buySellImbalanceAnalysis(
  bars: readonly Bar[],
  fromTime: number,
  toTime: number,
  options: BuySellImbalanceOptions = {}
): BuySellImbalanceResult {
  const result: BuySellImbalanceResult = {
    status: 'empty',
    historyPartial: false,
    rows: [],
    totalBuy: 0,
    totalSell: 0,
    buyPoc: null,
    sellPoc: null,
    missingVolumeBars: 0,
    invalidPriceBars: 0,
  }
  if (!Number.isFinite(fromTime) || !Number.isFinite(toTime)) return result
  result.historyPartial = bars.length > 0 && Math.min(fromTime, toTime) < bars[0]!.time
  const start = imbalanceTimeBound(bars, Math.min(fromTime, toTime))
  const end = imbalanceTimeBound(bars, Math.max(fromTime, toTime), true)
  let low = Infinity,
    high = -Infinity
  for (let i = start; i < end; i++) {
    const bar = bars[i]! // end <= length
    const { buy, sell } = classifyVolume(bar)
    if (buy === 0 && sell === 0) {
      const volume = bar.volume
      if (volume === undefined || !Number.isFinite(volume) || volume < 0) result.missingVolumeBars++
      const lo = bar.low,
        hi = bar.high
      if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi < lo) result.invalidPriceBars++
      continue
    }
    if (!Number.isFinite(bar.low) || !Number.isFinite(bar.high) || bar.high < bar.low) {
      result.invalidPriceBars++
      continue
    }
    low = Math.min(low, bar.low)
    high = Math.max(high, bar.high)
    result.totalBuy += buy
    result.totalSell += sell
  }
  const barsInWindow = end - start
  if (barsInWindow === 0) {
    result.status = 'empty'
    return result
  }
  if (result.totalBuy + result.totalSell <= 0) {
    result.status =
      result.missingVolumeBars > 0
        ? 'missing-volume'
        : result.invalidPriceBars > 0
          ? 'invalid-data'
          : 'zero-volume'
    return result
  }
  if (
    !Number.isFinite(result.totalBuy) ||
    !Number.isFinite(result.totalSell) ||
    !Number.isFinite(high - low)
  ) {
    return { ...result, status: 'invalid-data', totalBuy: 0, totalSell: 0 }
  }
  const partial =
    result.historyPartial || result.missingVolumeBars > 0 || result.invalidPriceBars > 0
  result.status = partial ? 'partial' : 'ready'

  const requested = Math.round(imbalanceNumber(options.rows, 48, 4, 200))
  const boundaries = [low]
  for (let i = 1; i < requested; i++) {
    const edge = low + (high - low) * (i / requested)
    if (edge > boundaries[boundaries.length - 1]! && edge < high) boundaries.push(edge) // never empty
  }
  boundaries.push(high)
  const count = boundaries.length - 1,
    step = (high - low) / count
  const edges = (i: number): number => boundaries[i]!
  const buys = new Float64Array(count),
    sells = new Float64Array(count)
  const buyChanges = new Float64Array(count + 1),
    sellChanges = new Float64Array(count + 1)
  const index = (price: number): number => {
    let lo = 0,
      hi = count
    while (lo < hi) {
      const mid = (lo + hi + 1) >>> 1
      if (boundaries[mid]! <= price) lo = mid
      else hi = mid - 1
    }
    return Math.min(count - 1, lo)
  }
  for (let i = start; i < end; i++) {
    const bar = bars[i]!
    const { buy, sell } = classifyVolume(bar)
    if (
      buy + sell <= 0 ||
      !Number.isFinite(bar.low) ||
      !Number.isFinite(bar.high) ||
      bar.high < bar.low
    )
      continue
    const scale = result.totalBuy + result.totalSell
    const buyWeight = buy / scale,
      sellWeight = sell / scale
    const first = index(bar.low),
      last = index(bar.high)
    if (first === last) {
      buys[first] += buyWeight
      sells[first] += sellWeight
      continue
    }
    const span = bar.high - bar.low
    const firstShare = (edges(first + 1) - bar.low) / span
    const lastShare = (bar.high - edges(last)) / span
    buys[first] += buyWeight * firstShare
    sells[first] += sellWeight * firstShare
    buys[last] += buyWeight * lastShare
    sells[last] += sellWeight * lastShare
    if (last > first + 1) {
      const interiorBuy = buyWeight * (step / span)
      const interiorSell = sellWeight * (step / span)
      buyChanges[first + 1] += interiorBuy
      buyChanges[last] -= interiorBuy
      sellChanges[first + 1] += interiorSell
      sellChanges[last] -= interiorSell
    }
  }
  // Weights are shares of the range's whole volume, so both sides restore to
  // absolute volume with the same scale: buys sum to totalBuy, sells to
  // totalSell, and a row's buy + sell matches the profile's volume for it.
  const scale = result.totalBuy + result.totalSell
  let buyRunning = 0,
    sellRunning = 0,
    buyPeak = 0,
    sellPeak = 0
  for (let i = 0; i < count; i++) {
    buyRunning += buyChanges[i]!
    sellRunning += sellChanges[i]!
    const interior = count === 1 ? 1 : (edges(i + 1) - edges(i)) / step
    buys[i] = Math.max(0, Math.min(1, buys[i]! + buyRunning * interior)) * scale
    sells[i] = Math.max(0, Math.min(1, sells[i]! + sellRunning * interior)) * scale
    if (buys[i]! > buys[buyPeak]!) buyPeak = i
    if (sells[i]! > sells[sellPeak]!) sellPeak = i
    result.rows.push({ low: edges(i), high: edges(i + 1), buy: buys[i]!, sell: sells[i]! })
  }
  if (result.totalBuy > 0) result.buyPoc = edges(buyPeak) / 2 + edges(buyPeak + 1) / 2
  if (result.totalSell > 0) result.sellPoc = edges(sellPeak) / 2 + edges(sellPeak + 1) / 2
  return result
}

// ── the drawing tool ───────────────────────────────────────────────────────

export const BUY_SELL_IMBALANCE_ID = 'buy-sell-imbalance'
export const BUY_SELL_IMBALANCE_NAME = 'Buy/Sell Imbalance'

/** Icon: a centre axis with histogram bars alternating to both sides. */
export const BUY_SELL_IMBALANCE_ICON = 'M12 3v18M12 6h8v2h-8M4 11h8v2H4M12 16h8v2h-8'

/**
 * Rail metadata for tools the host registers itself, shaped like the generated
 * `DRAW_TOOL_METADATA` entries. The generated file stays a pure snapshot of
 * the pinned package's registry; host-owned tools keep their labels and glyphs
 * here, where regenerating one cannot drop the other.
 */
export const HOST_DRAW_TOOL_METADATA: Readonly<
  Record<string, { name: string; path: string; text: boolean }>
> = {
  [BUY_SELL_IMBALANCE_ID]: {
    name: BUY_SELL_IMBALANCE_NAME,
    path: BUY_SELL_IMBALANCE_ICON,
    text: false,
  },
}

/** Stroke the engine falls back to when a drawing carries no font family. */
const DEFAULT_FONT = 'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif'

interface ImbalanceRect {
  left: number
  right: number
  top: number
  bottom: number
  strong: boolean
  sell: boolean
}
interface ImbalanceLine {
  a: { x: number; y: number }
  b: { x: number; y: number }
  color: string
}
interface ImbalanceGeometry {
  lines: ImbalanceLine[]
  rects: ImbalanceRect[]
  label: string
  labelAt: { x: number; y: number }
  buyColor: string
  sellColor: string
  status: ImbalanceStatus
}

function emptyGeometry(): ImbalanceGeometry {
  return {
    lines: [],
    rects: [],
    label: '',
    labelAt: { x: 0, y: 0 },
    buyColor: '#26a69a',
    sellColor: '#ef5350',
    status: 'empty',
  }
}

function propNumber(
  drawing: Drawing,
  key: string,
  fallback: number,
  min: number,
  max: number
): number {
  return imbalanceNumber(drawing.props?.[key], fallback, min, max)
}

function propBool(drawing: Drawing, key: string, fallback: boolean): boolean {
  const value = drawing.props?.[key]
  return value === undefined ? fallback : value === true
}

function propColor(drawing: Drawing, key: string, fallback: string): string {
  const value = drawing.props?.[key]
  return typeof value === 'string' && value !== '' ? value : fallback
}

/** The engine's partial-readout suffix, so a thin range still explains itself. */
function statusLabel(result: {
  status: ImbalanceStatus
  historyPartial: boolean
  missingVolumeBars: number
  invalidPriceBars: number
}): string {
  const history = result.historyPartial ? ' (partial history)' : ''
  switch (result.status) {
    case 'partial':
      return (
        history +
        (result.missingVolumeBars > 0 || result.invalidPriceBars > 0
          ? ' (partial volume/data)'
          : '')
      )
    case 'missing-volume':
      return `: volume unavailable${history}`
    case 'zero-volume':
      return `: zero volume${history}`
    case 'invalid-data':
      return `: data unavailable${history}`
    case 'empty':
      return `: no bars in range${history}`
    default:
      return ''
  }
}

/** The profile label's compact volume: same shape as the engine's compactVol. */
function compactVolume(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(2)}K`
  return `${Math.round(value)}`
}

function geometry(c: HitContext): ImbalanceGeometry {
  const g = emptyGeometry(),
    [a, b] = c.drawing.points
  if (!a || !b) return g
  const result = buySellImbalanceAnalysis(c.rc.bars?.() ?? [], a.time, b.time, {
    rows: propNumber(c.drawing, 'rows', 48, 4, 200),
  })
  g.status = result.status
  const theme = c.rc.theme as { buy?: string; sell?: string } | undefined
  g.buyColor = propColor(c.drawing, 'buyColor', theme?.buy ?? '#26a69a')
  g.sellColor = propColor(c.drawing, 'sellColor', theme?.sell ?? '#ef5350')
  const pa = project(c, a),
    pb = project(c, b)
  const left = Math.min(pa.x, pb.x),
    right = Math.max(pa.x, pb.x)
  const side = c.drawing.props?.side === 'right' ? 'right' : 'left'
  const stacked = c.drawing.props?.layout === 'stacked'
  const width = (Math.max(12, right - left) * propNumber(c.drawing, 'width', 35, 5, 100)) / 100
  const threshold = propNumber(c.drawing, 'threshold', 3, 1, 10)
  const showPoc = propBool(c.drawing, 'showPoc', true)
  const showDelta = propBool(c.drawing, 'showDelta', true)
  g.label = `Buy/Sell imbalance (estimated)${statusLabel(result)}`
  g.labelAt = { x: left, y: Math.min(pa.y, pb.y) }
  if (result.rows.length === 0 || result.totalBuy + result.totalSell <= 0) return g
  const max = Math.max(0, ...result.rows.map((row) => row.buy + row.sell))
  if (max <= 0) return g
  const buyPivot = side === 'right' ? right : left
  const sellPivot = side === 'right' ? left : right
  const buyDir = side === 'right' ? -1 : 1
  const sellDir = side === 'right' ? 1 : -1
  const imbalanceRatio = (row: ImbalanceRow): { strong: boolean; sell: boolean } => {
    if (row.buy > 0 && row.sell > 0) {
      const ratio = row.buy >= row.sell ? row.buy / row.sell : row.sell / row.buy
      return { strong: ratio >= threshold, sell: row.sell > row.buy }
    }
    return { strong: true, sell: row.sell > 0 }
  }
  for (const row of result.rows) {
    const y1 = c.rc.priceScale.priceToY(row.low),
      y2 = c.rc.priceScale.priceToY(row.high)
    if (![y1, y2].every(Number.isFinite)) continue
    const total = row.buy + row.sell
    if (total <= 0) continue
    const top = Math.max(0, Math.min(y1, y2))
    const bottom = Math.min(c.rc.plotHeight, Math.max(y1, y2) + (y1 === y2 ? 1 : 0))
    const flag = imbalanceRatio(row)
    if (stacked) {
      const w = (width * total) / max
      const buyShare = row.buy / total
      const buyW = w * buyShare,
        sellW = w - buyW
      const buyRect = {
        left: Math.min(buyPivot, buyPivot + buyDir * buyW),
        right: Math.max(buyPivot, buyPivot + buyDir * buyW),
        top,
        bottom,
        strong: flag.strong && !flag.sell,
        sell: false,
      }
      const sellStart = buyPivot + buyDir * buyW
      const sellRect = {
        left: Math.min(sellStart, sellStart + sellDir * sellW),
        right: Math.max(sellStart, sellStart + sellDir * sellW),
        top,
        bottom,
        strong: flag.strong && flag.sell,
        sell: true,
      }
      if (buyRect.left < buyRect.right && buyRect.top < buyRect.bottom) g.rects.push(buyRect)
      if (sellRect.left < sellRect.right && sellRect.top < sellRect.bottom) g.rects.push(sellRect)
    } else {
      const buyW = (width * row.buy) / max,
        sellW = (width * row.sell) / max
      const buyRect = {
        left: Math.min(buyPivot, buyPivot + buyDir * buyW),
        right: Math.max(buyPivot, buyPivot + buyDir * buyW),
        top,
        bottom,
        strong: flag.strong && !flag.sell,
        sell: false,
      }
      const sellRect = {
        left: Math.min(sellPivot, sellPivot + sellDir * sellW),
        right: Math.max(sellPivot, sellPivot + sellDir * sellW),
        top,
        bottom,
        strong: flag.strong && flag.sell,
        sell: true,
      }
      if (buyRect.left < buyRect.right && buyRect.top < buyRect.bottom) g.rects.push(buyRect)
      if (sellRect.left < sellRect.right && sellRect.top < sellRect.bottom) g.rects.push(sellRect)
    }
  }
  const level = (price: number | null, sell: boolean): void => {
    if (price === null) return
    const y = c.rc.priceScale.priceToY(price)
    if (!Number.isFinite(y)) return
    const pivot = sell ? sellPivot : buyPivot
    const to = sell ? sellPivot + sellDir * width : buyPivot + buyDir * width
    g.lines.push({
      a: { x: Math.min(pivot, to), y },
      b: { x: Math.max(pivot, to), y },
      color: sell ? g.sellColor : g.buyColor,
    })
  }
  if (showPoc) {
    level(result.buyPoc, false)
    level(result.sellPoc, true)
  }
  if (showDelta && (result.totalBuy > 0 || result.totalSell > 0)) {
    const total = result.totalBuy + result.totalSell
    const delta = result.totalBuy - result.totalSell
    const share = total > 0 ? (Math.abs(delta) / total) * 100 : 0
    const side_ = delta >= 0 ? 'buy' : 'sell'
    g.label = `${g.label}  Buy ${compactVolume(result.totalBuy)} · Sell ${compactVolume(result.totalSell)} · Δ ${delta >= 0 ? '+' : '−'}${compactVolume(Math.abs(delta))} (${share.toFixed(0)}% ${side_})`
  }
  if (result.buyPoc !== null)
    g.labelAt.y = c.rc.priceScale.priceToY(result.rows[result.rows.length - 1]!.high)
  return g
}

/**
 * Anchors are in time and price; the geometry projects them into media px the
 * same way the engine's own analysis tools do: a float bar index through the
 * data layer, then the time scale's index-to-x map.
 */
function project(c: HitContext, point: { time: number; price: number }): { x: number; y: number } {
  return {
    x: c.rc.timeScale.indexToX(c.rc.dataLayer.timeToIndexFloat(point.time)),
    y: c.rc.priceScale.priceToY(point.price),
  }
}

function paint(c: DrawContext, build: (c: HitContext) => ImbalanceGeometry): void {
  const { ctx, rc, drawing, style } = c,
    dpr = rc.dpr
  if (!(rc.plotWidth > 0 && rc.plotHeight > 0)) return
  const g = build({ drawing, rc, pts: [] })
  ctx.save()
  ctx.beginPath()
  ctx.rect(0, 0, rc.plotWidth * dpr, rc.plotHeight * dpr)
  ctx.clip()
  const baseOpacity = imbalanceNumber(style.fillOpacity, 0.4, 0, 1)
  // One rect at a time, so each side keeps its own colour and emphasis.
  for (const rect of g.rects) {
    const color = rect.sell ? g.sellColor : g.buyColor
    // Strong rows carry the requested opacity; balanced rows sink to 45% of
    // it, the same dimming the engine's profile gives non-value-area rows.
    const alpha = baseOpacity * (rect.strong ? 1 : 0.45)
    if (alpha <= 0) continue
    ctx.save()
    ctx.globalAlpha *= alpha
    ctx.fillStyle = color
    ctx.fillRect(
      rect.left * dpr,
      rect.top * dpr,
      (rect.right - rect.left) * dpr,
      (rect.bottom - rect.top) * dpr
    )
    ctx.restore()
  }
  if (g.lines.length > 0) {
    for (const line of g.lines) {
      ctx.save()
      ctx.strokeStyle = line.color
      ctx.lineWidth = Math.max(1, imbalanceNumber(style.lineWidth, 1, 1, 12)) * dpr
      ctx.globalAlpha *= 0.8
      ctx.beginPath()
      ctx.moveTo(line.a.x * dpr, line.a.y * dpr)
      ctx.lineTo(line.b.x * dpr, line.b.y * dpr)
      ctx.stroke()
      ctx.restore()
    }
  }
  if (
    (style.showLabels !== false || g.status !== 'ready') &&
    g.label &&
    Number.isFinite(g.labelAt.x) &&
    Number.isFinite(g.labelAt.y)
  ) {
    const text = drawing.text,
      size = imbalanceNumber(text?.fontSize, 11, 6, 96)
    ctx.fillStyle = text?.color ?? style.color
    const weight = text?.bold === true ? '700 ' : ''
    const italic = text?.italic === true ? 'italic ' : ''
    const family =
      text?.fontFamily === undefined || text?.fontFamily === '' ? DEFAULT_FONT : text?.fontFamily
    ctx.font = `${italic}${weight}${size * dpr}px ${family}`
    ctx.textAlign = 'left'
    ctx.textBaseline = 'bottom'
    const width = ctx.measureText(g.label).width / dpr
    const x = Math.max(4, Math.min(rc.plotWidth - width - 4, g.labelAt.x + 4))
    const y = Math.max(size + 3, Math.min(rc.plotHeight - 3, g.labelAt.y - 5))
    ctx.fillText(g.label, x * dpr, y * dpr)
  }
  ctx.restore()
}

function distance(
  x: number,
  y: number,
  c: HitContext,
  build: (c: HitContext) => ImbalanceGeometry
): number | null {
  if (x < 0 || y < 0 || x > c.rc.plotWidth || y > c.rc.plotHeight) return null
  const g = build(c)
  let best = Infinity
  for (const line of g.lines) {
    const dx = line.b.x - line.a.x,
      dy = line.b.y - line.a.y
    const length = dx * dx + dy * dy
    const t =
      length === 0
        ? 0
        : Math.max(0, Math.min(1, ((x - line.a.x) * dx + (y - line.a.y) * dy) / length))
    best = Math.min(best, Math.hypot(x - (line.a.x + t * dx), y - (line.a.y + t * dy)))
  }
  if (imbalanceNumber(c.drawing.style.fillOpacity, 0.4, 0, 1) > 0) {
    for (const rect of g.rects) {
      const inside = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
      best = Math.min(
        best,
        inside
          ? 0
          : Math.hypot(
              Math.max(rect.left - x, 0, x - rect.right),
              Math.max(rect.top - y, 0, y - rect.bottom)
            )
      )
    }
  }
  return Number.isFinite(best) ? best : null
}

const imbalanceSettings: SettingsSchema = {
  fields: [
    {
      path: 'style.fillOpacity',
      label: 'Histogram opacity',
      kind: 'opacity',
      min: 0,
      max: 1,
      step: 0.05,
      group: 'fill',
      defaultValue: 0.4,
    },
    {
      path: 'props.rows',
      label: 'Price rows',
      kind: 'number',
      min: 4,
      max: 200,
      step: 1,
      group: 'behavior',
      defaultValue: 48,
    },
    {
      path: 'props.width',
      label: 'Histogram width (%)',
      kind: 'number',
      min: 5,
      max: 100,
      step: 1,
      group: 'behavior',
      defaultValue: 35,
    },
    {
      path: 'props.side',
      label: 'Buy side',
      kind: 'select',
      group: 'behavior',
      defaultValue: 'left',
      options: [
        { value: 'left', label: 'Left' },
        { value: 'right', label: 'Right' },
      ],
    },
    {
      path: 'props.layout',
      label: 'Layout',
      kind: 'select',
      group: 'behavior',
      defaultValue: 'mirror',
      options: [
        { value: 'mirror', label: 'Mirrored' },
        { value: 'stacked', label: 'Stacked' },
      ],
    },
    {
      path: 'props.threshold',
      label: 'Imbalance ratio (x)',
      kind: 'number',
      min: 1,
      max: 10,
      step: 0.25,
      group: 'behavior',
      defaultValue: 3,
    },
    {
      path: 'props.showPoc',
      label: 'POC levels',
      kind: 'boolean',
      group: 'behavior',
      defaultValue: true,
    },
    {
      path: 'props.showDelta',
      label: 'Net delta readout',
      kind: 'boolean',
      group: 'behavior',
      defaultValue: true,
    },
    {
      path: 'props.buyColor',
      label: 'Buy color',
      kind: 'color',
      group: 'fill',
      defaultValue: '#26a69a',
    },
    {
      path: 'props.sellColor',
      label: 'Sell color',
      kind: 'color',
      group: 'fill',
      defaultValue: '#ef5350',
    },
    {
      path: 'style.showLabels',
      label: 'Show labels',
      kind: 'boolean',
      group: 'behavior',
      defaultValue: true,
    },
    { path: 'text.color', label: 'Text color', kind: 'color', group: 'text' },
    {
      path: 'text.fontSize',
      label: 'Font size',
      kind: 'number',
      min: 6,
      max: 96,
      step: 1,
      group: 'text',
    },
    {
      path: 'text.fontFamily',
      label: 'Font',
      kind: 'select',
      group: 'text',
      options: [
        { value: DEFAULT_FONT, label: 'Sans' },
        { value: 'ui-serif, Georgia, Times New Roman, serif', label: 'Serif' },
        { value: 'ui-monospace, Menlo, Consolas, monospace', label: 'Mono' },
      ],
    },
    { path: 'text.bold', label: 'Bold', kind: 'boolean', group: 'text' },
    { path: 'text.italic', label: 'Italic', kind: 'boolean', group: 'text' },
  ] satisfies SettingsField[],
}

/**
 * The descriptor. `draw` and `distance` mirror the engine's analysis tools:
 * build the geometry from the pane's bars, paint it clipped to the plot, and
 * hit-test the same rects and levels.
 */
export const BUY_SELL_IMBALANCE_TOOL: DrawingTool = {
  id: BUY_SELL_IMBALANCE_ID,
  name: BUY_SELL_IMBALANCE_NAME,
  points: 2,
  defaultStyle: { showLabels: true, fillOpacity: 0.4 },
  settings: imbalanceSettings,
  draw: (c) => paint(c, geometry),
  distance: (x, y, c) => distance(x, y, c, geometry),
}

// ── registration ───────────────────────────────────────────────────────────

let registered = false

/** Register every host drawing tool against the tier's own registry. Idempotent. */
export function registerHostDrawingTools(register: (tool: DrawingTool) => void): void {
  if (registered) return
  register(BUY_SELL_IMBALANCE_TOOL)
  registered = true
}

/**
 * Load the draw tier once and register into it. Call before the controller is
 * built and before any `getDrawingTool` validation, so a saved Buy/Sell
 * Imbalance drawing restores in a workspace exactly like a built-in one.
 */
export async function ensureHostDrawingTools(): Promise<void> {
  if (registered) return
  const { registerDrawingTool } = await import('openalgo-charts/draw')
  registerHostDrawingTools(registerDrawingTool)
}

/** What a saved workspace check needs before it validates drawing tool ids. */
export function hostDrawingToolsReady(): boolean {
  return registered
}
