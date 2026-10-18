import { describe, expect, it } from 'vitest'
import {
  constituentsFor,
  contributionLedger,
  normalizedWeights,
  weightedIndexMove,
} from './indexConstituents'

describe('constituentsFor', () => {
  it('finds the big four Indian indices', () => {
    expect(constituentsFor('NIFTY')?.label).toBe('Nifty 50')
    expect(constituentsFor('BANKNIFTY')?.label).toBe('Bank Nifty')
    expect(constituentsFor('SENSEX')?.stockExchange).toBe('BSE')
    expect(constituentsFor('FINNIFTY')?.stockExchange).toBe('NSE')
  })

  it('matches case-insensitively and trims', () => {
    expect(constituentsFor(' nifty ')?.underlying).toBe('NIFTY')
    expect(constituentsFor('banknifty')?.constituents.length).toBeGreaterThan(0)
  })

  it('answers null for commodities and unknown underlyings', () => {
    expect(constituentsFor('CRUDEOIL')).toBeNull()
    expect(constituentsFor('NATURALGAS')).toBeNull()
    expect(constituentsFor('GOLD')).toBeNull()
    expect(constituentsFor('SILVER')).toBeNull()
    expect(constituentsFor('NOTANINDEX')).toBeNull()
    expect(constituentsFor('')).toBeNull()
  })

  it('every profile has constituents and positive weights', () => {
    for (const underlying of ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY']) {
      const profile = constituentsFor(underlying)
      expect(profile).not.toBeNull()
      expect(profile?.constituents.length).toBeGreaterThanOrEqual(12)
      for (const row of profile?.constituents ?? []) {
        expect(row.weight).toBeGreaterThan(0)
        expect(row.symbol).toMatch(/^[A-Z0-9&\-.]+$/)
      }
    }
  })

  it('nifty carries the fifty-name set with the heavyweights on top', () => {
    const profile = constituentsFor('NIFTY')
    expect(profile?.constituents).toHaveLength(50)
    expect(profile?.constituents[0].symbol).toBe('HDFCBANK')
    const symbols = new Set(profile?.constituents.map((row) => row.symbol))
    for (const heavyweight of ['RELIANCE', 'ICICIBANK', 'INFY', 'TCS', 'ITC']) {
      expect(symbols.has(heavyweight)).toBe(true)
    }
  })
})

describe('normalizedWeights', () => {
  it('rescales the rounded published weights to sum exactly 100', () => {
    const profile = constituentsFor('BANKNIFTY')
    const weights = normalizedWeights(profile as NonNullable<ReturnType<typeof constituentsFor>>)
    let total = 0
    for (const value of weights.values()) total += value
    expect(total).toBeCloseTo(100, 6)
    // The heaviest bank stays the heaviest after normalization.
    expect(weights.get('HDFCBANK')).toBeGreaterThan(weights.get('PNB') ?? 0)
  })

  it('keeps relative proportions', () => {
    const profile = constituentsFor('SENSEX') as NonNullable<ReturnType<typeof constituentsFor>>
    const weights = normalizedWeights(profile)
    const raw = profile.constituents.find((row) => row.symbol === 'HDFCBANK')?.weight ?? 0
    const rawRel = profile.constituents.find((row) => row.symbol === 'RELIANCE')?.weight ?? 0
    const ratio = raw / rawRel
    expect((weights.get('HDFCBANK') ?? 0) / (weights.get('RELIANCE') ?? 0)).toBeCloseTo(ratio, 6)
  })
})

describe('contributionLedger', () => {
  const profile = constituentsFor('NIFTY') as NonNullable<ReturnType<typeof constituentsFor>>

  it('sums the points to the weighted index move', () => {
    const pct = new Map([
      ['HDFCBANK', 1.0],
      ['RELIANCE', -0.5],
      ['INFY', 0.2],
    ])
    const ledger = contributionLedger(profile, pct)
    const totalPoints = ledger.reduce((sum, row) => sum + row.points, 0)
    expect(totalPoints).toBeCloseTo(weightedIndexMove(profile, pct), 9)
  })

  it('splits the move into shares that sum to 100', () => {
    const pct = new Map([
      ['HDFCBANK', 2.0],
      ['RELIANCE', 1.0],
      ['TCS', -0.4],
    ])
    const ledger = contributionLedger(profile, pct)
    const totalShare = ledger.reduce((sum, row) => sum + row.share, 0)
    expect(totalShare).toBeCloseTo(100, 6)
    // HDFCBank is the heaviest weight and the strongest mover — biggest share.
    const top = ledger.reduce((max, row) => (row.share > max.share ? row : max))
    expect(top.symbol).toBe('HDFCBANK')
    expect(top.share).toBeGreaterThan(50)
  })

  it('keeps the sign of each stock on its share', () => {
    // HDFC pushes up harder than Reliance drags — the day nets positive.
    const pct = new Map([
      ['HDFCBANK', 3.0],
      ['RELIANCE', -1.0],
    ])
    const ledger = contributionLedger(profile, pct)
    const hdfc = ledger.find((row) => row.symbol === 'HDFCBANK')
    const rel = ledger.find((row) => row.symbol === 'RELIANCE')
    expect(hdfc?.share).toBeGreaterThan(0)
    expect(rel?.share).toBeLessThan(0)
  })

  it('gives a positive contributor a negative share on a net-down day', () => {
    const pct = new Map([
      ['HDFCBANK', 1.0],
      ['RELIANCE', -3.0],
    ])
    const ledger = contributionLedger(profile, pct)
    const hdfc = ledger.find((row) => row.symbol === 'HDFCBANK')
    expect(hdfc?.points).toBeGreaterThan(0)
    expect(hdfc?.share).toBeLessThan(0) // the move it opposed was downward
  })

  it('falls back to absolute magnitudes when the index is flat', () => {
    // Equal and opposite heavyweights cancel the index move exactly.
    const pct = new Map([
      ['HDFCBANK', 1.0],
      ['RELIANCE', -(13.2 / 9.0)], // cancels HDFC's move weight-for-weight
    ])
    const ledger = contributionLedger(profile, pct)
    const total = ledger.reduce((sum, row) => sum + row.points, 0)
    expect(Math.abs(total)).toBeLessThan(0.01)
    // Shares are now against the absolute sum: magnitudes form a full
    // ledger even though the signed total cancels to zero.
    const totalAbsShare = ledger.reduce((sum, row) => sum + Math.abs(row.share), 0)
    expect(totalAbsShare).toBeCloseTo(100, 6)
  })

  it('reports zero share on an all-flat board', () => {
    const ledger = contributionLedger(profile, new Map())
    for (const row of ledger) {
      expect(row.points).toBe(0)
      expect(row.share).toBe(0)
    }
  })

  it('treats missing quotes as flat, not NaN', () => {
    const pct = new Map([['HDFCBANK', 1.5]])
    const ledger = contributionLedger(profile, pct)
    for (const row of ledger) {
      expect(Number.isFinite(row.points)).toBe(true)
      expect(Number.isFinite(row.share)).toBe(true)
    }
    const hdfc = ledger.find((row) => row.symbol === 'HDFCBANK')
    expect(hdfc?.points).toBeGreaterThan(0)
  })
})

describe('weightedIndexMove', () => {
  it('equals the index move when every stock moves with the index', () => {
    const profile = constituentsFor('NIFTY') as NonNullable<ReturnType<typeof constituentsFor>>
    const pct = new Map(profile.constituents.map((row) => [row.symbol, 0.75]))
    expect(weightedIndexMove(profile, pct)).toBeCloseTo(0.75, 9)
  })

  it('answers 0 on an empty quote board', () => {
    const profile = constituentsFor('BANKNIFTY') as NonNullable<ReturnType<typeof constituentsFor>>
    expect(weightedIndexMove(profile, new Map())).toBe(0)
  })
})
