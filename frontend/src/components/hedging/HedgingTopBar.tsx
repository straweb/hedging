import { useQuery } from '@tanstack/react-query'
import { ChevronDown, LogOut, Menu as MenuIcon, Pin, RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { authClient } from '@/api/client'
import { scalpingApi } from '@/api/scalping'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useProfileMenuItems } from '@/hooks/useProfileMenuItems'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import type { CapitalBasis, CapitalSource, HedgingProduct, SideMode } from '@/types/hedging'
import type { SearchInstrument } from '@/types/scalping'

/**
 * The hedging terminal's compact top bar: one underlying picker that finds
 * every exchange's symbols, the expiries the active workspace's market
 * carries, funds + the session's capital basis, day P&L and one compact menu
 * standing in for the full navbar.
 *
 * There is no F&O/Equity switch and no exchange dropdown here — what a
 * workspace trades is the workspace's own kind, decided by the pick (a
 * symbol with derivatives opens an F&O board, a plain stock a single chart).
 * The picker answers for every exchange at once: the market's standard
 * indexes pinned at the top, and a live search beneath them that fans out
 * across NSE/BSE/NFO/BFO/MCX/CDS in one go.
 *
 * Everything is sized for density — this bar is 40px tall and every pixel of
 * vertical space it saves goes to the charts.
 */

export interface TopBarFunds {
  availablecash: number
  utiliseddebits: number
  m2munrealized: number
}

/**
 * The market's standard F&O names, each with its home exchange. They are
 * always offered — the "Indexes" section of the picker — while the search
 * beneath finds everything else on every exchange.
 */
export const DEFAULT_FNO_UNDERLYINGS: UnderlyingPick[] = [
  { symbol: 'NIFTY', exchange: 'NFO' },
  { symbol: 'BANKNIFTY', exchange: 'NFO' },
  { symbol: 'SENSEX', exchange: 'BFO' },
  { symbol: 'CRUDEOIL', exchange: 'MCX' },
  { symbol: 'NATURALGAS', exchange: 'MCX' },
  { symbol: 'GOLD', exchange: 'MCX' },
  { symbol: 'SILVER', exchange: 'MCX' },
]

/** Every exchange the unified search asks — legs and cash, one fan-out. */
const SEARCH_EXCHANGES = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS']

const fmtMoney = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 0 })

const fmtMoney2 = (value: number) =>
  value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export interface UnderlyingPick {
  symbol: string
  exchange: string
}

const BASIS_LABEL: Record<CapitalBasis, string> = {
  initial: 'Initial',
  withPnl: '+ P&L',
  house: 'House',
}

/**
 * The products a market may carry, in click-cycle order. F&O positions
 * default to NRML (carry them if the session runs long); cash equity
 * scalping is intraday, so MIS, with CNC for delivery swings.
 */
const PRODUCTS_FOR = (fno: boolean): HedgingProduct[] => (fno ? ['NRML', 'MIS'] : ['MIS', 'CNC'])

const nextProduct = (fno: boolean, current: string): HedgingProduct => {
  const list = PRODUCTS_FOR(fno)
  return list[(list.indexOf(current as HedgingProduct) + 1) % list.length]
}

const BASIS_HINT: Record<CapitalBasis, string> = {
  initial:
    'Only this allocation trades this session. Realized losses shrink it; profit is never added back.',
  withPnl: 'Allocation plus session P&L — profit recycles into new trades at any time.',
  house:
    'First deployment spends the allocation; after that only the profit the session has realized can be deployed.',
}

interface Props {
  /** The active workspace's kind — F&O shows the expiry pair, both show product. */
  fno: boolean
  /** The active workspace's home exchange (the expiry APIs answer per exchange). */
  exchange: string
  underlying: string
  expiry: string
  futuresExpiry: string
  capital: number
  capitalSource: CapitalSource
  capitalPercent: number
  capitalBasis: CapitalBasis
  /** The session's usable capital under the active basis (parent-computed). */
  usable: number
  sideMode: SideMode
  product: string
  pnl: number
  funds: TopBarFunds | null
  /**
   * A pick from the picker rows — pinned index or a search hit from any
   * exchange — always opens a fresh workspace for that market.
   */
  onUnderlyingChange(pick: UnderlyingPick): void
  onExpiryChange(expiry: string): void
  onFuturesExpiryChange(expiry: string): void
  onCapitalChange(capital: number): void
  onCapitalSourceChange(source: CapitalSource): void
  onCapitalPercentChange(percent: number): void
  onCapitalBasisChange(basis: CapitalBasis): void
  onSideModeChange(mode: SideMode): void
  onProductChange(product: string): void
  onRefreshFunds(): void
}

export function HedgingTopBar(props: Props) {
  const navigate = useNavigate()
  const logout = useAuthStore((s) => s.logout)
  const profileItems = useProfileMenuItems()
  const [underlyingOpen, setUnderlyingOpen] = useState(false)
  const [query, setQuery] = useState('')

  // ── Unified symbol search: one needle, every exchange ────────────────────
  // The fan-out fires all six exchanges in parallel and merges the answers,
  // de-duplicated by EXCHANGE:SYMBOL (the same name listing on two boards is
  // two rows — RELIANCE on NSE and its derivatives on NFO trade differently).
  const searchQuery = useQuery({
    queryKey: ['hedging', 'symbol-search', query],
    queryFn: async (): Promise<SearchInstrument[]> => {
      const answers = await Promise.all(
        SEARCH_EXCHANGES.map((exchange) =>
          scalpingApi
            .search(exchange, query)
            .then((res) => res.data ?? [])
            .catch(() => [] as SearchInstrument[])
        )
      )
      const seen = new Set<string>()
      const merged: SearchInstrument[] = []
      for (const rows of answers) {
        for (const row of rows) {
          const key = `${row.exchange}:${row.symbol}`
          if (seen.has(key)) continue
          seen.add(key)
          merged.push(row)
        }
      }
      merged.sort(
        (a, b) => a.symbol.localeCompare(b.symbol) || a.exchange.localeCompare(b.exchange)
      )
      return merged.slice(0, 40)
    },
    enabled: query.trim().length >= 2,
    staleTime: 60 * 1000,
  })

  useEffect(() => {
    if (underlyingOpen) setQuery('')
  }, [underlyingOpen])

  const needle = query.trim().toUpperCase()
  const shownDefaults = useMemo(
    () => DEFAULT_FNO_UNDERLYINGS.filter((pick) => (needle ? pick.symbol.includes(needle) : true)),
    [needle]
  )
  const searching = query.trim().length >= 2
  const hits = searchQuery.data ?? []

  const doLogout = async () => {
    try {
      await authClient.post('/auth/logout')
    } catch {
      // The server session may already be gone; clear locally regardless.
    }
    logout()
    navigate('/login')
  }

  const pnlTone =
    props.pnl > 0 ? 'text-emerald-500' : props.pnl < 0 ? 'text-red-500' : 'text-muted-foreground'

  return (
    <div className="flex h-10 shrink-0 items-center gap-1.5 border-b bg-background px-2 text-xs">
      {/* Underlying / symbol — a searchable picker across every exchange,
          primed with the market's standard indexes so it answers before (or
          without) the search */}
      <Popover open={underlyingOpen} onOpenChange={setUnderlyingOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            className="h-7 max-w-[150px] justify-between gap-1 px-2 font-mono text-xs"
            title={props.underlying}
          >
            <span className="truncate">{props.underlying || 'Select…'}</span>
            <ChevronDown className="h-3 w-3 shrink-0 opacity-60" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="w-64 p-2"
          align="start"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div>
            <div className="relative">
              <Search className="absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search any exchange (min 2 chars)…"
                className="h-7 pl-7 text-xs"
              />
            </div>
            <div className="mt-1 max-h-64 overflow-y-auto">
              {shownDefaults.length > 0 && (
                <>
                  <p className="px-2 pt-1 pb-0.5 text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
                    Indexes
                  </p>
                  {shownDefaults.map((pick) => (
                    <UnderlyingRow
                      key={`pin-${pick.exchange}:${pick.symbol}`}
                      pick={pick}
                      active={pick.symbol === props.underlying}
                      pinned
                      onPick={(chosen) => {
                        props.onUnderlyingChange(chosen)
                        setUnderlyingOpen(false)
                      }}
                    />
                  ))}
                </>
              )}
              {searching && (
                <p className="mt-1 px-2 pt-1 pb-0.5 text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
                  Symbols
                </p>
              )}
              {searching && searchQuery.isFetching && hits.length === 0 && (
                <p className="p-2 text-[11px] text-muted-foreground">Searching every exchange…</p>
              )}
              {searching && searchQuery.isError && (
                <button
                  type="button"
                  onClick={() => void searchQuery.refetch()}
                  className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-[11px] text-amber-600 hover:bg-muted"
                >
                  <RefreshCw className="h-3 w-3" /> Search failed — retry
                </button>
              )}
              {searching &&
                !searchQuery.isError &&
                hits.map((row) => (
                  <UnderlyingRow
                    key={`${row.exchange}:${row.symbol}`}
                    pick={{ symbol: row.symbol, exchange: row.exchange }}
                    active={row.symbol === props.underlying}
                    onPick={(chosen) => {
                      props.onUnderlyingChange(chosen)
                      setUnderlyingOpen(false)
                    }}
                  />
                ))}
              {searching &&
                !searchQuery.isFetching &&
                !searchQuery.isError &&
                hits.length === 0 && (
                  <p className="p-2 text-[11px] text-muted-foreground">No match</p>
                )}
              {!searching && (
                <p className="p-2 text-[10px] leading-4 text-muted-foreground">
                  Type to search every exchange. An index or an F&O name opens the options board; a
                  plain stock opens its own chart.
                </p>
              )}
            </div>
          </div>
        </PopoverContent>
      </Popover>

      {/* Option expiry — the OPT label sits BEFORE the dropdown, on its left,
          so the pair reads as one control. Only an F&O workspace carries an
          option chain. */}
      {props.fno && (
        <div className="flex items-center gap-1">
          <span className="text-[8px] leading-none font-semibold tracking-widest text-muted-foreground">
            OPT
          </span>
          <ExpirySelect
            label="Opt"
            underlying={props.underlying}
            exchange={props.exchange}
            value={props.expiry}
            instrumenttype="options"
            onChange={props.onExpiryChange}
          />
        </div>
      )}

      {/* Futures expiry — same label-before shape, FUT; the charts default to
          the SPOT index and every futures month sits one click away. */}
      {props.fno && (
        <div className="flex items-center gap-1">
          <span className="text-[8px] leading-none font-semibold tracking-widest text-muted-foreground">
            FUT
          </span>
          <ExpirySelect
            label="Spot"
            underlying={props.underlying}
            exchange={props.exchange}
            value={props.futuresExpiry}
            instrumenttype="futures"
            withSpot
            onChange={props.onFuturesExpiryChange}
          />
        </div>
      )}

      {/* Side allocation + product: direct buttons, not a menu — the two
          settings a scalper flips between entries sit on the bar itself. */}
      <div className="flex overflow-hidden rounded-md border" title="Side allocation mode">
        <button
          type="button"
          onClick={() => props.onSideModeChange('percentage')}
          className={cn(
            'px-1.5 py-1 text-[11px] font-semibold',
            props.sideMode === 'percentage'
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-muted'
          )}
          title="Split the side capital by each strike's % weight"
        >
          %
        </button>
        <button
          type="button"
          onClick={() => props.onSideModeChange('fixed')}
          className={cn(
            'px-1.5 py-1 text-[11px] font-semibold',
            props.sideMode === 'fixed'
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-muted'
          )}
          title="Fixed ₹ per strike"
        >
          ₹
        </button>
      </div>
      {PRODUCTS_FOR(props.fno).length > 1 && (
        <Button
          variant="outline"
          className="h-7 px-2 text-[11px] font-semibold"
          onClick={() => {
            const list = PRODUCTS_FOR(props.fno)
            const next = list[(list.indexOf(props.product as HedgingProduct) + 1) % list.length]
            props.onProductChange(next)
          }}
          title={`Product ${props.product} — click for ${nextProduct(props.fno, props.product)}`}
        >
          {props.product}
        </Button>
      )}

      <div className="flex-1" />

      {/* Funds + capital + P&L */}
      {props.funds && (
        <div className="hidden items-center gap-3 text-[11px] text-muted-foreground md:flex">
          <span title="Available cash">
            Cash{' '}
            <span className="font-mono text-foreground">{fmtMoney(props.funds.availablecash)}</span>
          </span>
          <span title="Utilised">
            Used{' '}
            <span className="font-mono text-foreground">
              {fmtMoney(props.funds.utiliseddebits)}
            </span>
          </span>
        </div>
      )}
      <CapitalPopover
        capital={props.capital}
        capitalSource={props.capitalSource}
        capitalPercent={props.capitalPercent}
        capitalBasis={props.capitalBasis}
        usable={props.usable}
        cash={props.funds?.availablecash ?? 0}
        onCapitalChange={props.onCapitalChange}
        onCapitalSourceChange={props.onCapitalSourceChange}
        onCapitalPercentChange={props.onCapitalPercentChange}
        onCapitalBasisChange={props.onCapitalBasisChange}
      />

      <span
        className={cn('font-mono text-xs font-semibold tabular-nums', pnlTone)}
        title="Day P&L (positions)"
      >
        {props.pnl >= 0 ? '+' : ''}
        {fmtMoney2(props.pnl)}
      </span>

      {props.funds && (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={props.onRefreshFunds}
          title="Refresh funds"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      )}

      {/* Compact menu: the OpenAlgo menu list without the top navbar */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="h-7 w-7" title="Menu">
            <MenuIcon className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="max-h-[70vh] overflow-y-auto text-xs">
          {profileItems.map((item) => (
            <DropdownMenuItem key={item.href} asChild>
              <Link to={item.href} className="flex items-center gap-2">
                <item.icon className="h-3.5 w-3.5" />
                {item.label}
              </Link>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void doLogout()} className="text-red-500">
            <LogOut className="h-3.5 w-3.5" />
            Logout
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

/** One picker row: symbol + its exchange (a pinned index shows its pin). */
function UnderlyingRow(props: {
  pick: UnderlyingPick
  active: boolean
  pinned?: boolean
  onPick(pick: UnderlyingPick): void
}) {
  return (
    <button
      type="button"
      onClick={() => props.onPick(props.pick)}
      className={cn(
        'flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs hover:bg-muted',
        props.active && 'bg-muted font-semibold'
      )}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        {props.pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-primary" />}
        <span className="truncate">{props.pick.symbol}</span>
      </span>
      <span className="text-[10px] text-muted-foreground">{props.pick.exchange}</span>
    </button>
  )
}

/** The capital picker: source (₹/% of cash) and the session's usable basis. */
function CapitalPopover(props: {
  capital: number
  capitalSource: CapitalSource
  capitalPercent: number
  capitalBasis: CapitalBasis
  usable: number
  cash: number
  onCapitalChange(capital: number): void
  onCapitalSourceChange(source: CapitalSource): void
  onCapitalPercentChange(percent: number): void
  onCapitalBasisChange(basis: CapitalBasis): void
}) {
  const overCash = props.capitalSource === 'fixed' && props.cash > 0 && props.capital > props.cash

  const commitFixed = (value: number) => {
    // A fixed allocation cannot exceed the cash the account actually holds.
    const clamped = Math.max(0, Math.min(value, props.cash > 0 ? props.cash : value))
    props.onCapitalChange(clamped)
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="h-7 gap-1 px-2 text-xs" title="Session capital">
          Cap <span className="font-mono">{fmtMoney(props.usable)}</span>
          <Badge variant="secondary" className="px-1 py-0 text-[9px]">
            {BASIS_LABEL[props.capitalBasis]}
          </Badge>
          {props.capitalSource === 'percent' && (
            <Badge variant="secondary" className="px-1 py-0 text-[9px]">
              {props.capitalPercent}%
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-3 text-xs" align="end">
        <p className="mb-2 font-medium">Session capital</p>

        {/* Basis: the house-money ladder */}
        <div className="mb-1 flex gap-1">
          {(Object.keys(BASIS_LABEL) as CapitalBasis[]).map((basis) => (
            <Button
              key={basis}
              size="sm"
              variant={props.capitalBasis === basis ? 'default' : 'outline'}
              className="h-6 flex-1 px-1 text-[10px]"
              onClick={() => props.onCapitalBasisChange(basis)}
            >
              {BASIS_LABEL[basis]}
            </Button>
          ))}
        </div>
        <p className="mb-2 text-[10px] leading-4 text-muted-foreground">
          {BASIS_HINT[props.capitalBasis]}
        </p>

        {/* Source: fixed rupees or a share of cash */}
        <div className="mb-2 flex gap-1">
          <Button
            size="sm"
            variant={props.capitalSource === 'fixed' ? 'default' : 'outline'}
            className="h-6 flex-1 text-[11px]"
            onClick={() => props.onCapitalSourceChange('fixed')}
          >
            Fixed ₹
          </Button>
          <Button
            size="sm"
            variant={props.capitalSource === 'percent' ? 'default' : 'outline'}
            className="h-6 flex-1 text-[11px]"
            onClick={() => props.onCapitalSourceChange('percent')}
          >
            % of cash
          </Button>
        </div>
        {props.capitalSource === 'fixed' ? (
          <div>
            <Input
              type="number"
              value={props.capital}
              min={1000}
              step={1000}
              max={props.cash > 0 ? props.cash : undefined}
              onChange={(e) => commitFixed(Number(e.target.value) || 0)}
              className="h-7 text-xs"
              aria-label="Fixed capital"
            />
            <p className="mt-1 text-[10px] text-muted-foreground">
              {props.cash > 0
                ? `Up to cash ₹${fmtMoney(props.cash)}`
                : 'Cash unknown until funds load'}
            </p>
            {overCash && (
              <p className="mt-0.5 text-[10px] text-amber-600">
                Above available cash — deploy uses the clamped amount.
              </p>
            )}
          </div>
        ) : (
          <label className="block">
            <span className="text-[11px] text-muted-foreground">
              {props.capitalPercent}% of available cash
              {props.cash > 0 ? ` = ${fmtMoney((props.cash * props.capitalPercent) / 100)}` : ''}
            </span>
            <input
              type="range"
              min={1}
              max={100}
              value={props.capitalPercent}
              onChange={(e) => props.onCapitalPercentChange(Number(e.target.value))}
              className="mt-1 w-full accent-primary"
            />
          </label>
        )}

        <p className="mt-2 border-t pt-2 text-[11px]">
          Usable <span className="font-mono font-semibold">₹{fmtMoney(props.usable)}</span>
        </p>
        <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
          Strikes split the usable capital by their % weights (CE/PE share lives on the quick
          actions bar).
        </p>
      </PopoverContent>
    </Popover>
  )
}

/**
 * Expiry dropdown fed by the scalping blueprint; defaults to the nearest and
 * retries on failure (a master contract still downloading answers empty, so
 * the failure state is visible and one click re-asks).
 *
 * `withSpot` (the futures picker) pins a Spot row at the top and makes it the
 * default: an index terminal opens on the index itself, with every futures
 * expiry one click below it.
 */
function ExpirySelect(props: {
  label: string
  underlying: string
  exchange: string
  value: string
  instrumenttype: 'options' | 'futures'
  withSpot?: boolean
  onChange(expiry: string): void
}) {
  const query = useQuery({
    queryKey: ['hedging', 'expiry', props.underlying, props.exchange, props.instrumenttype],
    queryFn: () => scalpingApi.getExpiry(props.underlying, props.exchange, props.instrumenttype),
    enabled: !!props.underlying,
    staleTime: 5 * 60 * 1000,
    retry: 1,
  })
  const expiries = useMemo(() => query.data?.data ?? [], [query.data])
  // Spot sits above every futures expiry and is picked by default; '' (an
  // untouched picker) means Spot too, so the charts seed the index itself.
  const SPOT = 'SPOT'
  const choices = useMemo(
    () => (props.withSpot ? [SPOT, ...expiries] : expiries),
    [props.withSpot, expiries]
  )
  const [open, setOpen] = useState(false)

  // Nearest expiry by default (Spot first for the futures picker): the API
  // returns them sorted, so index 0 is the front month. The pick re-runs
  // whenever the tab's own expiry changes too — a fresh workspace starts
  // with none, and a choices-only watch would leave it empty whenever this
  // underlying's list is already cached. A stale date (another underlying's)
  // snaps to the front month.
  // biome-ignore lint/correctness/useExhaustiveDependencies: auto-pick runs when the list lands or the tab's expiry changes
  useEffect(() => {
    if (choices.length === 0) return
    if (props.value && choices.includes(props.value)) return
    props.onChange(props.withSpot && !props.value ? SPOT : choices[0])
  }, [choices, props.value])

  return (
    <Select
      value={props.value || (props.withSpot ? SPOT : undefined)}
      onValueChange={props.onChange}
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        // Re-ask on open after a failure: the listing may have landed by now.
        if (next && query.isError) void query.refetch()
      }}
    >
      <SelectTrigger
        className={cn(
          'h-6 w-[108px] px-2 font-mono text-[11px]',
          query.isError && 'text-amber-600'
        )}
        title={
          query.isError
            ? 'Expiry listing failed — open to retry'
            : props.instrumenttype === 'futures'
              ? 'Futures expiry — Spot charts the index itself'
              : 'Option expiry'
        }
      >
        <SelectValue placeholder={query.isError ? `${props.label} ✕` : props.label} />
      </SelectTrigger>
      <SelectContent className="max-h-72">
        {choices.map((expiry) => (
          <SelectItem
            key={expiry}
            value={expiry}
            className={cn('font-mono text-xs', expiry === SPOT && 'font-semibold')}
          >
            {expiry === SPOT ? 'Spot (index)' : expiry}
          </SelectItem>
        ))}
        {choices.length === 0 && (
          <SelectItem value="—" disabled className="text-xs">
            {query.isError ? 'Failed — close & reopen to retry' : 'No expiry'}
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  )
}
