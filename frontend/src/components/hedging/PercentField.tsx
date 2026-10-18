import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * A percent figure the user can click to type an exact number — sliders are
 * for feel, this is for precision. Renders the value as a button; clicking
 * swaps in a numeric input that commits on Enter or blur (clamped to
 * 0–100) and escapes back to the read-only figure on Escape.
 */
export function PercentField(props: {
  value: number
  onCommit(value: number): void
  label?: string
  className?: string
  /** Digits shown in the read-only state. */
  digits?: number
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const start = () => {
    setText(String(props.value))
    setEditing(true)
  }
  const commit = () => {
    setEditing(false)
    const parsed = Number(text)
    if (Number.isFinite(parsed)) {
      props.onCommit(Math.min(100, Math.max(0, parsed)))
    }
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        type="number"
        min={0}
        max={100}
        step={props.digits && props.digits > 0 ? 0.5 : 1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') setEditing(false)
        }}
        className={cn(
          'h-4 w-12 rounded border bg-background px-1 text-right font-mono text-[10px] tabular-nums outline-none focus:ring-1 focus:ring-ring',
          props.className
        )}
        aria-label={props.label ?? 'Exact percent'}
      />
    )
  }

  return (
    <button
      type="button"
      onClick={start}
      className={cn(
        'rounded px-0.5 text-right font-mono text-[10px] tabular-nums hover:bg-muted hover:underline',
        props.className
      )}
      title={
        props.label
          ? `${props.label} — click to type an exact number`
          : 'Click to type an exact number'
      }
    >
      {props.value.toFixed(props.digits ?? 0)}%
    </button>
  )
}
