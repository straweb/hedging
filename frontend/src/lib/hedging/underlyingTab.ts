import {
  emptyTabContext,
  FNO_EXCHANGES,
  fnoExchangeFor,
  type HedgingWorkspaceTab,
  type TabKind,
} from '@/types/hedging'
import { newTabId } from './storage'

/**
 * What a top-bar underlying pick does to the workspace strip.
 *
 * The rule the terminal lives by: a pick from the picker rows always opens a
 * brand-new workspace tab for that market — never a re-dress of an older tab,
 * whose layout the trader may have rebuilt since. The one exception is the
 * auto-pick that lands a programmatic adoption: it adopts the pristine tab
 * that is active and empty (no rows, no underlying), so a scripted switch
 * does not litter the strip with a discarded tab.
 *
 * What the pick builds depends on the KIND the pick resolved to, not a
 * global switch: an F&O kind is born with the spot + legs layout and flagged
 * for auto-seed — when its option chain snapshot lands, the untouched panes
 * load the spot chart, the ATM call and the ATM put. A stock kind opens
 * single-pane and charts the stock itself.
 */

export type UnderlyingSource = 'user' | 'autopick'

export interface UnderlyingTabPlan {
  /** The full tab strip after the pick (adopted tab rewritten in place). */
  tabs: HedgingWorkspaceTab[]
  /** The tab the pick activates. */
  activeTabId: string
  /** The tab that should auto-seed spot + ATM CE/PE when its chain lands. */
  autoSeedTabId: string | null
}

/**
 * Resolve what a pick trades: a derivative exchange is F&O by definition; a
 * cash exchange (NSE/BSE) is F&O only when the symbol actually carries
 * derivatives — answered by whether its option-expiry listing came back
 * non-empty (`optionExpiries` null = the listing could not be asked, degrade
 * to a plain stock chart, which every symbol has).
 */
export function pickKind(
  pick: { symbol: string; exchange: string },
  optionExpiries: string[] | null
): TabKind {
  if (FNO_EXCHANGES.has(pick.exchange)) return 'fno'
  return optionExpiries != null && optionExpiries.length > 0 ? 'fno' : 'stock'
}

/** A tab name no other tab holds: "NIFTY", then "NIFTY 2", "NIFTY 3", … */
export function uniqueTabName(base: string, tabs: Array<{ name: string }>): string {
  const taken = new Set(tabs.map((tab) => tab.name))
  if (!taken.has(base)) return base
  for (let i = 2; ; i++) if (!taken.has(`${base} ${i}`)) return `${base} ${i}`
}

/** The pristine tab an auto-pick may adopt: the active one, brand-new empty. */
const adoptableTab = (previous: {
  tabs: HedgingWorkspaceTab[]
  activeTabId: string
}): HedgingWorkspaceTab | undefined =>
  previous.tabs.find(
    (tab) =>
      tab.id === previous.activeTabId &&
      tab.underlying === '' &&
      tab.options.length === 0 &&
      tab.stocks.length === 0
  )

export function planUnderlyingTabs(
  previous: { tabs: HedgingWorkspaceTab[]; activeTabId: string },
  pick: { symbol: string; exchange: string },
  kind: TabKind,
  source: UnderlyingSource = 'user'
): UnderlyingTabPlan {
  const adoptable = source === 'autopick' ? adoptableTab(previous) : undefined
  if (adoptable) {
    const rest = previous.tabs.filter((tab) => tab.id !== adoptable.id)
    const adopted: HedgingWorkspaceTab = {
      ...adoptable,
      name: uniqueTabName(pick.symbol, rest),
      layoutId: kind === 'fno' ? 'spotLegs' : 'single',
      kind,
      exchange: pick.exchange,
      underlying: pick.symbol,
      expiry: '',
      futuresExpiry: '',
    }
    return {
      tabs: previous.tabs.map((tab) => (tab.id === adoptable.id ? adopted : tab)),
      activeTabId: adopted.id,
      autoSeedTabId: kind === 'fno' ? adopted.id : null,
    }
  }

  const tab = emptyTabContext(
    newTabId(),
    uniqueTabName(pick.symbol, previous.tabs),
    kind === 'fno' ? 'spotLegs' : 'single',
    kind,
    // A stock pick keeps its cash exchange; an F&O pick trades on the home
    // derivative exchange of whatever the picker listed (BSE cash → BFO).
    kind === 'fno' ? fnoExchangeFor(pick.exchange) : pick.exchange
  )
  tab.underlying = pick.symbol
  return {
    tabs: [...previous.tabs, tab],
    activeTabId: tab.id,
    autoSeedTabId: kind === 'fno' ? tab.id : null,
  }
}
