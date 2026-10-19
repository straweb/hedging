import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { newScheduleId, scheduleActionLabel } from '@/lib/hedging/scheduler'
import { cn } from '@/lib/utils'
import type {
  Mode,
  ScheduleAction,
  ScheduledOrder,
  ScheduleTarget,
  ScheduleTrigger,
} from '@/types/hedging'

/**
 * Create or edit a scheduled batch order. The batch is picked with buttons —
 * the same verbs the quick actions strip runs — and armed on a clock time
 * (IST, to the second, optionally daily) or on an LTP level: a line the quote
 * crosses, or the band between two prices that it enters or escapes.
 *
 * In F&O mode a target toggle picks the book: the option panels (Deploy
 * CE/PE) or the underlying itself (Deploy Buy/Sell — the futures contract
 * when one lists, else the cash symbol). The entry joins the Scheduled table
 * beside Positions, where Repeat / Edit / Cancel live.
 */

export interface SchedulerSubmit {
  id?: string
  action: ScheduleAction
  trigger: ScheduleTrigger
  target?: ScheduleTarget
}

interface Props {
  open: boolean
  mode: Mode
  /** The entry being edited, or null for a fresh one. */
  initial: ScheduledOrder | null
  /** Instruments whose LTP the terminal already streams — trigger picks. */
  triggerSymbols: Array<{ symbol: string; exchange: string }>
  defaultSymbol?: { symbol: string; exchange: string }
  /** The instrument "stock" means here (futures contract or cash symbol). */
  stockSymbol?: { symbol: string; exchange: string }
  /** The spot index, when one is known (price triggers read its LTP). */
  spotSymbol?: { symbol: string; exchange: string } | null
  /**
   * What the batch will act on, per Trade toggle — shown on the dialog so
   * the armed verb's target is never a guess. Both entries may be equal
   * (a stock workspace trades one book).
   */
  actsOn?: { options: string; stock: string }
  onOpenChange(open: boolean): void
  onSubmit(submission: SchedulerSubmit): void
}

type PriceCondition = 'above' | 'below' | 'inside' | 'outside'

const fmtPrice = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 2 })

const ACTIONS: ScheduleAction[] = ['deployA', 'deployB', 'closeA', 'closeB', 'exitAll']

export function SchedulerDialog(props: Props) {
  const [action, setAction] = useState<ScheduleAction>('deployA')
  const [target, setTarget] = useState<ScheduleTarget>('options')
  const [kind, setKind] = useState<'time' | 'price'>('time')
  const [at, setAt] = useState('09:20:00')
  const [repeat, setRepeat] = useState<'once' | 'daily'>('once')
  const [symbol, setSymbol] = useState('')
  const [exchange, setExchange] = useState('')
  const [condition, setCondition] = useState<PriceCondition>('above')
  const [price, setPrice] = useState('')
  const [price2, setPrice2] = useState('')
  const [error, setError] = useState('')

  // Identity-stable view of the default trigger pick, so the seed below does
  // not re-run (and wipe mid-edit input) on every feed tick of the parent.
  const defaultKey = props.defaultSymbol
    ? `${props.defaultSymbol.exchange}:${props.defaultSymbol.symbol}`
    : ''

  // Re-seed the fields whenever the dialog opens for a different entry.
  useEffect(() => {
    if (!props.open) return
    setError('')
    const source = props.initial
    if (source) {
      setAction(source.action)
      setTarget(source.target ?? 'options')
      setKind(source.trigger.kind)
      if (source.trigger.kind === 'time') {
        setAt(source.trigger.at)
        setRepeat(source.trigger.repeat)
      } else {
        setSymbol(source.trigger.symbol)
        setExchange(source.trigger.exchange)
        setCondition(source.trigger.condition)
        setPrice(String(source.trigger.price))
        setPrice2(source.trigger.price2 != null ? String(source.trigger.price2) : '')
      }
      return
    }
    setAction('deployA')
    setTarget('options')
    setKind('time')
    setAt('09:20:00')
    setRepeat('once')
    if (defaultKey) {
      const [exch, sym] = defaultKey.split(':')
      setSymbol(sym)
      setExchange(exch)
    } else {
      setSymbol('')
      setExchange('')
    }
    setCondition('above')
    setPrice('')
    setPrice2('')
  }, [props.open, props.initial, defaultKey])

  const sideLabel = (act: ScheduleAction) => scheduleActionLabel(act, props.mode, target)

  const submit = () => {
    if (kind === 'time') {
      if (!/^\d{1,2}:\d{2}(:\d{2})?$/.test(at.trim())) {
        setError('Time must be HH:MM or HH:MM:SS (24h, IST).')
        return
      }
      const trigger: ScheduleTrigger = { kind: 'time', at: at.trim(), repeat }
      props.onSubmit({ id: props.initial?.id, action, trigger, target })
      props.onOpenChange(false)
      return
    }
    const value = Number(price)
    if (!symbol || !exchange) {
      setError('Pick the instrument the trigger watches.')
      return
    }
    if (!Number.isFinite(value) || value <= 0) {
      setError('Trigger price must be a positive number.')
      return
    }
    let trigger: ScheduleTrigger
    if (condition === 'inside' || condition === 'outside') {
      const second = Number(price2)
      if (!Number.isFinite(second) || second <= 0) {
        setError('A band needs two prices — fill the second one.')
        return
      }
      if (second === value) {
        setError('The band needs two different prices.')
        return
      }
      trigger = { kind: 'price', symbol, exchange, condition, price: value, price2: second }
    } else {
      trigger = { kind: 'price', symbol, exchange, condition, price: value }
    }
    props.onSubmit({ id: props.initial?.id, action, trigger, target })
    props.onOpenChange(false)
  }

  const stockNote = useMemo(() => {
    if (props.mode !== 'fno' || target !== 'stock') return null
    const inst = props.stockSymbol
    return inst
      ? `Stock batches trade ${inst.symbol} (${inst.exchange}) — Buy/Sell with the side's capital.`
      : 'Stock batches trade the underlying itself once a symbol is picked.'
  }, [props.mode, props.stockSymbol, target])

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="w-[360px] text-xs">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {props.initial ? 'Edit scheduled order' : 'Schedule a batch order'}
          </DialogTitle>
          <DialogDescription className="text-[11px]">
            The batch fires when the clock reaches the time (IST, seconds included) or the
            instrument's LTP crosses the level or band.
          </DialogDescription>
        </DialogHeader>

        {/* Batch — buttons, the quick actions' own verbs */}
        <div>
          <span className="mb-1 block text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
            Batch
          </span>
          <div className="grid grid-cols-3 gap-1">
            {ACTIONS.map((act) => (
              <Button
                key={act}
                size="sm"
                variant={action === act ? 'default' : 'outline'}
                className={cn(
                  'h-6 px-1 text-[10px]',
                  action === act &&
                    act.startsWith('deploy') &&
                    'bg-emerald-600 text-white hover:bg-emerald-600',
                  action === act &&
                    act.startsWith('close') &&
                    'bg-red-600 text-white hover:bg-red-600'
                )}
                onClick={() => setAction(act)}
                title={sideLabel(act)}
              >
                {sideLabel(act)}
              </Button>
            ))}
          </div>
        </div>

        {/* Target: option panels or the underlying itself (F&O only) */}
        {props.mode === 'fno' && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
              Trade
            </span>
            <Button
              size="sm"
              variant={target === 'options' ? 'default' : 'outline'}
              className="h-6 flex-1 px-1 text-[10px]"
              onClick={() => setTarget('options')}
            >
              Options (CE/PE)
            </Button>
            <Button
              size="sm"
              variant={target === 'stock' ? 'default' : 'outline'}
              className="h-6 flex-1 px-1 text-[10px]"
              onClick={() => setTarget('stock')}
            >
              Stock (Buy/Sell)
            </Button>
          </div>
        )}
        {stockNote && <p className="text-[10px] leading-4 text-muted-foreground">{stockNote}</p>}

        {/* What the batch acts on — the symbol the armed verbs will move. */}
        {props.actsOn && (
          <p className="text-[10px] leading-4 text-muted-foreground">
            Acts on{' '}
            <span className="font-mono font-semibold text-foreground">
              {target === 'stock' ? props.actsOn.stock : props.actsOn.options}
            </span>
          </p>
        )}

        {/* Trigger kind */}
        <div className="flex gap-1">
          <Button
            size="sm"
            variant={kind === 'time' ? 'default' : 'outline'}
            className="h-6 flex-1 text-[11px]"
            onClick={() => setKind('time')}
          >
            At a time
          </Button>
          <Button
            size="sm"
            variant={kind === 'price' ? 'default' : 'outline'}
            className="h-6 flex-1 text-[11px]"
            onClick={() => setKind('price')}
          >
            At a price
          </Button>
        </div>

        {kind === 'time' ? (
          <div className="flex items-center gap-2">
            <Input
              type="time"
              step={1}
              value={at}
              onChange={(e) => setAt(e.target.value)}
              className="h-7 w-32 font-mono text-xs"
              aria-label="Time of day (IST)"
            />
            <span className="text-[10px] text-muted-foreground">IST</span>
            <Select value={repeat} onValueChange={(value) => setRepeat(value as 'once' | 'daily')}>
              <SelectTrigger className="h-7 flex-1 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="once" className="text-xs">
                  Once
                </SelectItem>
                <SelectItem value="daily" className="text-xs">
                  Every trading day
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <Select
              value={symbol ? `${exchange}:${symbol}` : undefined}
              onValueChange={(value) => {
                const [exch, sym] = value.split(':')
                setExchange(exch)
                setSymbol(sym)
              }}
            >
              <SelectTrigger className="h-7 font-mono text-xs">
                <SelectValue placeholder="Instrument…" />
              </SelectTrigger>
              <SelectContent className="max-h-56">
                {props.triggerSymbols.map((pick) => (
                  <SelectItem
                    key={`${pick.exchange}:${pick.symbol}`}
                    value={`${pick.exchange}:${pick.symbol}`}
                    className="font-mono text-xs"
                  >
                    {pick.symbol}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center gap-1">
              <Select
                value={condition}
                onValueChange={(value) => setCondition(value as PriceCondition)}
              >
                <SelectTrigger className="h-7 w-[118px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="above" className="text-xs">
                    LTP ≥
                  </SelectItem>
                  <SelectItem value="below" className="text-xs">
                    LTP ≤
                  </SelectItem>
                  <SelectItem value="inside" className="text-xs">
                    Inside band
                  </SelectItem>
                  <SelectItem value="outside" className="text-xs">
                    Outside band
                  </SelectItem>
                </SelectContent>
              </Select>
              <Input
                type="number"
                value={price}
                min={0}
                step={0.05}
                placeholder={
                  condition === 'inside' || condition === 'outside' ? 'Low' : 'Trigger price'
                }
                onChange={(e) => setPrice(e.target.value)}
                className="h-7 flex-1 font-mono text-xs"
                aria-label="Trigger price"
              />
              {(condition === 'inside' || condition === 'outside') && (
                <Input
                  type="number"
                  value={price2}
                  min={0}
                  step={0.05}
                  placeholder="High"
                  onChange={(e) => setPrice2(e.target.value)}
                  className="h-7 flex-1 font-mono text-xs"
                  aria-label="Second band price"
                />
              )}
            </div>
            {symbol && price && Number(price) > 0 && (
              <p className="text-[10px] text-muted-foreground">
                {condition === 'above' &&
                  `Fires ${sideLabel(action)} at or above ₹${fmtPrice(Number(price))}.`}
                {condition === 'below' &&
                  `Fires ${sideLabel(action)} at or below ₹${fmtPrice(Number(price))}.`}
                {condition === 'inside' &&
                  Number(price2) > 0 &&
                  `Fires ${sideLabel(action)} while ${symbol} holds ₹${fmtPrice(Math.min(Number(price), Number(price2)))}–₹${fmtPrice(Math.max(Number(price), Number(price2)))}.`}
                {condition === 'outside' &&
                  Number(price2) > 0 &&
                  `Fires ${sideLabel(action)} the moment ${symbol} escapes ₹${fmtPrice(Math.min(Number(price), Number(price2)))}–₹${fmtPrice(Math.max(Number(price), Number(price2)))}.`}
              </p>
            )}
          </div>
        )}

        {error && <p className="text-[11px] text-destructive">{error}</p>}

        <DialogFooter className="gap-1">
          <Button
            size="sm"
            variant="outline"
            className={cn('h-7 text-[11px]')}
            onClick={() => props.onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button size="sm" className="h-7 text-[11px]" onClick={submit}>
            {props.initial ? 'Save' : 'Schedule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Build a persisted row from a dialog submission. */
export function scheduledFromSubmission(submission: SchedulerSubmit, mode: Mode): ScheduledOrder {
  return {
    id: submission.id ?? newScheduleId(),
    action: submission.action,
    trigger: submission.trigger,
    status: 'pending',
    createdAt: Date.now(),
    modeAtCreation: mode,
    target: submission.target ?? 'options',
  }
}
