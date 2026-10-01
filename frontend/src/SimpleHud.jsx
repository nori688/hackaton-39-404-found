import { useEffect, useRef, useState } from 'react'
import { adjacency, branchChoices, hm } from './util'

const ICON = { passenger: '🚆', container: '📦', freight: '🛢' }
const KIND = { passenger: 'пассажирский', container: 'контейнерный', freight: 'грузовой' }

function plainStatus(t, names, clock) {
  const n = (id) => names[id] || id
  if (t.status === 'pending') return `Ещё не на участке — ждём ~${hm(t.forecast_appear)}`
  if (t.status === 'approaching') return `Подходит к участку, будет в ${hm(t.run.eta)}`
  if (t.status === 'running') {
    const late = t.run.eta < clock - 60
    return `Едет: ${n(t.run.from)} → ${n(t.run.to)}${late ? ', ждёт сигнала у станции' : `, будет в ${hm(t.run.eta)}`}`
  }
  if (t.status === 'passing') return `Проезжает ${n(t.node)} без остановки`
  if (t.status === 'at_node') {
    if (!t.track) return `Ждёт перед участком (${n(t.node)})`
    return `Стоит на станции ${n(t.node)}, путь ${t.track}`
  }
  if (t.status === 'delivered') return t.port?.remaining
    ? `В порту ${n(t.port.node)}: ждёт парома, осталось ${t.port.remaining} ваг.` : `Все вагоны погружены на паром`
  return `Прибыл: ${n(t.final_node)}`
}

export function SimpleCard({ state, tid, onCommand, follow, onFollow, onClose }) {
  const { obs, names, orders, clock, layout } = state
  const t = obs.trains.find((x) => x.id === tid)
  if (!t) return null
  const n = (id) => names[id] || id
  const legal = obs.legal.filter((a) => a.train === tid)
  const held = tid in orders.holds
  const auto = orders.auto_override[tid] ?? orders.autopilot
  const br = branchChoices(adjacency(layout), t)
  const route = br && orders.routes.find((r) => r.train === tid && r.at === br.at)
  const finished = ['done', 'delivered'].includes(t.status)
  const late = Math.round(((t.arrived_at ?? clock) - t.planned_arrival) / 60)
  const stopsAt = (node) => t.destinations.includes(node) || t.stops.some((s) => s.node === node)

  async function go() {
    if (held) await onCommand({ type: 'release', train: tid })
    const want = route?.next
    const a = legal.find((x) => !want || x.next === want) || legal[0]
    if (a) return onCommand({ type: 'dispatch', train: tid, next: a.next })
    if (t.status === 'running' && t.run && !stopsAt(t.run.to)) return onCommand({ type: 'pass_next', train: tid, node: t.run.to })
    if (!auto) return onCommand({ type: 'auto', train: tid, on: true })
  }

  return (
    <div className="scard">
      <button className="x" onClick={onClose} aria-label="закрыть">×</button>
      <div className="scard-title">{ICON[t.type]} <b>{t.id}</b> <span className="muted">{KIND[t.type]}</span></div>
      <div className={late > 15 ? 'bad' : late > 0 ? 'warnc' : 'ok'}>
        {finished && t.arrived_at !== null
          ? (late > 0 ? `Приехал с опозданием ${late} мин` : 'Приехал вовремя')
          : late > 0 ? `Опаздывает на ${late} мин` : 'Идёт по графику'}
      </div>
      <div>{plainStatus(t, names, clock)}</div>
      <div className="muted small">
        {n(t.origin)} → {t.destinations.map(n).join(' или ')} к {hm(t.planned_arrival)} · {t.length_m} м · {t.wagons} ваг.
      </div>
      {t.failure && <div className="bad">⚠ Сломался локомотив — чинят до ~{hm(t.failure.est_until)}</div>}
      {!finished && (
        <div className="sbtns">
          <button className="big go" onClick={go}>▶ Ехать</button>
          <button className={`big ${held ? 'on' : ''}`}
            onClick={() => onCommand(held ? { type: 'release', train: tid } : { type: 'hold', train: tid, until: null })}>
            {held ? '⏸ Стоит (отпустить)' : '⏸ Стоять'}
          </button>
          <button className={follow === tid ? 'on' : ''} onClick={() => onFollow(follow === tid ? null : tid)}>🎥 Следить</button>
          <button className={auto ? 'on' : ''} onClick={() => onCommand({ type: 'auto', train: tid, on: !auto })}>
            {auto ? '🤖 Авто: вкл' : '🤖 Авто: выкл'}
          </button>
        </div>
      )}
      {!finished && br && (
        <div className="sbtns">
          <span className="muted small">Порт:</span>
          {br.options.map((o) => (
            <button key={o.dest} className={route?.next === o.next ? 'on' : ''}
              onClick={() => onCommand({ type: 'route', train: tid, at: br.at, next: route?.next === o.next ? null : o.next })}>
              {n(o.dest)}
            </button>
          ))}
        </div>
      )}
      {!auto && !finished && <div className="muted small">Ручное управление: на каждой станции игра спросит, что делать с этим поездом.</div>}
    </div>
  )
}

const NOTIFY = {
  loco_failure: (e) => [`⚠ ${e.train}: сломался локомотив (~${e.est_min} мин)`, e.train, 'bad'],
  segment_outage: (e) => [`⚠ Перегон ${e.segment} закрыт (~${e.est_min} мин)`, null, 'bad'],
  window_start: (e) => [`🛠 Ремонт пути на ${e.segment}`, null, 'warn'],
  window_end: (e) => [`✓ Ремонт на ${e.segment} закончен`, null, ''],
  ferry_arrive: (e, n) => [`⛴ Паром ${e.ferry} пришёл в ${n(e.port)}`, null, ''],
  ferry_depart: (e) => [`⛴ Паром ${e.ferry} ушёл, увёз ${e.wagons} ваг.`, null, ''],
  terminate: (e, n) => [`✓ ${e.train} прибыл в ${n(e.node)}`, e.train, 'ok'],
  appear_blocked: (e, n) => [`${e.train} не может выйти: на ${n(e.node)} нет свободного пути`, e.train, 'warn'],
}

export function Notifications({ state, onSelect }) {
  const [items, setItems] = useState([])
  const seen = useRef(null)
  useEffect(() => {
    const log = state.log
    const lastT = log.length ? log[log.length - 1].t : 0
    if (seen.current === null) { seen.current = lastT; return }
    const fresh = log.filter((e) => e.t > seen.current)
    seen.current = Math.max(seen.current, lastT)
    const n = (id) => state.names[id] || id
    const add = fresh.map((e) => NOTIFY[e.event]?.(e, n)).filter(Boolean)
      .map(([text, train, cls]) => ({ text, train, cls, id: `${Math.random()}`, born: performance.now() }))
    if (add.length) setItems((xs) => [...add.reverse(), ...xs].slice(0, 5)) // eslint-disable-line react-hooks/set-state-in-effect
  }, [state.log, state.names])
  useEffect(() => {
    const id = setInterval(() => setItems((xs) => xs.filter((x) => performance.now() - x.born < 7000)), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="notes">
      {items.map((it) => (
        <div key={it.id} className={`note ${it.cls}`} onClick={() => it.train && onSelect(it.train)}>{it.text}</div>
      ))}
    </div>
  )
}

export function HowTo({ onClose }) {
  return (
    <div className="howto">
      <h3>Как играть</h3>
      <ul>
        <li>Поезда едут сами (автопилот). Ваша задача — чтобы опозданий было меньше.</li>
        <li><b>Нажмите на поезд</b> — его можно придержать (⏸) или пустить вперёд (▶). Двойной клик — камера следит за ним.</li>
        <li><b>Кружок-стрелка у узла</b> переводит контейнерные поезда в Актау или Курык. Смотрите, в каком порту есть место и когда придёт паром.</li>
        <li>Колесо мыши — приблизить. Вблизи видно реальную длину составов и путей: длинный поезд не встанет на короткий путь.</li>
        <li>Пассажирские весят втрое больше: их опоздание стоит дороже всего.</li>
        <li>Пробел — пауза. Режим «Диспетчер» наверху — схема и график движения, как у настоящего ДНЦ.</li>
      </ul>
      <button className="primary" onClick={onClose}>Понятно</button>
    </div>
  )
}

const ORDER = { running: 0, passing: 0, at_node: 1, approaching: 2, pending: 3, delivered: 4, done: 5 }

export function TrainDrawer({ state, selected, follow, onPick }) {
  const [open, setOpen] = useState(false)
  const { obs, clock, names } = state
  const rows = [...obs.trains].filter((t) => t.status !== 'done' && t.status !== 'delivered')
    .sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.planned_arrival - b.planned_arrival)
  return (
    <div className={`drawer ${open ? 'open' : ''}`}>
      <button className="drawer-btn" onClick={() => setOpen(!open)}>🚆 Поезда ({rows.length}) {open ? '▸' : '◂'}</button>
      {open && (
        <div className="drawer-list">
          {rows.map((t) => {
            const late = Math.round(((t.arrived_at ?? clock) - t.planned_arrival) / 60)
            return (
              <button key={t.id} className={`drow ${selected === t.id ? 'sel' : ''}`} onClick={() => onPick(t.id)}>
                <span>{ICON[t.type]} {t.id}{follow === t.id ? ' 🎥' : ''}{t.failure ? ' ⚠' : ''}</span>
                <span className="muted small">{t.status === 'pending' ? `~${hm(t.forecast_appear)}` : t.node ? names[t.node] : t.run ? `→ ${names[t.run.to]}` : ''}</span>
                <span className={late > 15 ? 'bad' : late > 0 ? 'warnc' : 'muted'}>{late > 0 ? `+${late}` : '✓'}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
