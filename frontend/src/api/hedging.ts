/**
 * Hedging terminal API surface.
 *
 * The page's own endpoint is POST /api/v1/multileg (session CSRF-free REST
 * route authenticated by the user's apikey, same as every other /api/v1
 * endpoint). Everything else — search, expiry, strikes, funds, positions,
 * books — is reused from the existing modules, so this file stays small on
 * purpose.
 */

import type { MultilegLeg, MultilegResponse } from '@/types/hedging'
import { apiClient } from './client'

export const hedgingApi = {
  /**
   * Place a batch of legs with per-leg results (partial success allowed).
   * The terminal sequences the legs itself — for a swap, close legs come
   * first, then entries — and the service preserves that order.
   */
  placeMultileg: async (
    apiKey: string,
    legs: MultilegLeg[],
    strategy = 'HEDGING'
  ): Promise<MultilegResponse> => {
    const response = await apiClient.post<MultilegResponse>('/multileg', {
      apikey: apiKey,
      strategy,
      legs,
    })
    return response.data
  },
}
