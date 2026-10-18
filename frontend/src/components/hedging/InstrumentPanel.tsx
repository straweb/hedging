import { useQuery } from '@tanstack/react-query'
import {
  ArrowDownWideNarrow,
  ChartLine,
  ChevronDown,
  CirclePlus,
  Loader2,
  MoreVertical,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { scalpingApi } from '@/api/scalping'
import { PercentField } from '@/components/hedging/PercentField'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { computeRowLots, oneLotPrice } from '@/lib/hedging/allocation'
import { cn } from '@/lib/utils'
import type {
  HedgingInstrument,
  Order,
  OrderSide,
  Position,
  RowFilter,
  SideMode,
  SideSummary,
  SortKey,
  StrikeSide,
} from '@/types/hedging'

/**
 * One side panel of the hedging terminal: CALLS on the left, PUTS on the
 * right in F&O mode; the active stock list on the right in equity mode.
 *
 * The header carries what a scalper glances at ("5/9 enabled, 2 positions,
 * ₹1,240") plus sort/filter. The add-strip grows strikes from ATM (±1–7, a
 * custom input) or searches stocks. Each row shows the live 1-lot price, the
 * lots its capital weight affords, its trade direction and levels, and —
 * when a position or pending order exists — exit/edit affordances instead
 * of the add-time ones.
 */

const fmt = (value: number, digits = 2) =>
  value.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })

const fmtShort = (value: number) => {
  const abs = Math.abs(value)
  if (abs >= 1e7) return `${(value / 1e7).toFixed(2)}Cr`
  if (abs >= 1e5) return `${(value / 1e5).toFixed(2)}L`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}k`
  return value.toFixed(0)
}

const OFFSET_BUTTONS = [1, 2, 3, 4, 5, 6, 7]

/**
 * The ATM offset of a strike in interval units — 0 for the ATM itself,
 * null when the chain snapshot has not landed yet.
 */
function atmOffset(row: HedgingInstrument, atm?: number, interval?: number): number | null {
  if (atm == null || !interval || row.strike == null) return null
  return Math.round((row.strike - atm) / interval)
}

/** Offset badge text: ATM, ATM+2, ATM−3. */
function offsetLabel(offset: number | null): string {
  if (offset == null) return ''
  if (offset === 0) return 'ATM'
  return offset > 0 ? `ATM+${offset}` : `ATM−${Math.abs(offset)}`
}

/**
 * The strike accent ladder, read from the option side and the offset:
 * - ATM is a solid amber on both panels — the anchor.
 * - CE above ATM and PE below ATM — the profit directions — are painted
 *   green, dark at the first strike and lighter each step further out.
 * - The opposite (ITM) sides keep a red fading the same way, so moneyness
 *   still reads at a glance.
 * Both ladders multiply the alpha by the offset depth: distance from ATM is
 * pure colour weight.
 */
export function strikeAccent(side: 'CE' | 'PE', offset: number | null): string | null {
  if (offset == null) return null
  if (offset === 0) return 'rgba(245, 158, 11, 0.95)'
  const depth = Math.abs(offset)
  // Integer math keeps the alpha string deterministic (no float drift).
  const alpha = Math.max(0.15, (92 - (depth - 1) * 16) / 100)
  const profit = side === 'CE' ? offset > 0 : offset < 0
  return profit ? `rgba(16, 185, 129, ${alpha})` : `rgba(239, 68, 68, ${alpha})`
}

export interface InstrumentPanelProps {
  kind: 'option' | 'stock'
  side: StrikeSide | 'EQ'
  title: string
  rows: HedgingInstrument[]
  summary: SideSummary
  sideCapital: number
  sideMode: SideMode
  getLtp(row: HedgingInstrument): number | undefined
  positionsBySymbol: Map<string, Position>
  pendingBySymbol: Map<string, Order>
  busyIds: Set<string>
  /** FNO: ATM strike + one-strike interval from the chain snapshot. */
  atmStrike?: number
  strikeInterval?: number
  exchange: string
  onAddStrikes?(strikes: number[]): void
  onAddStock?(pick: { symbol: string; exchange: string; lotsize: number }): void
  onToggleRow(id: string): void
  onRemoveRow(id: string): void
  onSlideWeight(id: string, value: number): void
  onFixedAmount(id: string, value: number): void
  onRowAction(id: string, action: OrderSide): void
  onFieldChange(id: string, field: 'entry' | 'target' | 'stoploss', value: number | null): void
  onExitRow(id: string): void
  onEditOrder(order: Order): void
  onLoadChart(row: HedgingInstrument): void
}

export function InstrumentPanel(props: InstrumentPanelProps) {
  const [sortKey, setSortKey] = useState<SortKey>('strike')
  const [filter, setFilter] = useState<RowFilter>('all')
  const [dir, setDir] = useState<1 | -1>(1)
  const [custom, setCustom] = useState('')
  const [addOpen, setAddOpen] = useState(false)

  const view = useMemo(() => {
    let rows = props.rows
    if (filter === 'enabled') rows = rows.filter((row) => row.enabled)
    if (filter === 'positions')
      rows = rows.filter((row) => {
        const position = props.positionsBySymbol.get(row.symbol)
        return (position?.quantity ?? 0) !== 0
      })
    const sorted = [...rows]
    sorted.sort((a, b) => {
      if (sortKey === 'strike') return ((a.strike ?? 0) - (b.strike ?? 0)) * dir
      if (sortKey === 'ltp') return ((props.getLtp(a) ?? 0) - (props.getLtp(b) ?? 0)) * dir
      return (
        ((props.positionsBySymbol.get(a.symbol)?.pnl ?? 0) -
          (props.positionsBySymbol.get(b.symbol)?.pnl ?? 0)) *
        dir
      )
    })
    return sorted
  }, [props.rows, props.positionsBySymbol, props.getLtp, sortKey, filter, dir])

  const isOption = props.kind === 'option'

  const addStrikes = (offsets: number[]) => {
    const base = props.atmStrike
    const interval = props.strikeInterval ?? 0
    if (!base || !interval) return
    props.onAddStrikes?.(offsets.map((offset) => base + offset * interval * dir))
  }

  const addCustom = () => {
    const strike = Number(custom)
    if (!Number.isFinite(strike) || strike <= 0) return
    if (isOption) props.onAddStrikes?.([strike])
    setCustom('')
    setAddOpen(false)
  }

  return (
    <div className="flex h-full flex-col overflow-hidden border bg-background">
      {/* ── Header: title + summary chips ─────────────────────────────────── */}
      <div className="flex h-7 shrink-0 items-center gap-1 border-b px-1.5">
        <span className="text-[11px] font-semibold tracking-wide">{props.title}</span>
        <Badge variant="secondary" className="px-1 py-0 text-[9px]" title="Enabled / total">
          {props.summary.enabled}/{props.summary.total}
        </Badge>
        {props.summary.positions > 0 && (
          <Badge className="px-1 py-0 text-[9px]" title="Open positions">
            {props.summary.positions} pos
          </Badge>
        )}
        <span
          className={cn(
            'ml-auto font-mono text-[10px] tabular-nums',
            props.summary.pnl > 0 && 'text-emerald-500',
            props.summary.pnl < 0 && 'text-red-500'
          )}
          title="Side P&L"
        >
          {props.summary.pnl >= 0 ? '+' : ''}
          {fmtShort(props.summary.pnl)}
        </span>
        {/* Sort + filter */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-5 w-5" title="Sort / filter">
              <ArrowDownWideNarrow className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="text-[11px]">
            {(['strike', 'ltp', 'pnl'] as SortKey[]).map((key) => (
              <DropdownMenuItem key={key} onSelect={() => setSortKey(key)}>
                Sort by {key}
                {sortKey === key && <ChevronDown className="ml-auto h-3 w-3" />}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            {(['all', 'enabled', 'positions'] as RowFilter[]).map((key) => (
              <DropdownMenuItem key={key} onSelect={() => setFilter(key)}>
                Show {key}
                {filter === key && <ChevronDown className="ml-auto h-3 w-3" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* ── Add strip ─────────────────────────────────────────────────────── */}
      <div className="shrink-0 border-b bg-muted/30 px-1.5 py-1">
        {isOption ? (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="outline"
                className="h-5 px-1.5 text-[10px]"
                onClick={() => props.atmStrike && addStrikes([0])}
                disabled={!props.atmStrike}
                title="Add ATM strike"
              >
                ATM
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-5 w-5 px-0 text-[10px] font-bold"
                onClick={() => setDir((value) => (value === 1 ? -1 : 1))}
                title={`Offsets go ${dir === 1 ? 'above' : 'below'} ATM`}
              >
                {dir === 1 ? '+' : '−'}
              </Button>
              {OFFSET_BUTTONS.map((offset) => (
                <Button
                  key={offset}
                  size="sm"
                  variant="outline"
                  className="h-5 min-w-5 px-1 text-[10px]"
                  onClick={() => addStrikes([offset])}
                  disabled={!props.atmStrike}
                  title={`Add ATM ${dir === 1 ? '+' : '−'}${offset}`}
                >
                  {offset}
                </Button>
              ))}
              <Popover open={addOpen} onOpenChange={setAddOpen}>
                <PopoverTrigger asChild>
                  <Button
                    size="sm"
                    variant="outline"
                    className="ml-auto h-5 px-1 text-[10px]"
                    title="Custom strike"
                  >
                    <CirclePlus className="h-3 w-3" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-40 p-2" align="end">
                  <Input
                    autoFocus
                    type="number"
                    value={custom}
                    onChange={(e) => setCustom(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addCustom()}
                    placeholder="Strike…"
                    className="h-7 text-xs"
                  />
                  <Button size="sm" className="mt-1 h-6 w-full text-[11px]" onClick={addCustom}>
                    Add strike
                  </Button>
                </PopoverContent>
              </Popover>
            </div>
          </div>
        ) : (
          <StockSearchAdd exchange={props.exchange} onPick={props.onAddStock} />
        )}
      </div>

      {/* ── Rows ──────────────────────────────────────────────────────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {view.length === 0 && (
          <p className="p-3 text-center text-[11px] text-muted-foreground">
            {isOption ? 'Add strikes from ATM above.' : 'Search a symbol above to add it.'}
          </p>
        )}
        {view.map((row) => (
          <StrikeRow key={row.id} row={row} {...props} />
        ))}
      </div>
    </div>
  )
}

/* ── One row ─────────────────────────────────────────────────────────────── */

function StrikeRow(props: InstrumentPanelProps & { row: HedgingInstrument }) {
  const { row } = props
  const [levelsOpen, setLevelsOpen] = useState(false)
  const ltp = props.getLtp(row)
  const lots = computeRowLots(row, ltp, props.sideCapital, props.sideMode)
  const position = props.positionsBySymbol.get(row.symbol)
  const hasPosition = (position?.quantity ?? 0) !== 0
  const pending = props.pendingBySymbol.get(row.symbol)
  const busy = props.busyIds.has(row.id)
  const isOption = props.kind === 'option'
  const offset = isOption ? atmOffset(row, props.atmStrike, props.strikeInterval) : null
  const targetHit =
    row.target != null &&
    ltp != null &&
    (row.action === 'BUY' ? ltp >= row.target : ltp <= row.target)
  const slHit =
    row.stoploss != null &&
    ltp != null &&
    (row.action === 'BUY' ? ltp <= row.stoploss : ltp >= row.stoploss)

  // The row's accent: the side-aware ATM ladder for strikes — green fading
  // outward on the profit side, red on the ITM side — and the trade direction
  // for stocks, muted to grey when the row is deselected. The accent paints
  // the panel's outer edge: CALLS on the left border, PUTS on the right, so
  // the two panels bracket the chart in colour.
  const accent = !row.enabled
    ? 'rgba(128, 128, 128, 0.25)'
    : isOption
      ? (strikeAccent(props.side as 'CE' | 'PE', offset) ?? 'rgba(128, 128, 128, 0.4)')
      : row.action === 'BUY'
        ? 'rgba(16, 185, 129, 0.85)'
        : 'rgba(239, 68, 68, 0.85)'
  const edge = isOption && props.side === 'PE' ? 'borderRight' : 'borderLeft'

  return (
    <div
      className={cn(
        'border-b py-1 pr-1.5 text-[11px] transition-colors last:border-0',
        !row.enabled && 'opacity-45',
        hasPosition && 'bg-primary/5',
        busy && 'animate-pulse'
      )}
      style={
        {
          [edge]: `3px solid ${accent}`,
          [`${edge}Width`]: 3,
        } as React.CSSProperties
      }
    >
      {/* Line 1: identity + ltp + actions */}
      <div className="flex items-center gap-1">
        <Checkbox
          checked={row.enabled}
          onCheckedChange={() => props.onToggleRow(row.id)}
          className="h-3 w-3"
          aria-label={`Enable ${row.symbol}`}
        />
        <button
          type="button"
          onClick={() => props.onLoadChart(row)}
          className="min-w-0 flex-1 truncate text-left font-mono font-medium hover:underline"
          title={`${row.symbol} — load into selected chart`}
        >
          {isOption ? row.strike : row.symbol}
        </button>
        {isOption && offsetLabel(offset) && (
          <span
            className="shrink-0 rounded px-1 text-[9px] font-semibold"
            style={{
              color: strikeAccent(props.side as 'CE' | 'PE', offset) ?? undefined,
              backgroundColor: 'rgba(128,128,128,0.08)',
            }}
            title={`Offset from ATM (${props.atmStrike}) in ${props.strikeInterval}-point steps`}
          >
            {offsetLabel(offset)}
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-4 w-4 shrink-0"
          onClick={() => props.onLoadChart(row)}
          title={`Load ${row.symbol} into the selected chart`}
        >
          <ChartLine className="h-3 w-3" />
        </Button>
        {targetHit && <Badge className="px-1 py-0 text-[9px] bg-emerald-600">TGT</Badge>}
        {slHit && <Badge className="px-1 py-0 text-[9px] bg-red-600">SL</Badge>}
        <span className="font-mono tabular-nums">{ltp != null ? fmt(ltp) : '—'}</span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-4 w-4" title="Row menu">
              <MoreVertical className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="text-[11px]">
            <DropdownMenuItem onSelect={() => props.onLoadChart(row)}>Load chart</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setLevelsOpen((v) => !v)}>
              {levelsOpen ? 'Hide' : 'Edit'} entry / target / SL
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => props.onRemoveRow(row.id)} className="text-red-500">
              <Trash2 className="h-3 w-3" /> Remove
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Line 2: 1-lot price, lots, allocation, B/S */}
      <div className="mt-0.5 flex items-center gap-1">
        <span className="w-14 text-[10px] text-muted-foreground" title="1 lot price">
          1L {fmtShort(oneLotPrice(ltp, row.lotSize))}
        </span>
        <span
          className={cn(
            'w-12 rounded px-1 text-center font-mono text-[10px]',
            lots.lots > 0 ? 'bg-muted text-foreground' : 'bg-destructive/10 text-destructive'
          )}
          title={`Quantity ${lots.quantity} · unallocated ₹${fmtShort(lots.unallocated)}`}
        >
          {lots.lots}L
        </span>
        {props.sideMode === 'percentage' ? (
          <span className="flex min-w-0 flex-1 items-center gap-1" title={`Weight ${row.weight}%`}>
            <input
              type="range"
              min={1}
              max={100}
              value={row.weight}
              onChange={(e) => props.onSlideWeight(row.id, Number(e.target.value))}
              className="h-1 min-w-0 flex-1 accent-primary"
              aria-label={`Weight for ${row.symbol}`}
            />
            <PercentField
              value={row.weight}
              onCommit={(value) => props.onSlideWeight(row.id, value)}
              label={`Weight for ${row.symbol}`}
              className="w-8 shrink-0"
            />
          </span>
        ) : (
          <Input
            type="number"
            value={row.fixedAmount}
            min={0}
            step={1000}
            onChange={(e) => props.onFixedAmount(row.id, Math.max(0, Number(e.target.value) || 0))}
            className="h-4 min-w-0 flex-1 px-1 py-0 text-right font-mono text-[10px]"
            aria-label={`Fixed amount for ${row.symbol}`}
          />
        )}
        {hasPosition ? (
          <>
            <span
              className={cn(
                'font-mono text-[10px] tabular-nums',
                (position?.pnl ?? 0) >= 0 ? 'text-emerald-500' : 'text-red-500'
              )}
            >
              {position && position.quantity > 0 ? 'L' : 'S'}
              {Math.abs(position?.quantity ?? 0) / row.lotSize}L{' '}
              {(position?.pnl ?? 0) >= 0 ? '+' : ''}
              {fmtShort(position?.pnl ?? 0)}
            </span>
            <Button
              size="sm"
              variant="destructive"
              className="h-4 px-1 text-[9px]"
              onClick={() => props.onExitRow(row.id)}
              disabled={busy}
            >
              {busy ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : 'Exit'}
            </Button>
          </>
        ) : pending ? (
          <Button
            size="sm"
            variant="outline"
            className="h-4 px-1 text-[9px]"
            onClick={() => props.onEditOrder(pending)}
            title={`${pending.action} ${pending.quantity} @ ${pending.pricetype} — edit order`}
          >
            Edit ord
          </Button>
        ) : (
          <span className="flex gap-0.5">
            <Button
              size="sm"
              className="h-4 bg-emerald-600 px-1.5 text-[9px] text-white hover:bg-emerald-700"
              onClick={() => props.onRowAction(row.id, 'BUY')}
              disabled={!row.enabled || busy || lots.lots === 0}
              title={`BUY ${lots.quantity}`}
            >
              B
            </Button>
            <Button
              size="sm"
              className="h-4 bg-red-600 px-1.5 text-[9px] text-white hover:bg-red-700"
              onClick={() => props.onRowAction(row.id, 'SELL')}
              disabled={!row.enabled || busy || lots.lots === 0}
              title={`SELL ${lots.quantity}`}
            >
              S
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-4 w-4"
              onClick={() => setLevelsOpen((v) => !v)}
              title="Entry / target / SL"
            >
              <Settings2 className="h-3 w-3" />
            </Button>
          </span>
        )}
      </div>

      {/* Line 3 (collapsible): levels editor */}
      {levelsOpen && (
        <div className="mt-1 grid grid-cols-4 items-center gap-1 rounded bg-muted/50 p-1">
          <LevelField
            label="Entry"
            value={row.entry}
            onCommit={(value) => props.onFieldChange(row.id, 'entry', value)}
          />
          <LevelField
            label="Target"
            value={row.target}
            onCommit={(value) => props.onFieldChange(row.id, 'target', value)}
          />
          <LevelField
            label="SL"
            value={row.stoploss}
            onCommit={(value) => props.onFieldChange(row.id, 'stoploss', value)}
          />
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <span className="truncate" title={`Action ${row.action}`}>
              {row.action === 'BUY' ? 'Long' : 'Short'}
            </span>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto h-4 px-1 text-[9px]"
              onClick={() => props.onRowAction(row.id, row.action === 'BUY' ? 'SELL' : 'BUY')}
              title="Flip trade direction"
            >
              Flip
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-4 w-4"
              onClick={() => setLevelsOpen(false)}
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function LevelField(props: {
  label: string
  value: number | null
  onCommit(value: number | null): void
}) {
  const [text, setText] = useState(props.value?.toString() ?? '')
  const commit = () => {
    const trimmed = text.trim()
    props.onCommit(trimmed === '' ? null : Number(trimmed) || null)
  }
  return (
    <label className="block">
      <span className="block text-[9px] text-muted-foreground">{props.label}</span>
      <Input
        type="number"
        value={text}
        placeholder="—"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className="h-5 px-1 py-0 font-mono text-[10px]"
      />
    </label>
  )
}

/* ── Equity add box ──────────────────────────────────────────────────────── */

function StockSearchAdd(props: {
  exchange: string
  onPick?(pick: { symbol: string; exchange: string; lotsize: number }): void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const search = useQuery({
    queryKey: ['hedging', 'stock-add', props.exchange, query],
    queryFn: () => scalpingApi.search(props.exchange, query),
    enabled: open && query.trim().length >= 2,
    staleTime: 60 * 1000,
  })
  const rows = search.data?.data?.slice(0, 12) ?? []

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <div className="relative">
          <CirclePlus className="absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onFocus={() => setOpen(true)}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Add stock (min 2 chars)…"
            className="h-6 pl-6 text-[11px]"
          />
        </div>
      </PopoverTrigger>
      <PopoverContent
        className="w-56 p-1"
        align="start"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        {rows.length === 0 && (
          <p className="p-2 text-[11px] text-muted-foreground">
            {query.trim().length < 2 ? 'Type to search…' : 'No match'}
          </p>
        )}
        {rows.map((row) => (
          <button
            key={`${row.exchange}:${row.symbol}`}
            type="button"
            className="flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-[11px] hover:bg-muted"
            onClick={() => {
              props.onPick?.({ symbol: row.symbol, exchange: row.exchange, lotsize: row.lotsize })
              setQuery('')
              setOpen(false)
            }}
          >
            <span className="truncate">{row.symbol}</span>
            <span className="text-[9px] text-muted-foreground">lot {row.lotsize}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  )
}
