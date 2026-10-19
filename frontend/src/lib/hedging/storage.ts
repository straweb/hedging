import type {
  HedgingInstrument,
  HedgingState,
  HedgingWorkspaceTab,
  ScheduledOrder,
  TabKind,
} from '@/types/hedging'
import { emptyTabContext, FNO_EXCHANGES, makeInstrumentId } from '@/types/hedging'

/**
 * localStorage persistence for the hedging terminal.
 *
 * Everything the trader sets up — strike sets, weights, capital split,
 * scheduled orders, workspace tabs — lives in one versioned key so a page
 * load restores the terminal exactly as it was left. The reader merges over
 * DEFAULTS so an older or partial snapshot cannot produce a half-formed
 * terminal. Older snapshots migrate forward in place:
 *
 * - v1 → v2: capitalMode split into capitalSource + capitalBasis.
 * - v2 → v3: the instrument context (underlying, expiries, strike/stock
 *   rows) moved from the state into the workspace tabs, so every tab
 *   carries its own market. The tab that was active inherits the old
 *   global context; other tabs start empty rather than silently trading
 *   a market the trader did not give them.
 * - v3 → v4: the global F&O/Equity mode is gone — every tab carries its
 *   own `kind` ('fno' | 'stock'), derived from its home exchange (a
 *   derivative exchange means an F&O market) and, for the tab that was
 *   active, from the old global mode when its exchange does not decide.
 */

export const HEDGING_STORAGE_KEY = 'openalgo.hedging.v1'

export const DEFAULT_STATE: HedgingState = {
  version: 4,
  capital: 100000,
  capitalSource: 'fixed',
  capitalPercent: 25,
  capitalBasis: 'initial',
  ceShare: 50,
  sideMode: 'percentage',
  // NRML carries positions overnight on the F&O exchanges — the natural
  // default there; equity scalping is intraday, so MIS. The mode switch
  // re-applies this pairing (see the page).
  product: 'NRML',
  pricetype: 'MARKET',
  scheduled: [],
  tabs: [defaultTab()],
  activeTabId: 'ws-1',
}

function defaultTab(kind: TabKind = 'fno'): HedgingWorkspaceTab {
  return {
    ...emptyTabContext('ws-1', 'W1', 'single', kind, kind === 'fno' ? 'NFO' : 'NSE'),
    underlying: kind === 'fno' ? 'NIFTY' : '',
  }
}

const WORKSPACE_PREFIX = 'ws-'

export function newTabId(): string {
  return `${WORKSPACE_PREFIX}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

export function makeOptionRow(input: {
  symbol: string
  exchange: string
  strike: number
  optionType: 'CE' | 'PE'
  lotSize: number
  /** Initial weight before the side normalizes (0 splits in evenly). */
  weight?: number
}): HedgingInstrument {
  return {
    id: makeInstrumentId('option', input.symbol, input.exchange),
    kind: 'option',
    symbol: input.symbol,
    exchange: input.exchange,
    strike: input.strike,
    optionType: input.optionType,
    lotSize: input.lotSize,
    enabled: true,
    weight: input.weight ?? 0,
    fixedAmount: 0,
    action: 'BUY',
    entry: null,
    target: null,
    stoploss: null,
  }
}

export function makeStockRow(input: {
  symbol: string
  exchange: string
  lotSize?: number
  weight?: number
}): HedgingInstrument {
  return {
    id: makeInstrumentId('stock', input.symbol, input.exchange),
    kind: 'stock',
    symbol: input.symbol,
    exchange: input.exchange,
    lotSize: input.lotSize ?? 1,
    enabled: true,
    weight: input.weight ?? 0,
    fixedAmount: 0,
    action: 'BUY',
    entry: null,
    target: null,
    stoploss: null,
  }
}

function sanitizedScheduled(rows: unknown): ScheduledOrder[] {
  if (!Array.isArray(rows)) return []
  return rows
    .filter(
      (row): row is ScheduledOrder =>
        !!row &&
        typeof row === 'object' &&
        typeof (row as ScheduledOrder).id === 'string' &&
        (row as ScheduledOrder).trigger != null
    )
    .map((row) => ({
      ...row,
      action: row.action,
      status:
        row.status === 'done' || row.status === 'missed' || row.status === 'canceled'
          ? (row.status as ScheduledOrder['status'])
          : ('pending' as const),
      createdAt: typeof row.createdAt === 'number' ? row.createdAt : 0,
      modeAtCreation: (row.modeAtCreation === 'equity'
        ? 'equity'
        : 'fno') as ScheduledOrder['modeAtCreation'],
      target: (row.target === 'stock' ? 'stock' : 'options') as ScheduledOrder['target'],
    }))
    .filter((row) => ['deployA', 'deployB', 'closeA', 'closeB', 'exitAll'].includes(row.action))
}

function sanitizedList(rows: unknown): HedgingInstrument[] {
  if (!Array.isArray(rows)) return []
  return rows
    .filter(
      (row): row is HedgingInstrument =>
        !!row &&
        typeof row === 'object' &&
        typeof (row as HedgingInstrument).symbol === 'string' &&
        typeof (row as HedgingInstrument).exchange === 'string'
    )
    .map((row) => ({
      ...row,
      kind: row.kind === 'stock' ? 'stock' : 'option',
      lotSize: typeof row.lotSize === 'number' && row.lotSize > 0 ? row.lotSize : 1,
      enabled: row.enabled !== false,
      weight: typeof row.weight === 'number' ? row.weight : 0,
      fixedAmount: typeof row.fixedAmount === 'number' ? row.fixedAmount : 0,
      action: row.action === 'SELL' ? 'SELL' : 'BUY',
      entry: typeof row.entry === 'number' ? row.entry : null,
      target: typeof row.target === 'number' ? row.target : null,
      stoploss: typeof row.stoploss === 'number' ? row.stoploss : null,
    }))
}

/** A tab's kind: what it trades. A stored value wins; otherwise the home
 * exchange decides — a derivative exchange is an F&O market, a cash exchange
 * a plain stock. Only a tab with NO exchange at all (older than the split)
 * inherits the legacy fallback, from the snapshot's old global mode. */
function tabKind(tab: Partial<HedgingWorkspaceTab>, fallback: TabKind = 'fno'): TabKind {
  if (tab.kind === 'fno' || tab.kind === 'stock') return tab.kind
  if (!tab.exchange) return fallback
  return FNO_EXCHANGES.has(tab.exchange) ? 'fno' : 'stock'
}

function sanitizedTab(
  tab: Partial<HedgingWorkspaceTab>,
  index: number,
  fallbackKind: TabKind = 'fno'
): HedgingWorkspaceTab {
  const kind = tabKind(tab, fallbackKind)
  const base = emptyTabContext(
    tab.id ?? `ws-migrated-${index}`,
    typeof tab.name === 'string' && tab.name ? tab.name : `W${index + 1}`,
    typeof tab.layoutId === 'string' && tab.layoutId ? tab.layoutId : 'single',
    kind,
    typeof tab.exchange === 'string' && tab.exchange ? tab.exchange : kind === 'fno' ? 'NFO' : 'NSE'
  )
  return {
    ...base,
    underlying: typeof tab.underlying === 'string' ? tab.underlying : '',
    expiry: typeof tab.expiry === 'string' ? tab.expiry : '',
    futuresExpiry: typeof tab.futuresExpiry === 'string' ? tab.futuresExpiry : '',
    options: sanitizedList(tab.options),
    stocks: sanitizedList(tab.stocks),
  }
}

/** Merge a (possibly older or partial) snapshot over the defaults. */
export function hydrateState(raw: unknown): HedgingState {
  const source = (raw ?? {}) as Partial<HedgingState> & {
    // v2 globals that v3 moved into the tabs; v3's global mode that v4
    // folded into each tab's kind.
    exchange?: string
    underlying?: string
    expiry?: string
    futuresExpiry?: string
    options?: HedgingInstrument[]
    stocks?: HedgingInstrument[]
    mode?: 'fno' | 'equity'
    // v1 field, dropped in v2.
    capitalMode?: 'fixed' | 'percent'
  }
  // v3 → v4: a tab with no exchange of its own (older than the split)
  // inherits the old global mode's kind; every other tab reads its exchange.
  const legacyKind: TabKind = source.mode === 'equity' ? 'stock' : 'fno'
  const rawTabs: HedgingWorkspaceTab[] = Array.isArray(source.tabs)
    ? source.tabs
        .filter((tab) => tab && typeof tab.id === 'string')
        .map((tab, index) => sanitizedTab(tab as Partial<HedgingWorkspaceTab>, index, legacyKind))
    : []
  const tabs = rawTabs.length > 0 ? rawTabs : [defaultTab(legacyKind)]
  const activeTabId = tabs.some((tab) => tab.id === source.activeTabId)
    ? (source.activeTabId as string)
    : tabs[0].id

  // v2 → v3: the active tab inherits the snapshot's global instrument context,
  // so the terminal looks exactly as it was left. Tabs that carried no context
  // of their own (or older tabs) stay empty — the trader picks their market.
  const legacyContext = {
    exchange: typeof source.exchange === 'string' ? source.exchange : '',
    underlying: typeof source.underlying === 'string' ? source.underlying : '',
    expiry: typeof source.expiry === 'string' ? source.expiry : '',
    futuresExpiry: typeof source.futuresExpiry === 'string' ? source.futuresExpiry : '',
    options: sanitizedList(source.options),
    stocks: sanitizedList(source.stocks),
  }
  const migratedTabs = tabs.map((tab) =>
    tab.underlying || tab.options.length > 0 || tab.stocks.length > 0
      ? tab
      : tab.id === activeTabId
        ? { ...tab, ...legacyContext }
        : tab
  )

  const capitalSource: HedgingState['capitalSource'] =
    (source.capitalSource ?? source.capitalMode) === 'percent' ? 'percent' : 'fixed'
  return {
    version: 4,
    capital:
      typeof source.capital === 'number' && source.capital > 0
        ? source.capital
        : DEFAULT_STATE.capital,
    capitalSource,
    capitalBasis:
      source.capitalBasis === 'withPnl' || source.capitalBasis === 'house'
        ? source.capitalBasis
        : 'initial',
    capitalPercent:
      typeof source.capitalPercent === 'number'
        ? Math.min(100, Math.max(1, source.capitalPercent))
        : DEFAULT_STATE.capitalPercent,
    ceShare:
      typeof source.ceShare === 'number'
        ? Math.min(100, Math.max(0, source.ceShare))
        : DEFAULT_STATE.ceShare,
    sideMode: source.sideMode === 'fixed' ? 'fixed' : 'percentage',
    product:
      source.product === 'NRML' || source.product === 'CNC' || source.product === 'MIS'
        ? source.product
        : DEFAULT_STATE.product,
    pricetype: source.pricetype === 'LIMIT' ? 'LIMIT' : 'MARKET',
    scheduled: sanitizedScheduled(source.scheduled),
    tabs: migratedTabs,
    activeTabId,
  }
}

export function loadHedgingState(storage: Pick<Storage, 'getItem'> = localStorage): HedgingState {
  try {
    const raw = storage.getItem(HEDGING_STORAGE_KEY)
    if (!raw) return { ...DEFAULT_STATE, tabs: [defaultTab()] }
    return hydrateState(JSON.parse(raw))
  } catch {
    // Corrupt or refused storage: a fresh terminal beats a broken one.
    return { ...DEFAULT_STATE, tabs: [defaultTab()] }
  }
}

export function saveHedgingState(
  state: HedgingState,
  storage: Pick<Storage, 'setItem'> = localStorage
): void {
  try {
    storage.setItem(HEDGING_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Quota or private mode: the session keeps working, persistence resumes later.
  }
}
