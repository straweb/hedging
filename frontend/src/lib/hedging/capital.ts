import type { CapitalBasis } from '@/types/hedging'

/**
 * The session's usable-capital ladder — the "house money" concept.
 *
 * Three bases, in the risk they let the trader take with a session's profit:
 *
 * - `initial` : the allocated capital alone. Realized losses reduce it (a
 *   losing morning shrinks the next deploy), but profits never add back, so
 *   the trader caps the session at the amount they chose at the start. The
 *   allocated amount itself is always clamped to available cash — a fixed
 *   allocation larger than the account cannot pretend to exist.
 * - `withPnl` : allocated capital plus the session's P&L (realized and
 *   unrealized). A winning session can compound; the trader who wants the
 *   profit recycled into new trades at any time picks this.
 * - `house`   : the first deployment of the session spends the allocated
 *   capital; every deployment after it may only spend the profit the session
 *   has realized so far. After the first trade the trader is playing with the
 *   house's money — a losing start means no further deployments, never a
 *   top-up.
 *
 * Losses and costs enter through the P&L the books report: a flattened
 * position's net result (brokerage and taxes included, as brokers report it)
 * is what the engine subtracts.
 */

export interface CapitalInputs {
  /** The allocated amount (fixed ₹ or % of cash, already resolved). */
  base: number
  /** Available cash from the funds snapshot. */
  cash: number
  /** Session P&L across all positions (realized + unrealized). */
  dayPnl: number
  /**
   * Realized session P&L — the net result of everything already closed.
   * Open positions' unrealized marks are excluded, so a floating profit
   * cannot be spent before it is booked.
   */
  realizedPnl: number
  /** Whether a deployment already ran this session (house basis). */
  deployedOnce: boolean
}

/** The allocated amount clamped to what the account actually holds. */
export function clampedBase(base: number, cash: number): number {
  const safeCash = Number.isFinite(cash) && cash > 0 ? cash : 0
  return Math.max(0, Math.min(base, safeCash))
}

/**
 * Usable trading capital for the session under the chosen basis.
 * Never negative; every basis keeps the allocation inside available cash.
 */
export function usableCapital(basis: CapitalBasis, inputs: CapitalInputs): number {
  const base = clampedBase(inputs.base, inputs.cash)
  const realized = Number.isFinite(inputs.realizedPnl) ? inputs.realizedPnl : 0
  const pnl = Number.isFinite(inputs.dayPnl) ? inputs.dayPnl : 0
  switch (basis) {
    case 'initial':
      // Losses shrink the session; profits do not grow it.
      return Math.max(0, base + Math.min(0, realized))
    case 'withPnl':
      // Profit recycles into the session at any time.
      return Math.max(0, base + pnl)
    case 'house':
      // First deployment spends the allocation; afterwards only realized profit.
      return inputs.deployedOnce ? Math.max(0, realized) : base
  }
}
