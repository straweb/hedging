import { ChartLine, Loader2, RefreshCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { type SymbolData, useMarketData } from '@/hooks/useMarketData'
import {
  type Contribution,
  constituentsFor,
  contributionLedger,
  type IndexProfile,
  weightedIndexMove,
} from '@/lib/hedging/indexConstituents'
import { cn } from '@/lib/utils'

/**
 * The dock's Index tab: the selected FNO index taken apart into its
 * constituents, live.
 *
 * Every stock of the index (Nifty 50, Bank Nifty, Sensex 30, Fin Nifty —
 * the static profiles in lib/hedging/indexConstituents) shows its current
 * price, traded volume and its % change since the session open (0% is the
 * open, per the spec). Two 1-minute studies sit above the list: a combined
 * volume stack (each minute's traded volume as stacked per-stock segments)
 * and the weighted price-% trail — every stock's series starts at 0% at the
 * session open. The contribution bubble pack shows, per stock, how much of
 * the index's move it explains (weight × move), so the stock driving the
 * index is visible at a glance; clicking a bubble or a row opens the stock's
 * detail popup. A chart icon on each row loads that stock straight into the
 * focused chart pane — the same quick action the side panels carry.
 *
 * The minute series accumulates in this tab only while it is open; the %
 * reference itself is the day open, so a reopened tab still reads the
 * session correctly and just rebuilds its 1m history.
 */

/** Maximum minute buckets kept for the 1m studies. */
const MAX_MINUTES = 24

const STOCK_COLORS = [
  '#3b82f6',
  '#f59e0b',
  '#10b981',
  '#8b5cf6',
  '#ec4899',
  '#14b8a6',
  '#f97316',
  '#a3a3a3',
]

const fmt = (value: number, digits = 2) =>
  value.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })

const fmtPct = (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`

const fmtVol = (value: number) => {
  const abs = Math.abs(value)
  if (abs >= 1e7) return `${(value / 1e7).toFixed(2)}Cr`
  if (abs >= 1e5) return `${(value / 1e5).toFixed(2)}L`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}k`
  return value.toFixed(0)
}

interface MinuteSeries {
  /** Per-minute % change snapshots (session-open referenced). */
  points: number[]
  /** Per-minute traded volume deltas aligned with `points`. */
  volumes: number[]
  lastBucket: number
  firstLtp: number
  lastVolume: number
}

export interface IndexPanelProps {
  /** The FNO underlying whose constituents are shown (e.g. NIFTY). */
  underlying: string
  /** The spot index symbol to quote, from the chain snapshot. */
  indexSpot: { symbol: string; exchange: string } | null
  onLoadChart(pick: { symbol: string; exchange: string; name?: string }): void
}

/** One live row of the constituents table. */
interface StockRow {
  symbol: string
  weight: number
  ltp: number | undefined
  pct: number
  volume: number | undefined
  points: Contribution
  /** 1m % change snapshots since session open. */
  spark: number[]
  /** Per-minute traded-volume deltas, aligned with `spark`. */
  volumes: number[]
}

/** Columns the constituent table sorts on; weight (index order) is the default. */
type ConstituentSort = 'weight' | 'symbol' | 'ltp' | 'pct' | 'volume' | 'contrib' | 'share'

export function IndexPanel(props: IndexPanelProps) {
  const profile = constituentsFor(props.underlying)
  if (!profile) {
    return <IndexEmpty underlying={props.underlying} />
  }
  return <IndexLive profile={profile} {...props} />
}

function IndexEmpty(props: { underlying: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 p-3 text-center">
      <p className="text-[11px] text-muted-foreground">
        No constituent set for <span className="font-mono">{props.underlying}</span>.
      </p>
      <p className="text-[10px] text-muted-foreground">
        Index detail covers NIFTY, BANKNIFTY, SENSEX and FINNIFTY — pick one of those underlyings to
        break the index into its stocks.
      </p>
    </div>
  )
}

/** The live tab for an index with a constituent profile. */
function IndexLive(props: IndexPanelProps & { profile: IndexProfile }) {
  const { profile } = props
  const [tick, setTick] = useState(0)
  const [detail, setDetail] = useState<StockRow | null>(null)
  const [sortKey, setSortKey] = useState<ConstituentSort>('weight')
  const [sortDir, setSortDir] = useState<1 | -1>(1)

  // Quote everything at once: the constituents + the index spot itself, in
  // Quote mode so volumes and opens ride along with the LTPs.
  const quoteSymbols = useMemo(() => {
    const list = profile.constituents.map((row) => ({
      symbol: row.symbol,
      exchange: profile.stockExchange,
    }))
    if (props.indexSpot) list.push(props.indexSpot)
    return list
  }, [profile, props.indexSpot])

  const { data: quotes, isConnected } = useMarketData({ symbols: quoteSymbols, mode: 'Quote' })

  // ── 1m accumulators ──────────────────────────────────────────────────────
  const seriesRef = useRef(new Map<string, MinuteSeries>())
  const quotesRef = useRef(quotes)
  quotesRef.current = quotes

  useEffect(() => {
    const timer = window.setInterval(() => {
      const bucket = Math.floor(Date.now() / 60000)
      for (const row of profile.constituents) {
        const quote = quotesRef.current.get(`${profile.stockExchange}:${row.symbol}`)?.data
        let series = seriesRef.current.get(row.symbol)
        if (!series) {
          series = { points: [], volumes: [], lastBucket: bucket, firstLtp: 0, lastVolume: 0 }
          seriesRef.current.set(row.symbol, series)
        }
        const ltp = quote?.ltp ?? 0
        if (ltp > 0 && series.firstLtp === 0) series.firstLtp = ltp
        const volume = quote?.volume ?? 0
        if (bucket !== series.lastBucket) {
          // A new minute closed: snapshot % change and the volume delta.
          const reference = sessionReference(quote, series.firstLtp)
          series.points.push(reference ? ((ltp - reference) / reference) * 100 : 0)
          series.volumes.push(Math.max(0, volume - series.lastVolume))
          if (series.points.length > MAX_MINUTES) series.points.shift()
          if (series.volumes.length > MAX_MINUTES) series.volumes.shift()
          series.lastBucket = bucket
        }
        series.lastVolume = volume
      }
      setTick((value) => value + 1)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [profile])

  // ── Derived rows ─────────────────────────────────────────────────────────
  const pctBySymbol = useMemo(() => {
    // `tick` re-derives at minute boundaries even without fresh quotes.
    void tick
    const map = new Map<string, number>()
    for (const row of profile.constituents) {
      const quote = quotes.get(`${profile.stockExchange}:${row.symbol}`)?.data
      const series = seriesRef.current.get(row.symbol)
      map.set(row.symbol, sessionPct(quote, series?.firstLtp ?? 0))
    }
    return map
  }, [profile, quotes, tick])

  const ledger = useMemo(() => contributionLedger(profile, pctBySymbol), [profile, pctBySymbol])

  const rows: StockRow[] = useMemo(
    () =>
      profile.constituents.map((row) => {
        const quote = quotes.get(`${profile.stockExchange}:${row.symbol}`)?.data
        const series = seriesRef.current.get(row.symbol)
        return {
          symbol: row.symbol,
          weight: row.weight,
          ltp: quote?.ltp,
          pct: pctBySymbol.get(row.symbol) ?? 0,
          volume: quote?.volume,
          points: ledger.find((entry) => entry.symbol === row.symbol) ?? {
            symbol: row.symbol,
            pctChange: 0,
            points: 0,
            share: 0,
          },
          spark: series?.points ?? [],
          volumes: series?.volumes ?? [],
        }
      }),
    [profile, quotes, pctBySymbol, ledger]
  )

  const spotQuote = props.indexSpot
    ? quotes.get(`${props.indexSpot.exchange}:${props.indexSpot.symbol}`)?.data
    : undefined
  const spotPct = spotQuote ? sessionPct(spotQuote, 0) : weightedIndexMove(profile, pctBySymbol)
  const spotLtp = spotQuote?.ltp

  const byVolume = useMemo(
    () => [...rows].sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0)),
    [rows]
  )
  const colorOf = useCallback(
    (symbol: string) => {
      const index = byVolume.findIndex((row) => row.symbol === symbol)
      return STOCK_COLORS[(index < 0 ? 0 : index) % STOCK_COLORS.length]
    },
    [byVolume]
  )

  /** The rows in the table's current sort — a click on a header flips it. */
  const sortedRows = useMemo(() => {
    const value = (row: StockRow): number | string => {
      switch (sortKey) {
        case 'weight':
          return row.weight
        case 'symbol':
          return row.symbol
        case 'ltp':
          return row.ltp ?? 0
        case 'pct':
          return row.pct
        case 'volume':
          return row.volume ?? 0
        case 'contrib':
          return row.points.points
        case 'share':
          return row.points.share
      }
    }
    return [...rows].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      const cmp =
        typeof va === 'string' || typeof vb === 'string'
          ? String(va).localeCompare(String(vb))
          : (va as number) - (vb as number)
      return cmp * sortDir
    })
  }, [rows, sortKey, sortDir])

  const sortBy = (key: ConstituentSort) => {
    if (key === sortKey) setSortDir((dir) => (dir === 1 ? -1 : 1))
    else {
      setSortKey(key)
      // Numbers read best biggest-first; names, A→Z.
      setSortDir(key === 'symbol' ? 1 : -1)
    }
  }

  return (
    <div className="flex flex-col">
      <div className="flex items-stretch gap-2 border-b px-1.5 py-1">
        {/* Spot + combined 1m studies */}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold">{profile.label}</span>
            <span className="font-mono text-[11px] tabular-nums">
              {spotLtp ? fmt(spotLtp) : '—'}
            </span>
            <span
              className={cn(
                'font-mono text-[11px] tabular-nums',
                spotPct >= 0 ? 'text-emerald-500' : 'text-red-500'
              )}
              title={
                spotQuote ? 'Since session open' : 'Weighted estimate — spot quote unavailable'
              }
            >
              {fmtPct(spotPct)}
              {!spotQuote && ' est'}
            </span>
            <span className="text-[9px] text-muted-foreground" title="Live quote subscriptions">
              {isConnected ? (
                <RefreshCcw className="h-2.5 w-2.5" />
              ) : (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              )}
              <span className="ml-1">{profile.constituents.length} stocks</span>
            </span>
            <span className="ml-auto text-[9px] text-muted-foreground">
              weights indicative · 1m series builds while this tab is open
            </span>
          </div>
          <MinuteStudies rows={rows} colorOf={colorOf} />
        </div>

        {/* Contribution bubble pack */}
        <ContributionBubbles rows={rows} onPick={setDetail} />
      </div>

      {/* Constituents table — click a header to sort, click again to flip */}
      <div className="max-h-52 overflow-y-auto">
        <table className="w-full border-collapse text-[10px]">
          <thead className="sticky top-0 z-10 bg-background">
            <tr className="text-[9px] uppercase tracking-wide text-muted-foreground">
              {(
                [
                  ['symbol', 'Stock', 'left'],
                  ['ltp', 'LTP', 'right'],
                  ['pct', 'Chg%', 'right'],
                  [null, '1m %', 'left'],
                  ['volume', 'Volume', 'right'],
                  [null, 'Vol share', 'left'],
                  ['contrib', 'Contrib pt', 'right'],
                  ['share', 'Move%', 'right'],
                  ['weight', 'Wt%', 'left'],
                ] as const
              ).map(([key, label, align]) => (
                <th
                  key={label}
                  className={cn(
                    'cursor-pointer px-1 py-0.5 font-medium select-none hover:text-foreground',
                    align === 'right' ? 'text-right' : 'text-left'
                  )}
                  onClick={key ? () => sortBy(key) : undefined}
                  title={key ? `Sort by ${label}` : undefined}
                >
                  {label}
                  {key && sortKey === key && (
                    <span className="ml-0.5">{sortDir === 1 ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
              <th className="px-1 py-0.5" />
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row) => (
              <StockRowLine
                key={row.symbol}
                row={row}
                colorOf={colorOf}
                totalVolume={byVolume[0]?.volume ?? 0}
                onPick={() => setDetail(row)}
                onLoadChart={() =>
                  props.onLoadChart({ symbol: row.symbol, exchange: profile.stockExchange })
                }
              />
            ))}
          </tbody>
        </table>
      </div>

      <StockDetailDialog row={detail} profile={profile} onClose={() => setDetail(null)} />
    </div>
  )
}

/* ── Session reference / % helpers ───────────────────────────────────────── */

/**
 * The price the stock's session is measured from: the day open when the
 * feed carries it, else the first LTP this tab saw (documented fallback).
 */
function sessionReference(quote: SymbolData['data'] | undefined, firstLtp: number): number {
  if (quote?.open && quote.open > 0) return quote.open
  return firstLtp
}

function sessionPct(quote: SymbolData['data'] | undefined, firstLtp: number): number {
  const ltp = quote?.ltp ?? 0
  if (ltp <= 0) return 0
  const reference = sessionReference(quote, firstLtp)
  if (reference > 0) return ((ltp - reference) / reference) * 100
  // Last resort: the feed's own change%, referenced to the previous close.
  return quote?.change_percent ?? 0
}

/* ── One table row ───────────────────────────────────────────────────────── */

function StockRowLine(props: {
  row: StockRow
  colorOf(symbol: string): string
  totalVolume: number
  onPick(): void
  onLoadChart(): void
}) {
  const { row } = props
  const up = row.pct >= 0
  const volShare = props.totalVolume > 0 ? (row.volume ?? 0) / props.totalVolume : 0
  return (
    <tr className="border-t border-border/50 hover:bg-muted/40">
      <td className="px-1 py-0.5">
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 shrink-0 rounded-sm"
            style={{ backgroundColor: props.colorOf(row.symbol) }}
          />
          <button
            type="button"
            className="max-w-[110px] truncate font-mono font-medium hover:underline"
            onClick={props.onPick}
            title={`${row.symbol} — open details`}
          >
            {row.symbol}
          </button>
        </span>
      </td>
      <td className="px-1 py-0.5 text-right font-mono tabular-nums">
        {row.ltp != null ? fmt(row.ltp) : '—'}
      </td>
      <td
        className={cn(
          'px-1 py-0.5 text-right font-mono tabular-nums font-semibold',
          up ? 'text-emerald-500' : 'text-red-500'
        )}
      >
        {row.ltp != null ? fmtPct(row.pct) : '—'}
      </td>
      <td className="px-1 py-0.5">
        <Sparkline points={row.spark} width={84} height={16} />
      </td>
      <td className="px-1 py-0.5 text-right font-mono tabular-nums">
        {row.volume != null ? fmtVol(row.volume) : '—'}
      </td>
      <td className="px-1 py-0.5">
        <span className="block h-1.5 w-16 overflow-hidden rounded bg-muted">
          <span
            className="block h-full rounded"
            style={{
              width: `${Math.min(100, volShare * 100)}%`,
              backgroundColor: props.colorOf(row.symbol),
            }}
          />
        </span>
      </td>
      <td
        className={cn(
          'px-1 py-0.5 text-right font-mono tabular-nums',
          row.points.points > 0 && 'text-emerald-500',
          row.points.points < 0 && 'text-red-500'
        )}
        title="Weight × move — points of index % this stock explains"
      >
        {row.points.points >= 0 ? '+' : ''}
        {row.points.points.toFixed(2)}
      </td>
      <td className="px-1 py-0.5 text-right font-mono tabular-nums">
        {row.points.share >= 0 ? '+' : ''}
        {row.points.share.toFixed(0)}%
      </td>
      <td className="px-1 py-0.5 text-right font-mono tabular-nums text-muted-foreground">
        {row.weight.toFixed(1)}
      </td>
      <td className="px-1 py-0.5">
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={props.onLoadChart}
          title={`Load ${row.symbol} into the selected chart`}
        >
          <ChartLine className="h-3 w-3" />
        </button>
      </td>
    </tr>
  )
}

/* ── Sparkline: 1m % series, 0% = session open ──────────────────────────── */

export function Sparkline(props: { points: number[]; width: number; height: number }) {
  const { points, width, height } = props
  if (points.length === 0) {
    return (
      <svg width={width} height={height} aria-hidden>
        <line
          x1={1}
          y1={height / 2}
          x2={width - 1}
          y2={height / 2}
          stroke="currentColor"
          strokeDasharray="2 3"
          className="text-muted-foreground/40"
        />
      </svg>
    )
  }
  const series = points.length === 1 ? [points[0], points[0]] : points
  const min = Math.min(0, ...series)
  const max = Math.max(0, ...series)
  const span = max - min || 1
  const x = (index: number) => 2 + (index / (series.length - 1)) * (width - 4)
  const y = (value: number) => height - 2 - ((value - min) / span) * (height - 4)
  const path = series
    .map((value, index) => `${index === 0 ? 'M' : 'L'}${x(index)},${y(value)}`)
    .join(' ')
  const last = points[points.length - 1]
  return (
    <svg width={width} height={height} aria-hidden>
      <line
        x1={1}
        y1={y(0)}
        x2={width - 1}
        y2={y(0)}
        stroke="currentColor"
        strokeDasharray="2 3"
        className="text-muted-foreground/40"
      />
      <path
        d={path}
        fill="none"
        strokeWidth={1.25}
        className={last >= 0 ? 'stroke-emerald-500' : 'stroke-red-500'}
      />
      <circle
        cx={x(series.length - 1)}
        cy={y(last)}
        r={1.75}
        className={last >= 0 ? 'fill-emerald-500' : 'fill-red-500'}
      />
    </svg>
  )
}

/* ── Combined 1m studies: stacked volume + weighted price % ─────────────── */

function MinuteStudies(props: { rows: StockRow[]; colorOf(symbol: string): string }) {
  const { rows, colorOf } = props
  // The stacked volume frame: per minute, per stock volume deltas. Bucket
  // epochs come from the clock at build time — every stock pushes at the
  // same tick, so their series share one length and one clock.
  const frame = useMemo(() => {
    const lastBucket = Math.floor(Date.now() / 60000) - 1
    const stocks = [...rows].sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0))
    const length = Math.max(0, ...stocks.map((row) => row.volumes.length))
    const minutes: Array<{
      bucket: number
      total: number
      segments: Array<{ symbol: string; value: number; color: string }>
    }> = []
    for (let minute = 0; minute < length; minute++) {
      let total = 0
      const segments: Array<{ symbol: string; value: number; color: string }> = []
      for (const row of stocks) {
        const value = row.volumes[minute] ?? 0
        if (value <= 0) continue
        total += value
        segments.push({ symbol: row.symbol, value, color: colorOf(row.symbol) })
      }
      if (total > 0) {
        minutes.push({
          bucket: (lastBucket - (length - 1 - minute)) * 60000,
          total,
          segments,
        })
      }
    }
    return minutes
  }, [rows, colorOf])

  const maxMinute = Math.max(1, ...frame.map((minute) => minute.total))

  // The weighted price-% trail, one bar per minute bucket.
  const pctTrail = useMemo(() => {
    const length = Math.max(0, ...rows.map((row) => row.spark.length))
    const out: number[] = []
    for (let minute = 0; minute < length; minute++) {
      let move = 0
      for (const row of rows) {
        const value = row.spark[minute]
        if (value == null) continue
        move += (row.weight * value) / 100
      }
      out.push(move)
    }
    return out
  }, [rows])
  const maxAbsPct = Math.max(0.05, ...pctTrail.map((value) => Math.abs(value)))

  if (frame.length === 0 && pctTrail.length === 0) {
    return (
      <div className="flex h-14 items-center rounded border border-dashed px-2 text-[9px] text-muted-foreground">
        1m volume stack + price-% trail appear as the first minute closes…
      </div>
    )
  }

  return (
    <div className="flex h-14 gap-2">
      {/* Stacked per-minute volume, segments = stocks */}
      <div className="flex min-w-0 flex-1 flex-col rounded border px-1 pt-0.5">
        <span className="text-[8px] uppercase tracking-wide text-muted-foreground">
          Volume / min — stacked by stock (hover a segment)
        </span>
        <div className="flex min-h-0 flex-1 items-end gap-px">
          {frame.slice(-MAX_MINUTES).map((minute) => (
            <div
              key={minute.bucket}
              className="flex min-w-[3px] flex-1 flex-col-reverse overflow-hidden rounded-[1px]"
              style={{ height: `${(minute.total / maxMinute) * 100}%` }}
              title={`${minuteLabel(minute.bucket)} · ${fmtVol(minute.total)} traded`}
            >
              {minute.segments.map((segment) => (
                <div
                  key={segment.symbol}
                  style={{
                    height: `${(segment.value / minute.total) * 100}%`,
                    backgroundColor: segment.color,
                  }}
                  title={`${segment.symbol} · ${fmtVol(segment.value)} · ${minuteLabel(minute.bucket)}`}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
      {/* Weighted price-% per minute */}
      <div className="flex w-36 flex-col rounded border px-1 pt-0.5">
        <span className="text-[8px] uppercase tracking-wide text-muted-foreground">
          Index % / min
        </span>
        <div className="relative min-h-0 flex-1">
          <div className="absolute top-1/2 w-full border-t border-dashed border-muted-foreground/30" />
          <div className="flex h-full items-center gap-px">
            {pctTrail.slice(-MAX_MINUTES).map((value, index) => {
              const length = pctTrail.length
              const bucket = Math.floor(Date.now() / 60000) - 1 - (length - 1 - index)
              return (
                <div
                  key={index}
                  className={cn(
                    'min-w-[3px] flex-1 self-center rounded-[1px]',
                    value >= 0 ? 'bg-emerald-500/80' : 'bg-red-500/80'
                  )}
                  style={{ height: `${(Math.abs(value) / maxAbsPct) * 48}%` }}
                  title={`${minuteLabel(bucket)} · ${fmtPct(value)} weighted`}
                />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

function minuteLabel(bucket: number): string {
  // `bucket` is a minute-epoch (floor(Date.now()/60000) of that minute).
  const date = new Date(bucket * 60000)
  return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/* ── Contribution bubble pack ────────────────────────────────────────────── */

function ContributionBubbles(props: { rows: StockRow[]; onPick(row: StockRow): void }) {
  const W = 210
  const H = 132
  const layout = useMemo(() => {
    const live = props.rows.filter((row) => row.ltp != null)
    const maxShare = Math.max(0, ...live.map((row) => Math.abs(row.points.share)))
    if (maxShare <= 0) return { circles: [], maxShare }
    const maxR = 17
    const minR = 4
    const packed: Array<{ row: StockRow; cx: number; cy: number; r: number }> = []
    const ordered = [...live].sort((a, b) => Math.abs(b.points.share) - Math.abs(a.points.share))
    for (const row of ordered) {
      const r = minR + Math.sqrt(Math.abs(row.points.share) / maxShare) * (maxR - minR)
      // Golden-angle spiral out from the centre; first spot with no overlap wins.
      let placed = { cx: W / 2, cy: H / 2 }
      for (let step = 0; step < 260; step++) {
        const angle = step * 2.399963
        const radius = 4 + step * 1.9
        const candidate = {
          cx: W / 2 + Math.cos(angle) * radius,
          cy: H / 2 + Math.sin(angle) * radius * 0.72,
        }
        if (
          candidate.cx - r < 2 ||
          candidate.cx + r > W - 2 ||
          candidate.cy - r < 2 ||
          candidate.cy + r > H - 2
        )
          continue
        const collides = packed.some(
          (other) =>
            Math.hypot(other.cx - candidate.cx, other.cy - candidate.cy) < other.r + r + 1.5
        )
        if (!collides) {
          placed = candidate
          break
        }
      }
      packed.push({ row, ...placed, r })
    }
    return { circles: packed, maxShare }
  }, [props.rows])

  const total = props.rows.reduce((sum, row) => sum + row.points.points, 0)

  return (
    <div className="w-[218px] shrink-0 rounded border px-1 pt-0.5">
      <span className="text-[8px] uppercase tracking-wide text-muted-foreground">
        Who moves the index — click a circle
      </span>
      {layout.circles.length === 0 ? (
        <div className="flex h-[118px] items-center justify-center text-[9px] text-muted-foreground">
          Awaiting live quotes…
        </div>
      ) : (
        <svg
          width={W - 4}
          height={H - 14}
          className="mt-0.5"
          role="img"
          aria-label="Contribution bubbles"
        >
          {layout.circles.map(({ row, cx, cy, r }) => {
            const positive = row.points.points >= 0
            return (
              <g key={row.symbol} onClick={() => props.onPick(row)} className="cursor-pointer">
                <title>
                  {`${row.symbol} ${fmtPct(row.pct)} · ${row.points.points >= 0 ? '+' : ''}${row.points.points.toFixed(2)}pt · ${row.points.share >= 0 ? '+' : ''}${row.points.share.toFixed(0)}% of move`}
                </title>
                <circle
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill={positive ? '#10b981' : '#ef4444'}
                  fillOpacity={
                    0.25 + 0.55 * Math.min(1, Math.abs(row.points.share) / (layout.maxShare || 1))
                  }
                  stroke={positive ? '#059669' : '#dc2626'}
                  strokeWidth={1}
                />
                {r >= 9 && (
                  <text
                    x={cx}
                    y={cy + 2.5}
                    textAnchor="middle"
                    fontSize={7.5}
                    className="fill-foreground"
                    style={{ pointerEvents: 'none' }}
                  >
                    {row.symbol.slice(0, 8)}
                  </text>
                )}
              </g>
            )
          })}
          <text x={3} y={H - 18} fontSize={8} className="fill-muted-foreground">
            net move {total >= 0 ? '+' : ''}
            {total.toFixed(2)}pt · area = share
          </text>
        </svg>
      )}
    </div>
  )
}

/* ── Stock detail popup ──────────────────────────────────────────────────── */

function StockDetailDialog(props: {
  row: StockRow | null
  profile: IndexProfile
  onClose(): void
}) {
  const open = props.row != null
  const row = props.row
  return (
    <Dialog open={open} onOpenChange={(value) => !value && props.onClose()}>
      <DialogContent className="max-w-md p-4 text-xs">
        {row && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 font-mono text-sm">
                {row.symbol}
                <span className="text-[10px] font-normal text-muted-foreground">
                  {props.profile.label} · {props.profile.stockExchange}
                </span>
              </DialogTitle>
              <DialogDescription className="text-[10px]">
                Live quote, session trail and index contribution.
              </DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-3 gap-1.5">
              <DetailCell label="LTP" value={row.ltp != null ? fmt(row.ltp) : '—'} />
              <DetailCell
                label="Chg%"
                value={fmtPct(row.pct)}
                tone={row.pct >= 0 ? 'up' : 'down'}
              />
              <DetailCell label="Volume" value={row.volume != null ? fmtVol(row.volume) : '—'} />
              <DetailCell label="Index weight" value={`≈${row.weight.toFixed(2)}%`} />
              <DetailCell
                label="Contrib"
                value={`${row.points.points >= 0 ? '+' : ''}${row.points.points.toFixed(3)}pt`}
                tone={row.points.points >= 0 ? 'up' : 'down'}
              />
              <DetailCell
                label="Of move"
                value={`${row.points.share >= 0 ? '+' : ''}${row.points.share.toFixed(0)}%`}
                tone={row.points.share >= 0 ? 'up' : 'down'}
              />
            </div>
            <div className="mt-2 rounded border p-1">
              <span className="text-[9px] uppercase tracking-wide text-muted-foreground">
                % since session open · 1m closes (0% = open)
              </span>
              <Sparkline points={row.spark} width={400} height={72} />
            </div>
            <p className="mt-1 text-[9px] text-muted-foreground">
              Weights are indicative free-float figures; contribution = weight × stock move.
            </p>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function DetailCell(props: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="rounded border px-1.5 py-1">
      <span className="block text-[9px] text-muted-foreground">{props.label}</span>
      <span
        className={cn(
          'font-mono text-[11px] tabular-nums',
          props.tone === 'up' && 'text-emerald-500',
          props.tone === 'down' && 'text-red-500'
        )}
      >
        {props.value}
      </span>
    </div>
  )
}
