/**
 * Ghost candles: the higher timeframe drawn as hollow candles inside the
 * chart's own.
 *
 * A 1m chart answers "what is the price doing now"; a 5m chart answers "what
 * is the structure doing". Traders switch intervals to see both and lose one
 * while seeing the other. The ghost overlay draws the larger frame around the
 * smaller one: every 5m candle sits hollow and translucent over the 1m candles
 * it contains, so structure and ticks are on screen together.
 *
 * The ghost bars are never fetched separately. They are aggregated from the
 * same bars the chart already shows, which buys three things at once: no extra
 * history requests, exact agreement with what is on screen (a ghost candle's
 * volume is the sum of the volume bars under it), and live updates that are a
 * tail fold of the bar the tick already touched. Only bucket boundaries ask
 * the session calendar, so an overnight gap or a weekend starts a new ghost
 * candle exactly where the exchange's session does.
 */
import { type Bar, type createChart, type SeriesApi, withAlpha } from 'openalgo-charts'
import { intervalSeconds } from './intervals'
import { DN, UP } from './legend'

/** The higher timeframes a ghost overlay can be built from. */
export const GHOST_INTERVALS = ['3m', '5m', '10m', '15m', '30m', '1h'] as const
export type GhostInterval = (typeof GHOST_INTERVALS)[number]

export function isGhostInterval(code: unknown): code is GhostInterval {
  return typeof code === 'string' && (GHOST_INTERVALS as readonly string[]).includes(code)
}

/**
 * Session-aware bucket start for a time, as `createTradingTimeframes` exposes
 * it. Narrowed to what the aggregator asks: the ghost interval and a time.
 */
export type GhostBucketStart = (interval: string, time: number) => number

/** Ghost candle colours, translucent so the lower frame stays visible through them. */
export const GHOST_UP = withAlpha(UP, 0.55)
export const GHOST_DOWN = withAlpha(DN, 0.55)

/** One aggregated higher-timeframe candle. */
export interface GhostBar {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/**
 * Fold source bars into their ghost buckets, in order.
 *
 * Bars are stamped at their bucket's open, so a bar belongs to the bucket whose
 * span contains it. The arithmetic fast path answers "same bucket" for every
 * bar inside the current one; the calendar is asked only at a boundary, which
 * is also where a session gap (overnight, weekend, holiday) is caught — the
 * calendar's answer may be hours past the arithmetic one, and it wins.
 *
 * Bars the calendar cannot place (outside every session) are skipped rather
 * than folded into an invented bucket.
 */
export function aggregateGhostBars(
  bars: readonly Bar[],
  ghostInterval: string,
  bucketStart: GhostBucketStart
): GhostBar[] {
  const sec = intervalSeconds(ghostInterval)
  if (!sec || sec <= 0 || !bars.length) return []
  const out: GhostBar[] = []
  let cur: GhostBar | null = null
  let curEnd = 0
  for (const bar of bars) {
    if (!cur || bar.time >= curEnd) {
      let start: number
      try {
        start = bucketStart(ghostInterval, bar.time)
      } catch {
        cur = null
        curEnd = 0
        continue
      }
      if (!Number.isFinite(start)) {
        cur = null
        curEnd = 0
        continue
      }
      curEnd = start + sec
      cur = {
        time: start,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume ?? 0,
      }
      out.push(cur)
    } else {
      if (bar.high > cur.high) cur.high = bar.high
      if (bar.low < cur.low) cur.low = bar.low
      cur.close = bar.close
      cur.volume += bar.volume ?? 0
    }
  }
  return out
}

/** The ghost candle a lower-frame bar belongs to, by bucket containment. */
export function ghostBarAt(bars: readonly GhostBar[], time: number | null): GhostBar | null {
  if (time === null || !bars.length) return null
  let lo = 0
  let hi = bars.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1
    const bar = bars[mid]
    if (bar.time === time) return bar
    if (bar.time < time) lo = mid + 1
    else hi = mid - 1
  }
  // The hovered bar sits inside the bucket before `lo`, when one exists.
  return bars[lo - 1] ?? null
}

/**
 * What one live fold changed, so the caller can write the minimum to the
 * series: `'append'` a new bucket opened, `'update'` the tail bar moved,
 * `'none'` the bar carried nothing new.
 */
export type GhostFold = 'none' | 'update' | 'append'

/**
 * Incremental tail-fold state. `lastTime`/`lastVolume` remember what the tail
 * bar already absorbed, so re-folding the same forming bar on every tick adds
 * its volume once, not once per tick.
 */
export interface GhostFoldState {
  bar: GhostBar | null
  lastTime: number | null
  lastVolume: number
}

/**
 * Fold one live bar into the forming ghost candle, in place.
 *
 * A bar for a bucket already closed (a late repair) is refused: the history
 * refresh re-aggregates everything and is the only writer allowed backwards.
 */
export function foldGhostBar(
  state: GhostFoldState,
  bar: Bar,
  ghostInterval: string,
  bucketStart: GhostBucketStart
): GhostFold {
  const sec = intervalSeconds(ghostInterval)
  if (!sec || sec <= 0) return 'none'
  const last = state.bar
  if (last && bar.time < last.time) return 'none'
  const vol = bar.volume ?? 0
  if (last && bar.time < last.time + sec) {
    const delta = state.lastTime === bar.time ? vol - state.lastVolume : vol
    state.lastTime = bar.time
    state.lastVolume = vol
    if (bar.high > last.high) last.high = bar.high
    if (bar.low < last.low) last.low = bar.low
    last.close = bar.close
    last.volume += delta
    return 'update'
  }
  let start: number
  try {
    start = bucketStart(ghostInterval, bar.time)
  } catch {
    return 'none'
  }
  if (!Number.isFinite(start)) return 'none'
  state.bar = {
    time: start,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: vol,
  }
  state.lastTime = bar.time
  state.lastVolume = vol
  return 'append'
}

/** The series style every ghost overlay carries: hollow, translucent, silent. */
export function ghostSeriesStyle() {
  return {
    upColor: GHOST_UP,
    downColor: GHOST_DOWN,
    borderUpColor: GHOST_UP,
    borderDownColor: GHOST_DOWN,
    wickUpColor: GHOST_UP,
    wickDownColor: GHOST_DOWN,
    // Outline and wick only: the body's interior stays empty, which is what
    // lets the chart's own candles show through and makes the overlay read as
    // a ghost rather than a second chart painted over the first.
    bodyVisible: false,
    // One price on this scale already has the engine's dashed last-price line
    // and its axis tag. A second series answering at the same scale would draw
    // both again, one printed through the other.
    priceLineVisible: false,
    lastValueVisible: false,
  }
}

type GhostChart = ReturnType<typeof createChart>

/**
 * Owns the ghost series on one chart, rebuilt with it.
 *
 * Lifecycle mirrors the chart's: `attach` after every build (the series the
 * old chart owned went away with it), `syncAll` whenever history is replaced,
 * `foldLive` on every tick, `detach` when the pane closes. Toggling the
 * timeframe reuses the live series — `setData` replaces its rows — so the
 * overlay turns on, off and between frames without touching the chart.
 */
export class GhostCandles {
  private tf: string | null = null
  private chart: GhostChart | null = null
  private series: SeriesApi | null = null
  private source: (() => readonly Bar[]) | null = null
  private bucketStart: GhostBucketStart | null = null
  private format: ((value: number) => string) | null = null
  private bars: GhostBar[] = []
  /** The forming ghost candle; the same object the tail of `bars` holds. */
  private fold: GhostFoldState = { bar: null, lastTime: null, lastVolume: 0 }
  /** Off while replay owns the display: the overlay must not show the future. */
  private visible = true

  interval(): string | null {
    return this.tf
  }

  /** The aggregated ghost candles as they stand, oldest first. */
  ghostBars(): readonly GhostBar[] {
    return this.bars
  }

  /** Whether a series is live on the current chart. */
  attached(): boolean {
    return this.series !== null
  }

  /**
   * Point the overlay at a chart. Called after every chart rebuild; anything
   * the previous chart owned is already gone.
   */
  attach(
    chart: GhostChart | null,
    source: () => readonly Bar[],
    bucketStart: GhostBucketStart,
    format: (value: number) => string
  ): void {
    this.chart = chart
    this.source = source
    this.bucketStart = bucketStart
    this.format = format
    this.series = null
    this.bars = []
    this.fold = { bar: null, lastTime: null, lastVolume: 0 }
    if (chart) this.ensureSeries()
  }

  /** Forget the chart (it is being torn down, or the pane is closing). */
  detach(): void {
    this.chart = null
    this.series = null
    this.bars = []
    this.fold = { bar: null, lastTime: null, lastVolume: 0 }
  }

  /** Choose the higher timeframe, or null to take the overlay off the chart. */
  setInterval(tf: string | null): 'added' | 'removed' | 'changed' | null {
    const next = tf && isGhostInterval(tf) ? tf : null
    if (next === this.tf) return null
    const had = this.tf !== null
    this.tf = next
    this.bars = []
    this.fold = { bar: null, lastTime: null, lastVolume: 0 }
    if (this.chart) {
      this.ensureSeries()
      this.syncAll()
    }
    if (!had && next) return 'added'
    if (had && !next) return 'removed'
    return 'changed'
  }

  /** Hide or show the overlay without forgetting its data. */
  setVisible(on: boolean): void {
    this.visible = on
    this.series?.applyOptions({ visible: on })
  }

  /** Re-aggregate everything the chart's history holds and replace the rows. */
  syncAll(): void {
    const series = this.series
    const src = this.source
    if (!series || !src || !this.tf || !this.bucketStart) return
    const bars = src()
    this.bars = aggregateGhostBars(bars, this.tf, (interval, time) =>
      this.bucketStart!(interval, time)
    )
    const last = bars.length ? bars[bars.length - 1] : null
    this.fold = {
      bar: this.bars.length ? this.bars[this.bars.length - 1] : null,
      lastTime: last ? last.time : null,
      lastVolume: last ? (last.volume ?? 0) : 0,
    }
    series.setData(this.bars)
    if (!this.visible) series.applyOptions({ visible: false })
  }

  /**
   * Fold one live bar into the forming ghost candle and write the minimum to
   * the series. Returns the ghost bar that changed, if any.
   */
  foldLive(bar: Bar): GhostBar | null {
    const series = this.series
    if (!series || !this.tf || !this.bucketStart) return null
    const action = foldGhostBar(this.fold, bar, this.tf, (interval, time) =>
      this.bucketStart!(interval, time)
    )
    if (action === 'none' || !this.fold.bar) return null
    if (action === 'append') this.bars.push(this.fold.bar)
    series.update(this.fold.bar)
    return this.fold.bar
  }

  /** The ghost candle containing `time`, for the legend row. */
  barAt(time: number | null): GhostBar | null {
    return ghostBarAt(this.bars, time)
  }

  private ensureSeries(): void {
    const chart = this.chart
    if (!chart || !this.tf) {
      // Turning the overlay off removes the series it drew; one whose chart is
      // gone is already dead and must not be `remove`d twice.
      if (this.series && chart) {
        this.series.remove()
        this.series = null
      }
      return
    }
    if (this.series) return
    this.series = chart.addSeries('candlestick', {
      paneIndex: 0,
      // The instrument's own scale: same symbol, same prices. An overlay scale
      // would autoscale the ghost range separately and let the two frames
      // disagree about where a price sits.
      priceScaleId: 'right',
      style: ghostSeriesStyle(),
      ...(this.format ? { priceFormat: { type: 'custom', formatter: this.format } as const } : {}),
    })
    if (!this.visible) this.series.applyOptions({ visible: false })
  }
}
