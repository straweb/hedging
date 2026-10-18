import { useQuery } from '@tanstack/react-query'
import { ListPlus, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { watchlistApi } from '@/api/watchlist'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

/**
 * Equity mode's left panel: the user's OpenAlgo watchlists with live prices
 * from the terminal's shared quote feed. Clicking a row charts it; the +
 * button adds it to the active trading list on the right.
 */

export interface WatchlistMiniProps {
  getLtp(symbol: string, exchange: string): number | undefined
  activeSymbols: Set<string>
  onLoadChart(pick: { symbol: string; exchange: string }): void
  onAddStock(pick: { symbol: string; exchange: string; lotsize: number }): void
}

export function WatchlistMini(props: WatchlistMiniProps) {
  const listsQuery = useQuery({
    queryKey: ['hedging', 'watchlists'],
    queryFn: () => watchlistApi.list(),
    staleTime: 60 * 1000,
  })
  const lists = listsQuery.data ?? []
  const [activeId, setActiveId] = useState<number | null>(null)

  const active = useMemo(
    () => lists.find((list) => list.id === activeId) ?? lists[0] ?? null,
    [lists, activeId]
  )

  const fmt = (value: number | undefined) =>
    value === undefined ? '—' : value.toLocaleString('en-IN', { maximumFractionDigits: 2 })

  return (
    <div className="flex h-full flex-col overflow-hidden border bg-background">
      <div className="flex h-7 shrink-0 items-center gap-1 border-b px-1.5">
        <ListPlus className="h-3 w-3 text-muted-foreground" />
        <span className="text-[11px] font-semibold">Watchlist</span>
        <div className="ml-auto w-[104px]">
          <Select
            value={active ? String(active.id) : undefined}
            onValueChange={(value) => setActiveId(Number(value))}
          >
            <SelectTrigger className="h-5 px-1.5 text-[10px]" aria-label="Watchlist">
              <SelectValue placeholder="List…" />
            </SelectTrigger>
            <SelectContent className="max-h-64">
              {lists.map((list) => (
                <SelectItem key={list.id} value={String(list.id)} className="text-[11px]">
                  {list.name} ({list.items.length})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!active && (
          <p className="p-3 text-center text-[11px] text-muted-foreground">
            {listsQuery.isLoading ? 'Loading lists…' : 'Create a watchlist from the Trading page.'}
          </p>
        )}
        {active?.items.map((item) => {
          const ltp = props.getLtp(item.symbol, item.exchange)
          const isActive = props.activeSymbols.has(`${item.exchange}:${item.symbol}`)
          return (
            <div
              key={`${item.exchange}:${item.symbol}`}
              className="group flex items-center gap-1 border-b px-1.5 py-1 text-[11px] last:border-0"
            >
              <button
                type="button"
                onClick={() => props.onLoadChart({ symbol: item.symbol, exchange: item.exchange })}
                className="min-w-0 flex-1 truncate text-left font-mono hover:underline"
                title={`${item.exchange}:${item.symbol} — load into selected chart`}
              >
                {item.symbol}
              </button>
              <span className="font-mono tabular-nums">{fmt(ltp)}</span>
              <button
                type="button"
                onClick={() =>
                  props.onAddStock({ symbol: item.symbol, exchange: item.exchange, lotsize: 1 })
                }
                className={cn(
                  'rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100',
                  isActive && 'text-emerald-500 opacity-100'
                )}
                title={isActive ? 'Already in the active list' : 'Add to active trading list'}
              >
                <Plus className="h-3 w-3" />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
