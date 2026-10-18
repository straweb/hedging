import { describe, expect, it } from 'vitest'
import { strikeAccent } from './InstrumentPanel'

describe('strikeAccent', () => {
  it('paints the ATM solid amber on both sides', () => {
    expect(strikeAccent('CE', 0)).toBe('rgba(245, 158, 11, 0.95)')
    expect(strikeAccent('PE', 0)).toBe('rgba(245, 158, 11, 0.95)')
  })

  it('fades the green outward on the profit side', () => {
    // CE above ATM: dark at +1, lighter each step.
    expect(strikeAccent('CE', 1)).toBe('rgba(16, 185, 129, 0.92)')
    expect(strikeAccent('CE', 2)).toBe('rgba(16, 185, 129, 0.76)')
    expect(strikeAccent('CE', 5)).toBe('rgba(16, 185, 129, 0.28)')
    // PE below ATM mirrors it.
    expect(strikeAccent('PE', -1)).toBe('rgba(16, 185, 129, 0.92)')
    expect(strikeAccent('PE', -3)).toBe('rgba(16, 185, 129, 0.6)')
  })

  it('keeps a fading red on the ITM side', () => {
    expect(strikeAccent('CE', -1)).toBe('rgba(239, 68, 68, 0.92)')
    expect(strikeAccent('CE', -2)).toBe('rgba(239, 68, 68, 0.76)')
    expect(strikeAccent('PE', 1)).toBe('rgba(239, 68, 68, 0.92)')
  })

  it('clamps the fade so far strikes stay visible', () => {
    expect(strikeAccent('CE', 50)).toBe('rgba(16, 185, 129, 0.15)')
  })

  it('answers null before the ATM is known', () => {
    expect(strikeAccent('CE', null)).toBeNull()
  })
})
