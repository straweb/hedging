import { describe, expect, it } from 'vitest'
import { indexDeploySummary, levelCrosses, profitCross, strikesForPick } from './indexLevels'

describe('strikesForPick', () => {
  const atm = 24500
  const interval = 50

  it('picks the ATM strike alone', () => {
    expect(strikesForPick({ mode: 'atm' }, atm, interval)).toEqual([24500])
  })

  it('counts strikes strictly above and below the ATM', () => {
    expect(strikesForPick({ mode: 'above', count: 3 }, atm, interval)).toEqual([
      24550, 24600, 24650,
    ])
    expect(strikesForPick({ mode: 'below', count: 2 }, atm, interval)).toEqual([24400, 24450])
  })

  it('clamps the count into the 1..7 rail', () => {
    expect(strikesForPick({ mode: 'above', count: 99 }, atm, interval)).toHaveLength(7)
    expect(strikesForPick({ mode: 'below', count: 0 }, atm, interval)).toHaveLength(1)
  })

  it('dedupes, drops junk and sorts custom strikes', () => {
    expect(
      strikesForPick({ mode: 'custom', strikes: [24700, 24500, 24700, -5, 0] }, atm, interval)
    ).toEqual([24500, 24700])
    expect(strikesForPick({ mode: 'custom' }, atm, interval)).toEqual([])
  })

  it('answers nothing without an anchor or interval', () => {
    expect(strikesForPick({ mode: 'atm' }, null, interval)).toEqual([])
    expect(strikesForPick({ mode: 'above', count: 2 }, atm, 0)).toEqual([])
  })
})

describe('profitCross / levelCrosses', () => {
  it('long CE and short PE profit when the index rises', () => {
    expect(profitCross('CE', 'BUY')).toBe('above')
    expect(profitCross('PE', 'SELL')).toBe('above')
  })

  it('long PE and short CE profit when the index falls', () => {
    expect(profitCross('PE', 'BUY')).toBe('below')
    expect(profitCross('CE', 'SELL')).toBe('below')
  })

  it('mirrors the stop against the target', () => {
    expect(levelCrosses('CE', 'BUY')).toEqual({ target: 'above', stop: 'below' })
    expect(levelCrosses('PE', 'BUY')).toEqual({ target: 'below', stop: 'above' })
  })
})

describe('indexDeploySummary', () => {
  it('summarises an armed plan in one line', () => {
    const text = indexDeploySummary({
      side: 'CE',
      optionAction: 'BUY',
      strikes: [24500, 24550],
      entry: 24500,
      target: 24700,
      stop: 24350,
      spotSymbol: 'NIFTY',
    })
    expect(text).toContain('Buy 2 × CE')
    expect(text).toContain('enter when NIFTY ≥ 24,500')
    expect(text).toContain('exit when NIFTY ≥ 24,700')
    expect(text).toContain('stop when NIFTY ≤ 24,350')
  })

  it('reads "enter now" without an entry and names a PE plan downwards', () => {
    const text = indexDeploySummary({
      side: 'PE',
      optionAction: 'BUY',
      strikes: [24450],
      target: 24300,
      stop: 24650,
    })
    expect(text).toContain('enter now')
    expect(text).toContain('exit when index ≤ 24,300')
    expect(text).toContain('stop when index ≥ 24,650')
  })
})
