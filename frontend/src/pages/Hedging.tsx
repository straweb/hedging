import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, KeyRound } from 'lucide-react'
import { createLinkGroup } from 'openalgo-charts'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { hedgingApi } from '@/api/hedging'
import { scalpingApi } from '@/api/scalping'
import { tradingApi } from '@/api/trading'
import { HedgingDock, type QuickActionKey } from '@/components/hedging/HedgingDock'
import { HedgingTopBar, type UnderlyingPick } from '@/components/hedging/HedgingTopBar'
import {
  HedgingWorkspace,
  type WorkspaceAutoSeed,
  type WorkspaceFocusRequest,
} from '@/components/hedging/HedgingWorkspace'
import { IndexDeployDialog, type IndexDeploySubmit } from '@/components/hedging/IndexDeployDialog'
import { InstrumentPanel } from '@/components/hedging/InstrumentPanel'
import {
  SchedulerDialog,
  type SchedulerSubmit,
  scheduledFromSubmission,
} from '@/components/hedging/SchedulerDialog'
import { WatchlistMini } from '@/components/hedging/WatchlistMini'
import type { PanePriceLine, PaneQuickTrade } from '@/components/trading/ChartPane'
import type { DockOrder } from '@/components/trading/dock/blotter'
import { Button } from '@/components/ui/button'
import { useMarketData } from '@/hooks/useMarketData'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import {
  slideWeight as applySlide,
  computeRowLots,
  normalizeWeights,
  seedWeightFor,
  sideCapitals,
  sideOf,
} from '@/lib/hedging/allocation'
import { clampedBase, usableCapital } from '@/lib/hedging/capital'
import { levelCrosses, strikesForPick } from '@/lib/hedging/indexLevels'
import {
  istSeconds,
  newScheduleId,
  parseClock,
  scheduleActionLabel,
  isDue as scheduleIsDue,
  scheduleTriggerLabel,
} from '@/lib/hedging/scheduler'
import {
  loadHedgingState,
  makeOptionRow,
  makeStockRow,
  newTabId,
  saveHedgingState,
} from '@/lib/hedging/storage'
import { pickKind, planUnderlyingTabs } from '@/lib/hedging/underlyingTab'
import type {
  HedgingInstrument,
  HedgingState,
  HedgingWorkspaceTab,
  MultilegLeg,
  MultilegLegResult,
  Order,
  OrderSide,
  Position,
  ScheduledOrder,
  StrikeSide,
  TabKind,
  Trade,
} from '@/types/hedging'
import { emptyTabContext, FNO_EXCHANGES, fnoExchangeFor } from '@/types/hedging'
import { showToast } from '@/utils/toast'

/**
 * The /hedging terminal — a compact three-column trading surface:
 *
 *   F&O:    CALL strikes | workspace tabs of charts | PUT strikes
 *   Equity: watchlist    | workspace tabs of charts | active stocks
 *
 * with the position/trade/scheduled books and the deploy quick actions docked
 * at the bottom. All order fan-outs go through POST /api/v1/multileg, which
 * honours the global Analyzer toggle (sandbox fills in analyze mode), and all
 * setup — strike sets, weights, capital, scheduled orders, workspace tabs —
 * persists to localStorage.
 *
 * Every workspace tab owns its market AND its species: picking an underlying
 * from the top bar asks whether the symbol carries F&O derivatives — an F&O
 * board opens with the spot + legs layout and the CE/PE panels, a plain
 * stock opens a single chart with the stocks panel. There is no global
 * switch; the active tab's kind drives the panels, the quick actions and
 * the scheduler's vocabulary. Picking an underlying from the top bar opens
 * a fresh tab for it, and switching tabs moves the side panels, the quick
 * actions and the scheduler's batches onto that tab's context, so two
 * markets can be worked without overwriting each other.
 *
 * Capital follows the house-money ladder: the session trades the allocated
 * amount only (initial), the amount plus P&L (withPnl), or the amount once
 * and then realized profit alone (house). The scheduler arms any quick-action
 * batch on a clock time (IST, daily or once, to the second) or on an LTP
 * level (a line, or the band between two — entered or escaped); when the
 * trigger is met the batch fires through the same multileg path, with the
 * same busy guards as a manual press.
 */

const SIDE_WIDTH = 280

type QuickSide = 'A' | 'B'

export default function Hedging() {
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [wsUrl, setWsUrl] = useState<string | null>(null)
  const [noKey, setNoKey] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [keyRes, cfgRes] = await Promise.all([
          fetch('/api/websocket/apikey').then((r) => r.json()),
          fetch('/api/websocket/config').then((r) => r.json()),
        ])
        if (!alive) return
        if (keyRes.status !== 'success') {
          setNoKey(true)
          return
        }
        setApiKey(keyRes.api_key)
        setWsUrl(cfgRes.websocket_url || 'ws://127.0.0.1:8765')
      } catch {
        if (alive) setNoKey(true)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  if (noKey) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        <KeyRound className="h-8 w-8" />
        <p>The hedging terminal needs a broker login and an API key.</p>
        <Button asChild variant="outline" size="sm">
          <Link to="/dashboard">Back to dashboard</Link>
        </Button>
      </div>
    )
  }

  if (!apiKey || !wsUrl) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        Loading terminal…
      </div>
    )
  }

  return <HedgingTerminal apiKey={apiKey} wsUrl={wsUrl} />
}

function HedgingTerminal({ apiKey, wsUrl }: { apiKey: string; wsUrl: string }) {
  const queryClient = useQueryClient()
  const [state, setState] = useState<HedgingState>(() => loadHedgingState())
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [dockCollapsed, setDockCollapsed] = useState(false)
  const [focusRequest, setFocusRequest] = useState<WorkspaceFocusRequest | null>(null)
  const [rowBusy, setRowBusy] = useState<Set<string>>(new Set())
  const [quickBusy, setQuickBusy] = useState<QuickActionKey>({
    deployA: false,
    deployB: false,
    closeA: false,
    closeB: false,
    swap: false,
    exitAll: false,
  })
  const [linkGroup, setLinkGroup] = useState<ReturnType<typeof createLinkGroup> | null>(null)
  const [editingOrder, setEditingOrder] = useState<Order | null>(null)
  const lastDeployed = useRef<QuickSide>('A')
  const stamp = useRef(0)
  const autoSeedStamp = useRef(0)
  /** Set once a deployment ran this session — the house basis reads it. */
  const deployedOnce = useRef(false)
  /** The tab waiting for its chain snapshot to auto-seed the panes. */
  const pendingAutoSeed = useRef<{ tabId: string; underlying: string } | null>(null)
  /** Scheduler rows whose batch is firing right now (double-fire guard). */
  const firingSchedules = useRef(new Set<string>())
  const widerChain = useRef(new Map<string, Awaited<ReturnType<typeof scalpingApi.getStrikes>>>())
  const [schedulerOpen, setSchedulerOpen] = useState(false)
  const [editingScheduled, setEditingScheduled] = useState<ScheduledOrder | null>(null)
  const [scheduledBusy, setScheduledBusy] = useState<Set<string>>(new Set())
  const [indexDeployOpen, setIndexDeployOpen] = useState(false)

  useEffect(() => {
    const group = createLinkGroup()
    setLinkGroup(group)
    return () => group.destroy()
  }, [])

  // Persist every setup change (strike sets, weights, capital, tabs).
  useEffect(() => {
    saveHedgingState(state)
  }, [state])

  const patch = useCallback((changes: Partial<HedgingState>) => {
    setState((previous) => ({ ...previous, ...changes }))
  }, [])

  // ── The active tab IS the instrument context ─────────────────────────────
  // Each workspace tab carries its own KIND (F&O market or plain stock),
  // underlying, expiries and strike/stock rows. Everything below reads this
  // context; the top bar edits it; picking an underlying from the top bar
  // opens a fresh tab for that market with the kind the pick resolves to.
  const ctx = useMemo(
    () => state.tabs.find((tab) => tab.id === state.activeTabId) ?? state.tabs[0],
    [state.tabs, state.activeTabId]
  )
  const fno = ctx.kind === 'fno'
  /** Mutate the active tab's market context (underlying, expiries, rows). */
  const patchCtx = useCallback((changes: Partial<HedgingWorkspaceTab>) => {
    setState((previous) => ({
      ...previous,
      tabs: previous.tabs.map((tab) =>
        tab.id === previous.activeTabId ? { ...tab, ...changes } : tab
      ),
    }))
  }, [])

  // The product follows the active tab's species — NRML carries F&O
  // positions, cash-equity scalping is intraday (MIS). A tab switch that
  // lands on the other species re-applies the pairing; a manual product
  // pick inside a tab survives switches within the same kind.
  const productKindRef = useRef<TabKind | null>(null)
  useEffect(() => {
    if (productKindRef.current === null) {
      productKindRef.current = ctx.kind
      return
    }
    if (productKindRef.current === ctx.kind) return
    productKindRef.current = ctx.kind
    patch({ product: ctx.kind === 'fno' ? 'NRML' : 'MIS' })
  }, [ctx.kind, patch])

  const chainQuery = useQuery({
    queryKey: ['hedging', 'strikes', ctx.underlying, ctx.exchange, ctx.expiry],
    queryFn: () => scalpingApi.getStrikes(ctx.underlying, ctx.exchange, ctx.expiry, 10),
    enabled: fno && !!ctx.underlying && !!ctx.expiry,
    staleTime: 60 * 1000,
    retry: false,
  })
  const chain = fno ? chainQuery.data : undefined
  const strikeInterval = useMemo(() => {
    const strikes = (chain?.chain ?? []).map((row) => row.strike).sort((a, b) => a - b)
    for (let i = 1; i < strikes.length; i++) {
      const diff = strikes[i] - strikes[i - 1]
      if (diff > 0) return diff
    }
    return 0
  }, [chain])

  // Futures contract for chart seeding (FNO): the selected futures expiry —
  // or none at all when the picker sits on Spot, the default, in which case
  // the charts draw the index itself from the chain snapshot.
  const futuresQuery = useQuery({
    queryKey: ['hedging', 'futures', ctx.underlying, ctx.exchange],
    queryFn: () => scalpingApi.futures(ctx.underlying, ctx.exchange),
    enabled: fno && !!ctx.underlying,
    staleTime: 60 * 60 * 1000,
  })
  const futuresExpiries = futuresQuery.data?.data ?? []
  const chartFuture = useMemo(() => {
    if (!fno) return null
    if (!ctx.futuresExpiry || ctx.futuresExpiry === 'SPOT') return null
    return futuresExpiries.find((contract) => contract.expiry === ctx.futuresExpiry) ?? null
  }, [fno, futuresExpiries, ctx.futuresExpiry])

  /** The instrument "trade the stock" means in F&O mode: the futures
   * contract when one lists, else the cash symbol on its home exchange. */
  const stockInstrument = useMemo(() => {
    if (!fno || !ctx.underlying) return null
    const contract = chartFuture ?? futuresExpiries[0] ?? null
    if (contract)
      return { symbol: contract.symbol, exchange: ctx.exchange, lotSize: contract.lotsize || 1 }
    const cash = ctx.exchange === 'NFO' ? 'NSE' : ctx.exchange === 'BFO' ? 'BSE' : ctx.exchange
    return { symbol: ctx.underlying, exchange: cash, lotSize: 1 }
  }, [fno, ctx.underlying, ctx.exchange, chartFuture, futuresExpiries])

  // ── Books + funds ────────────────────────────────────────────────────────
  const positionsQuery = useQuery({
    queryKey: ['hedging', 'positions', apiKey],
    queryFn: () => tradingApi.getPositions(apiKey),
    enabled: !!apiKey,
    refetchInterval: 5000,
  })
  const ordersQuery = useQuery({
    queryKey: ['hedging', 'orders', apiKey],
    queryFn: () => tradingApi.getOrders(apiKey),
    enabled: !!apiKey,
    refetchInterval: 8000,
  })
  const tradesQuery = useQuery({
    queryKey: ['hedging', 'trades', apiKey],
    queryFn: () => tradingApi.getTrades(apiKey),
    enabled: !!apiKey,
    refetchInterval: 10000,
  })
  const fundsQuery = useQuery({
    queryKey: ['hedging', 'funds', apiKey],
    queryFn: () => tradingApi.getFunds(apiKey),
    enabled: !!apiKey,
    staleTime: 30 * 1000,
  })

  const positions: Position[] = positionsQuery.data?.data ?? []
  const orders: Order[] = ordersQuery.data?.data?.orders ?? []
  const trades: Trade[] = tradesQuery.data?.data ?? []
  const funds = fundsQuery.data?.data ?? null

  const refreshBooks = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['hedging', 'positions'] })
    void queryClient.invalidateQueries({ queryKey: ['hedging', 'orders'] })
    void queryClient.invalidateQueries({ queryKey: ['hedging', 'trades'] })
    void queryClient.invalidateQueries({ queryKey: ['hedging', 'funds'] })
  }, [queryClient])

  // Event-driven refresh on top of the gentle poll: order events land here
  // within a beat of Deploy/Exit, so the books never look stale.
  useOrderEventRefresh(refreshBooks, { events: ['order_event', 'analyzer_update'] })

  const positionsBySymbol = useMemo(() => {
    const map = new Map<string, Position>()
    for (const position of positions) {
      if (position.quantity !== 0) map.set(position.symbol, position)
    }
    return map
  }, [positions])
  const pendingBySymbol = useMemo(() => {
    const map = new Map<string, Order>()
    for (const order of orders) {
      if (['open', 'pending', 'trigger pending'].includes(order.order_status.toLowerCase()))
        map.set(order.symbol, order)
    }
    return map
  }, [orders])

  // ── Quote subscriptions: everything the panels display ──────────────────
  const watchlistsQuery = useQuery({
    queryKey: ['hedging', 'watchlists'],
    queryFn: () => import('@/api/watchlist').then((module_) => module_.watchlistApi.list()),
    enabled: !fno,
    staleTime: 60 * 1000,
  })
  const watchlistSymbols = useMemo(() => {
    const lists = watchlistsQuery.data ?? []
    const active = lists[0]
    return (active?.items ?? []).map((item) => ({ symbol: item.symbol, exchange: item.exchange }))
  }, [watchlistsQuery.data])

  const quoteSymbols = useMemo(() => {
    const list: Array<{ symbol: string; exchange: string }> = []
    if (ctx.kind === 'fno') {
      if (chartFuture) list.push({ symbol: chartFuture.symbol, exchange: ctx.exchange })
      if (stockInstrument) list.push(stockInstrument)
      // The spot index feeds the Index tab and price triggers armed on it.
      if (chain?.underlying_symbol)
        list.push({
          symbol: chain.underlying_symbol,
          exchange: chain.underlying_exchange ?? ctx.exchange,
        })
    } else if (ctx.underlying) {
      list.push({ symbol: ctx.underlying, exchange: ctx.exchange })
    }
    for (const row of ctx.options) list.push({ symbol: row.symbol, exchange: row.exchange })
    for (const row of ctx.stocks) list.push({ symbol: row.symbol, exchange: row.exchange })
    for (const item of watchlistSymbols) list.push(item)
    const unique = new Map<string, { symbol: string; exchange: string }>()
    for (const item of list) unique.set(`${item.exchange}:${item.symbol}`, item)
    return [...unique.values()]
  }, [
    ctx.kind,
    chartFuture,
    stockInstrument,
    chain,
    ctx.exchange,
    ctx.underlying,
    ctx.options,
    ctx.stocks,
    watchlistSymbols,
  ])

  // Quote mode, not bare LTP: the rows' session low/high (the range rail on
  // every strike and stock) rides the same feed, and the OHLC fields come
  // with the tick — one subscription per symbol either way.
  const { data: marketData } = useMarketData({ symbols: quoteSymbols, mode: 'Quote' })
  // Live quotes read through this ref by the 1s cadences (the scheduler tick
  // and the LTP tape) — stable regardless of how fast the feed re-renders.
  const marketDataRef = useRef(marketData)
  marketDataRef.current = marketData
  const getLtp = useCallback(
    (symbol: string, exchange: string) => marketData.get(`${exchange}:${symbol}`)?.data.ltp,
    [marketData]
  )
  const getRowLtp = useCallback(
    (row: HedgingInstrument) => getLtp(row.symbol, row.exchange),
    [getLtp]
  )
  /** The session's low/high marks for a row's quote (the range rail). */
  const getSessionRange = useCallback(
    (row: HedgingInstrument) => {
      const data = marketData.get(`${row.exchange}:${row.symbol}`)?.data
      return { low: data?.low, high: data?.high }
    },
    [marketData]
  )

  // ── LTP tape: one second, one point per subscribed symbol ──────────────
  // Open-position rows draw their live tape against the fill's entry and
  // the row's monitoring levels; the buffer is a ref the interval mutates,
  // so the 1s cadence never re-renders anything by itself (rows re-render
  // on the quote ticks that matter anyway). ~3 minutes of tape at 1s.
  const ltpHistory = useRef(new Map<string, number[]>())
  const quoteSymbolsRef = useRef(quoteSymbols)
  quoteSymbolsRef.current = quoteSymbols
  useEffect(() => {
    const timer = window.setInterval(() => {
      for (const item of quoteSymbolsRef.current) {
        const key = `${item.exchange}:${item.symbol}`
        const ltp = marketDataRef.current.get(key)?.data.ltp
        if (ltp == null || !Number.isFinite(ltp)) continue
        const tape = ltpHistory.current.get(key)
        if (tape) {
          tape.push(ltp)
          if (tape.length > 180) tape.shift()
        } else {
          ltpHistory.current.set(key, [ltp])
        }
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])
  const getLtpHistory = useCallback((row: HedgingInstrument) => {
    return ltpHistory.current.get(`${row.exchange}:${row.symbol}`) ?? []
  }, [])

  // ── Derived capital + summaries ─────────────────────────────────────────
  // The allocation: a fixed amount (clamped to cash — an allocation larger
  // than the account cannot exist) or a share of it.
  const cash = funds?.availablecash ?? 0
  const baseCapital =
    state.capitalSource === 'percent'
      ? (cash * state.capitalPercent) / 100
      : clampedBase(state.capital, cash)

  const dayPnl = positions.reduce((sum, position) => sum + position.pnl, 0)
  // Realized session P&L: the net result of everything already closed (the
  // books' flattened rows). Unrealized marks cannot fund a deployment.
  const realizedPnl = positions
    .filter((position) => position.quantity === 0)
    .reduce((sum, position) => sum + position.pnl, 0)
  // Unrealized: the live marks on whatever is still open — the dock strip
  // pairs the two so the running P&L reads at a glance.
  const unrealizedPnl = positions
    .filter((position) => position.quantity !== 0)
    .reduce((sum, position) => sum + position.pnl, 0)

  // The house-money ladder turns the allocation into what this session may
  // still deploy, per the chosen basis.
  const capital = usableCapital(state.capitalBasis, {
    base: baseCapital,
    cash,
    dayPnl,
    realizedPnl,
    deployedOnce: deployedOnce.current,
  })
  const capitals = sideCapitals(capital, state.ceShare)

  const summarize = useCallback(
    (side: StrikeSide | 'EQ') => {
      const rows = side === 'EQ' ? ctx.stocks : ctx.options.filter((row) => row.optionType === side)
      const enabled = rows.filter((row) => row.enabled)
      let positionsCount = 0
      let pnl = 0
      for (const row of rows) {
        const position = positionsBySymbol.get(row.symbol)
        if (position && position.quantity !== 0) {
          positionsCount += 1
          pnl += position.pnl
        }
      }
      return { total: rows.length, enabled: enabled.length, positions: positionsCount, pnl }
    },
    [ctx.options, ctx.stocks, positionsBySymbol]
  )

  // ── Row mutations (on the active tab's rows) ────────────────────────────
  const toggleRow = (id: string) => {
    const list = fno ? ctx.options : ctx.stocks
    const updated = list.map((row) => (row.id === id ? { ...row, enabled: !row.enabled } : row))
    patchCtx(fno ? { options: normalizeWeights(updated) } : { stocks: normalizeWeights(updated) })
  }
  const removeRow = (id: string) => {
    const list = fno ? ctx.options : ctx.stocks
    const updated = list.filter((row) => row.id !== id)
    patchCtx(fno ? { options: normalizeWeights(updated) } : { stocks: normalizeWeights(updated) })
  }
  const slideWeight = (id: string, value: number) => {
    const list = fno ? ctx.options : ctx.stocks
    const updated = applySlide(list, id, value)
    patchCtx(fno ? { options: updated } : { stocks: updated })
  }
  const setFixedAmount = (id: string, value: number) => {
    const list = fno ? ctx.options : ctx.stocks
    const updated = list.map((row) => (row.id === id ? { ...row, fixedAmount: value } : row))
    patchCtx(fno ? { options: updated } : { stocks: updated })
  }
  const setField = (id: string, field: 'entry' | 'target' | 'stoploss', value: number | null) => {
    const list = fno ? ctx.options : ctx.stocks
    const updated = list.map((row) => (row.id === id ? { ...row, [field]: value } : row))
    patchCtx(fno ? { options: updated } : { stocks: updated })
  }

  // ── Adding strikes (chain-resolved) and stocks ──────────────────────────
  /** Add chain-resolved strikes to the active tab; `action` pre-sets the row
   * direction (the index-level deploy chooses Buy/Sell itself). Returns the
   * rows that now exist for the requested strikes (previously present ones
   * included, enabled) so callers can deploy exactly what they picked. */
  const addStrikes = useCallback(
    async (
      side: StrikeSide,
      strikes: number[],
      action?: OrderSide
    ): Promise<HedgingInstrument[]> => {
      if (strikes.length === 0) return []
      const existing = new Set(ctx.options.map((row) => row.id))
      const fresh: HedgingInstrument[] = []

      const resolve = async (strike: number) => {
        // From the current snapshot first, then a wider fetch (custom strikes
        // beyond the ±10 window) with a per-underlying cache.
        const find = (snapshot: typeof chain) =>
          snapshot?.chain.find((row) => row.strike === strike)?.[side.toLowerCase() as 'ce' | 'pe']
        let leg = find(chain)
        if (!leg?.symbol) {
          const maxDistance = Math.max(
            ...strikes.map((wanted) =>
              chain?.atm_strike ? Math.abs(wanted - chain.atm_strike) : 0
            ),
            1
          )
          const count = Math.min(50, maxDistance + 5)
          const key = `${ctx.underlying}|${ctx.exchange}|${ctx.expiry}|${count}`
          let snapshot = widerChain.current.get(key)
          if (!snapshot) {
            snapshot = await scalpingApi.getStrikes(ctx.underlying, ctx.exchange, ctx.expiry, count)
            widerChain.current.set(key, snapshot)
          }
          leg = find(snapshot)
        }
        if (!leg?.symbol) return null
        return makeOptionRow({
          symbol: leg.symbol,
          exchange: chain?.fo_exchange ?? ctx.exchange,
          strike,
          optionType: side,
          lotSize: leg.lotsize ?? 1,
          // A new strike starts at half the existing strikes' weight — the
          // merge's normalize then lands it at roughly half an existing
          // strike's share instead of halving everyone else.
          weight: seedWeightFor(ctx.options, side),
        })
      }

      for (const wanted of strikes) {
        const row = await resolve(wanted)
        if (row && !existing.has(row.id)) {
          existing.add(row.id)
          if (action) row.action = action
          fresh.push(row)
        }
      }
      if (fresh.length === 0) {
        if (!action) showToast.warning('Those strikes are already on the panel or not listed.')
        return ctx.options.filter(
          (row) => row.optionType === side && strikes.includes(row.strike ?? Number.NaN)
        )
      }
      const merged = normalizeWeights([...ctx.options, ...fresh])
      patchCtx({ options: merged })
      return merged.filter(
        (row) => row.optionType === side && strikes.includes(row.strike ?? Number.NaN)
      )
    },
    [chain, ctx.options, ctx.underlying, ctx.exchange, ctx.expiry, patchCtx]
  )

  const addStock = useCallback(
    (pick: { symbol: string; exchange: string; lotsize: number }) => {
      const row = makeStockRow({
        symbol: pick.symbol,
        exchange: pick.exchange,
        lotSize: pick.lotsize || 1,
        weight: seedWeightFor(ctx.stocks, 'EQ'),
      })
      if (ctx.stocks.some((existing) => existing.id === row.id)) {
        showToast.info(`${pick.symbol} is already in the active list.`)
        return
      }
      patchCtx({ stocks: normalizeWeights([...ctx.stocks, row]) })
    },
    [ctx.stocks, patchCtx]
  )

  // ── Order composition + actions ─────────────────────────────────────────
  const rowsForSide = useCallback(
    (side: QuickSide): HedgingInstrument[] => {
      if (!fno) return ctx.stocks
      const optionSide: StrikeSide = side === 'A' ? 'CE' : 'PE'
      return ctx.options.filter((row) => row.optionType === optionSide)
    },
    [fno, ctx.stocks, ctx.options]
  )

  const sideForQuick = useCallback(
    (side: QuickSide): StrikeSide | 'EQ' => (fno ? (side === 'A' ? 'CE' : 'PE') : 'EQ'),
    [fno]
  )

  const legFromRow = useCallback(
    (
      row: HedgingInstrument,
      action: OrderSide,
      quantity: number,
      overridePricetype?: 'MARKET' | 'LIMIT',
      price?: number
    ): MultilegLeg => ({
      legid: row.id,
      symbol: row.symbol,
      exchange: row.exchange,
      action,
      quantity,
      pricetype: overridePricetype ?? (row.entry != null ? 'LIMIT' : state.pricetype),
      product: state.product,
      ...(overridePricetype === 'LIMIT' || row.entry != null
        ? { price: price ?? row.entry ?? 0 }
        : {}),
    }),
    [state.pricetype, state.product]
  )

  const markBusy = useCallback((id: string, busy: boolean) => {
    setRowBusy((previous) => {
      const next = new Set(previous)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  const fireLegs = useCallback(
    async (legs: MultilegLeg[], label: string): Promise<number> => {
      if (legs.length === 0) {
        showToast.warning(`${label}: nothing to place — check enabled strikes and lots.`)
        return 0
      }
      try {
        const response = await hedgingApi.placeMultileg(apiKey, legs)
        const results = response.results ?? []
        const failures = results.filter((result: MultilegLegResult) => result.status !== 'success')
        const modeTag = response.mode === 'analyze' ? ' (analyze)' : ''
        if (failures.length === 0) {
          showToast.success(`${label}: ${results.length}/${legs.length} legs placed${modeTag}`)
        } else {
          showToast.warning(
            `${label}: ${results.length - failures.length}/${legs.length} placed${modeTag} — ${failures
              .map((result) => result.symbol)
              .slice(0, 3)
              .join(', ')}${failures.length > 3 ? '…' : ''} failed`
          )
        }
        return results.length - failures.length
      } catch (error) {
        showToast.error(
          `${label} failed: ${error instanceof Error ? error.message : 'request error'}`
        )
        return 0
      } finally {
        refreshBooks()
      }
    },
    [apiKey, refreshBooks]
  )

  /** Enabled rows of a side with their live lot computation. */
  const deployRows = useCallback(
    (side: QuickSide) => {
      const sideCapital = capitals[sideForQuick(side)]
      return rowsForSide(side)
        .filter((row) => row.enabled)
        .map((row) => ({
          row,
          lots: computeRowLots(row, getRowLtp(row), sideCapital, state.sideMode),
        }))
    },
    [capitals, rowsForSide, getRowLtp, state.sideMode, sideForQuick]
  )

  const deploy = useCallback(
    async (side: QuickSide) => {
      setQuickBusy((previous) => ({ ...previous, [side === 'A' ? 'deployA' : 'deployB']: true }))
      try {
        const label = fno
          ? `Deploy ${side === 'A' ? 'CE' : 'PE'}`
          : `Deploy ${side === 'A' ? 'Buy' : 'Sell'}`
        // F&O deploys each strike in its own configured direction (a short
        // strike sells); equity Deploy Buy/Sell forces the whole batch.
        const forcedAction: OrderSide | null = fno ? null : side === 'A' ? 'BUY' : 'SELL'
        const legs = deployRows(side)
          .filter(({ lots }) => lots.quantity > 0)
          .map(({ row, lots }) => legFromRow(row, forcedAction ?? row.action, lots.quantity))
        const placed = await fireLegs(legs, label)
        if (placed > 0) deployedOnce.current = true
        lastDeployed.current = side
      } finally {
        setQuickBusy((previous) => ({ ...previous, [side === 'A' ? 'deployA' : 'deployB']: false }))
      }
    },
    [fno, deployRows, legFromRow, fireLegs]
  )

  /** Close legs for the open positions that belong to `side`.
   * F&O: membership by strike panel. Equity: A closes longs, B closes shorts. */
  const closeLegsForSide = useCallback(
    (side: QuickSide): MultilegLeg[] => {
      const symbols = fno ? new Set(rowsForSide(side).map((row) => row.symbol)) : null
      return positions
        .filter((position) => {
          if (position.quantity === 0) return false
          if (fno) return symbols?.has(position.symbol) ?? false
          return side === 'A' ? position.quantity > 0 : position.quantity < 0
        })
        .map((position) => ({
          legid: `close:${position.symbol}`,
          symbol: position.symbol,
          exchange: position.exchange,
          action: (position.quantity > 0 ? 'SELL' : 'BUY') as OrderSide,
          quantity: Math.abs(position.quantity),
          pricetype: 'MARKET' as const,
          product: position.product as 'MIS' | 'NRML' | 'CNC',
        }))
    },
    [fno, rowsForSide, positions]
  )

  const closeSide = useCallback(
    async (side: QuickSide) => {
      setQuickBusy((previous) => ({ ...previous, [side === 'A' ? 'closeA' : 'closeB']: true }))
      try {
        const label = fno
          ? `Close ${side === 'A' ? 'CE' : 'PE'}`
          : `Close ${side === 'A' ? 'Buy' : 'Sell'}`
        await fireLegs(closeLegsForSide(side), label)
      } finally {
        setQuickBusy((previous) => ({ ...previous, [side === 'A' ? 'closeA' : 'closeB']: false }))
      }
    },
    [fno, closeLegsForSide, fireLegs]
  )

  /** Close the side holding positions and deploy the opposite side in one batch. */
  const swap = useCallback(async () => {
    setQuickBusy((previous) => ({ ...previous, swap: true }))
    try {
      const hasA = closeLegsForSide('A').length > 0
      const hasB = closeLegsForSide('B').length > 0
      const from: QuickSide = hasA && !hasB ? 'A' : hasB && !hasA ? 'B' : lastDeployed.current
      const to: QuickSide = from === 'A' ? 'B' : 'A'
      const label = fno
        ? `Swap ${from === 'A' ? 'CE' : 'PE'} → ${to === 'A' ? 'CE' : 'PE'}`
        : `Swap ${from === 'A' ? 'Buy' : 'Sell'} → ${to === 'A' ? 'Buy' : 'Sell'}`
      const forcedAction: OrderSide | null = fno ? null : to === 'A' ? 'BUY' : 'SELL'
      const legs = [...closeLegsForSide(from)]
      for (const { row, lots } of deployRows(to)) {
        if (lots.quantity > 0) legs.push(legFromRow(row, forcedAction ?? row.action, lots.quantity))
      }
      const placed = await fireLegs(legs, label)
      if (placed > 0) deployedOnce.current = true
      lastDeployed.current = to
    } finally {
      setQuickBusy((previous) => ({ ...previous, swap: false }))
    }
  }, [closeLegsForSide, deployRows, legFromRow, fireLegs, fno])

  const exitAll = useCallback(async () => {
    setQuickBusy((previous) => ({ ...previous, exitAll: true }))
    try {
      const legs = positions
        .filter((position) => position.quantity !== 0)
        .map((position) => ({
          legid: `exit:${position.symbol}`,
          symbol: position.symbol,
          exchange: position.exchange,
          action: (position.quantity > 0 ? 'SELL' : 'BUY') as OrderSide,
          quantity: Math.abs(position.quantity),
          pricetype: 'MARKET' as const,
          product: position.product as 'MIS' | 'NRML' | 'CNC',
        }))
      await fireLegs(legs, 'Exit All')
    } finally {
      setQuickBusy((previous) => ({ ...previous, exitAll: false }))
    }
  }, [positions, fireLegs])

  // ── Scheduler: time / price-triggered batches ───────────────────────────
  // One tick a second asks every pending row whether its trigger is met. A
  // due row fires the same batch a manual press would — deploy or close a
  // side, or exit all — through the multileg endpoint, with the same busy
  // guards. Time triggers run on the IST wall clock (to the second); daily
  // entries re-arm for the next day, once entries retire as done. Price
  // triggers watch the live feed against a level or a band and fire once.
  //
  // `target` picks the book: the option panels (CE/PE vocabulary) or the
  // underlying itself (Buy/Sell — the F&O/Equity choice for F&O stocks).
  // A row carrying a `deploy` plan (from the index-level dialog) places
  // exactly its planned strikes, split evenly, and arms its target/stop on
  // the spot index once filled.

  /** Deploy/Close Buy/Sell on the underlying itself (scheduler stock target). */
  const fireStockBatch = useCallback(
    async (action: 'deployA' | 'deployB' | 'closeA' | 'closeB') => {
      const inst = stockInstrument
      if (!inst) {
        showToast.warning('No underlying picked — nothing for the stock batch to trade.')
        return
      }
      if (action === 'deployA' || action === 'deployB') {
        const side: OrderSide = action === 'deployA' ? 'BUY' : 'SELL'
        const sideCapital = capitals[action === 'deployA' ? 'CE' : 'PE']
        const ltp = getLtp(inst.symbol, inst.exchange)
        const lots = ltp && ltp > 0 ? Math.floor(sideCapital / (ltp * inst.lotSize)) : 0
        const quantity = lots * inst.lotSize
        if (quantity <= 0) {
          showToast.warning(`No lots affordable for ${inst.symbol} — check the side's capital.`)
          return
        }
        await fireLegs(
          [
            {
              legid: `sched-stock:${inst.symbol}`,
              symbol: inst.symbol,
              exchange: inst.exchange,
              action: side,
              quantity,
              pricetype: 'MARKET',
              product: state.product,
            },
          ],
          `Scheduled ${side === 'BUY' ? 'Buy' : 'Sell'} ${inst.symbol}`
        )
        return
      }
      const wantLong = action === 'closeA'
      const legs = positions
        .filter(
          (position) =>
            position.symbol === inst.symbol &&
            position.quantity !== 0 &&
            (wantLong ? position.quantity > 0 : position.quantity < 0)
        )
        .map((position) => ({
          legid: `sched-stock-close:${position.symbol}`,
          symbol: position.symbol,
          exchange: position.exchange,
          action: (position.quantity > 0 ? 'SELL' : 'BUY') as OrderSide,
          quantity: Math.abs(position.quantity),
          pricetype: 'MARKET' as const,
          product: position.product as 'MIS' | 'NRML' | 'CNC',
        }))
      await fireLegs(legs, `Scheduled Close ${wantLong ? 'Buy' : 'Sell'} ${inst.symbol}`)
    },
    [stockInstrument, capitals, getLtp, state.product, positions, fireLegs]
  )

  /** Place exactly the planned strikes, the side capital split evenly. */
  const fireIndexDeployLegs = useCallback(
    async (plan: NonNullable<ScheduledOrder['deploy']>): Promise<number> => {
      const rows = ctx.options.filter(
        (row) =>
          row.optionType === plan.side &&
          row.enabled &&
          plan.strikes.includes(row.strike ?? Number.NaN)
      )
      if (rows.length === 0) {
        showToast.warning('The planned strikes are no longer on the panel — nothing placed.')
        return 0
      }
      const per = capitals[plan.side] / rows.length
      const legs = rows
        .map((row) => {
          // Weight 100: each strike spends its full equal share of the side.
          const lots = computeRowLots({ ...row, weight: 100 }, getRowLtp(row), per, 'percentage')
          return legFromRow(row, plan.optionAction, lots.quantity)
        })
        .filter((leg) => leg.quantity > 0)
      return fireLegs(
        legs,
        `${plan.optionAction === 'BUY' ? 'Buy' : 'Sell'} ${plan.side} ×${rows.length} (index levels)`
      )
    },
    [ctx.options, capitals, getRowLtp, legFromRow, fireLegs]
  )

  /** Arm the target/stop close rows on the spot index after a planned deploy. */
  const armDeployExits = useCallback((plan: NonNullable<ScheduledOrder['deploy']>) => {
    if (!plan.spot) return
    const crosses = levelCrosses(plan.side, plan.optionAction)
    const closeAction = plan.side === 'CE' ? ('closeA' as const) : ('closeB' as const)
    const rows: ScheduledOrder[] = []
    const mk = (kind: 'target' | 'stop') => {
      const level = kind === 'target' ? plan.target : plan.stop
      if (level == null || !Number.isFinite(level) || level <= 0) return
      rows.push({
        id: newScheduleId(),
        action: closeAction,
        trigger: {
          kind: 'price',
          symbol: plan.spot!.symbol,
          exchange: plan.spot!.exchange,
          condition: kind === 'target' ? crosses.target : crosses.stop,
          price: level,
        },
        status: 'pending',
        createdAt: Date.now(),
        modeAtCreation: 'fno',
        target: 'options',
      })
    }
    mk('target')
    mk('stop')
    if (rows.length === 0) return
    setState((previous) => ({ ...previous, scheduled: [...previous.scheduled, ...rows] }))
    showToast.info(
      `Exit armed on ${plan.spot.symbol}: ${rows
        .map((row) => scheduleTriggerLabel(row.trigger))
        .join(' · ')}.`
    )
  }, [])

  const fireScheduled = useCallback(
    async (entry: ScheduledOrder) => {
      if (firingSchedules.current.has(entry.id)) return
      firingSchedules.current.add(entry.id)
      setScheduledBusy((previous) => new Set(previous).add(entry.id))
      const label = `Scheduled ${scheduleActionLabel(entry.action, entry.modeAtCreation, entry.target)}`
      try {
        switch (entry.action) {
          case 'deployA':
          case 'deployB':
            if (entry.target === 'stock') await fireStockBatch(entry.action)
            else if (entry.deploy) {
              const placed = await fireIndexDeployLegs(entry.deploy)
              if (placed > 0) {
                deployedOnce.current = true
                armDeployExits(entry.deploy)
              }
            } else await deploy(entry.action === 'deployA' ? 'A' : 'B')
            break
          case 'closeA':
          case 'closeB':
            if (entry.target === 'stock') await fireStockBatch(entry.action)
            else await closeSide(entry.action === 'closeA' ? 'A' : 'B')
            break
          case 'exitAll':
            await exitAll()
            break
        }
        showToast.info(`${label} fired`)
      } finally {
        firingSchedules.current.delete(entry.id)
        setScheduledBusy((previous) => {
          const next = new Set(previous)
          next.delete(entry.id)
          return next
        })
      }
    },
    [deploy, closeSide, exitAll, fireStockBatch, fireIndexDeployLegs, armDeployExits]
  )

  const markScheduled = useCallback((id: string, changes: Partial<ScheduledOrder>) => {
    setState((previous) => ({
      ...previous,
      scheduled: previous.scheduled.map((entry) =>
        entry.id === id ? { ...entry, ...changes } : entry
      ),
    }))
  }, [])

  // The engine tick: quotes and batch actions read through the refs above
  // (marketDataRef / quoteSymbolsRef), so the one-second cadence is stable
  // regardless of how fast the feed re-renders.
  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = new Date()
      for (const entry of state.scheduled) {
        if (entry.status !== 'pending') continue
        if (firingSchedules.current.has(entry.id)) continue
        const due = scheduleIsDue(entry, {
          now,
          ltpOf: (symbol, exchange) => marketDataRef.current.get(`${exchange}:${symbol}`)?.data.ltp,
        })
        if (!due) continue
        if (entry.trigger.kind === 'time') {
          if (entry.trigger.repeat === 'daily') {
            // Stays armed: firedAt pins today, so it re-arms tomorrow.
            markScheduled(entry.id, { firedAt: now.getTime() })
          } else {
            markScheduled(entry.id, { status: 'done', firedAt: now.getTime() })
          }
        } else {
          markScheduled(entry.id, { status: 'done', firedAt: now.getTime() })
        }
        void fireScheduled(entry)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [state.scheduled, fireScheduled, markScheduled])

  /** Add (or replace) a scheduled row from the dialog, arming it safely. */
  const submitScheduled = useCallback(
    (submission: SchedulerSubmit) => {
      // Vocabulary at creation: an F&O board reads CE/PE, a stock board
      // Buy/Sell (the persisted rows keep that label through tab switches).
      const row = scheduledFromSubmission(submission, fno ? 'fno' : 'equity')
      // A time already passed must not fire the moment it is armed: a once
      // entry retires as missed, a daily entry waits for tomorrow's pass.
      if (row.trigger.kind === 'time') {
        const target = parseClock(row.trigger.at)
        if (target != null && target <= istSeconds(new Date())) {
          if (row.trigger.repeat === 'daily') {
            row.firedAt = Date.now()
            showToast.info(`${row.trigger.at} IST has passed — armed for the next trading day.`)
          } else {
            row.status = 'missed'
            showToast.warning(`${row.trigger.at} IST has already passed — nothing armed.`)
          }
        }
      }
      setState((previous) => ({
        ...previous,
        scheduled: submission.id
          ? previous.scheduled.map((entry) => (entry.id === submission.id ? row : entry))
          : [...previous.scheduled, row],
      }))
      showToast.success(
        `${scheduleActionLabel(row.action, row.modeAtCreation, row.target)} scheduled — ${row.trigger.kind === 'time' ? `${row.trigger.at} IST` : 'on the price trigger'}.`
      )
    },
    [fno]
  )

  /** Cancel = history, not deletion: the row survives with its trigger and
   * batch, badge flips to canceled, and Repeat/Edit stay — the trader can
   * see what was disarmed and re-arm it without reconstructing it. */
  const cancelScheduled = useCallback((id: string) => {
    setState((previous) => ({
      ...previous,
      scheduled: previous.scheduled.map((entry) =>
        entry.id === id && entry.status === 'pending'
          ? { ...entry, status: 'canceled' as const }
          : entry
      ),
    }))
  }, [])

  /** Repeat: arm a fresh copy of an old row (pending, never fired). */
  const repeatScheduled = useCallback((entry: ScheduledOrder) => {
    const copy: ScheduledOrder = {
      ...entry,
      id: `sch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
      status: 'pending',
      firedAt: undefined,
      createdAt: Date.now(),
    }
    setState((previous) => ({ ...previous, scheduled: [...previous.scheduled, copy] }))
    showToast.info(
      `Re-armed ${scheduleActionLabel(entry.action, entry.modeAtCreation, entry.target)}.`
    )
  }, [])

  // ── Auto-open on an underlying pick: spot + ATM CE/PE ──────────────────
  // The pick arms the request; when the chain snapshot for THAT tab lands
  // (the query is keyed on the tab's own underlying + expiry, so the
  // snapshot is inherently the one the top bar auto-picked), the untouched
  // panes load the spot chart (pane 1), the ATM call (pane 2, left) and the
  // ATM put (pane 3, right). A 'single' workspace becomes the top + 2-legs
  // layout so the three charts have somewhere to land; anything the trader
  // loaded by hand is never touched.
  const [autoSeed, setAutoSeed] = useState<WorkspaceAutoSeed | null>(null)
  useEffect(() => {
    if (!chain || !fno || !chain.atm_strike) return
    const pending = pendingAutoSeed.current
    if (!pending) return
    if (pending.tabId !== ctx.id) return
    if (pending.underlying !== ctx.underlying) return
    const spot = {
      symbol: chain.underlying_symbol ?? ctx.underlying,
      exchange: chain.underlying_exchange ?? ctx.exchange,
    }
    const atmRow = chain.chain.find((row) => row.strike === chain.atm_strike)
    const foExchange = chain.fo_exchange ?? ctx.exchange
    const loads: WorkspaceAutoSeed['loads'] = [{ paneIndex: 0, ...spot }]
    if (atmRow?.ce?.symbol) {
      loads.push({ paneIndex: 1, symbol: atmRow.ce.symbol, exchange: foExchange })
    }
    if (atmRow?.pe?.symbol) {
      loads.push({ paneIndex: 2, symbol: atmRow.pe.symbol, exchange: foExchange })
    }
    pendingAutoSeed.current = null
    autoSeedStamp.current += 1
    setAutoSeed({ stamp: autoSeedStamp.current, loads })
  }, [chain, fno, ctx.id, ctx.underlying, ctx.exchange])

  const rowAction = useCallback(
    async (id: string, action: OrderSide) => {
      markBusy(id, true)
      try {
        const row = [...ctx.options, ...ctx.stocks].find((item) => item.id === id)
        if (!row) return
        const sideCapital = capitals[sideOf(row)]
        const lots = computeRowLots(row, getRowLtp(row), sideCapital, state.sideMode)
        if (lots.quantity <= 0) {
          showToast.warning(`No lots affordable for ${row.symbol} — check its weight.`)
          return
        }
        await fireLegs([legFromRow(row, action, lots.quantity)], `${action} ${row.symbol}`)
      } finally {
        markBusy(id, false)
      }
    },
    [ctx.options, ctx.stocks, capitals, getRowLtp, state.sideMode, legFromRow, fireLegs, markBusy]
  )

  const exitRow = useCallback(
    async (id: string) => {
      markBusy(id, true)
      try {
        const row = [...ctx.options, ...ctx.stocks].find((item) => item.id === id)
        const position = row ? positionsBySymbol.get(row.symbol) : undefined
        if (!row || !position || position.quantity === 0) return
        await fireLegs(
          [
            {
              legid: `exit:${row.symbol}`,
              symbol: row.symbol,
              exchange: row.exchange,
              action: (position.quantity > 0 ? 'SELL' : 'BUY') as OrderSide,
              quantity: Math.abs(position.quantity),
              pricetype: 'MARKET' as const,
              product: position.product as 'MIS' | 'NRML' | 'CNC',
            },
          ],
          `Exit ${row.symbol}`
        )
      } finally {
        markBusy(id, false)
      }
    },
    [ctx.options, ctx.stocks, positionsBySymbol, fireLegs, markBusy]
  )

  const swapPosition = useCallback(
    async (position: Position) => {
      markBusy(position.symbol, true)
      try {
        const closeAction: OrderSide = position.quantity > 0 ? 'SELL' : 'BUY'
        const openAction: OrderSide = position.quantity > 0 ? 'BUY' : 'SELL'
        await fireLegs(
          [
            {
              legid: `swap-close:${position.symbol}`,
              symbol: position.symbol,
              exchange: position.exchange,
              action: closeAction,
              quantity: Math.abs(position.quantity),
              pricetype: 'MARKET' as const,
              product: position.product as 'MIS' | 'NRML' | 'CNC',
            },
            {
              legid: `swap-open:${position.symbol}`,
              symbol: position.symbol,
              exchange: position.exchange,
              action: openAction,
              quantity: Math.abs(position.quantity),
              pricetype: 'MARKET' as const,
              product: position.product as 'MIS' | 'NRML' | 'CNC',
            },
          ],
          `Swap ${position.symbol}`
        )
      } finally {
        markBusy(position.symbol, false)
      }
    },
    [fireLegs, markBusy]
  )

  const repeatOrder = useCallback(
    async (order: Order) => {
      markBusy(order.orderid, true)
      try {
        await fireLegs(
          [
            {
              legid: `repeat:${order.orderid}`,
              symbol: order.symbol,
              exchange: order.exchange,
              action: order.action,
              quantity: order.quantity,
              pricetype: order.pricetype as 'MARKET' | 'LIMIT',
              product: order.product as 'MIS' | 'NRML' | 'CNC',
              ...(order.pricetype === 'LIMIT' || order.pricetype === 'SL'
                ? { price: order.price }
                : {}),
              ...(order.pricetype === 'SL' || order.pricetype === 'SL-M'
                ? { trigger_price: order.trigger_price }
                : {}),
            },
          ],
          `Repeat ${order.symbol}`
        )
      } finally {
        markBusy(order.orderid, false)
      }
    },
    [fireLegs, markBusy]
  )

  const cancelOrder = useCallback(
    async (orderid: string) => {
      markBusy(orderid, true)
      try {
        const response = await tradingApi.cancelOrder(orderid)
        if (response.status === 'success') showToast.success(`Order cancelled: ${orderid}`)
        else showToast.error(response.message || 'Cancel failed')
        refreshBooks()
      } catch {
        showToast.error('Cancel failed')
      } finally {
        markBusy(orderid, false)
      }
    },
    [refreshBooks, markBusy]
  )

  // ── Workspace helpers ───────────────────────────────────────────────────
  const loadIntoChart = useCallback((pick: { symbol: string; exchange: string; name?: string }) => {
    stamp.current += 1
    setFocusRequest({ ...pick, stamp: stamp.current })
  }, [])

  const addTab = () => {
    const id = newTabId()
    const index = state.tabs.length + 1
    patch({
      tabs: [...state.tabs, emptyTabContext(id, `W${index}`, 'single', ctx.kind, ctx.exchange)],
      activeTabId: id,
    })
  }

  /** Ask the symbol's home derivative exchange whether it lists option
   * expiries — the one honest answer to "does this have F&O". A failed ask
   * degrades to a plain stock chart, which every symbol has. */
  const askOptionExpiries = async (pick: UnderlyingPick): Promise<string[] | null> => {
    try {
      const res = await scalpingApi.getExpiry(pick.symbol, fnoExchangeFor(pick.exchange), 'options')
      return res.data ?? []
    } catch {
      return null
    }
  }

  /** A top-bar pick opens that market in a brand-new workspace tab — every
   * selection gets its own board, never a re-dress of an older tab (whose
   * layout the trader may have rebuilt). The KIND the pick resolves to
   * decides the board: a symbol with F&O derivatives (or picked straight off
   * a derivative exchange) opens the spot + legs layout and auto-seeds the
   * spot / ATM CE / ATM PE charts when its chain lands; a plain stock opens
   * single-pane and charts itself. The strip arithmetic lives in
   * lib/hedging/underlyingTab, under test. */
  const openUnderlyingWorkspace = async (pick: UnderlyingPick) => {
    setAutoSeed(null)
    const expiries = FNO_EXCHANGES.has(pick.exchange) ? [] : await askOptionExpiries(pick)
    const kind = pickKind(pick, expiries)
    setState((previous) => {
      const plan = planUnderlyingTabs(previous, pick, kind)
      pendingAutoSeed.current = plan.autoSeedTabId
        ? { tabId: plan.autoSeedTabId, underlying: pick.symbol }
        : null
      return { ...previous, tabs: plan.tabs, activeTabId: plan.activeTabId }
    })
  }
  const closeTab = (id: string) => {
    if (state.tabs.length <= 1) return
    const tabs = state.tabs.filter((tab) => tab.id !== id)
    patch({
      tabs,
      activeTabId: state.activeTabId === id ? tabs[0].id : state.activeTabId,
    })
  }

  // ── Chart seed: what an untouched pane charts first ─────────────────────
  const seed = useMemo(() => {
    if (fno) {
      // Spot is the default chart: the index itself, then a futures month.
      if (chain?.underlying_symbol)
        return {
          symbol: chain.underlying_symbol,
          exchange: chain.underlying_exchange ?? ctx.exchange,
        }
      if (chartFuture) return { symbol: chartFuture.symbol, exchange: ctx.exchange }
      return null
    }
    return ctx.underlying ? { symbol: ctx.underlying, exchange: ctx.exchange } : null
  }, [fno, chartFuture, ctx.exchange, chain, ctx.underlying])

  const activeStockSymbols = useMemo(
    () => new Set(ctx.stocks.map((row) => `${row.exchange}:${row.symbol}`)),
    [ctx.stocks]
  )

  // The spot index quote target for the dock's Index tab — the chain
  // snapshot knows which symbol/exchange the underlying index trades as.
  const indexSpot = useMemo(
    () =>
      chain?.underlying_symbol
        ? {
            symbol: chain.underlying_symbol,
            exchange: chain.underlying_exchange ?? ctx.exchange,
          }
        : null,
    [chain, ctx.exchange]
  )

  // ── Index-level deploy: entry / target / SL read the index chart ──────
  // The picked strikes join the panel (forced to the chosen direction), then
  // either place now — the side capital split evenly across them — or arm a
  // spot-price trigger that places exactly this plan when the index reaches
  // the entry. Target/stop arm as close rows on the spot once filled.
  const submitIndexDeploy = useCallback(
    async (plan: IndexDeploySubmit) => {
      if (!chain?.atm_strike) {
        showToast.warning('No option chain snapshot yet — wait for the expiries to load.')
        return
      }
      const strikes = strikesForPick(plan.pick, chain.atm_strike, strikeInterval)
      if (strikes.length === 0) {
        showToast.warning('No strikes resolved for that pick.')
        return
      }
      const picked = await addStrikes(plan.side, strikes, plan.optionAction)
      if (picked.length === 0) {
        showToast.warning('Those strikes are not on the chain — nothing to deploy.')
        return
      }
      const deployPlan: NonNullable<ScheduledOrder['deploy']> = {
        side: plan.side,
        strikes: picked.map((row) => row.strike ?? 0).filter((strike) => strike > 0),
        optionAction: plan.optionAction,
        target: plan.target ?? null,
        stop: plan.stop ?? null,
        spot: indexSpot,
      }
      const crosses = levelCrosses(plan.side, plan.optionAction)
      const spotLtp = indexSpot ? getLtp(indexSpot.symbol, indexSpot.exchange) : undefined
      const entryMet =
        plan.entry == null ||
        (spotLtp != null &&
          (crosses.target === 'above' ? spotLtp >= plan.entry : spotLtp <= plan.entry))
      if (entryMet) {
        const placed = await fireIndexDeployLegs(deployPlan)
        if (placed > 0) {
          deployedOnce.current = true
          armDeployExits(deployPlan)
        }
        return
      }
      if (!indexSpot) {
        showToast.error('The spot symbol is not known yet — cannot arm an index trigger.')
        return
      }
      const row: ScheduledOrder = {
        id: newScheduleId(),
        action: plan.side === 'CE' ? 'deployA' : 'deployB',
        trigger: {
          kind: 'price',
          symbol: indexSpot.symbol,
          exchange: indexSpot.exchange,
          condition: crosses.target,
          price: plan.entry as number,
        },
        status: 'pending',
        createdAt: Date.now(),
        modeAtCreation: 'fno',
        target: 'options',
        deploy: deployPlan,
      }
      setState((previous) => ({ ...previous, scheduled: [...previous.scheduled, row] }))
      showToast.info(
        `Armed: ${plan.optionAction} ${plan.side} ×${deployPlan.strikes.length} when ${indexSpot.symbol} ${crosses.target === 'above' ? '≥' : '≤'} ${(plan.entry as number).toLocaleString('en-IN')}.`
      )
    },
    [chain, strikeInterval, addStrikes, fireIndexDeployLegs, armDeployExits, indexSpot, getLtp]
  )

  // Which charted symbols are "the index" — the spot quote the terminal
  // follows. Those panes are quote-only, so they get the docked Deploy
  // CE/PE pair instead of the engine's own Buy/Sell panel.
  const indexSymbols = useMemo(() => {
    const set = new Set<string>()
    if (fno) {
      if (chain?.underlying_symbol)
        set.add(`${chain.underlying_exchange ?? ctx.exchange}:${chain.underlying_symbol}`)
      if (ctx.underlying) set.add(`${ctx.exchange}:${ctx.underlying}`)
    }
    return set
  }, [fno, chain, ctx.exchange, ctx.underlying])

  const quickTradeFor = useCallback(
    (symbolKey: string | null): PaneQuickTrade | null => {
      if (!fno || !symbolKey || !indexSymbols.has(symbolKey)) return null
      return {
        primaryLabel: 'DEPLOY CE',
        secondaryLabel: 'DEPLOY PE',
        primaryTone: 'buy',
        secondaryTone: 'sell',
        onPrimary: () => void deploy('A'),
        onSecondary: () => void deploy('B'),
      }
    },
    [fno, indexSymbols, deploy]
  )

  // ── Focused pane: what the trader is looking at right now ──────────────
  // Selecting a pane re-aims the action bar's symbol chip and the
  // scheduler's default at that pane's chart — an F&O chart reads CE/PE and
  // the spot index; a simple stock reads Buy/Sell and the stock itself. The
  // user can change the scheduler's pick afterwards; this only sets defaults.
  const [focusedPane, setFocusedPane] = useState<{
    tabId: string
    paneId: string
    symbolKey: string | null
  } | null>(null)
  const onFocusedPaneChange = useCallback(
    (info: { paneId: string; symbolKey: string | null } | null) => {
      if (!info) {
        setFocusedPane(null)
        return
      }
      setFocusedPane({ tabId: ctx.id, paneId: info.paneId, symbolKey: info.symbolKey })
    },
    [ctx.id]
  )
  const focusedInstrument = useMemo(() => {
    if (!focusedPane || focusedPane.tabId !== ctx.id || !focusedPane.symbolKey) return null
    const [exchange, symbol] = focusedPane.symbolKey.split(':')
    if (!symbol) return null
    return { symbol, exchange }
  }, [focusedPane, ctx.id])

  // The action bar's chip: for the CE/PE batch the spot index (the panels
  // hold that index's options), for a Buy/Sell batch the stock the panel
  // trades. A focused pane only refines the stock case when it charts a
  // different symbol than the tab's underlying — the batches still act on
  // the active tab's panels, so the tab's own market stays the answer.
  const actionSymbol = useMemo(() => {
    if (fno) return chain?.underlying_symbol ?? (ctx.underlying || null)
    return focusedInstrument?.symbol ?? ctx.underlying ?? null
  }, [fno, chain, ctx.underlying, focusedInstrument])

  // The scheduler's default trigger symbol: the focused pane's chart, then
  // the tab's own market (futures contract for stock-target, spot index,
  // cash symbol). Displayed on the dialog; changeable there.
  const schedulerDefaultSymbol = useMemo(() => {
    if (focusedInstrument) return focusedInstrument
    if (fno) {
      if (stockInstrument)
        return { symbol: stockInstrument.symbol, exchange: stockInstrument.exchange }
      return indexSpot ?? undefined
    }
    return ctx.underlying ? { symbol: ctx.underlying, exchange: ctx.exchange } : undefined
  }, [focusedInstrument, fno, stockInstrument, indexSpot, ctx.underlying, ctx.exchange])

  // The dialog's "Acts on" line, per Trade toggle.
  const schedulerActsOn = useMemo(() => {
    if (fno) {
      return {
        options: indexSpot
          ? `${indexSpot.symbol} option panels (CE/PE)`
          : `${ctx.underlying || 'the workspace'}'s option panels (CE/PE)`,
        stock: stockInstrument
          ? `${stockInstrument.symbol} (${stockInstrument.exchange}) — Buy/Sell`
          : 'the underlying once a symbol is picked',
      }
    }
    const name = focusedInstrument?.symbol ?? ctx.underlying ?? 'the workspace'
    return { options: `${name} stocks panel (Buy/Sell)`, stock: `${name} (Buy/Sell)` }
  }, [fno, indexSpot, ctx.underlying, stockInstrument, focusedInstrument])

  // ── Chart marker lines: the open position's entry and its levels ────────
  // A pane charting a symbol with an open position draws the fill's average
  // price (solid, direction-coloured) and the row's target/SL (dotted) —
  // the same numbers the row's sparkline carries, on the price chart itself.
  const priceLinesFor = useCallback(
    (_paneId: string, symbolKey: string | null): PanePriceLine[] => {
      if (!symbolKey) return []
      const [exchange, symbol] = symbolKey.split(':')
      if (!symbol) return []
      const position = positionsBySymbol.get(symbol)
      const row = [...ctx.options, ...ctx.stocks].find(
        (item) => item.symbol === symbol && item.exchange === exchange
      )
      const lines: PanePriceLine[] = []
      if (position) {
        lines.push({
          id: 'pos-entry',
          price: position.average_price,
          color: position.quantity > 0 ? '#10b981' : '#ef4444',
          badge: position.quantity > 0 ? 'LONG' : 'SHORT',
          label: `Entry ${position.average_price.toFixed(2)}`,
        })
      }
      if (row?.target != null) {
        lines.push({
          id: 'pos-target',
          price: row.target,
          color: '#10b981',
          badge: 'TGT',
          dotted: true,
        })
      }
      if (row?.stoploss != null) {
        lines.push({
          id: 'pos-sl',
          price: row.stoploss,
          color: '#ef4444',
          badge: 'SL',
          dotted: true,
        })
      }
      return lines
    },
    [positionsBySymbol, ctx.options, ctx.stocks]
  )

  const sidePanel = (side: StrikeSide | 'EQ', title: string) => (
    <InstrumentPanel
      kind={side === 'EQ' ? 'stock' : 'option'}
      side={side}
      title={title}
      rows={side === 'EQ' ? ctx.stocks : ctx.options.filter((row) => row.optionType === side)}
      summary={summarize(side)}
      sideCapital={capitals[side]}
      sideMode={state.sideMode}
      getLtp={getRowLtp}
      getSessionRange={getSessionRange}
      getLtpHistory={getLtpHistory}
      positionsBySymbol={positionsBySymbol}
      pendingBySymbol={pendingBySymbol}
      busyIds={rowBusy}
      atmStrike={chain?.atm_strike}
      strikeInterval={strikeInterval}
      exchange={ctx.exchange}
      onAddStrikes={(strikes) => void addStrikes(side === 'EQ' ? 'CE' : side, strikes)}
      onAddStock={addStock}
      onToggleRow={toggleRow}
      onRemoveRow={removeRow}
      onSlideWeight={slideWeight}
      onFixedAmount={setFixedAmount}
      onRowAction={(id, action) => void rowAction(id, action)}
      onFieldChange={setField}
      onExitRow={(id) => void exitRow(id)}
      onEditOrder={(order) => setEditingOrder(order)}
      onLoadChart={(row) => loadIntoChart({ symbol: row.symbol, exchange: row.exchange })}
    />
  )

  return (
    <div className="flex h-screen flex-col overflow-hidden text-xs">
      <HedgingTopBar
        fno={fno}
        exchange={ctx.exchange}
        underlying={ctx.underlying}
        expiry={ctx.expiry}
        futuresExpiry={ctx.futuresExpiry}
        capital={state.capital}
        capitalSource={state.capitalSource}
        capitalPercent={state.capitalPercent}
        capitalBasis={state.capitalBasis}
        usable={capital}
        sideMode={state.sideMode}
        product={state.product}
        pnl={dayPnl}
        funds={funds}
        onUnderlyingChange={(pick) => void openUnderlyingWorkspace(pick)}
        onExpiryChange={(expiry) => patchCtx({ expiry })}
        onFuturesExpiryChange={(expiry) => patchCtx({ futuresExpiry: expiry })}
        onCapitalChange={(value) => patch({ capital: value })}
        onCapitalSourceChange={(capitalSource) => patch({ capitalSource })}
        onCapitalPercentChange={(capitalPercent) => patch({ capitalPercent })}
        onCapitalBasisChange={(capitalBasis) => patch({ capitalBasis })}
        onSideModeChange={(sideMode) => patch({ sideMode })}
        onProductChange={(product) => patch({ product: product as HedgingState['product'] })}
        onRefreshFunds={() =>
          void queryClient.invalidateQueries({ queryKey: ['hedging', 'funds'] })
        }
      />

      <div className="flex min-h-0 flex-1">
        {/* Left column */}
        {!leftCollapsed && (
          <div className="w-[280px] shrink-0" style={{ width: SIDE_WIDTH }}>
            {fno ? (
              sidePanel('CE', 'CALLS')
            ) : (
              <WatchlistMini
                getLtp={getLtp}
                activeSymbols={activeStockSymbols}
                onLoadChart={loadIntoChart}
                onAddStock={addStock}
              />
            )}
          </div>
        )}
        <button
          type="button"
          onClick={() => setLeftCollapsed((value) => !value)}
          className="flex w-3 shrink-0 items-center justify-center border-r bg-muted/40 text-muted-foreground hover:bg-muted"
          title={leftCollapsed ? 'Show left panel' : 'Hide left panel'}
        >
          {leftCollapsed ? (
            <ChevronRight className="h-3 w-3" />
          ) : (
            <ChevronLeft className="h-3 w-3" />
          )}
        </button>

        {/* Middle: workspace */}
        <HedgingWorkspace
          apiKey={apiKey}
          wsUrl={wsUrl}
          tabs={state.tabs}
          activeTabId={state.activeTabId}
          linkGroup={linkGroup}
          focusRequest={focusRequest}
          autoSeed={autoSeed}
          seed={seed}
          onSelectTab={(id) => patch({ activeTabId: id })}
          onAddTab={addTab}
          onCloseTab={closeTab}
          onRenameTab={(id, name) =>
            patch({ tabs: state.tabs.map((tab) => (tab.id === id ? { ...tab, name } : tab)) })
          }
          onLayoutChange={(tabId, layoutId) =>
            patch({
              tabs: state.tabs.map((tab) => (tab.id === tabId ? { ...tab, layoutId } : tab)),
            })
          }
          quickTradeFor={quickTradeFor}
          priceLinesFor={priceLinesFor}
          onFocusedPaneChange={onFocusedPaneChange}
        />

        {/* Right column */}
        <button
          type="button"
          onClick={() => setRightCollapsed((value) => !value)}
          className="flex w-3 shrink-0 items-center justify-center border-l bg-muted/40 text-muted-foreground hover:bg-muted"
          title={rightCollapsed ? 'Show right panel' : 'Hide right panel'}
        >
          {rightCollapsed ? (
            <ChevronLeft className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
        </button>
        {!rightCollapsed && (
          <div className="shrink-0" style={{ width: SIDE_WIDTH }}>
            {fno ? sidePanel('PE', 'PUTS') : sidePanel('EQ', 'STOCKS')}
          </div>
        )}
      </div>

      <HedgingDock
        mode={fno ? 'fno' : 'equity'}
        collapsed={dockCollapsed}
        quickBusy={quickBusy}
        rowBusy={rowBusy}
        positions={positions}
        orders={orders}
        trades={trades}
        scheduled={state.scheduled}
        ceShare={state.ceShare}
        onCeShareChange={(share) => patch({ ceShare: share })}
        onOpenScheduler={() => {
          setEditingScheduled(null)
          setSchedulerOpen(true)
        }}
        onEditScheduled={(entry) => {
          setEditingScheduled(entry)
          setSchedulerOpen(true)
        }}
        onCancelScheduled={(id) => cancelScheduled(id)}
        onRepeatScheduled={(entry) => repeatScheduled(entry)}
        scheduledBusy={scheduledBusy}
        underlying={ctx.underlying}
        actionSymbol={actionSymbol}
        indexSpot={indexSpot}
        onLoadChart={loadIntoChart}
        pnl={realizedPnl}
        upnl={unrealizedPnl}
        onOpenIndexDeploy={() => setIndexDeployOpen(true)}
        editing={editingOrder as DockOrder | null}
        onCloseEdit={() => setEditingOrder(null)}
        onOpenEdit={(order) => setEditingOrder(order)}
        onToggleCollapsed={() => setDockCollapsed((value) => !value)}
        onDeploy={(side) => void deploy(side)}
        onCloseSide={(side) => void closeSide(side)}
        onSwap={() => void swap()}
        onExitAll={() => void exitAll()}
        onCancelOrder={(orderid) => void cancelOrder(orderid)}
        onRepeatOrder={(order) => void repeatOrder(order)}
        onExitPosition={(position) =>
          void fireLegs(
            [
              {
                legid: `dock-exit:${position.symbol}`,
                symbol: position.symbol,
                exchange: position.exchange,
                action: (position.quantity > 0 ? 'SELL' : 'BUY') as OrderSide,
                quantity: Math.abs(position.quantity),
                pricetype: 'MARKET' as const,
                product: position.product as 'MIS' | 'NRML' | 'CNC',
              },
            ],
            `Exit ${position.symbol}`
          )
        }
        onSwapPosition={(position) => void swapPosition(position)}
        onRefreshBooks={refreshBooks}
      />

      <SchedulerDialog
        open={schedulerOpen}
        mode={fno ? 'fno' : 'equity'}
        initial={editingScheduled}
        triggerSymbols={quoteSymbols}
        defaultSymbol={schedulerDefaultSymbol}
        stockSymbol={stockInstrument ?? undefined}
        spotSymbol={indexSpot ?? undefined}
        actsOn={schedulerActsOn}
        onOpenChange={(open) => {
          if (!open) {
            setSchedulerOpen(false)
            setEditingScheduled(null)
          }
        }}
        onSubmit={submitScheduled}
      />

      <IndexDeployDialog
        open={indexDeployOpen}
        onOpenChange={setIndexDeployOpen}
        spot={indexSpot}
        spotLtp={indexSpot ? getLtp(indexSpot.symbol, indexSpot.exchange) : undefined}
        atmStrike={chain?.atm_strike}
        strikeInterval={strikeInterval}
        ceShare={state.ceShare}
        onSubmit={(plan) => void submitIndexDeploy(plan)}
      />
    </div>
  )
}
