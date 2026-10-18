import type { Order, Position, Trade } from '@/types/trading'

/**
 * Domain model for the /hedging terminal.
 *
 * One instrument row (a strike in FNO mode, a stock in equity mode) carries
 * its quotes-independent trading configuration: how much capital it gets, in
 * which direction it trades and where its entry / target / stop sit. The
 * allocation engine turns that configuration into live lot counts as LTPs
 * move; the dock and the quick actions turn it into multileg order batches.
 */

export type Mode = 'fno' | 'equity'
export type StrikeSide = 'CE' | 'PE'
export type OrderSide = 'BUY' | 'SELL'
export type SideMode = 'percentage' | 'fixed'
/**
 * How the session's trading capital is derived — the "house money" ladder:
 * - initial : the allocated capital only. Realized losses reduce it; profits
 *             are never added back, so a winning session cannot raise risk.
 * - withPnl : allocated capital plus the session's P&L, so profit is
 *             recyclable into new trades at any time.
 * - house   : the first deployment of the session spends the full allocated
 *             capital; every deployment after that may only spend the profit
 *             the session has realized (trading with the house's money).
 */
export type CapitalBasis = 'initial' | 'withPnl' | 'house'
/** How the allocated capital itself is picked: a rupee amount or % of cash. */
export type CapitalSource = 'fixed' | 'percent'
export type HedgingProduct = 'MIS' | 'NRML' | 'CNC'
export type HedgingPricetype = 'MARKET' | 'LIMIT'
export type SortKey = 'strike' | 'ltp' | 'pnl'
export type RowFilter = 'all' | 'enabled' | 'positions'

/** One tradable row on a side panel (an option strike or an equity stock). */
export interface HedgingInstrument {
  /** Stable id: `${kind}:${exchange}:${symbol}` — survives renames of weights. */
  id: string
  kind: 'option' | 'stock'
  symbol: string
  exchange: string
  /** Options only: the strike price, for sorting and ATM offsets. */
  strike?: number
  /** Options only: which side panel this row belongs to. */
  optionType?: StrikeSide
  /** Shares per lot (1 for equity). */
  lotSize: number
  /** Disabled rows are shown greyed, excluded from deploy, but keep config. */
  enabled: boolean
  /**
   * Percentage-mode weight within the side. The engine normalizes the side's
   * enabled weights to sum to 100 as the user slides any of them.
   */
  weight: number
  /** Fixed-mode rupee allocation for this row. */
  fixedAmount: number
  /** Direction this row trades when its side deploys. */
  action: OrderSide
  /** LIMIT entry when set; MARKET when null. */
  entry: number | null
  /** Monitoring levels: the row highlights when LTP crosses them. */
  target: number | null
  stoploss: number | null
}

export function makeInstrumentId(kind: 'option' | 'stock', symbol: string, exchange: string) {
  return `${kind}:${exchange}:${symbol}`
}

/**
 * A workspace tab in the middle column. Each tab owns a LAYOUTS preset and —
 * since v3 — its own instrument context: the underlying it follows, its
 * expiries and its strike/stock rows. Switching tabs switches the side
 * panels, the quick actions and the books filters to that context, so two
 * underlyings can be worked side by side without overwriting each other.
 */
export interface HedgingWorkspaceTab {
  id: string
  name: string
  /** A LAYOUTS preset id from '@/lib/chart/layouts'. */
  layoutId: string
  /** FNO: NFO | BFO | MCX | CDS. Equity: NSE | BSE. */
  exchange: string
  underlying: string
  /** Options expiry, DDMMMYY as the API returns it. */
  expiry: string
  /** Futures expiry (charts), DDMMMYY — kept apart from the option expiry. */
  futuresExpiry: string
  /** FNO mode strike rows (both sides) that belong to this tab. */
  options: HedgingInstrument[]
  /** Equity mode active stock list that belongs to this tab. */
  stocks: HedgingInstrument[]
}

/** A fresh, empty tab context for a new workspace. */
export function emptyTabContext(
  id: string,
  name: string,
  layoutId: string,
  exchange = 'NFO'
): HedgingWorkspaceTab {
  return {
    id,
    name,
    layoutId,
    exchange,
    underlying: '',
    expiry: '',
    futuresExpiry: '',
    options: [],
    stocks: [],
  }
}

/** What a scheduled entry fires: the same batches the quick actions run. */
export type ScheduleAction = 'deployA' | 'deployB' | 'closeA' | 'closeB' | 'exitAll'
/**
 * Which book a scheduled batch works on. `options` (default) runs the strike
 * panels — Deploy CE/PE vocabulary. `stock` trades the underlying itself
 * (futures contract when one lists, else the cash symbol) with the Buy/Sell
 * vocabulary — the F&O/Equity choice for F&O stocks.
 */
export type ScheduleTarget = 'options' | 'stock'
export type ScheduleTrigger =
  | {
      kind: 'time'
      /** HH:MM or HH:MM:SS in IST, 24h. */
      at: string
      repeat: 'once' | 'daily'
    }
  | {
      kind: 'price'
      symbol: string
      exchange: string
      /**
       * above/below fire on a single level; inside/outside take the band
       * between `price` and `price2` (either order — normalised in code).
       */
      condition: 'above' | 'below' | 'inside' | 'outside'
      price: number
      price2?: number
    }
export type ScheduleStatus = 'pending' | 'done' | 'missed'

/**
 * The strike-level plan an index-price deploy carries: which strikes to place
 * (equal split of the side capital), in which direction, and the index levels
 * that close it. Armed rows fire this plan instead of the panel deploy.
 */
export interface ScheduledDeployPlan {
  side: StrikeSide
  strikes: number[]
  optionAction: OrderSide
  target?: number | null
  stop?: number | null
  /** Where the target/stop triggers read the index price. */
  spot?: { symbol: string; exchange: string } | null
}

/**
 * One scheduler row: a batch action armed on a clock time or an LTP level.
 * Fired entries keep their row (status done) so the books show what ran;
 * Repeat re-arms a copy, Edit reopens it, Cancel removes it.
 */
export interface ScheduledOrder {
  id: string
  action: ScheduleAction
  trigger: ScheduleTrigger
  status: ScheduleStatus
  createdAt: number
  /** When the trigger fired (epoch ms), for the audit column. */
  firedAt?: number
  /** The mode at creation time, so the label survives a mode switch. */
  modeAtCreation: Mode
  /** Options panels (CE/PE) or the underlying itself (Buy/Sell). */
  target?: ScheduleTarget
  /** Present on index-level deploys: fire exactly these strikes. */
  deploy?: ScheduledDeployPlan
}

export interface HedgingState {
  version: 3
  mode: Mode
  /** Allocated capital, and whether it is a rupee amount or % of cash. */
  capital: number
  capitalSource: CapitalSource
  capitalPercent: number
  /**
   * How the session's usable capital is derived: the allocated capital alone
   * (initial), capital + P&L (withPnl), or capital once then profit only
   * (house).
   */
  capitalBasis: CapitalBasis
  /** CE share of the capital in percent; PE gets the rest. */
  ceShare: number
  sideMode: SideMode
  product: HedgingProduct
  pricetype: HedgingPricetype
  /** Scheduled time/price-triggered batch orders. */
  scheduled: ScheduledOrder[]
  /** Workspace tabs; each carries its own underlying/expiry/rows context. */
  tabs: HedgingWorkspaceTab[]
  activeTabId: string
}

/** A per-leg result row from POST /api/v1/multileg. */
export interface MultilegLegResult {
  legid: string | null
  symbol: string
  exchange: string
  action: string
  status: 'success' | 'error'
  orderid?: string
  message?: string
}

export interface MultilegResponse {
  status: 'success' | 'error'
  mode?: 'live' | 'analyze'
  message?: string
  results?: MultilegLegResult[]
  successful?: number
  total?: number
}

export interface MultilegLeg {
  legid: string
  symbol: string
  exchange: string
  action: OrderSide
  quantity: number
  pricetype: HedgingPricetype
  product: HedgingProduct
  price?: number
  trigger_price?: number
}

/** A side-panel summary chip row: counts + money for CALLS / PUTS / STOCKS. */
export interface SideSummary {
  total: number
  enabled: number
  positions: number
  pnl: number
}

export type { Order, Position, Trade }
