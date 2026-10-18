import { Columns3, Plus, Ruler, X } from 'lucide-react'
import type { LinkGroup } from 'openalgo-charts'
import type { MagnetMode } from 'openalgo-charts/draw'
import type { WorkspacePane } from 'openalgo-charts/workspace'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ChartPane, type PaneQuickTrade } from '@/components/trading/ChartPane'
import { DrawingRail, DrawingRailTab } from '@/components/trading/DrawingRail'
import { GridDividers } from '@/components/trading/GridDividers'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { LAYOUTS, type LayoutPreset } from '@/lib/chart/layouts'
import {
  type GridWeights,
  parseAreas,
  parseTracks,
  readGridWeights,
  tracksTemplate,
  writeGridWeights,
} from '@/lib/trading/gridSizes'
import type { DrawStats, TradingTerminal } from '@/lib/trading/terminal'
import { cn } from '@/lib/utils'
import type { HedgingWorkspaceTab } from '@/types/hedging'

/**
 * The middle column: workspace tabs, each holding its own chart layout.
 *
 * Every tab owns a LAYOUTS preset (1 / 2-col / 2-row / spot+legs / 2x2 …) and
 * its panes are full ChartPane terminals — indicators, drawing tools, the
 * works — with per-pane persistence keyed by `hedging:{tabId}:p{n}`, so
 * switching tabs and coming back restores every chart. Draggable splitters
 * resize the grid; each tab remembers its own split. `focusRequest` is the
 * click-to-load channel: a strike or watchlist click lands on the focused
 * pane (or the first pane when none is focused yet).
 *
 * Around the grid sit the pieces the trading page has and a dense terminal
 * needs just as much: the drawing rail on the left edge (shared tool, magnet,
 * undo/redo across the panes), a compact icon-only toolbar per pane (labels
 * hidden, thin scrollbar — a 2×2 of terminals has no room for text), and a
 * footer strip naming every pane with the symbol it is charting, so a click
 * on a strike always lands where the trader thinks it will.
 *
 * `autoSeed` is the top bar's auto-open: when a new underlying is picked, the
 * untouched panes load the spot chart, the ATM call and the ATM put, in that
 * pane order. Panes the trader has explicitly loaded are never overwritten.
 */

const NO_DRAW: DrawStats = {
  count: 0,
  canUndo: false,
  canRedo: false,
  hasSelection: false,
  magnet: false,
  magnetMode: 'off',
  removable: 0,
  selectable: 0,
  stay: false,
  tool: null,
  shortcuts: {},
}

function presetWeights(preset: LayoutPreset): GridWeights {
  return { columns: parseTracks(preset.cols), rows: parseTracks(preset.rows) }
}

export interface WorkspaceFocusRequest {
  symbol: string
  exchange: string
  name?: string
  /** Monotonic stamp: identical picks still load. */
  stamp: number
}

/** One auto-open placement: which pane index gets which instrument. */
export interface AutoSeedLoad {
  paneIndex: number
  symbol: string
  exchange: string
  name?: string
}

export interface WorkspaceAutoSeed {
  /** Monotonic stamp so a re-pick of the same underlying still re-seeds. */
  stamp: number
  loads: AutoSeedLoad[]
}

interface Props {
  apiKey: string
  wsUrl: string
  tabs: HedgingWorkspaceTab[]
  activeTabId: string
  linkGroup: LinkGroup | null
  focusRequest: WorkspaceFocusRequest | null
  autoSeed: WorkspaceAutoSeed | null
  /** Seed for panes that have never been charted (current underlying). */
  seed: { symbol: string; exchange: string; name?: string } | null
  onSelectTab(id: string): void
  onAddTab(): void
  onCloseTab(id: string): void
  onRenameTab(id: string, name: string): void
  onLayoutChange(tabId: string, layoutId: string): void
  /**
   * The page's quick-trade pair for a charted symbol (the hedging terminal
   * docks Deploy CE/PE on index panes, which the chart itself cannot trade).
   * Called with the pane's `EXCHANGE:SYMBOL` key; null shows nothing.
   */
  quickTradeFor?(symbolKey: string | null): PaneQuickTrade | null
}

export function HedgingWorkspace(props: Props) {
  const tab = props.tabs.find((item) => item.id === props.activeTabId) ?? props.tabs[0]
  const layout = LAYOUTS.find((item) => item.id === tab?.layoutId) ?? LAYOUTS[0]

  const [focusedPaneId, setFocusedPaneId] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const terminals = useRef(new Map<string, TradingTerminal>())
  /** Panes the trader explicitly loaded — auto-seed never overwrites these. */
  const touchedPanes = useRef(new Set<string>())
  /** Live EXCHANGE:SYMBOL per pane, for the footer strip. */
  const [paneSymbols, setPaneSymbols] = useState<Record<string, string>>({})

  // ── Drawing rail state (shared across panes, like the trading page) ──────
  const [railFolded, setRailFolded] = useState(() => {
    try {
      return localStorage.getItem('oa-hedging-rail-folded') === '1'
    } catch {
      return false
    }
  })
  const [tool, setTool] = useState<string | null>(null)
  const [latched, setLatched] = useState(false)
  const [magnet, setMagnet] = useState<MagnetMode>('off')
  const [stay, setStay] = useState(false)
  const [stats, setStats] = useState<DrawStats>(NO_DRAW)
  const activeRef = useRef<TradingTerminal | null>(null)

  useEffect(() => {
    try {
      localStorage.setItem('oa-hedging-rail-folded', railFolded ? '1' : '0')
    } catch {
      // Storage refused: the fold holds for this visit only.
    }
  }, [railFolded])

  // ── Split weights: per tab+layout, the document's own storage ────────────
  const weightsKey = `hedging.${tab?.id ?? 'none'}.${layout.id}`
  const [liveWeights, setLiveWeights] = useState<GridWeights | null>(null)
  const storedWeights = useMemo(
    () => readGridWeights(localStorage, weightsKey, presetWeights(layout)),
    [weightsKey, layout]
  )
  const weights = liveWeights ?? storedWeights
  const commitWeights = (next: GridWeights) => {
    writeGridWeights(localStorage, weightsKey, next, presetWeights(layout))
    setLiveWeights(null)
  }

  // ── Pane seeds: what a pane charts on its very first mount ──────────────
  const seeds = useRef(new Map<string, WorkspacePane>())
  const seedFor = (paneId: string): WorkspacePane => {
    const existing = seeds.current.get(paneId)
    if (existing) return existing
    const built: WorkspacePane = {
      id: paneId,
      symbol: props.seed?.symbol ?? 'BHEL',
      exchange: props.seed?.exchange ?? 'NSE',
      interval: '5m',
      chartType: 'candlestick',
      chart: { version: 1 },
      settings: {},
      volume: true,
      magnet: 'off',
      stay: false,
      comparisons: [],
      comparisonMode: 'price',
    }
    seeds.current.set(paneId, built)
    return built
  }

  const noteTerminal = useCallback((paneId: string, terminal: TradingTerminal | null) => {
    if (terminal) {
      terminals.current.set(paneId, terminal)
      // The first pane to register is the default drawing/act target, so the
      // rail works before the trader has clicked any chart.
      if (!activeRef.current) activeRef.current = terminal
    } else {
      const previous = terminals.current.get(paneId)
      terminals.current.delete(paneId)
      if (previous && activeRef.current === previous) activeRef.current = null
    }
  }, [])

  // The focused pane is the drawing/act target; remember its terminal.
  const focusPane = useCallback((terminal: TradingTerminal | null, paneId?: string) => {
    activeRef.current = terminal
    if (paneId) setFocusedPaneId(paneId)
  }, [])

  /** Run a drawing action on the focused terminal and refresh the rail. */
  const act = useCallback((fn: (t: TradingTerminal) => void) => {
    const t = activeRef.current
    if (!t) return
    fn(t)
    setStats(t.drawStats())
    setTool(t.drawStats().tool)
  }, [])

  const pickTool = useCallback((id: string | null, latch = false) => {
    setTool(id)
    setLatched(id !== null && latch)
  }, [])

  // A pane dropping its tool (shape placed without latch, Escape) disarms the
  // shared tool, so the rail never advertises a tool no pane is using.
  const onPaneDrawStats = useCallback((value: DrawStats) => {
    setStats(value)
    if (value.tool === null) {
      setTool(null)
      setLatched(false)
    }
  }, [])

  // Magnet / stay apply to every mounted pane: the rail is shared.
  useEffect(() => {
    for (const terminal of terminals.current.values()) terminal.setMagnet(magnet)
  }, [magnet])
  useEffect(() => {
    for (const terminal of terminals.current.values()) terminal.setDrawStay(stay)
  }, [stay])

  /** Keyboard chords for the drawing tier, handled by the focused terminal. */
  const onDrawKey = useCallback((e: KeyboardEvent) => {
    const t = activeRef.current
    if (!t || !t.handleDrawKey(e)) return false
    setTool(t.drawStats().tool)
    setStats(t.drawStats())
    return true
  }, [])

  // ── Click-to-load: strike / watchlist click → focused pane ──────────────
  // biome-ignore lint/correctness/useExhaustiveDependencies: the stamp is the load trigger; panes and seeds are read live
  useEffect(() => {
    const request = props.focusRequest
    if (!request) return
    const paneId = focusedPaneId ?? `${tab?.id}:p0`
    touchedPanes.current.add(paneId)
    const terminal = terminals.current.get(paneId)
    if (!terminal) {
      // Pane not built yet: seed it, so it charts the pick when it mounts.
      seeds.current.set(paneId, {
        ...seedFor(paneId),
        symbol: request.symbol,
        exchange: request.exchange,
      })
      return
    }
    void terminal
      .loadSymbol(
        { symbol: request.symbol, exchange: request.exchange, name: request.name },
        { silent: true }
      )
      .then((loaded) => {
        if (!loaded) {
          toast.error(`Could not chart ${request.symbol}`)
          seeds.current.delete(paneId)
        } else {
          seeds.current.set(paneId, {
            ...seedFor(paneId),
            symbol: request.symbol,
            exchange: request.exchange,
          })
        }
      })
  }, [props.focusRequest?.stamp])

  // ── Auto-open: spot + ATM CE/PE into the untouched panes of this tab ─────
  // biome-ignore lint/correctness/useExhaustiveDependencies: the auto-seed stamp is the trigger; pane ids are read live
  useEffect(() => {
    const request = props.autoSeed
    if (!request) return
    for (const load of request.loads) {
      const paneId = `${tab?.id}:p${load.paneIndex}`
      if (!layout.cells[load.paneIndex]) continue // layout has no such pane
      if (touchedPanes.current.has(paneId)) continue // trader owns this pane
      const terminal = terminals.current.get(paneId)
      seeds.current.set(paneId, {
        ...seedFor(paneId),
        symbol: load.symbol,
        exchange: load.exchange,
      })
      void terminal
        ?.loadSymbol(
          { symbol: load.symbol, exchange: load.exchange, name: load.name },
          { silent: true }
        )
        .then((loaded) => {
          if (!loaded) seeds.current.delete(paneId)
        })
    }
  }, [props.autoSeed?.stamp])

  const noteSymbol = useCallback((paneId: string, key: string | null) => {
    setPaneSymbols((previous) => {
      if (key === null) {
        if (!(paneId in previous)) return previous
        const next = { ...previous }
        delete next[paneId]
        return next
      }
      if (previous[paneId] === key) return previous
      return { ...previous, [paneId]: key }
    })
  }, [])

  const areas = useMemo(() => parseAreas(layout.areas), [layout.areas])
  const showDividers = layout.cells.length > 1

  const railStats: DrawStats = {
    ...stats,
    tool,
    magnet: magnet !== 'off',
    magnetMode: magnet,
    stay,
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      {/* Tab strip */}
      <div className="flex h-8 shrink-0 items-center gap-0.5 border-b bg-background px-1">
        <Columns3 className="mr-1 h-3 w-3 text-muted-foreground" />
        {props.tabs.map((item) => (
          <div key={item.id} className="group relative">
            {renaming === item.id ? (
              <Input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={() => {
                  const name = renameValue.trim()
                  if (name) props.onRenameTab(item.id, name)
                  setRenaming(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') setRenaming(null)
                }}
                className="h-6 w-20 px-1 text-[11px]"
              />
            ) : (
              <button
                type="button"
                onDoubleClick={() => {
                  setRenaming(item.id)
                  setRenameValue(item.name)
                }}
                onClick={() => props.onSelectTab(item.id)}
                className={cn(
                  'rounded px-2 py-1 text-[11px] font-medium',
                  item.id === props.activeTabId
                    ? 'bg-muted text-foreground'
                    : 'text-muted-foreground hover:bg-muted/60'
                )}
                title="Click to open · double-click to rename"
              >
                {item.name}
              </button>
            )}
            {props.tabs.length > 1 && (
              <button
                type="button"
                onClick={() => props.onCloseTab(item.id)}
                className="absolute -top-1 -right-1 hidden h-3.5 w-3.5 items-center justify-center rounded-full border bg-background text-muted-foreground hover:text-destructive group-hover:flex"
                title="Close workspace"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            )}
          </div>
        ))}
        <Button
          variant="ghost"
          size="icon"
          className="h-5 w-5"
          onClick={props.onAddTab}
          title="New workspace"
        >
          <Plus className="h-3 w-3" />
        </Button>

        <div className="flex-1" />

        {/* Layout picker */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-6 gap-1 px-1.5 text-[11px]">
              <Columns3 className="h-3 w-3" />
              {layout.label}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="text-[11px]">
            {LAYOUTS.map((item) => (
              <DropdownMenuItem
                key={item.id}
                onSelect={() => tab && props.onLayoutChange(tab.id, item.id)}
              >
                {item.label}
                {item.id === layout.id && <span className="ml-auto">•</span>}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {/* Fold / unfold the drawing rail from here too */}
        <Button
          variant={railFolded ? 'ghost' : 'secondary'}
          size="sm"
          className="h-6 gap-1 px-1.5 text-[11px]"
          onClick={() => setRailFolded((value) => !value)}
          title={railFolded ? 'Show drawing tools' : 'Hide drawing tools'}
        >
          <Ruler className="h-3 w-3" />
          {railFolded ? '' : 'Draw'}
        </Button>
      </div>

      {/* Grid + rail */}
      <div className="flex min-h-0 flex-1">
        <DrawingRail
          folded={railFolded}
          onFold={() => setRailFolded(true)}
          stats={railStats}
          latched={latched}
          onPick={pickTool}
          onUndo={() => act((t) => t.historyPress('undo'))}
          onRedo={() => act((t) => t.historyPress('redo'))}
          onDeleteSelected={() => act((t) => t.removeDrawings(false))}
          onRemoveAll={() => act((t) => t.requestRemoveAllDrawings())}
          onSelectAll={() => act((t) => t.selectAllDrawings())}
          onHideSelected={() => act((t) => t.hideSelectedDrawings())}
          onLockSelected={() => act((t) => t.styleSelectedDrawing({ locked: true }))}
          onMagnet={(mode) => setMagnet(mode)}
          onStay={setStay}
          onShortcut={onDrawKey}
        />
        {!railFolded && <div className="w-px shrink-0 bg-border" />}
        <div className="relative min-h-0 min-w-0 flex-1">
          <div
            className="grid h-full w-full gap-px bg-border"
            style={{
              gridTemplateColumns: tracksTemplate(weights.columns),
              gridTemplateRows: tracksTemplate(weights.rows),
              gridTemplateAreas: layout.areas,
            }}
          >
            {layout.cells.map((cell, index) => {
              const paneId = `${tab?.id}:p${index}`
              return (
                <ChartPane
                  key={paneId}
                  paneId={paneId}
                  paneLabel={`${tab?.name ?? 'W'} · ${index + 1}`}
                  apiKey={props.apiKey}
                  wsUrl={props.wsUrl}
                  initialWorkspacePane={seedFor(paneId)}
                  linkGroup={props.linkGroup}
                  toolbarCompact
                  focused={paneId === (focusedPaneId ?? `${tab?.id}:p0`)}
                  style={{ gridArea: cell }}
                  sharedTool={tool}
                  sharedMagnet={magnet}
                  sharedStay={stay}
                  sharedLatch={latched}
                  quickTrade={props.quickTradeFor?.(paneSymbols[paneId] ?? null) ?? undefined}
                  onFocusPane={focusPane}
                  onTerminalChange={noteTerminal}
                  onDrawStats={onPaneDrawStats}
                  onSymbolChange={noteSymbol}
                />
              )
            })}
          </div>
          {railFolded && <DrawingRailTab onUnfold={() => setRailFolded(false)} bottom={8} />}
          {showDividers && (
            <GridDividers
              key={weightsKey}
              cells={areas}
              weights={weights}
              onChange={setLiveWeights}
              onCommit={commitWeights}
              onReset={() => commitWeights(presetWeights(layout))}
            />
          )}
        </div>
      </div>

      {/* Footer: pane name + charted symbol, so click-to-load lands where the
          trader expects. Click a chip to make that pane the load target. */}
      <div className="flex h-6 shrink-0 items-center gap-1 overflow-x-auto border-t bg-muted/30 px-1 text-[10px]">
        <span className="shrink-0 pr-1 font-semibold tracking-wide text-muted-foreground uppercase">
          Panes
        </span>
        {layout.cells.map((_, index) => {
          const paneId = `${tab?.id}:p${index}`
          const symbol = paneSymbols[paneId]
          const focused = paneId === (focusedPaneId ?? `${tab?.id}:p0`)
          return (
            <button
              key={paneId}
              type="button"
              onClick={() => setFocusedPaneId(paneId)}
              className={cn(
                'flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-mono',
                focused
                  ? 'bg-primary/10 font-semibold text-foreground'
                  : 'text-muted-foreground hover:bg-muted'
              )}
              title={`${tab?.name ?? 'W'} · pane ${index + 1}${symbol ? ` — ${symbol}` : ''}`}
            >
              <span className="text-[9px] tracking-wide">
                {tab?.name ?? 'W'}·{index + 1}
              </span>
              <span>{symbol ?? '—'}</span>
            </button>
          )
        })}
        <span className="ml-auto shrink-0 text-muted-foreground">
          click a strike or watchlist row to load it into the highlighted pane
        </span>
      </div>
    </div>
  )
}
