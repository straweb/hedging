import {
  ChartColumnBig,
  ChevronDown,
  ChevronUp,
  Clock,
  Crosshair,
  Loader2,
  RefreshCw,
  Timer,
} from 'lucide-react'
import { useState } from 'react'
import { tradingApi } from '@/api/trading'
import { IndexPanel } from '@/components/hedging/IndexPanel'
import { PercentField } from '@/components/hedging/PercentField'
import type { DockOrder } from '@/components/trading/dock/blotter'
import type { ModifyValues } from '@/components/trading/dock/ModifyOrderDialog'
import {
  ModifyOrderDialog,
  sendsPrice,
  sendsTrigger,
} from '@/components/trading/dock/ModifyOrderDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { scheduleActionLabel, scheduleTriggerLabel } from '@/lib/hedging/scheduler'
import { cn } from '@/lib/utils'
import type { Mode, Order, Position, ScheduledOrder, Trade } from '@/types/hedging'
import { showToast } from '@/utils/toast'

/**
 * The bottom dock: quick deploy actions on one always-visible strip, and the
 * positions / orders / trades / scheduled / index views below.
 *
 * Row verbs follow the position state, per the spec: a filled position gets
 * Exit (flatten) and Swap (flatten and reverse); a pending order gets Edit
 * (modify dialog), Cancel and Repeat (place the same order again); a
 * scheduled row gets Repeat (re-arm a copy), Edit (re-open the dialog) and
 * Cancel (remove it).
 *
 * The Index tab (F&O mode) decomposes the selected underlying into its live
 * constituents — price, volume, 1m % trail, contribution bubbles — via the
 * IndexPanel. The strip also carries the CE/PE share slider — the split of
 * the session's capital between the two side panels — so re-balancing both
 * sides is one drag, and the % figure is clickable when a drag is too
 * imprecise.
 */

const fmt = (value: number, digits = 2) =>
  value.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })

const statusTone = (status: string) => {
  const s = status.toLowerCase()
  if (s === 'complete') return 'bg-emerald-600'
  if (s === 'rejected') return 'bg-red-600'
  if (s === 'open' || s === 'pending' || s === 'trigger pending') return 'bg-amber-500'
  return 'bg-muted text-muted-foreground'
}

export interface QuickActionKey {
  deployA: boolean
  deployB: boolean
  closeA: boolean
  closeB: boolean
  swap: boolean
  exitAll: boolean
}

interface Props {
  mode: Mode
  collapsed: boolean
  quickBusy: QuickActionKey
  rowBusy: Set<string>
  positions: Position[]
  orders: Order[]
  trades: Trade[]
  /** Time/price-triggered batch orders, armed in the scheduler. */
  scheduled: ScheduledOrder[]
  /** CE share of the capital in percent (F&O mode slider on the strip). */
  ceShare: number
  onCeShareChange(share: number): void
  onOpenScheduler(): void
  onEditScheduled(entry: ScheduledOrder): void
  onCancelScheduled(id: string): void
  onRepeatScheduled(entry: ScheduledOrder): void
  scheduledBusy: Set<string>
  /** FNO: the underlying whose constituents the Index tab breaks down. */
  underlying: string
  /** The spot index quote target from the chain snapshot, when known. */
  indexSpot: { symbol: string; exchange: string } | null
  /** Load any symbol into the focused chart pane (Index tab rows). */
  onLoadChart(pick: { symbol: string; exchange: string; name?: string }): void
  /** Realized session P&L (flattened positions), for the strip. */
  pnl: number
  /** Unrealized P&L (open positions' live marks), for the strip. */
  upnl: number
  /** Open the index-level deploy dialog (entry/target/SL on the index). */
  onOpenIndexDeploy(): void
  /** The order being edited — lifted so the side panels can open it too. */
  editing: DockOrder | null
  onCloseEdit(): void
  onOpenEdit(order: Order): void
  onToggleCollapsed(): void
  onDeploy(side: 'A' | 'B'): void
  onCloseSide(side: 'A' | 'B'): void
  onSwap(): void
  onExitAll(): void
  onCancelOrder(orderid: string): void
  onRepeatOrder(order: Order): void
  onExitPosition(position: Position): void
  onSwapPosition(position: Position): void
  onRefreshBooks(): void
}

export function HedgingDock(props: Props) {
  const [book, setBook] = useState<'positions' | 'orders' | 'trades' | 'scheduled' | 'index'>(
    'positions'
  )
  const fno = props.mode === 'fno'
  // Equity mode has no Index tab — fall back to Positions if it was open
  // when the mode flipped.
  const activeBook = book === 'index' && !fno ? 'positions' : book
  const sideA = fno ? 'CE' : 'Buy'
  const sideB = fno ? 'PE' : 'Sell'

  const openPositions = props.positions.filter((position) => position.quantity !== 0)
  const openOrders = props.orders.filter((order) =>
    ['open', 'pending', 'trigger pending'].includes(order.order_status.toLowerCase())
  )

  const pnlTone = (value: number) =>
    value > 0 ? 'text-emerald-500' : value < 0 ? 'text-red-500' : 'text-muted-foreground'

  const submitModify = async (order: DockOrder, values: ModifyValues): Promise<boolean> => {
    try {
      const res = await tradingApi.modifyOrder(order.orderid, {
        symbol: order.symbol,
        exchange: order.exchange,
        action: order.action,
        product: order.product,
        pricetype: order.pricetype,
        quantity: values.quantity,
        ...(sendsPrice(order.pricetype) && { price: values.price }),
        ...(sendsTrigger(order.pricetype) && { trigger_price: values.trigger_price }),
      })
      if (res.status === 'success') {
        showToast.success(`Order modified: ${order.orderid}`)
        return true
      }
      showToast.error(res.message || 'Modify failed')
      return false
    } catch {
      showToast.error('Modify failed')
      return false
    }
  }

  const btn = (
    key: keyof QuickActionKey,
    label: string,
    onClick: () => void,
    tone: 'green' | 'red' | 'amber' | 'outline'
  ) => (
    <Button
      size="sm"
      variant={tone === 'outline' ? 'outline' : 'default'}
      disabled={props.quickBusy[key]}
      onClick={onClick}
      className={cn(
        'h-6 px-2 text-[10px] font-medium',
        tone === 'green' && 'bg-emerald-600 text-white hover:bg-emerald-700',
        tone === 'red' && 'bg-red-600 text-white hover:bg-red-700',
        tone === 'amber' && 'bg-amber-500 text-white hover:bg-amber-600'
      )}
    >
      {props.quickBusy[key] && <Loader2 className="mr-1 h-2.5 w-2.5 animate-spin" />}
      {label}
    </Button>
  )

  return (
    <div className="shrink-0 border-t bg-background">
      {/* Quick actions strip */}
      <div className="flex h-9 items-center gap-1 px-2">
        <span className="mr-1 text-[10px] font-semibold tracking-wide text-muted-foreground">
          ACTIONS
        </span>
        {btn('deployA', `Deploy ${sideA}`, () => props.onDeploy('A'), 'green')}
        {btn('deployB', `Deploy ${sideB}`, () => props.onDeploy('B'), 'green')}
        {btn('closeA', `Close ${sideA}`, () => props.onCloseSide('A'), 'red')}
        {btn('closeB', `Close ${sideB}`, () => props.onCloseSide('B'), 'red')}
        {btn('swap', 'Swap', () => props.onSwap(), 'amber')}
        {btn('exitAll', 'Exit All', () => props.onExitAll(), 'outline')}

        {/* CE/PE share: the capital split between the side panels, one drag —
            and the % figures are clickable for an exact number. */}
        {props.mode === 'fno' && (
          <span
            className="ml-1 flex items-center gap-1 rounded-md border px-1.5 py-0.5"
            title="Capital split — CE share of the session capital; PE gets the rest"
          >
            <span className="text-[10px] font-semibold text-emerald-600">CE</span>
            <input
              type="range"
              min={0}
              max={100}
              value={props.ceShare}
              onChange={(e) => props.onCeShareChange(Number(e.target.value))}
              className="h-1 w-24 accent-primary"
              aria-label="CE share of capital"
            />
            <span className="text-[10px] font-semibold text-red-500">PE</span>
            <span className="flex w-16 items-center justify-end gap-0.5 font-mono text-[10px] tabular-nums">
              <PercentField
                value={props.ceShare}
                onCommit={props.onCeShareChange}
                label="CE share"
                className="text-foreground"
              />
              /
              <PercentField
                value={100 - props.ceShare}
                onCommit={(share) => props.onCeShareChange(100 - share)}
                label="PE share"
                className="text-foreground"
              />
            </span>
          </span>
        )}

        <div className="flex-1" />
        {props.mode === 'fno' && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 gap-1 px-2 text-[10px] font-medium"
            onClick={props.onOpenIndexDeploy}
            title="Deploy CE/PE from index levels — entry, target and SL on the index chart"
          >
            <Crosshair className="h-3 w-3" />
            By Index
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-6 gap-1 px-2 text-[10px] font-medium"
          onClick={props.onOpenScheduler}
          title="Arm a batch on a clock time or an LTP level"
        >
          <Timer className="h-3 w-3" />
          Schedule
        </Button>
        {/* Running P&L: unrealized on the open marks, realized on the closed
            ones — the pair a scalper glances at between entries. */}
        <span
          className={cn('font-mono text-[10px] font-semibold tabular-nums', pnlTone(props.upnl))}
          title="Unrealized P&L — open positions at their live marks"
        >
          UPnL {props.upnl >= 0 ? '+' : ''}
          {fmt(props.upnl, 0)}
        </span>
        <span
          className={cn('font-mono text-[10px] font-semibold tabular-nums', pnlTone(props.pnl))}
          title="Realized P&L — positions closed this session"
        >
          PnL {props.pnl >= 0 ? '+' : ''}
          {fmt(props.pnl, 0)}
        </span>
        <span className="text-[10px] text-muted-foreground">
          {openPositions.length} pos · {openOrders.length} open ord
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={props.onRefreshBooks}
          title="Refresh books"
        >
          <RefreshCw className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={props.onToggleCollapsed}
          title={props.collapsed ? 'Show books' : 'Hide books'}
        >
          {props.collapsed ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {/* Books — the Index tab grows the dock, it carries its own charts. */}
      {!props.collapsed && (
        <Tabs
          value={activeBook}
          onValueChange={(value) => setBook(value as typeof book)}
          className={cn('overflow-y-auto', activeBook === 'index' ? 'max-h-96' : 'max-h-44')}
        >
          <TabsList className="mx-2 h-6">
            <TabsTrigger value="positions" className="h-5 px-2 text-[10px]">
              Positions ({openPositions.length})
            </TabsTrigger>
            <TabsTrigger value="orders" className="h-5 px-2 text-[10px]">
              Orders ({openOrders.length})
            </TabsTrigger>
            <TabsTrigger value="trades" className="h-5 px-2 text-[10px]">
              Trades ({props.trades.length})
            </TabsTrigger>
            <TabsTrigger value="scheduled" className="h-5 gap-1 px-2 text-[10px]">
              <Clock className="h-2.5 w-2.5" />
              Scheduled ({props.scheduled.filter((entry) => entry.status === 'pending').length})
            </TabsTrigger>
            {props.mode === 'fno' && (
              <TabsTrigger value="index" className="h-5 gap-1 px-2 text-[10px]">
                <ChartColumnBig className="h-2.5 w-2.5" />
                Index
              </TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="positions" className="m-0">
            <BookTable
              headers={['Symbol', 'Qty', 'Avg', 'LTP', 'P&L', 'Product', '']}
              empty="No positions yet."
              note={
                <span className="flex gap-2 font-mono text-[10px] tabular-nums">
                  <span className={pnlTone(props.upnl)}>
                    UPnL {props.upnl >= 0 ? '+' : ''}
                    {fmt(props.upnl)}
                  </span>
                  <span className={pnlTone(props.pnl)}>
                    Realized {props.pnl >= 0 ? '+' : ''}
                    {fmt(props.pnl)}
                  </span>
                </span>
              }
            >
              {props.positions.map((position) => {
                const busy = props.rowBusy.has(position.symbol)
                return (
                  <TableRow
                    key={`${position.exchange}:${position.symbol}:${position.product}`}
                    className="text-[10px]"
                  >
                    <TableCell className="py-0.5 font-mono">{position.symbol}</TableCell>
                    <TableCell
                      className={cn(
                        'py-0.5 font-mono tabular-nums',
                        position.quantity > 0 && 'text-emerald-500',
                        position.quantity < 0 && 'text-red-500'
                      )}
                    >
                      {position.quantity}
                    </TableCell>
                    <TableCell className="py-0.5 font-mono tabular-nums">
                      {fmt(position.average_price)}
                    </TableCell>
                    <TableCell className="py-0.5 font-mono tabular-nums">
                      {fmt(position.ltp)}
                    </TableCell>
                    <TableCell
                      className={cn(
                        'py-0.5 font-mono tabular-nums font-semibold',
                        position.pnl >= 0 ? 'text-emerald-500' : 'text-red-500'
                      )}
                    >
                      {fmt(position.pnl)}
                    </TableCell>
                    <TableCell className="py-0.5 text-muted-foreground">
                      {position.product}
                    </TableCell>
                    <TableCell className="py-0.5 text-right">
                      {position.quantity !== 0 && (
                        <span className="flex justify-end gap-1">
                          <Button
                            size="sm"
                            variant="destructive"
                            className="h-4 px-1.5 text-[9px]"
                            disabled={busy}
                            onClick={() => props.onExitPosition(position)}
                          >
                            Exit
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-4 px-1.5 text-[9px]"
                            disabled={busy}
                            onClick={() => props.onSwapPosition(position)}
                            title="Close and open the opposite side"
                          >
                            Swap
                          </Button>
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </BookTable>
          </TabsContent>

          <TabsContent value="orders" className="m-0">
            <BookTable
              headers={['Time', 'Symbol', 'Action', 'Qty', 'Price', 'Type', 'Status', '']}
              empty="No orders yet."
            >
              {props.orders.map((order) => {
                const busy = props.rowBusy.has(order.orderid)
                const actionable = ['open', 'pending', 'trigger pending'].includes(
                  order.order_status.toLowerCase()
                )
                return (
                  <TableRow key={order.orderid} className="text-[10px]">
                    <TableCell className="py-0.5 font-mono text-muted-foreground">
                      {order.timestamp?.slice(11, 19) || order.timestamp}
                    </TableCell>
                    <TableCell className="py-0.5 font-mono">{order.symbol}</TableCell>
                    <TableCell
                      className={cn(
                        'py-0.5 font-semibold',
                        order.action === 'BUY' ? 'text-emerald-500' : 'text-red-500'
                      )}
                    >
                      {order.action}
                    </TableCell>
                    <TableCell className="py-0.5 font-mono tabular-nums">
                      {order.quantity}
                    </TableCell>
                    <TableCell className="py-0.5 font-mono tabular-nums">
                      {order.price > 0 ? fmt(order.price) : '—'}
                    </TableCell>
                    <TableCell className="py-0.5 text-muted-foreground">
                      {order.pricetype}
                    </TableCell>
                    <TableCell className="py-0.5">
                      <Badge className={cn('px-1 py-0 text-[9px]', statusTone(order.order_status))}>
                        {order.order_status}
                      </Badge>
                    </TableCell>
                    <TableCell className="py-0.5 text-right">
                      <span className="flex justify-end gap-1">
                        {actionable && (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-4 px-1.5 text-[9px]"
                              disabled={busy}
                              onClick={() => props.onOpenEdit(order)}
                            >
                              Edit
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-4 px-1.5 text-[9px]"
                              disabled={busy}
                              onClick={() => props.onCancelOrder(order.orderid)}
                            >
                              Cancel
                            </Button>
                          </>
                        )}
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-4 px-1.5 text-[9px]"
                          disabled={busy}
                          onClick={() => props.onRepeatOrder(order)}
                          title="Place this order again"
                        >
                          Repeat
                        </Button>
                      </span>
                    </TableCell>
                  </TableRow>
                )
              })}
            </BookTable>
          </TabsContent>

          <TabsContent value="trades" className="m-0">
            <BookTable
              headers={['Time', 'Symbol', 'Action', 'Qty', 'Price']}
              empty="No trades yet."
            >
              {props.trades.map((trade, index) => (
                <TableRow key={`${trade.orderid ?? index}-${index}`} className="text-[10px]">
                  <TableCell className="py-0.5 font-mono text-muted-foreground">
                    {trade.timestamp?.slice(11, 19) || trade.timestamp}
                  </TableCell>
                  <TableCell className="py-0.5 font-mono">{trade.symbol}</TableCell>
                  <TableCell
                    className={cn(
                      'py-0.5 font-semibold',
                      trade.action === 'BUY' ? 'text-emerald-500' : 'text-red-500'
                    )}
                  >
                    {trade.action}
                  </TableCell>
                  <TableCell className="py-0.5 font-mono tabular-nums">{trade.quantity}</TableCell>
                  <TableCell className="py-0.5 font-mono tabular-nums">
                    {fmt(trade.average_price)}
                  </TableCell>
                </TableRow>
              ))}
            </BookTable>
          </TabsContent>
          <TabsContent value="scheduled" className="m-0">
            <ScheduledTable
              rows={props.scheduled}
              busy={props.scheduledBusy}
              onEdit={props.onEditScheduled}
              onCancel={props.onCancelScheduled}
              onRepeat={props.onRepeatScheduled}
            />
          </TabsContent>
          {props.mode === 'fno' && (
            <TabsContent value="index" className="m-0">
              <IndexPanel
                underlying={props.underlying}
                indexSpot={props.indexSpot}
                onLoadChart={props.onLoadChart}
              />
            </TabsContent>
          )}
        </Tabs>
      )}

      <ModifyOrderDialog
        order={props.editing}
        onOpenChange={(open) => {
          if (!open) props.onCloseEdit()
        }}
        onSubmit={submitModify}
      />
    </div>
  )
}

function BookTable(props: {
  headers: string[]
  empty: string
  /** A right-aligned summary beside the header row (e.g. running P&L). */
  note?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="h-5">
          {props.headers.map((header, index) => (
            <TableHead
              key={index}
              className="h-5 px-2 py-0 text-[9px] uppercase tracking-wide text-muted-foreground"
            >
              {header}
            </TableHead>
          ))}
        </TableRow>
        {props.note && (
          <TableRow className="border-b-0 hover:bg-transparent">
            <TableCell colSpan={props.headers.length} className="py-0.5 text-right">
              {props.note}
            </TableCell>
          </TableRow>
        )}
      </TableHeader>
      <TableBody>
        {props.children}
        {Array.isArray(props.children) && props.children.length === 0 && (
          <TableRow>
            <TableCell
              colSpan={props.headers.length}
              className="py-2 text-center text-[10px] text-muted-foreground"
            >
              {props.empty}
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}

/** The scheduler's book: armed time/price triggers with their batch verbs. */
function ScheduledTable(props: {
  rows: ScheduledOrder[]
  busy: Set<string>
  onEdit(entry: ScheduledOrder): void
  onCancel(id: string): void
  onRepeat(entry: ScheduledOrder): void
}) {
  const ordered = [...props.rows].sort((a, b) => b.createdAt - a.createdAt)
  return (
    <BookTable
      headers={['Trigger', 'Batch', 'Status', 'Created', 'Fired', '']}
      empty="Nothing scheduled — use Schedule on the actions bar to arm a batch."
    >
      {ordered.map((entry) => {
        const busy = props.busy.has(entry.id)
        const mode = entry.modeAtCreation
        const pending = entry.status === 'pending'
        return (
          <TableRow key={entry.id} className="text-[10px]">
            <TableCell className="py-0.5 font-mono">
              <span className="flex items-center gap-1">
                {entry.trigger.kind === 'time' ? (
                  <Clock className="h-2.5 w-2.5 text-muted-foreground" />
                ) : (
                  <Timer className="h-2.5 w-2.5 text-muted-foreground" />
                )}
                {scheduleTriggerLabel(entry.trigger)}
              </span>
            </TableCell>
            <TableCell
              className={cn(
                'py-0.5 font-semibold',
                entry.action.startsWith('deploy') && 'text-emerald-600',
                entry.action.startsWith('close') && 'text-red-500'
              )}
            >
              {scheduleActionLabel(entry.action, mode)}
            </TableCell>
            <TableCell className="py-0.5">
              <Badge
                className={cn(
                  'px-1 py-0 text-[9px]',
                  pending && 'bg-amber-500',
                  entry.status === 'done' && 'bg-emerald-600',
                  entry.status === 'missed' && 'bg-muted text-muted-foreground'
                )}
              >
                {pending && entry.trigger.kind === 'time' && entry.trigger.repeat === 'daily'
                  ? 'daily'
                  : entry.status}
              </Badge>
            </TableCell>
            <TableCell className="py-0.5 font-mono text-muted-foreground">
              {new Date(entry.createdAt).toLocaleTimeString('en-IN', { hour12: false })}
            </TableCell>
            <TableCell className="py-0.5 font-mono text-muted-foreground">
              {entry.firedAt
                ? new Date(entry.firedAt).toLocaleTimeString('en-IN', { hour12: false })
                : '—'}
            </TableCell>
            <TableCell className="py-0.5 text-right">
              <span className="flex justify-end gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-4 px-1.5 text-[9px]"
                  disabled={busy}
                  onClick={() => props.onEdit(entry)}
                  title="Change the batch or the trigger"
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-4 px-1.5 text-[9px]"
                  disabled={busy}
                  onClick={() => props.onRepeat(entry)}
                  title="Arm a fresh copy of this entry"
                >
                  Repeat
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-4 px-1.5 text-[9px] text-red-500"
                  disabled={busy}
                  onClick={() => props.onCancel(entry.id)}
                  title={pending ? 'Remove this scheduled order' : 'Clear this row'}
                >
                  {pending ? 'Cancel' : 'Clear'}
                </Button>
              </span>
            </TableCell>
          </TableRow>
        )
      })}
    </BookTable>
  )
}
