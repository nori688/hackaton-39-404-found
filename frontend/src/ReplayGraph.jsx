import { useMemo } from 'react'
import { rows } from './graphRows'
import { TYPE_COLOR, hm } from './util'

const W = 1100, H = 320, L = 92, R = 12, T = 16, B = 24

/** Нитка из компактной записи: ["n", узел, приб, отпр] | ["s", откуда, куда, вход, выход]. */
function points(path, y, end) {
  const pts = []
  for (const r of path) {
    if (r[0] === 'n') {
      if (!(r[1] in y)) continue
      pts.push([r[2] ?? r[3] ?? end, r[1]])
      if (r[3] !== null && r[3] !== undefined) pts.push([r[3], r[1]])
    } else {
      pts.push([r[3], r[1]])
      pts.push([r[4] ?? end, r[2]])
    }
  }
  return pts
}

/**
 * График «было / могло быть»: базовая линия серым пунктиром, альтернатива — цветом.
 * Поезда, которых касается решение, — жирнее; вертикаль — момент решения.
 */
export default function ReplayGraph({ layout, names, types, horizon, base, alt, t, node, involved, changed, full }) {
  const { y, max } = useMemo(() => rows(layout), [layout])
  const t0 = full ? 0 : Math.max(0, t - 3600)
  const t1 = full ? horizon : Math.min(horizon, t + 10 * 3600)
  const X = (s) => L + ((s - t0) / (t1 - t0)) * (W - L - R)
  const Y = (id) => T + (y[id] / max) * (H - T - B)
  const line = (path) => points(path, y, t1).map(([s, n]) => `${X(Math.max(t0, Math.min(t1, s))).toFixed(1)},${Y(n).toFixed(1)}`).join(' ')
  const hours = []
  for (let h = Math.ceil(t0 / 3600); h * 3600 <= t1; h += full ? 2 : 1) hours.push(h)
  const hot = new Set(involved || [])
  const moved = new Set(changed || [])
  return (
    <svg className="graph rgraph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Сравнение графиков движения">
      <defs>
        <clipPath id="plot"><rect x={L} y={0} width={W - L - R} height={H} /></clipPath>
      </defs>
      {hours.map((h) => (
        <g key={h}>
          <line x1={X(h * 3600)} x2={X(h * 3600)} y1={T} y2={H - B} className="grid" />
          <text x={X(h * 3600)} y={H - 7} textAnchor="middle" className="axis">{String(h % 24).padStart(2, '0')}</text>
        </g>
      ))}
      {Object.keys(y).map((id) => (
        <g key={id}>
          <line x1={L} x2={W - R} y1={Y(id)} y2={Y(id)} className={`grid st ${id === node ? 'hot-row' : ''}`} />
          <text x={L - 6} y={Y(id) + 3} textAnchor="end" className={`axis ${id === node ? 'hot-label' : ''}`}>{names[id]}</text>
        </g>
      ))}
      <g clipPath="url(#plot)">
        {Object.entries(base).map(([tid, p]) => (
          <polyline key={`b-${tid}`} points={line(p)} className="thread base" />
        ))}
        {Object.entries(alt).map(([tid, p]) => (
          <polyline key={`a-${tid}`} points={line(p)}
            className={`thread alt ${hot.has(tid) ? 'hot' : ''} ${moved.has(tid) || hot.has(tid) ? '' : 'calm'}`}
            style={{ stroke: TYPE_COLOR[types[tid]] }}>
            <title>{tid}</title>
          </polyline>
        ))}
      </g>
      <line x1={X(t)} x2={X(t)} y1={T - 8} y2={H - B} className="now" />
      <text x={X(t) + 4} y={T - 2} className="axis hot-label">решение {hm(t)}</text>
      {node && node in y && <circle cx={X(t)} cy={Y(node)} r="6" className="decision-dot" />}
    </svg>
  )
}
