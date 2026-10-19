/**
 * The session range slider's pure side: where a quote sits between the
 * session's low and high, as 0..1. Null when the range cannot speak —
 * missing marks, an inverted or zero-width session (the pre-open flat line),
 * or a quote outside what the session printed (clamped onto the rail
 * instead: a tick past the high pins the needle at the end, it does not
 * vanish).
 */
export function rangePct(low: number, high: number, value: number): number | null {
  if (![low, high, value].every((n) => Number.isFinite(n))) return null
  if (high <= low) return null
  return Math.min(1, Math.max(0, (value - low) / (high - low)))
}

/**
 * Scale a sparkline's y-axis over its points AND its level lines, with a
 * little headroom, so a dotted entry/target/SL line is always on the canvas
 * even when the session's tape never reached it. Returns null when there is
 * nothing drawable (fewer than two points and no levels).
 */
export function sparkScale(
  points: readonly number[],
  levels: readonly number[]
): { min: number; max: number } | null {
  const finite = [...points, ...levels].filter((n) => Number.isFinite(n))
  if (finite.length < (points.length >= 2 ? 1 : 2)) return null
  let min = Math.min(...finite)
  let max = Math.max(...finite)
  if (min === max) {
    // A flat tape: open a band around the price so the line reads as a line.
    const pad = Math.abs(min) * 0.001 || 1
    min -= pad
    max += pad
  }
  const pad = (max - min) * 0.08
  return { min: min - pad, max: max + pad }
}
