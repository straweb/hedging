import { describe, expect, it } from 'vitest'
import { emptyTabContext, type HedgingWorkspaceTab } from '@/types/hedging'
import { pickKind, planUnderlyingTabs, uniqueTabName } from './underlyingTab'

const tab = (over: Partial<HedgingWorkspaceTab> & { id: string }): HedgingWorkspaceTab => ({
  ...emptyTabContext(over.id, over.id.toUpperCase(), 'single', 'fno', 'NFO'),
  ...over,
})

const NIFTY = { symbol: 'NIFTY', exchange: 'NFO' }
const SENSEX = { symbol: 'SENSEX', exchange: 'BFO' }

describe('pickKind', () => {
  it('calls a derivative exchange F&O by definition', () => {
    expect(pickKind(NIFTY, null)).toBe('fno')
    expect(pickKind({ symbol: 'CRUDEOIL', exchange: 'MCX' }, [])).toBe('fno')
  })

  it('asks the cash exchange option expiries for the answer', () => {
    // RELIANCE with listed option expiries is an F&O stock.
    expect(pickKind({ symbol: 'RELIANCE', exchange: 'NSE' }, ['26FEB26'])).toBe('fno')
    // A symbol the derivative exchange does not know is a plain stock.
    expect(pickKind({ symbol: 'SOMEPENNYSTOCK', exchange: 'NSE' }, [])).toBe('stock')
  })

  it('degrades to a stock when the expiry ask could not be made', () => {
    // null = the listing errored (master contract still downloading, say):
    // a plain chart every symbol has beats a dead options board.
    expect(pickKind({ symbol: 'RELIANCE', exchange: 'NSE' }, null)).toBe('stock')
  })
})

describe('planUnderlyingTabs', () => {
  it('opens a brand-new F&O workspace for a user pick, spot+legs layout, active', () => {
    const strip = { tabs: [tab({ id: 'ws-1', underlying: 'BANKNIFTY' })], activeTabId: 'ws-1' }
    const plan = planUnderlyingTabs(strip, NIFTY, 'fno', 'user')
    expect(plan.tabs).toHaveLength(2)
    const opened = plan.tabs[1]
    expect(plan.activeTabId).toBe(opened.id)
    expect(opened.id).not.toBe('ws-1')
    expect(opened.name).toBe('NIFTY')
    expect(opened.kind).toBe('fno')
    expect(opened.layoutId).toBe('spotLegs')
    expect(opened.exchange).toBe('NFO')
    expect(opened.underlying).toBe('NIFTY')
    expect(opened.expiry).toBe('')
    expect(opened.futuresExpiry).toBe('')
    expect(plan.autoSeedTabId).toBe(opened.id)
  })

  it('never reuses an older tab — the same pick twice yields two workspaces', () => {
    const first = planUnderlyingTabs(
      { tabs: [tab({ id: 'ws-1', underlying: 'NIFTY' })], activeTabId: 'ws-1' },
      NIFTY,
      'fno',
      'user'
    )
    const second = planUnderlyingTabs(
      { tabs: first.tabs, activeTabId: first.activeTabId },
      NIFTY,
      'fno',
      'user'
    )
    expect(second.tabs).toHaveLength(3)
    expect(second.tabs[1].id).not.toBe(second.tabs[2].id)
    expect(second.tabs[2].name).toBe('NIFTY 2')
    expect(second.activeTabId).toBe(second.tabs[2].id)
  })

  it('adopts the pristine active tab on an autopick after an exchange switch', () => {
    const cleared = tab({ id: 'ws-2', exchange: 'BFO', underlying: '' })
    const strip = {
      tabs: [tab({ id: 'ws-1', underlying: 'NIFTY', options: [] }), cleared],
      activeTabId: 'ws-2',
    }
    const plan = planUnderlyingTabs(strip, SENSEX, 'fno', 'autopick')
    expect(plan.tabs).toHaveLength(2)
    const adopted = plan.tabs.find((item) => item.id === 'ws-2')!
    expect(adopted.name).toBe('SENSEX')
    expect(adopted.kind).toBe('fno')
    expect(adopted.layoutId).toBe('spotLegs')
    expect(adopted.exchange).toBe('BFO')
    expect(adopted.underlying).toBe('SENSEX')
    expect(plan.activeTabId).toBe('ws-2')
    expect(plan.autoSeedTabId).toBe('ws-2')
  })

  it('never adopts a tab that carries rows or a market, even on autopick', () => {
    const busy = tab({ id: 'ws-1', underlying: 'BANKNIFTY' })
    const plan = planUnderlyingTabs(
      { tabs: [busy], activeTabId: 'ws-1' },
      SENSEX,
      'fno',
      'autopick'
    )
    expect(plan.tabs).toHaveLength(2)
    expect(plan.tabs[0].id).toBe('ws-1')
    expect(plan.tabs[0].underlying).toBe('BANKNIFTY')
    expect(plan.tabs[1].underlying).toBe('SENSEX')
    expect(plan.activeTabId).toBe(plan.tabs[1].id)
  })

  it('keeps a user pick from adopting, even when the active tab is pristine', () => {
    const cleared = tab({ id: 'ws-1', underlying: '' })
    const plan = planUnderlyingTabs({ tabs: [cleared], activeTabId: 'ws-1' }, NIFTY, 'fno', 'user')
    expect(plan.tabs).toHaveLength(2)
    expect(plan.tabs[0].id).toBe('ws-1')
    expect(plan.tabs[0].underlying).toBe('')
  })

  it('opens stock picks single-pane on their cash exchange with no auto-seed', () => {
    const plan = planUnderlyingTabs(
      {
        tabs: [tab({ id: 'ws-1', exchange: 'NSE', kind: 'stock', underlying: '' })],
        activeTabId: 'ws-1',
      },
      { symbol: 'RELIANCE', exchange: 'NSE' },
      'stock',
      'user'
    )
    const opened = plan.tabs[1]
    expect(opened.layoutId).toBe('single')
    expect(opened.kind).toBe('stock')
    expect(opened.exchange).toBe('NSE')
    expect(opened.underlying).toBe('RELIANCE')
    expect(plan.autoSeedTabId).toBeNull()
  })

  it('re-homes an F&O pick found on a cash exchange to its derivative exchange', () => {
    // A search hit can hand over NSE:RELIANCE for an F&O stock; the board
    // must trade on NFO (its derivatives), not the cash listing.
    const plan = planUnderlyingTabs(
      { tabs: [tab({ id: 'ws-1', underlying: 'BANKNIFTY' })], activeTabId: 'ws-1' },
      { symbol: 'RELIANCE', exchange: 'NSE' },
      'fno',
      'user'
    )
    const opened = plan.tabs[1]
    expect(opened.kind).toBe('fno')
    expect(opened.exchange).toBe('NFO')
    expect(opened.underlying).toBe('RELIANCE')
  })

  it('defaults the source to a user pick', () => {
    const plan = planUnderlyingTabs(
      { tabs: [tab({ id: 'ws-1', underlying: '' })], activeTabId: 'ws-1' },
      NIFTY,
      'fno'
    )
    expect(plan.tabs).toHaveLength(2)
  })
})

describe('uniqueTabName', () => {
  it('passes a free name through and numbers the collisions', () => {
    expect(uniqueTabName('NIFTY', [])).toBe('NIFTY')
    expect(uniqueTabName('NIFTY', [{ name: 'NIFTY' }, { name: 'NIFTY 2' }, { name: 'W1' }])).toBe(
      'NIFTY 3'
    )
  })
})
