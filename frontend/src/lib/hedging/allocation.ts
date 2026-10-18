import type { HedgingInstrument, SideMode, StrikeSide } from '@/types/hedging'

/**
 * The allocation engine: turns "this strike gets 20% of the side's capital"
 * into a live lot count.
 *
 * Rules the terminal commits to:
 * - Weights are per side (CALLS and PUTS normalize independently; in equity
 *   mode the stock list is one side). Enabled weights always sum to 100.
 * - Sliding one weight rescales the others proportionally, so the side keeps
 *   summing to 100 without the user arithmetic.
 * - lots(side, row) = floor(sideCapital x weight% / (ltp x lotSize)) — a row
 *   whose budget cannot afford one lot shows 0 lots and cannot deploy.
 * - Sides share the terminal capital by ceShare% / (100 - ceShare)%.
 * - Fixed mode replaces the percentage math: each row's fixedAmount is its
 *   budget in rupees.
 */

export const sideOf = (row: HedgingInstrument): StrikeSide | 'EQ' =>
  row.kind === 'option' ? (row.optionType ?? 'CE') : 'EQ'

export function isSameSide(a: StrikeSide | 'EQ', b: StrikeSide | 'EQ'): boolean {
  return a === b
}

/**
 * Scale a side's enabled weights so they sum to exactly 100 (rounded to one
 * decimal). Called after adds/removes/toggles so the side never drifts.
 */
export function normalizeWeights(rows: HedgingInstrument[]): HedgingInstrument[] {
  const groups = new Map<StrikeSide | 'EQ', number>()
  for (const row of rows) {
    if (!row.enabled) continue
    const side = sideOf(row)
    groups.set(side, (groups.get(side) ?? 0) + Math.max(0, row.weight))
  }
  return rows.map((row) => {
    if (!row.enabled) return row
    const side = sideOf(row)
    const total = groups.get(side) ?? 0
    if (total <= 0) return row
    const scaled = (Math.max(0, row.weight) / total) * 100
    return { ...row, weight: Math.round(scaled * 10) / 10 }
  })
}

/**
 * Slide one row's weight to `value` and rescale its side's other enabled rows
 * so the side still sums to 100. A row pushed to 100 collapses the rest
 * proportionally; a side with a single enabled row always sits at 100.
 */
export function slideWeight(
  rows: HedgingInstrument[],
  id: string,
  value: number
): HedgingInstrument[] {
  const target = rows.find((row) => row.id === id)
  if (!target) return rows
  const clamped = Math.min(100, Math.max(1, Math.round(value * 10) / 10))
  const side = sideOf(target)
  const others = rows.filter((row) => row.id !== id && row.enabled && isSameSide(sideOf(row), side))
  const othersTotal = others.reduce((sum, row) => sum + Math.max(0, row.weight), 0)
  return rows.map((row) => {
    if (row.id === id) return { ...row, weight: clamped }
    if (!row.enabled || !isSameSide(sideOf(row), side)) return row
    if (othersTotal <= 0) return { ...row, weight: 0 }
    const scaled = (Math.max(0, row.weight) / othersTotal) * (100 - clamped)
    return { ...row, weight: Math.round(scaled * 10) / 10 }
  })
}

/** Capital each side gets from the terminal capital, by ceShare. */
export function sideCapitals(capital: number, ceShare: number): Record<StrikeSide | 'EQ', number> {
  const share = Math.min(100, Math.max(0, ceShare))
  return { CE: (capital * share) / 100, PE: (capital * (100 - share)) / 100, EQ: capital }
}

/** Rupee budget for one row under the current side mode. */
export function rowBudget(row: HedgingInstrument, sideCapital: number, sideMode: SideMode): number {
  if (sideMode === 'fixed') return Math.max(0, row.fixedAmount)
  return (sideCapital * Math.max(0, row.weight)) / 100
}

/** Whole lots a row can afford at the given premium (0 when it cannot). */
export function lotsForBudget(budget: number, ltp: number, lotSize: number): number {
  const perLot = ltp * lotSize
  if (!Number.isFinite(perLot) || perLot <= 0 || budget <= 0) return 0
  return Math.floor(budget / perLot)
}

export interface LotComputation {
  /** Whole lots the row's budget affords right now. */
  lots: number
  /** Whole lots multiplied by lot size — the deploy quantity. */
  quantity: number
  /** Rupees the row will actually spend (lots x ltp x lotSize). */
  deployedAmount: number
  /** Budget minus deployed — what rounding left on the table. */
  unallocated: number
}

export function computeRowLots(
  row: HedgingInstrument,
  ltp: number | undefined,
  sideCapital: number,
  sideMode: SideMode
): LotComputation {
  const budget = rowBudget(row, sideCapital, sideMode)
  const premium = ltp ?? 0
  const lots = lotsForBudget(budget, premium, row.lotSize)
  const deployedAmount = lots * premium * row.lotSize
  return {
    lots,
    quantity: lots * row.lotSize,
    deployedAmount,
    unallocated: Math.max(0, budget - deployedAmount),
  }
}

/** One-lot notional at the given premium — the row's "1 lot price". */
export function oneLotPrice(ltp: number | undefined, lotSize: number): number {
  if (ltp === undefined || !Number.isFinite(ltp)) return 0
  return ltp * lotSize
}

/**
 * Total premium a deploy of this side would spend, using current LTPs.
 * Pass the side's own rows and that side's capital from sideCapitals().
 */
export function sideDeployedAmount(
  rows: HedgingInstrument[],
  ltpOf: (row: HedgingInstrument) => number | undefined,
  sideCapital: number,
  sideMode: SideMode
): number {
  return rows.reduce((sum, row) => {
    if (!row.enabled) return sum
    return sum + computeRowLots(row, ltpOf(row), sideCapital, sideMode).deployedAmount
  }, 0)
}
