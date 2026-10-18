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
import { indexDeploySummary, type StrikePickMode, strikesForPick } from '@/lib/hedging/indexLevels'
import { cn } from '@/lib/utils'
import type { OrderSide, StrikeSide } from '@/types/hedging'

/**
 * Deploy CE or PE from the index chart's price, not the option's: pick the
 * strikes (ATM, n above, n below, or explicit), an optional index entry
 * level, and optional index target / stop. An entry already met — or none —
 * places now; otherwise the plan arms as a spot-price trigger that places
 * exactly these strikes, and the target/stop arm as close rows once filled.
 */

export interface IndexDeploySubmit {
  side: StrikeSide
  optionAction: OrderSide
  pick: { mode: StrikePickMode; count?: number; strikes?: number[] }
  entry?: number | null
  target?: number | null
  stop?: number | null
}

interface Props {
  open: boolean
  onOpenChange(open: boolean): void
  /** The spot index the levels read (from the chain snapshot). */
  spot: { symbol: string; exchange: string } | null
  spotLtp?: number
  atmStrike?: number
  strikeInterval?: number
  /** CE share of the capital, for the allocation footnote. */
  ceShare: number
  onSubmit(plan: IndexDeploySubmit): void
}

const fmt = (value: number) => value.toLocaleString('en-IN', { maximumFractionDigits: 2 })

const PICK_MODES: Array<{ id: StrikePickMode; label: string }> = [
  { id: 'atm', label: 'ATM' },
  { id: 'above', label: 'Above' },
  { id: 'below', label: 'Below' },
  { id: 'custom', label: 'Strikes' },
]

export function IndexDeployDialog(props: Props) {
  const [side, setSide] = useState<StrikeSide>('CE')
  const [optionAction, setOptionAction] = useState<OrderSide>('BUY')
  const [mode, setMode] = useState<StrikePickMode>('atm')
  const [count, setCount] = useState(2)
  const [custom, setCustom] = useState('')
  const [entry, setEntry] = useState('')
  const [target, setTarget] = useState('')
  const [stop, setStop] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (props.open) setError('')
  }, [props.open])

  const customStrikes = useMemo(
    () =>
      custom
        .split(/[,\s]+/)
        .map((part) => Number(part))
        .filter((value) => Number.isFinite(value) && value > 0),
    [custom]
  )

  const pick = useMemo(
    () => ({ mode, count, strikes: mode === 'custom' ? customStrikes : undefined }),
    [mode, count, customStrikes]
  )

  const planned = useMemo(
    () => strikesForPick(pick, props.atmStrike, props.strikeInterval),
    [pick, props.atmStrike, props.strikeInterval]
  )

  const num = (text: string) => {
    const trimmed = text.trim()
    if (trimmed === '') return null
    const value = Number(trimmed)
    return Number.isFinite(value) && value > 0 ? value : Number.NaN
  }

  const submit = () => {
    if (mode !== 'custom' && (props.atmStrike == null || !props.strikeInterval)) {
      setError('The option chain snapshot has not landed yet — no ATM to anchor to.')
      return
    }
    if (mode === 'custom' && customStrikes.length === 0) {
      setError('List the strikes to deploy (comma separated).')
      return
    }
    for (const [label, text] of [
      ['Entry', entry],
      ['Target', target],
      ['Stop', stop],
    ] as const) {
      const value = num(text)
      if (Number.isNaN(value)) {
        setError(`${label} must be a positive number (or blank).`)
        return
      }
    }
    if (mode === 'custom' && planned.length === 0) {
      setError('No valid strikes in the list.')
      return
    }
    props.onSubmit({
      side,
      optionAction,
      pick,
      entry: num(entry),
      target: num(target),
      stop: num(stop),
    })
    props.onOpenChange(false)
  }

  const summary = planned.length
    ? indexDeploySummary({
        side,
        optionAction,
        strikes: planned,
        entry: num(entry),
        target: num(target),
        stop: num(stop),
        spotSymbol: props.spot?.symbol,
      })
    : null

  const levelField = (
    label: string,
    text: string,
    setText: (value: string) => void,
    hint: string
  ) => (
    <label className="block flex-1">
      <span className="mb-0.5 block text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      <Input
        type="number"
        value={text}
        min={0}
        step={0.05}
        placeholder="—"
        onChange={(e) => setText(e.target.value)}
        className="h-7 font-mono text-xs"
        aria-label={label}
      />
      <span className="mt-0.5 block text-[9px] leading-3 text-muted-foreground">{hint}</span>
    </label>
  )

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="w-[360px] text-xs">
        <DialogHeader>
          <DialogTitle className="text-sm">Deploy by index level</DialogTitle>
          <DialogDescription className="text-[11px]">
            Entry, target and stop read the index chart; the orders go to the {side} strikes you
            pick here.
          </DialogDescription>
        </DialogHeader>

        {/* Side + action */}
        <div className="flex gap-2">
          <div className="flex flex-1 gap-1">
            {(['CE', 'PE'] as StrikeSide[]).map((value) => (
              <Button
                key={value}
                size="sm"
                variant={side === value ? 'default' : 'outline'}
                className="h-6 flex-1 px-1 text-[11px]"
                onClick={() => setSide(value)}
              >
                {value}
              </Button>
            ))}
          </div>
          <div className="flex flex-1 gap-1">
            {(['BUY', 'SELL'] as OrderSide[]).map((value) => (
              <Button
                key={value}
                size="sm"
                variant={optionAction === value ? 'default' : 'outline'}
                className={cn(
                  'h-6 flex-1 px-1 text-[11px]',
                  optionAction === value &&
                    (value === 'BUY'
                      ? 'bg-emerald-600 text-white hover:bg-emerald-600'
                      : 'bg-red-600 text-white hover:bg-red-600')
                )}
                onClick={() => setOptionAction(value)}
              >
                {value}
              </Button>
            ))}
          </div>
        </div>

        {/* Strike pick */}
        <div>
          <span className="mb-1 block text-[9px] font-semibold tracking-wide text-muted-foreground uppercase">
            Strikes
          </span>
          <div className="flex gap-1">
            {PICK_MODES.map((item) => (
              <Button
                key={item.id}
                size="sm"
                variant={mode === item.id ? 'default' : 'outline'}
                className="h-6 flex-1 px-1 text-[10px]"
                onClick={() => setMode(item.id)}
              >
                {item.label}
              </Button>
            ))}
          </div>
          {mode === 'above' || mode === 'below' ? (
            <div className="mt-1 flex items-center gap-1">
              <span className="text-[10px] text-muted-foreground">How many:</span>
              {[1, 2, 3, 4, 5, 6, 7].map((value) => (
                <Button
                  key={value}
                  size="sm"
                  variant={count === value ? 'default' : 'outline'}
                  className="h-5 min-w-5 px-1 text-[10px]"
                  onClick={() => setCount(value)}
                >
                  {value}
                </Button>
              ))}
            </div>
          ) : null}
          {mode === 'custom' ? (
            <Input
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              placeholder="e.g. 24500, 24600, 24700"
              className="mt-1 h-7 font-mono text-xs"
              aria-label="Custom strikes"
            />
          ) : null}
          {planned.length > 0 && (
            <p className="mt-1 font-mono text-[10px] text-muted-foreground">
              → {planned.map((strike) => fmt(strike)).join(', ')}
            </p>
          )}
        </div>

        {/* Index levels */}
        <div className="flex items-start gap-1.5">
          {levelField(
            'Entry',
            entry,
            setEntry,
            props.spotLtp ? `Spot ${fmt(props.spotLtp)}` : 'blank = now'
          )}
          {levelField('Target', target, setTarget, 'closes when met')}
          {levelField('Stop', stop, setStop, 'closes when met')}
        </div>

        {summary && (
          <p className="rounded bg-muted/50 px-2 py-1 text-[10px] leading-4 text-muted-foreground">
            {summary}
          </p>
        )}
        <p className="text-[10px] leading-4 text-muted-foreground">
          Capital: the {side} side's share ({side === 'CE' ? props.ceShare : 100 - props.ceShare}%)
          splits evenly across the picked strikes. Triggers run in this browser tab while it stays
          open.
        </p>

        {error && <p className="text-[11px] text-destructive">{error}</p>}

        <DialogFooter className="gap-1">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[11px]"
            onClick={() => props.onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button size="sm" className="h-7 text-[11px]" onClick={submit}>
            Deploy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
