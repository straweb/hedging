import { useQuery } from '@tanstack/react-query'
import { ChevronDown, LogOut, Menu as MenuIcon, Pin, RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
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
import type { CapitalBasis, CapitalSource, HedgingProduct, Mode, SideMode } from '@/types/hedging'

/**
 * The hedging terminal's compact top bar: instrument selection (exchange,
 * underlying, expiries), the equity/FNO switch, funds + the session's capital
 * basis, day P&L and one compact menu standing in for the full navbar.
 *
 * F&O underlyings start from a pinned list of the market's standard indices
 * and commodities — NIFTY, SENSEX, BANKNIFTY, CRUDEOIL, NATURALGAS, GOLD,
 * SILVER — so the terminal is usable the moment it opens, before (or without)
 * the exchange's full master contract listing. The full list for the chosen
 * exchange loads under the pinned names.
 *
 * Everything is sized for density — this bar is 40px tall and every pixel of
 * vertical space it saves goes to the charts.
 */

export interface TopBarFunds {
  availablecash: number
  utiliseddebits: number
  m2munrealized: number
}

const FNO_EXCHANGES = ['NFO', 'BFO', 'MCX', 'CDS']
const EQUITY_EXCHANGES = ['NSE', 'BSE']

/**
 * The market's standard F&O names, each with its home exchange. Picking one
 * moves the terminal to that exchange. They are always offered — the fetched
 * listing supplements them, never replaces them.
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

const fmtMoney = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 0 })

const fmtMoney2 = (value: number) =>
  value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export interface UnderlyingPick {
  symbol: string
  exchange: string
}

/** The pinned names that belong to one exchange. */
const defaultsFor = (exchange: string) =>
  DEFAULT_FNO_UNDERLYINGS.filter((pick) => pick.exchange === exchange)

const BASIS_LABEL: Record<CapitalBasis, string> = {
  initial: 'Initial',
  withPnl: '+ P&L',
  house: 'House',
}

/**
 * The products a mode may carry, in click-cycle order. F&O positions default
 * to NRML (carry them if the session runs long); cash equity scalping is
 * intraday, so MIS, with CNC for delivery swings.
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
  mode: Mode
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
  onModeChange(mode: Mode): void
  onExchangeChange(exchange: string): void
  /**
   * A pick from the picker rows ('user') always opens a fresh workspace;
   * the auto-pick that lands an exchange/mode switch on a valid underlying
   * ('autopick') adopts the pristine tab that switch just cleared.
   */
  onUnderlyingChange(pick: UnderlyingPick, source?: 'user' | 'autopick'): void
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
  const searchRef = useRef<HTMLDivElement | null>(null)

  const fno = props.mode === 'fno'
  const exchanges = fno ? FNO_EXCHANGES : EQUITY_EXCHANGES

  // ── Underlying candidates ────────────────────────────────────────────────
  // FNO: the pinned market defaults, always, plus every option underlying the
  // exchange lists (indices first) via the scalping blueprint. Equity: a live
  // search over the exchange's symbols.
  const underlyingsQuery = useQuery({
    queryKey: ['hedging', 'underlyings', props.exchange],
    queryFn: () => scalpingApi.getAllUnderlyings(props.exchange, 'options'),
    enabled: fno,
    staleTime: 60 * 60 * 1000,
    retry: 1,
  })
  const searchQuery = useQuery({
    queryKey: ['hedging', 'symbol-search', props.exchange, query],
    queryFn: () => scalpingApi.search(props.exchange, query),
    enabled: !fno && query.trim().length >= 2,
    staleTime: 60 * 1000,
  })

  const fetchedPicks: UnderlyingPick[] = useMemo(() => {
    const list = underlyingsQuery.data?.data ?? []
    return list.map((symbol) => ({ symbol, exchange: props.exchange }))
  }, [underlyingsQuery.data, props.exchange])

  const equityOptions: UnderlyingPick[] = useMemo(() => {
    const rows = searchQuery.data?.data ?? []
    return rows.slice(0, 30).map((row) => ({ symbol: row.symbol, exchange: row.exchange }))
  }, [searchQuery.data])

  // Auto-pick: an exchange (or mode) change lands on a valid underlying — the
  // current one if the listing knows it or a pinned default covers it, else
  // the exchange's first pinned default, else the listing's first.
  // biome-ignore lint/correctness/useExhaustiveDependencies: auto-pick runs only when the underlying list arrives
  useEffect(() => {
    if (!fno) return
    if (underlyingsQuery.isLoading) return
    if (props.underlying) {
      const pinned = defaultsFor(props.exchange).some((pick) => pick.symbol === props.underlying)
      const listed = (underlyingsQuery.data?.data ?? []).includes(props.underlying)
      if (pinned || listed) return
    }
    const fallback =
      defaultsFor(props.exchange)[0] ?? (underlyingsQuery.data?.data ?? [])[0] ?? null
    if (fallback) props.onUnderlyingChange(fallback, 'autopick')
  }, [underlyingsQuery.data, underlyingsQuery.isLoading, props.exchange])

  useEffect(() => {
    if (underlyingOpen) setQuery('')
  }, [underlyingOpen])

  const needle = query.trim().toUpperCase()
  const pinnedDefaults = fno ? DEFAULT_FNO_UNDERLYINGS : []
  const shownDefaults = useMemo(
    () => pinnedDefaults.filter((pick) => (needle ? pick.symbol.includes(needle) : true)),
    [pinnedDefaults, needle]
  )
  const shownFetched = useMemo(() => {
    const base = fetchedPicks.filter(
      (pick) => !shownDefaults.some((pinned) => pinned.symbol === pick.symbol)
    )
    return (needle ? base.filter((pick) => pick.symbol.includes(needle)) : base).slice(0, 60)
  }, [fetchedPicks, shownDefaults, needle])

  const shownPicks = fno ? shownFetched : equityOptions
  const listingEmpty = fno && !underlyingsQuery.isLoading && fetchedPicks.length === 0

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
      {/* Mode switch */}
      <div className="flex overflow-hidden rounded-md border">
        <button
          type="button"
          onClick={() => props.onModeChange('fno')}
          className={cn(
            'px-2 py-1 font-medium',
            fno ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
          )}
        >
          F&O
        </button>
        <button
          type="button"
          onClick={() => props.onModeChange('equity')}
          className={cn(
            'px-2 py-1 font-medium',
            !fno ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
          )}
        >
          Equity
        </button>
      </div>

      {/* Exchange */}
      <Select
        value={props.exchange}
        onValueChange={(value) => {
          props.onExchangeChange(value)
          setUnderlyingOpen(true)
        }}
      >
        <SelectTrigger className="h-7 w-[74px] px-2 text-xs" aria-label="Exchange">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {exchanges.map((exchange) => (
            <SelectItem key={exchange} value={exchange} className="text-xs">
              {exchange}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Underlying / symbol — a searchable picker, primed with the market's
          standard names so it answers before (or without) the full listing */}
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
          <div ref={searchRef}>
            <div className="relative">
              <Search className="absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={fno ? 'Filter underlyings…' : 'Search symbol (min 2 chars)…'}
                className="h-7 pl-7 text-xs"
              />
            </div>
            <div className="mt-1 max-h-64 overflow-y-auto">
              {fno && shownDefaults.length > 0 && (
                <>
                  <p className="px-2 pt-1 pb-0.5 text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
                    Market defaults
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
                  <p className="mt-1 px-2 pt-1 pb-0.5 text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
                    {props.exchange} listing
                  </p>
                </>
              )}
              {underlyingsQuery.isLoading && (
                <p className="p-2 text-[11px] text-muted-foreground">Loading listing…</p>
              )}
              {underlyingsQuery.isError && fno && (
                <button
                  type="button"
                  onClick={() => void underlyingsQuery.refetch()}
                  className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-[11px] text-amber-600 hover:bg-muted"
                >
                  <RefreshCw className="h-3 w-3" /> Listing failed — retry
                </button>
              )}
              {listingEmpty && (
                <p className="p-2 text-[10px] leading-4 text-muted-foreground">
                  The exchange listing is empty — the master contract may still be downloading. The
                  market defaults above stay available.
                </p>
              )}
              {!underlyingsQuery.isError &&
                shownPicks.map((pick) => (
                  <UnderlyingRow
                    key={`${pick.exchange}:${pick.symbol}`}
                    pick={pick}
                    active={pick.symbol === props.underlying}
                    onPick={(chosen) => {
                      props.onUnderlyingChange(chosen)
                      setUnderlyingOpen(false)
                    }}
                  />
                ))}
              {!fno && searchQuery.isError && (
                <button
                  type="button"
                  onClick={() => void searchQuery.refetch()}
                  className="flex w-full items-center gap-1 rounded px-2 py-1 text-left text-[11px] text-amber-600 hover:bg-muted"
                >
                  <RefreshCw className="h-3 w-3" /> Search failed — retry
                </button>
              )}
              {!fno && shownPicks.length === 0 && query.trim().length < 2 && (
                <p className="p-2 text-[11px] text-muted-foreground">Type at least 2 characters…</p>
              )}
              {!fno &&
                shownPicks.length === 0 &&
                query.trim().length >= 2 &&
                !searchQuery.isFetching &&
                !searchQuery.isError && (
                  <p className="p-2 text-[11px] text-muted-foreground">No match</p>
                )}
            </div>
          </div>
        </PopoverContent>
      </Popover>

      {/* Option expiry (auto = nearest) — the OPT caption stays under the
          dropdown so the pair reads at a glance even after a date lands in
          the trigger. Equity has no option chain, so the picker disappears
          with the mode. */}
      {fno && (
        <div className="flex flex-col items-center gap-0.5">
          <ExpirySelect
            label="Opt"
            underlying={props.underlying}
            exchange={props.exchange}
            value={props.expiry}
            instrumenttype="options"
            onChange={props.onExpiryChange}
          />
          <span className="text-[8px] leading-none font-semibold tracking-widest text-muted-foreground">
            OPT
          </span>
        </div>
      )}

      {/* Futures expiry (charts default to the SPOT index, front month one
          click away) with its FUT caption. */}
      {fno && (
        <div className="flex flex-col items-center gap-0.5">
          <ExpirySelect
            label="Spot"
            underlying={props.underlying}
            exchange={props.exchange}
            value={props.futuresExpiry}
            instrumenttype="futures"
            withSpot
            onChange={props.onFuturesExpiryChange}
          />
          <span className="text-[8px] leading-none font-semibold tracking-widest text-muted-foreground">
            FUT
          </span>
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
      {PRODUCTS_FOR(fno).length > 1 && (
        <Button
          variant="outline"
          className="h-7 px-2 text-[11px] font-semibold"
          onClick={() => {
            const list = PRODUCTS_FOR(fno)
            const next = list[(list.indexOf(props.product as HedgingProduct) + 1) % list.length]
            props.onProductChange(next)
          }}
          title={`Product ${props.product} — click for ${nextProduct(fno, props.product)}`}
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

/** One picker row: symbol + its exchange (a pinned default shows its pin). */
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
