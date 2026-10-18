/**
 * Static index constituent profiles for the hedging terminal's Index tab.
 *
 * The lists mirror the publicly reported free-float compositions (weights
 * approximately as of late 2025). Index sets rebalance quarterly and weights
 * drift daily, so every consumer treats these numbers as indicative — the
 * contribution view says so on screen. Each profile maps the OpenAlgo
 * underlying symbol (NIFTY, BANKNIFTY, …) to its cash-market constituents,
 * which is what the Index tab subscribes to for live price / volume quotes.
 *
 * Commodity underlyings (CRUDEOIL, NATURALGAS, GOLD, SILVER) have no
 * constituent set — `constituentsFor` answers null and the tab shows an
 * empty state instead of pretending.
 */

export interface Constituent {
  /** OpenAlgo / broker cash-market symbol (e.g. RELIANCE). */
  symbol: string
  /** Free-float weight in the index, in percent — approximate. */
  weight: number
}

export interface IndexProfile {
  /** OpenAlgo underlying symbol this profile answers to. */
  underlying: string
  /** Exchange the constituents' cash quotes come from (NSE / BSE). */
  stockExchange: string
  /** Human label for the tab header. */
  label: string
  /** Raw constituent list — normalize before doing contribution math. */
  constituents: Constituent[]
}

const NIFTY_50: Constituent[] = [
  { symbol: 'HDFCBANK', weight: 13.2 },
  { symbol: 'RELIANCE', weight: 9.0 },
  { symbol: 'ICICIBANK', weight: 7.6 },
  { symbol: 'INFY', weight: 5.7 },
  { symbol: 'BHARTIARTL', weight: 4.6 },
  { symbol: 'TCS', weight: 4.1 },
  { symbol: 'LT', weight: 3.9 },
  { symbol: 'SBIN', weight: 3.1 },
  { symbol: 'BAJFINANCE', weight: 2.9 },
  { symbol: 'HINDUNILVR', weight: 2.2 },
  { symbol: 'ITC', weight: 2.1 },
  { symbol: 'HCLTECH', weight: 1.9 },
  { symbol: 'M&M', weight: 1.9 },
  { symbol: 'MARUTI', weight: 1.9 },
  { symbol: 'SUNPHARMA', weight: 1.9 },
  { symbol: 'KOTAKBANK', weight: 1.8 },
  { symbol: 'AXISBANK', weight: 1.8 },
  { symbol: 'ULTRACEMCO', weight: 1.7 },
  { symbol: 'NTPC', weight: 1.3 },
  { symbol: 'TITAN', weight: 1.3 },
  { symbol: 'TATAMOTORS', weight: 1.3 },
  { symbol: 'ADANIENT', weight: 1.2 },
  { symbol: 'POWERGRID', weight: 1.2 },
  { symbol: 'ASIANPAINT', weight: 1.1 },
  { symbol: 'TATASTEEL', weight: 1.1 },
  { symbol: 'ADANIPORTS', weight: 1.1 },
  { symbol: 'BAJAJFINSV', weight: 1.0 },
  { symbol: 'COALINDIA', weight: 1.0 },
  { symbol: 'NESTLEIND', weight: 1.0 },
  { symbol: 'WIPRO', weight: 0.9 },
  { symbol: 'JSWSTEEL', weight: 0.9 },
  { symbol: 'GRASIM', weight: 0.9 },
  { symbol: 'ONGC', weight: 0.8 },
  { symbol: 'TECHM', weight: 0.8 },
  { symbol: 'DMART', weight: 0.8 },
  { symbol: 'TATACONSUM', weight: 0.8 },
  { symbol: 'SBILIFE', weight: 0.7 },
  { symbol: 'HINDALCO', weight: 0.7 },
  { symbol: 'ADANIGREEN', weight: 0.7 },
  { symbol: 'HDFCLIFE', weight: 0.7 },
  { symbol: 'INDUSINDBK', weight: 0.7 },
  { symbol: 'BEL', weight: 0.7 },
  { symbol: 'DIVISLAB', weight: 0.6 },
  { symbol: 'CIPLA', weight: 0.6 },
  { symbol: 'BAJAJ-AUTO', weight: 0.6 },
  { symbol: 'EICHERMOT', weight: 0.6 },
  { symbol: 'DRREDDY', weight: 0.5 },
  { symbol: 'HEROMOTOCO', weight: 0.5 },
  { symbol: 'APOLLOHOSP', weight: 0.5 },
  { symbol: 'TRENT', weight: 0.5 },
]

const BANKNIFTY: Constituent[] = [
  { symbol: 'HDFCBANK', weight: 27.9 },
  { symbol: 'ICICIBANK', weight: 22.9 },
  { symbol: 'SBIN', weight: 10.2 },
  { symbol: 'KOTAKBANK', weight: 9.3 },
  { symbol: 'AXISBANK', weight: 8.3 },
  { symbol: 'BAJFINANCE', weight: 7.5 },
  { symbol: 'INDUSINDBK', weight: 4.9 },
  { symbol: 'BANKBARODA', weight: 2.6 },
  { symbol: 'PNB', weight: 2.6 },
  { symbol: 'CANBK', weight: 2.5 },
  { symbol: 'IDFCFIRSTB', weight: 1.6 },
  { symbol: 'FEDERALBNK', weight: 1.4 },
]

const SENSEX_30: Constituent[] = [
  { symbol: 'HDFCBANK', weight: 13.5 },
  { symbol: 'RELIANCE', weight: 9.2 },
  { symbol: 'ICICIBANK', weight: 7.7 },
  { symbol: 'INFY', weight: 5.8 },
  { symbol: 'TCS', weight: 4.2 },
  { symbol: 'BHARTIARTL', weight: 4.7 },
  { symbol: 'LT', weight: 4.0 },
  { symbol: 'ITC', weight: 2.1 },
  { symbol: 'SBIN', weight: 3.2 },
  { symbol: 'HINDUNILVR', weight: 2.2 },
  { symbol: 'BAJFINANCE', weight: 3.0 },
  { symbol: 'KOTAKBANK', weight: 1.8 },
  { symbol: 'MARUTI', weight: 1.9 },
  { symbol: 'M&M', weight: 1.9 },
  { symbol: 'SUNPHARMA', weight: 1.9 },
  { symbol: 'AXISBANK', weight: 1.8 },
  { symbol: 'HCLTECH', weight: 1.9 },
  { symbol: 'TITAN', weight: 1.3 },
  { symbol: 'ULTRACEMCO', weight: 1.7 },
  { symbol: 'NTPC', weight: 1.3 },
  { symbol: 'POWERGRID', weight: 1.2 },
  { symbol: 'TATASTEEL', weight: 1.1 },
  { symbol: 'JSWSTEEL', weight: 0.9 },
  { symbol: 'ASIANPAINT', weight: 1.1 },
  { symbol: 'ADANIPORTS', weight: 1.1 },
  { symbol: 'NESTLEIND', weight: 1.0 },
  { symbol: 'WIPRO', weight: 0.9 },
  { symbol: 'TECHM', weight: 0.8 },
  { symbol: 'BAJAJFINSV', weight: 1.0 },
  { symbol: 'INDUSINDBK', weight: 0.7 },
]

const FINNIFTY: Constituent[] = [
  { symbol: 'HDFCBANK', weight: 28.6 },
  { symbol: 'ICICIBANK', weight: 24.7 },
  { symbol: 'SBIN', weight: 8.0 },
  { symbol: 'AXISBANK', weight: 7.4 },
  { symbol: 'KOTAKBANK', weight: 6.6 },
  { symbol: 'BAJFINANCE', weight: 6.2 },
  { symbol: 'BAJAJFINSV', weight: 2.9 },
  { symbol: 'INDUSINDBK', weight: 2.6 },
  { symbol: 'PNB', weight: 2.3 },
  { symbol: 'LICI', weight: 1.6 },
  { symbol: 'BANKBARODA', weight: 1.6 },
  { symbol: 'IDFCFIRSTB', weight: 1.5 },
  { symbol: 'CANBK', weight: 1.4 },
  { symbol: 'HDFCLIFE', weight: 1.3 },
  { symbol: 'CHOLAFIN', weight: 1.3 },
  { symbol: 'SBILIFE', weight: 1.2 },
  { symbol: 'ICICIPRULI', weight: 1.1 },
  { symbol: 'ICICIGI', weight: 1.1 },
  { symbol: 'HDFCAMC', weight: 1.0 },
  { symbol: 'SHRIRAMFIN', weight: 1.0 },
]

const PROFILES: IndexProfile[] = [
  {
    underlying: 'NIFTY',
    stockExchange: 'NSE',
    label: 'Nifty 50',
    constituents: NIFTY_50,
  },
  {
    underlying: 'BANKNIFTY',
    stockExchange: 'NSE',
    label: 'Bank Nifty',
    constituents: BANKNIFTY,
  },
  {
    underlying: 'SENSEX',
    stockExchange: 'BSE',
    label: 'Sensex 30',
    constituents: SENSEX_30,
  },
  {
    underlying: 'FINNIFTY',
    stockExchange: 'NSE',
    label: 'Fin Nifty',
    constituents: FINNIFTY,
  },
]

/** The profile for an underlying, matched case-insensitively, or null. */
export function constituentsFor(underlying: string): IndexProfile | null {
  const wanted = underlying.trim().toUpperCase()
  if (!wanted) return null
  return PROFILES.find((profile) => profile.underlying === wanted) ?? null
}

/**
 * The weights renormalized to exactly 100 — the published lists are rounded,
 * and the contribution math needs a consistent total.
 */
export function normalizedWeights(profile: IndexProfile): Map<string, number> {
  const total = profile.constituents.reduce((sum, row) => sum + row.weight, 0)
  const map = new Map<string, number>()
  if (total <= 0) return map
  for (const row of profile.constituents) map.set(row.symbol, (row.weight / total) * 100)
  return map
}

/** A row of the contribution ledger for one stock, live-computed. */
export interface Contribution {
  /** Symbol the row belongs to. */
  symbol: string
  /** Stock % change since the session open. */
  pctChange: number
  /** Points of index % move this stock explains (weight × move / 100). */
  points: number
  /** Share of the index move, in percent, signed. 0 when there is no move. */
  share: number
}

/**
 * The contribution ledger: how much of the index's move each stock explains.
 *
 * A stock's contribution to the index move is its normalized weight times
 * its own % change; the sum of those approximates the index % move. The
 * share column rescales each contribution against the signed total — so on
 * a +0.5% Nifty day a stock contributing +0.2pt reads as 40% of the move.
 * When the index is effectively flat (|move| below 0.01pt) shares fall back
 * to absolute magnitudes so the bubbles stay readable.
 */
export function contributionLedger(
  profile: IndexProfile,
  pctBySymbol: Map<string, number>
): Contribution[] {
  const weights = normalizedWeights(profile)
  const rows: Contribution[] = profile.constituents.map((row) => {
    const weight = weights.get(row.symbol) ?? 0
    const pct = pctBySymbol.get(row.symbol) ?? 0
    return { symbol: row.symbol, pctChange: pct, points: (weight * pct) / 100, share: 0 }
  })
  const total = rows.reduce((sum, row) => sum + row.points, 0)
  const flat = Math.abs(total) < 0.01
  const normalizer = flat ? rows.reduce((sum, row) => sum + Math.abs(row.points), 0) : total
  for (const row of rows) {
    row.share = normalizer !== 0 ? (row.points / normalizer) * 100 : 0
  }
  return rows
}

/**
 * The weighted average of the constituents' % changes — a proxy for the
 * index's own % move, used when the spot index quote itself is unavailable.
 */
export function weightedIndexMove(profile: IndexProfile, pctBySymbol: Map<string, number>): number {
  const weights = normalizedWeights(profile)
  let move = 0
  for (const row of profile.constituents) {
    move += ((weights.get(row.symbol) ?? 0) * (pctBySymbol.get(row.symbol) ?? 0)) / 100
  }
  return move
}
