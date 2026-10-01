import { useMemo } from 'react'
import { rows } from './graphRows'
import { TYPE_COLOR } from './util'

const W = 1100, H = 300, L = 70, R = 10, T = 14, B = 22

export default function TrainGraph({ state, selected, onSelect }) {
  const { layout, paths, obs, clock, names } = state
  const { y, max } = useMemo(() => rows(layout), [layout])
  const horizon = obs.horizon
  const X = (t) => L + (t / horizon) * (W - L - R)
  const Y = (id) => T + (y[id] / max) * (H - T - B)
  const types = Object.fromEntries(obs.trains.map((t) => [t.id, t]))

  function line(tid) {
    const pts = []
    const p = paths[tid] || []
    const st = types[tid].status
    const finished = st === 'done' || st === 'delivered'
    const now = Math.min(clock, horizon)
    p.forEach((rec, i) => {
      const last = i === p.length - 1
      if ('node' in rec) {
        if (!(rec.node in y)) return
        pts.push([rec.arr ?? rec.dep ?? now, rec.node])
        if (rec.dep !== null && rec.dep !== undefined) pts.push([rec.dep, rec.node])
        else if (last && !finished) pts.push([now, rec.node])
      } else {
        pts.push([rec.enter, rec.from])
        if (rec.exit !== null && rec.exit !== undefined) pts.push([rec.exit, rec.to])
        else {
          const r = types[tid].run
          const f = r && r.eta > r.entered ? Math.max(0, Math.min(1, (clock - r.entered) / (r.eta - r.entered))) : 0
          pts.push([now, null, Y(rec.from) + (Y(rec.to) - Y(rec.from)) * f])
        }
      }
    })
    return pts.map(([t, n, yy]) => `${X(t).toFixed(1)},${(yy ?? Y(n)).toFixed(1)}`).join(' ')
  }

  const hours = []
  for (let h = 0; h * 3600 <= horizon; h += 2) hours.push(h)
  return (
    <svg className="graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="График движения">
      {hours.map((h) => (
        <g key={h}>
          <line x1={X(h * 3600)} x2={X(h * 3600)} y1={T} y2={H - B} className="grid" />
          <text x={X(h * 3600)} y={H - 6} textAnchor="middle" className="axis">{String(h).padStart(2, '0')}</text>
        </g>
      ))}
      {Object.keys(y).map((id) => (
        <g key={id}>
          <line x1={L} x2={W - R} y1={Y(id)} y2={Y(id)} className="grid st" />
          <text x={L - 6} y={Y(id) + 3} textAnchor="end" className="axis">{names[id]}</text>
        </g>
      ))}
      <line x1={X(clock)} x2={X(clock)} y1={T - 6} y2={H - B} className="now" />
      {Object.keys(paths).map((tid) => (paths[tid].length ? (
        <polyline key={tid} points={line(tid)} onClick={() => onSelect(tid)}
          className={`thread ${selected === tid ? 'sel' : ''}`} style={{ stroke: TYPE_COLOR[types[tid].type] }} />
      ) : null))}
    </svg>
  )
}
