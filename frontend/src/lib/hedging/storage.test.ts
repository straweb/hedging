import { afterEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_STATE,
  HEDGING_STORAGE_KEY,
  hydrateState,
  loadHedgingState,
  makeOptionRow,
  makeStockRow,
  newTabId,
  saveHedgingState,
} from './storage'

class MemoryStorage {
  private map = new Map<string, string>()
  getItem(key: string) {
    return this.map.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.map.set(key, value)
  }
  removeItem(key: string) {
    this.map.delete(key)
  }
}

afterEach(() => {
  localStorage.clear()
})

describe('hydrateState', () => {
  it('returns defaults for empty input', () => {
    const state = hydrateState(null)
    expect(state.mode).toBe('fno')
    expect(state.tabs).toEqual(DEFAULT_STATE.tabs)
    expect(state.tabs[0].options).toEqual([])
    expect(state.tabs[0].underlying).toBe('NIFTY')
  })

  it('merges a partial snapshot over defaults', () => {
    const state = hydrateState({ mode: 'equity', capital: 250000 })
    expect(state.mode).toBe('equity')
    expect(state.capital).toBe(250000)
  })

  it('repairs malformed fields', () => {
    const state = hydrateState({
      capital: -5,
      capitalPercent: 400,
      ceShare: 900,
      mode: 'nonsense',
      pricetype: 'ICEBERG',
      capitalBasis: 'nonsense',
      tabs: [],
    })
    expect(state.capital).toBe(DEFAULT_STATE.capital)
    expect(state.capitalPercent).toBe(100)
    expect(state.ceShare).toBe(100)
    expect(state.mode).toBe('fno')
    expect(state.pricetype).toBe('MARKET')
    expect(state.capitalBasis).toBe('initial')
    expect(state.tabs.length).toBeGreaterThan(0)
  })

  it('defaults the product to NRML in F&O and MIS in equity', () => {
    expect(hydrateState({}).product).toBe('NRML')
    expect(hydrateState({ mode: 'equity' }).product).toBe('NRML') // stored value wins
    expect(hydrateState({ product: 'MIS' }).product).toBe('MIS')
  })

  it('keeps instrument rows inside the tab that carries them', () => {
    const good = makeOptionRow({
      symbol: 'NIFTY28OCT2626000CE',
      exchange: 'NFO',
      strike: 26000,
      optionType: 'CE',
      lotSize: 75,
    })
    const state = hydrateState({
      tabs: [
        {
          id: 'a',
          name: 'A',
          layoutId: 'single',
          exchange: 'NFO',
          underlying: 'NIFTY',
          expiry: '',
          futuresExpiry: '',
          options: [good, null, { symbol: 42 }, { exchange: 'NFO' }],
          stocks: [],
        },
      ],
      activeTabId: 'a',
    })
    const tab = state.tabs[0]
    expect(tab.options).toHaveLength(1)
    expect(tab.options[0].symbol).toBe('NIFTY28OCT2626000CE')
    expect(tab.underlying).toBe('NIFTY')
  })

  it('falls back to the first tab when activeTabId is stale', () => {
    const state = hydrateState({
      tabs: [
        { id: 'a', name: 'A', layoutId: 'single' },
        { id: 'b', name: 'B', layoutId: 'grid4' },
      ],
      activeTabId: 'missing',
    })
    expect(state.activeTabId).toBe('a')
  })

  it('migrates a v1 snapshot: capitalMode becomes source + initial basis', () => {
    const v1 = {
      version: 1,
      mode: 'fno',
      exchange: 'NFO',
      underlying: 'NIFTY',
      expiry: '',
      capital: 150000,
      capitalMode: 'percent',
      capitalPercent: 40,
      ceShare: 50,
      sideMode: 'percentage',
      product: 'MIS',
      pricetype: 'MARKET',
      stocks: [],
      options: [],
      tabs: [{ id: 'ws-1', name: 'W1', layoutId: 'single' }],
      activeTabId: 'ws-1',
    }
    const state = hydrateState(v1)
    expect(state.capitalSource).toBe('percent')
    expect(state.capitalBasis).toBe('initial')
    expect(state.capitalPercent).toBe(40)
    expect(state.scheduled).toEqual([])
  })

  it('migrates a v2 snapshot: the globals land in the active tab', () => {
    const option = makeOptionRow({
      symbol: 'NIFTY28OCT2626000CE',
      exchange: 'NFO',
      strike: 26000,
      optionType: 'CE',
      lotSize: 75,
    })
    const state = hydrateState({
      version: 2,
      exchange: 'NFO',
      underlying: 'BANKNIFTY',
      expiry: '26FEB26',
      futuresExpiry: '26FEB26',
      options: [option],
      stocks: [],
      tabs: [
        { id: 'ws-1', name: 'W1', layoutId: 'single' },
        { id: 'ws-2', name: 'W2', layoutId: 'grid4' },
      ],
      activeTabId: 'ws-2',
    })
    expect(state.version).toBe(3)
    const active = state.tabs.find((tab) => tab.id === 'ws-2')
    expect(active?.underlying).toBe('BANKNIFTY')
    expect(active?.expiry).toBe('26FEB26')
    expect(active?.options).toHaveLength(1)
    // The other tab starts empty rather than silently trading that market.
    const other = state.tabs.find((tab) => tab.id === 'ws-1')
    expect(other?.underlying).toBe('')
    expect(other?.options).toHaveLength(0)
  })

  it('keeps scheduled rows through a round-trip and drops junk ones', () => {
    const good = {
      id: 'sch-1',
      action: 'deployA',
      trigger: { kind: 'time', at: '09:20:30', repeat: 'once' },
      status: 'pending',
      createdAt: 1,
      modeAtCreation: 'fno',
      target: 'stock',
    }
    const planned = {
      ...good,
      id: 'sch-2',
      action: 'deployB',
      target: 'options',
      trigger: {
        kind: 'price',
        symbol: 'NIFTY',
        exchange: 'NSE',
        condition: 'above',
        price: 24500,
      },
      deploy: {
        side: 'CE',
        strikes: [24500, 24600],
        optionAction: 'BUY',
        target: 24700,
        stop: 24350,
        spot: { symbol: 'NIFTY', exchange: 'NSE' },
      },
    }
    const state = hydrateState({
      scheduled: [good, planned, { id: 'x' }, null, { ...good, action: 'moonshot' }],
    })
    expect(state.scheduled).toHaveLength(2)
    expect(state.scheduled[0].id).toBe('sch-1')
    expect(state.scheduled[0].target).toBe('stock')
    expect(state.scheduled[0].trigger).toEqual({
      kind: 'time',
      at: '09:20:30',
      repeat: 'once',
    })
    const withPlan = state.scheduled[1]
    expect(withPlan.deploy?.strikes).toEqual([24500, 24600])
    expect(withPlan.trigger).toMatchObject({ condition: 'above', price: 24500 })
  })
})

describe('round-trip', () => {
  it('restores what was saved, tab contexts included', () => {
    const storage = new MemoryStorage()
    const options = [
      makeOptionRow({
        symbol: 'NIFTY28OCT2626000CE',
        exchange: 'NFO',
        strike: 26000,
        optionType: 'CE',
        lotSize: 75,
      }),
      makeOptionRow({
        symbol: 'NIFTY28OCT2626000PE',
        exchange: 'NFO',
        strike: 26000,
        optionType: 'PE',
        lotSize: 75,
      }),
    ]
    const stocks = [makeStockRow({ symbol: 'RELIANCE', exchange: 'NSE' })]
    const tab = {
      ...DEFAULT_STATE.tabs[0],
      underlying: 'NIFTY',
      exchange: 'NFO',
      options,
      stocks,
    }
    const state = hydrateState({
      ...DEFAULT_STATE,
      tabs: [tab],
      activeTabId: tab.id,
      capital: 500000,
      ceShare: 70,
    })
    saveHedgingState(state, storage)
    const restored = loadHedgingState(storage)
    expect(restored.capital).toBe(500000)
    expect(restored.ceShare).toBe(70)
    expect(restored.tabs[0].options.map((row) => row.symbol)).toEqual([
      'NIFTY28OCT2626000CE',
      'NIFTY28OCT2626000PE',
    ])
    expect(restored.tabs[0].stocks[0].symbol).toBe('RELIANCE')
    expect(restored.tabs[0].underlying).toBe('NIFTY')
  })

  it('survives corrupt JSON', () => {
    const storage = new MemoryStorage()
    storage.setItem(HEDGING_STORAGE_KEY, '{not json')
    const state = loadHedgingState(storage)
    expect(state.mode).toBe(DEFAULT_STATE.mode)
  })

  it('survives a refusing storage', () => {
    const refusing = {
      getItem: () => null,
      setItem: () => {
        throw new Error('quota')
      },
    }
    expect(() => saveHedgingState(DEFAULT_STATE, refusing)).not.toThrow()
  })

  it('returns defaults when the key is absent', () => {
    expect(loadHedgingState(new MemoryStorage())).toEqual({
      ...DEFAULT_STATE,
      tabs: [DEFAULT_STATE.tabs[0]],
    })
  })
})

describe('ids', () => {
  it('builds unique tab ids', () => {
    const seen = new Set<string>()
    for (let i = 0; i < 50; i++) seen.add(newTabId())
    expect(seen.size).toBe(50)
  })

  it('ids derive from symbol and exchange', () => {
    const row = makeOptionRow({
      symbol: 'NIFTY28OCT2626000CE',
      exchange: 'NFO',
      strike: 26000,
      optionType: 'CE',
      lotSize: 75,
    })
    expect(row.id).toBe('option:NFO:NIFTY28OCT2626000CE')
  })
})
