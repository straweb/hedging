import { describe, expect, it } from 'vitest'
import type { HedgingInstrument } from '@/types/hedging'
import {
  computeRowLots,
  lotsForBudget,
  normalizeWeights,
  oneLotPrice,
  rowBudget,
  sideCapitals,
  sideDeployedAmount,
  sideOf,
  slideWeight,
} from './allocation'
import { makeOptionRow, makeStockRow } from './storage'

const niftyCE = (strike: number) =>
  makeOptionRow({
    symbol: `NIFTY28OCT26${strike}CE`,
    exchange: 'NFO',
    strike,
    optionType: 'CE',
    lotSize: 75,
  })
const niftyPE = (strike: number) =>
  makeOptionRow({
    symbol: `NIFTY28OCT26${strike}PE`,
    exchange: 'NFO',
    strike,
    optionType: 'PE',
    lotSize: 75,
  })

function withWeights(rows: HedgingInstrument[], weights: number[]): HedgingInstrument[] {
  return rows.map((row, index) => ({ ...row, weight: weights[index] ?? row.weight, enabled: true }))
}

describe('sideOf', () => {
  it('routes option rows to their CE/PE side', () => {
    expect(sideOf(niftyCE(26000))).toBe('CE')
    expect(sideOf(niftyPE(26000))).toBe('PE')
  })

  it('routes stock rows to the single equity side', () => {
    expect(sideOf(makeStockRow({ symbol: 'RELIANCE', exchange: 'NSE' }))).toBe('EQ')
  })
})

describe('normalizeWeights', () => {
  it('scales each side to 100 independently', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100), niftyPE(25000)], [30, 30, 50])
    const normalized = normalizeWeights(rows)
    const ce = normalized.filter((row) => row.optionType === 'CE')
    const pe = normalized.filter((row) => row.optionType === 'PE')
    expect(ce.reduce((sum, row) => sum + row.weight, 0)).toBeCloseTo(100, 1)
    expect(pe.reduce((sum, row) => sum + row.weight, 0)).toBeCloseTo(100, 1)
  })

  it('ignores disabled rows', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100)], [40, 60])
    rows[1].enabled = false
    const normalized = normalizeWeights(rows)
    expect(normalized[0].weight).toBe(100)
    expect(normalized[1].weight).toBe(60) // untouched while disabled
  })

  it('leaves rows alone when a side has no enabled weight mass', () => {
    const rows = withWeights([niftyCE(26000)], [0])
    expect(normalizeWeights(rows)[0].weight).toBe(0)
  })
})

describe('slideWeight', () => {
  it('rescales the side so the sum stays 100', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100)], [50, 50])
    const slid = slideWeight(rows, rows[0].id, 80)
    expect(slid[0].weight).toBe(80)
    expect(slid[1].weight).toBe(20)
  })

  it('rescales only the touched side', () => {
    const rows = withWeights([niftyCE(26000), niftyPE(25000)], [50, 50])
    const slid = slideWeight(rows, rows[0].id, 70)
    expect(slid[0].weight).toBe(70)
    expect(slid[1].weight).toBe(50) // PE untouched
  })

  it('pushing one row to 100 collapses the rest proportionally', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100), niftyCE(26200)], [40, 30, 30])
    const slid = slideWeight(rows, rows[0].id, 100)
    expect(slid[0].weight).toBe(100)
    expect(slid[1].weight).toBe(0)
    expect(slid[2].weight).toBe(0)
  })

  it('clamps absurd values', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100)], [50, 50])
    const slid = slideWeight(rows, rows[0].id, 5000)
    expect(slid[0].weight).toBe(100)
    expect(slid[1].weight).toBe(0)
  })

  it('keeps the other side and disabled rows untouched', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100), niftyPE(25000)], [40, 30, 30])
    rows[1].enabled = false
    const slid = slideWeight(rows, rows[0].id, 70)
    expect(slid[1].weight).toBe(30) // disabled: untouched
    expect(slid[2].weight).toBe(30) // other side: untouched
  })
})

describe('sideCapitals', () => {
  it('splits capital by ceShare', () => {
    const caps = sideCapitals(100000, 60)
    expect(caps.CE).toBe(60000)
    expect(caps.PE).toBe(40000)
    expect(caps.EQ).toBe(100000)
  })

  it('clamps the share', () => {
    expect(sideCapitals(100000, 150).CE).toBe(100000)
    expect(sideCapitals(100000, -5).PE).toBe(100000)
  })
})

describe('lots', () => {
  it('floors to whole lots', () => {
    // 50000 budget / (250 x 75) = 2.66 lots -> 2
    expect(lotsForBudget(50000, 250, 75)).toBe(2)
    expect(lotsForBudget(18750, 250, 75)).toBe(1)
    expect(lotsForBudget(1000, 250, 75)).toBe(0)
  })

  it('guards non-finite and non-positive inputs', () => {
    expect(lotsForBudget(50000, 0, 75)).toBe(0)
    expect(lotsForBudget(50000, NaN, 75)).toBe(0)
    expect(lotsForBudget(0, 250, 75)).toBe(0)
  })

  it('computes the row budget from weight in percentage mode', () => {
    const row = { ...niftyCE(26000), weight: 25 }
    expect(rowBudget(row, 80000, 'percentage')).toBe(20000)
  })

  it('computes the row budget from fixedAmount in fixed mode', () => {
    const row = { ...niftyCE(26000), fixedAmount: 15000, weight: 25 }
    expect(rowBudget(row, 80000, 'fixed')).toBe(15000)
  })

  it('computeRowLots reports quantity, spend and rounding remainder', () => {
    const row = { ...niftyCE(26000), weight: 50, lotSize: 75 }
    const result = computeRowLots(row, 200, 30000, 'percentage')
    // budget 15000 / (200 x 75 = 15000) -> exactly 1 lot
    expect(result.lots).toBe(1)
    expect(result.quantity).toBe(75)
    expect(result.deployedAmount).toBe(15000)
    expect(result.unallocated).toBe(0)
  })

  it('computeRowLots returns zero lots without an LTP, budget fully unallocated', () => {
    const row = { ...niftyCE(26000), weight: 50 }
    const result = computeRowLots(row, undefined, 30000, 'percentage')
    expect(result.lots).toBe(0)
    expect(result.quantity).toBe(0)
    expect(result.deployedAmount).toBe(0)
    expect(result.unallocated).toBe(15000)
  })
})

describe('oneLotPrice', () => {
  it('multiplies ltp by lot size', () => {
    expect(oneLotPrice(250, 75)).toBe(18750)
    expect(oneLotPrice(undefined, 75)).toBe(0)
  })
})

describe('sideDeployedAmount', () => {
  it('sums only enabled rows under percentage mode', () => {
    const rows = withWeights([niftyCE(26000), niftyCE(26100)], [50, 50])
    rows[1].enabled = false
    const total = sideDeployedAmount(
      rows,
      (row) => (row.strike === 26000 ? 200 : 100),
      30000,
      'percentage'
    )
    expect(total).toBe(15000)
  })

  it('sums fixed budgets in fixed mode', () => {
    // 30000 budget at 200 premium x 75 lot = exactly 2 lots -> 30000 deployed.
    const rows = withWeights([niftyCE(26000)], [100]).map((row) => ({ ...row, fixedAmount: 30000 }))
    const total = sideDeployedAmount(rows, () => 200, 30000, 'fixed')
    expect(total).toBe(30000)
  })

  it('fixed budget below one lot deploys nothing', () => {
    const rows = withWeights([niftyCE(26000)], [100]).map((row) => ({ ...row, fixedAmount: 5000 }))
    const total = sideDeployedAmount(rows, () => 200, 30000, 'fixed')
    expect(total).toBe(0)
  })
})
