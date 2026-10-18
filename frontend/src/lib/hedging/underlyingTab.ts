import { emptyTabContext, type HedgingWorkspaceTab } from '@/types/hedging'
import { newTabId } from './storage'

/**
 * What a top-bar underlying pick does to the workspace strip.
 *
 * The rule the terminal lives by: a pick from the picker rows always opens a
 * brand-new workspace tab for that market — never a re-dress of an older tab,
 * whose layout the trader may have rebuilt since. The one exception is the
 * auto-pick that lands an exchange or mode switch: it adopts the pristine tab
 * that switch just cleared (no rows, no underlying), so the strip does not
 * accumulate a discarded tab every time the trader moves between exchanges.
 *
 * An F&O tab is born with the spot + legs layout and flagged for auto-seed:
 * when its option chain snapshot lands, the untouched panes load the spot
 * chart, the ATM call and the ATM put. Equity tabs open single-pane and chart
 * the stock itself.
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
  fnoMode: boolean,
  source: UnderlyingSource = 'user'
): UnderlyingTabPlan {
  const adoptable = source === 'autopick' ? adoptableTab(previous) : undefined
  if (adoptable) {
    const rest = previous.tabs.filter((tab) => tab.id !== adoptable.id)
    const adopted: HedgingWorkspaceTab = {
      ...adoptable,
      name: uniqueTabName(pick.symbol, rest),
      layoutId: fnoMode ? 'spotLegs' : 'single',
      exchange: pick.exchange,
      underlying: pick.symbol,
      expiry: '',
      futuresExpiry: '',
    }
    return {
      tabs: previous.tabs.map((tab) => (tab.id === adoptable.id ? adopted : tab)),
      activeTabId: adopted.id,
      autoSeedTabId: fnoMode ? adopted.id : null,
    }
  }

  const tab = emptyTabContext(
    newTabId(),
    uniqueTabName(pick.symbol, previous.tabs),
    fnoMode ? 'spotLegs' : 'single',
    pick.exchange
  )
  tab.underlying = pick.symbol
  return {
    tabs: [...previous.tabs, tab],
    activeTabId: tab.id,
    autoSeedTabId: fnoMode ? tab.id : null,
  }
}
