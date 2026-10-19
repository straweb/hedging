import { describe, expect, it } from 'vitest'
import { rangePct, sparkScale } from './range'

describe('rangePct', () => {
  it('maps the quote onto 0..1 between the session marks', () => {
    expect(rangePct(100, 200, 100)).toBe(0)
    expect(rangePct(100, 200, 200)).toBe(1)
    expect(rangePct(100, 200, 175)).toBeCloseTo(0.75)
  })

  it('clamps a quote that poked past the marks', () => {
    expect(rangePct(100, 200, 220)).toBe(1)
    expect(rangePct(100, 200, 80)).toBe(0)
  })

  it('answers null when the range cannot speak', () => {
    expect(rangePct(Number.NaN, 200, 150)).toBeNull()
    expect(rangePct(100, Number.NaN, 150)).toBeNull()
    expect(rangePct(150, 150, 150)).toBeNull() // zero-width session
    expect(rangePct(200, 100, 150)).toBeNull() // inverted marks
  })
})

describe('sparkScale', () => {
  it('spans the points and the level lines with headroom', () => {
    const scale = sparkScale([100, 110, 105], [120, 95])
    expect(scale).not.toBeNull()
    // Levels outside the tape still land on the canvas.
    expect(scale!.min).toBeLessThan(95)
    expect(scale!.max).toBeGreaterThan(120)
  })

  it('opens a band around a flat tape', () => {
    const scale = sparkScale([100, 100, 100], [])
    expect(scale).not.toBeNull()
    expect(scale!.min).toBeLessThan(100)
    expect(scale!.max).toBeGreaterThan(100)
  })

  it('needs something drawable', () => {
    expect(sparkScale([], [])).toBeNull()
    expect(sparkScale([100], [])).toBeNull()
  })
})
