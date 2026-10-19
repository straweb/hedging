import { sparkScale } from '@/lib/hedging/range'

/**
 * One row's live LTP tape, in miniature: the session's accumulated last
 * prices as a line, with the position's entry and its monitoring levels
 * (target, stop) drawn across as dotted rules. An open position's row is
 * where this lives — the tape against its own levels is the trade's story:
 * where it filled, where it is aiming, where it is defended.
 */
export function LtpSparkline(props: {
  points: readonly number[]
  levels: Array<{ price: number; color: string; label?: string }>
  width?: number
  height?: number
  className?: string
}) {
  const width = props.width ?? 96
  const height = props.height ?? 22
  const scale = sparkScale(
    props.points,
    props.levels.map((level) => level.price)
  )
  if (!scale || props.points.length < 2) return null
  const { min, max } = scale
  const span = max - min
  const x = (index: number) => (index / (props.points.length - 1)) * (width - 2) + 1
  const y = (price: number) => height - 1 - ((price - min) / span) * (height - 2)
  const path = props.points.map((point, index) => `${x(index).toFixed(1)},${y(point).toFixed(1)}`)
  const title = props.levels
    .map((level) => `${level.label ?? 'L'} ${level.price.toFixed(2)}`)
    .join(' · ')
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={props.className}
      role="img"
      aria-label={title ? `LTP tape with ${title}` : 'LTP tape'}
    >
      {props.levels.map((level, index) =>
        Number.isFinite(level.price) ? (
          <line
            key={`${level.label ?? 'L'}-${index}`}
            x1={0}
            x2={width}
            y1={y(level.price).toFixed(1)}
            y2={y(level.price).toFixed(1)}
            stroke={level.color}
            strokeWidth={1}
            strokeDasharray="2 2"
            opacity={0.85}
          />
        ) : null
      )}
      <polyline
        points={path.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.2}
        strokeLinejoin="round"
      />
    </svg>
  )
}
