import { TYPE_COLOR, hm } from './util'

const FERRY_STATUS = {
  expected: 'в море', waiting_berth: 'ждёт причала', unloading: 'выгрузка', loading: 'погрузка', departed: 'ушёл',
}

function frac(clock, a, b) {
  if (b <= a) return 1
  return Math.max(0, Math.min(1, (clock - a) / (b - a)))
}

export default function MapView({ state, selected, onSelect }) {
  const { layout, obs, clock, names } = state
  const pos = Object.fromEntries(layout.nodes.map((n) => [n.id, n]))
  const trains = Object.fromEntries(obs.trains.map((t) => [t.id, t]))
  const segObs = Object.fromEntries(obs.segments.map((s) => [s.id, s]))
  const nodeObs = Object.fromEntries(obs.nodes.map((n) => [n.id, n]))
  const color = (tid) => TYPE_COLOR[trains[tid]?.type] || '#999'

  const atNode = {}
  for (const t of obs.trains) {
    if ((t.status === 'at_node' || t.status === 'passing') && t.node) (atNode[t.node] ||= []).push(t)
    if (t.status === 'delivered' && t.port) (atNode[t.port.node] ||= []).push(t)
  }
  const boundaries = layout.nodes.filter((n) => n.type === 'boundary')
  const queueOf = (b) => obs.trains.filter((t) => t.status === 'pending' && t.origin === b.id)
    .sort((a, c) => a.forecast_appear - c.forecast_appear).slice(0, 3)
    .sort((a, b) => a.forecast_appear - b.forecast_appear).slice(0, 4)

  return (
    <svg className="map" viewBox="20 20 1100 360" role="img" aria-label="Схема участка">
      <rect x="35" y="45" width="110" height="320" rx="10" className="sea" />
      <text x="90" y="215" className="sea-label" textAnchor="middle">Каспий</text>
      {obs.ferries.map((f) => {
        const port = pos[f.port]
        const list = obs.ferries.filter((x) => x.port === f.port)
        const i = list.indexOf(f)
        const y = port.y - 40 + i * 30
        const active = ['waiting_berth', 'unloading', 'loading'].includes(f.status)
        return (
          <g key={f.id} className={`ferry ${f.status}`}>
            <text x="45" y={y} className="ferry-id">{f.id} · {FERRY_STATUS[f.status]}</text>
            <text x="45" y={y + 12} className="ferry-sub">
              {f.status === 'expected' ? `ETA ${hm(f.eta)}${f.eta !== f.planned_arrival ? ` (план ${hm(f.planned_arrival)})` : ''}`
                : f.status === 'departed' ? `увёз ${f.loaded} ваг. в ${hm(f.departed)}`
                  : `${f.loaded}/${f.capacity} ваг.${f.deadline ? ` до ${hm(f.deadline)}` : ''}`}
            </text>
            {active && <line x1="145" y1={y - 4} x2={port.x - 28} y2={port.y} className="berth" />}
          </g>
        )
      })}

      {layout.segments.map((s) => {
        const a = pos[s.a], b = pos[s.b], so = segObs[s.id]
        const cls = ['seg', so.outage ? 'outage' : '', so.windows.some((w) => w.state === 'active' || w.state === 'pending') ? 'window' : '',
          so.occupant ? 'occ' : '', so.reserved ? 'res' : ''].join(' ')
        const sched = so.windows.find((w) => w.state === 'scheduled')
        return (
          <g key={s.id}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={cls}
              style={so.occupant ? { stroke: color(so.occupant) } : so.reserved ? { stroke: color(so.reserved) } : undefined} />
            <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 + 16} className="seg-km" textAnchor="middle">
              {s.length_km} км{sched ? ` · окно ${hm(sched.start)}` : ''}{so.outage ? ` · закрыт до ~${hm(so.outage.est_until)}` : ''}
            </text>
          </g>
        )
      })}

      {layout.nodes.map((n) => {
        const no = nodeObs[n.id]
        const main = no.tracks.filter((t) => t.kind === 'main')
        const port = no.tracks.filter((t) => t.kind === 'port')
        return (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r={n.type === 'junction' ? 9 : 7} className={`node ${n.type}`} />
            <text x={n.x} y={n.y - 14 - (atNode[n.id]?.length || 0) * 12} textAnchor="middle" className="node-name">{names[n.id]}</text>
            {main.map((t, i) => (
              <rect key={t.id} x={n.x - main.length * 6 + i * 12 + 1} y={n.y + 26} width="10" height="7"
                className={`trk ${t.reserved ? 'res' : ''}`} style={t.occupant ? { fill: color(t.occupant) } : undefined}>
                <title>путь {t.id}, {t.length_m} м{t.occupant ? ` — ${t.occupant}` : ''}{t.reserved ? ` — ждёт ${t.reserved}` : ''}</title>
              </rect>
            ))}
            {port.length > 0 && (
              <g>
                {port.map((t, i) => (
                  <rect key={t.id} x={n.x - port.length * 5 + i * 10} y={n.y + 38} width="8" height="12"
                    className={`trk port ${t.reserved ? 'res' : ''}`} style={t.occupant ? { fill: color(t.occupant) } : undefined}>
                    <title>путь порта {t.id}{t.occupant ? ` — вагоны ${t.occupant}` : ''}</title>
                  </rect>
                ))}
                <text x={n.x} y={n.y + 62} textAnchor="middle" className="port-fill">
                  порт {port.filter((t) => t.occupant).length}/{port.length}
                </text>
              </g>
            )}
            {(atNode[n.id] || []).map((t, i) => (
              <text key={t.id} x={n.x} y={n.y - 12 - i * 12} textAnchor="middle"
                className={`chip ${selected === t.id ? 'sel' : ''} ${t.failure ? 'fail' : ''}`}
                style={{ fill: TYPE_COLOR[t.type] }} onClick={() => onSelect(t.id)}>
                {t.id}{t.failure ? ' ⚠' : ''}
              </text>
            ))}
          </g>
        )
      })}

      {obs.trains.filter((t) => t.run && (t.status === 'running' || t.status === 'approaching')).map((t) => {
        const r = t.run
        const f = frac(clock, r.entered, r.eta)
        let x, y
        if (!r.from) {
          const nb = pos[layout.segments.find((g) => g.a === r.to || g.b === r.to)?.[layout.segments.find((g) => g.a === r.to) ? 'b' : 'a']]
          const dx = pos[r.to].x - (nb?.x ?? pos[r.to].x - 1), dy = pos[r.to].y - (nb?.y ?? pos[r.to].y)
          const l = Math.hypot(dx, dy) || 1
          x = pos[r.to].x + (dx / l) * 45 * (1 - f); y = pos[r.to].y + (dy / l) * 45 * (1 - f)
        }
        else {
          const a = pos[r.from], b = pos[r.to]
          x = a.x + (b.x - a.x) * f; y = a.y + (b.y - a.y) * f
        }
        return (
          <g key={t.id} className={`runner ${selected === t.id ? 'sel' : ''}`} onClick={() => onSelect(t.id)}>
            <rect x={x - 14} y={y - 7} width="28" height="14" rx="4" style={{ fill: TYPE_COLOR[t.type] }} />
            <text x={x} y={y + 4} textAnchor="middle" className="runner-id">{t.id.slice(1)}</text>
            {t.failure && <text x={x} y={y - 10} textAnchor="middle" className="warn">⚠</text>}
            {r.through_next && <text x={x + 18} y={y + 4} className="go">»</text>}
          </g>
        )
      })}

      {boundaries.map((b) => {
        const q = queueOf(b)
        if (!q.length) return null
        const right = b.x > 900
        return (
          <text key={b.id} x={right ? 1110 : b.x + 12} y={b.y + (b.y < 100 ? 4 : 22)} textAnchor={right ? 'end' : 'start'} className="queue">
            ждём: {q.map((t) => `${t.id} ${hm(t.forecast_appear)}`).join(', ')}
          </text>
        )
      })}
    </svg>
  )
}
