import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import MapView from './MapView'
import { EventFeed, PortPanel, PromptPanel, TrainCard, TrainList } from './Panels'
import { HowTo, Notifications, SimpleCard, TrainDrawer } from './SimpleHud'
import TrainGraph from './TrainGraph'
import { adjacency, branchChoices, hm, rejectText } from './util'
import WorldMap from './WorldMap'

// игровых секунд за реальную секунду
const SPEEDS = [[0, '⏸'], [60, '1×'], [300, '5×'], [1200, '20×'], [3600, '60×']]
const TICK_MS = 200

function readHowtoSeen() {
  try { return localStorage.getItem('howto-seen') === '1' } catch { return false }
}

export default function Game({ initial, initialView = 'map', onEnd, onQuit }) {
  const [state, setState] = useState(initial)
  const [stateTime, setStateTime] = useState(() => performance.now())
  const [speed, setSpeed] = useState(initialView === 'map' ? 300 : 60)
  const [view, setView] = useState(initialView)
  const [selected, setSelected] = useState(null)
  const [follow, setFollow] = useState(null)
  const [focus, setFocus] = useState(null)
  const [junctionPref, setJunctionPref] = useState({})
  const [toast, setToast] = useState(null)
  const [tab, setTab] = useState('trains')
  const [howto, setHowto] = useState(() => initialView === 'map' && !readHowtoSeen())
  const ref = useRef(state)
  const inflight = useRef(false)
  useEffect(() => { ref.current = state }, [state])

  const apply = useCallback((s) => { setState(s); setStateTime(performance.now()) }, [])
  const fail = (e) => setToast({ bad: true, text: e.message })

  useEffect(() => {
    if (!speed) return
    const id = setInterval(async () => {
      const s = ref.current
      if (inflight.current || s.paused || s.status !== 'running') return
      inflight.current = true
      try { apply(await api.advance(s.id, Math.round((speed * TICK_MS) / 1000))) } catch (e) { fail(e) } finally { inflight.current = false }
    }, TICK_MS)
    return () => clearInterval(id)
  }, [speed, apply])

  const command = useCallback(async (cmd) => {
    try {
      const r = await api.command(ref.current.id, cmd)
      apply(r.state)
      if (!r.result.ok) {
        const rej = r.result.rejections?.[0]
        setToast({ bad: true, text: rej ? `${rej.command.train}: ${rejectText(rej.reason)}` : r.result.reason })
      }
    } catch (e) { fail(e) }
  }, [apply])

  const resume = useCallback(async () => {
    try { apply(await api.resume(ref.current.id)) } catch (e) { fail(e) }
  }, [apply])

  // «перевести стрелку»: авто → ветка 1 → ветка 2 → авто
  const onJunction = useCallback((node, kids) => {
    const cur = junctionPref[node] ?? null
    const order = [null, ...kids]
    const next = order[(order.indexOf(cur) + 1) % order.length]
    setJunctionPref((p) => ({ ...p, [node]: next }))
    const adj = adjacency(ref.current.layout)
    const trains = ref.current.obs.trains
      .filter((t) => !['done', 'delivered'].includes(t.status) && branchChoices(adj, t)?.at === node)
      .map((t) => t.id)
    command({ type: 'route_all', at: node, next, trains })
  }, [junctionPref, command])

  useEffect(() => {
    const onKey = (e) => {
      if (e.code !== 'Space' || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return
      e.preventDefault()
      if (ref.current.paused) resume()
      else setSpeed((s) => (s ? 0 : 300))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [resume])

  useEffect(() => {
    if (state.prompt?.reasons?.length === 1 && state.prompt.reasons[0].train) setSelected(state.prompt.reasons[0].train) // eslint-disable-line react-hooks/set-state-in-effect
  }, [state.prompt])

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(id)
  }, [toast])

  function closeHowto() {
    setHowto(false)
    try { localStorage.setItem('howto-seen', '1') } catch { /* приватный режим */ }
  }

  const m = state.metrics
  const total = state.obs.trains.length
  const arrived = state.obs.trains.filter((t) => t.arrived_at !== null).length
  const ended = state.status !== 'running'
  const lateNow = state.obs.trains.filter((t) => t.arrived_at === null && t.status !== 'pending' && state.clock > t.planned_arrival).length
  const ports = state.obs.nodes.filter((n) => n.type === 'port').map((n) => {
    const p = n.tracks.filter((t) => t.kind === 'port')
    return { id: n.id, used: p.filter((t) => t.occupant).length, total: p.length }
  })
  return (
    <div className="game">
      <header className="bar">
        <button className="link" onClick={onQuit}>← меню</button>
        <div className="clock">{hm(state.clock)}</div>
        <div className="speeds">
          {SPEEDS.map(([v, label]) => (
            <button key={v} className={speed === v ? 'on' : ''} onClick={() => setSpeed(v)} disabled={ended}>{label}</button>
          ))}
        </div>
        <div className="views">
          <button className={view === 'map' ? 'on' : ''} onClick={() => setView('map')}>🗺 Карта</button>
          <button className={view === 'dispatcher' ? 'on' : ''} onClick={() => setView('dispatcher')}>📈 Диспетчер</button>
        </div>
        {view === 'map' ? (
          <div className="score">
            <span title="суммарная задержка" className={m.total_delay_min > 0 ? 'warnc' : ''}>⏱ опоздания {Math.round(m.total_delay_min)} мин</span>
            <span title="доехали">✓ {arrived}/{total}</span>
            {lateNow > 0 && <span className="bad" title="сейчас опаздывают">⚠ {lateNow} опаздывают</span>}
            {ports.map((p) => <span key={p.id} title="занято путей порта">⚓ {state.names[p.id]} {p.used}/{p.total}</span>)}
          </div>
        ) : (
          <div className="score">
            <span title="суммарная задержка">⏱ {m.total_delay_min} мин</span>
            <span title="задержка с весом приоритета">⚖ {m.weighted_delay_min}</span>
            <span title="доехали">🚆 {arrived}/{total}</span>
            <span title="отправлено паромами">⛴ {m.wagons_shipped} ваг.</span>
            {m.rejected_commands > 0 && <span className="bad" title="отклонённые приказы">✗ {m.rejected_commands}</span>}
          </div>
        )}
        <label className="check small">
          <input type="checkbox" checked={state.orders.autopilot}
            onChange={(e) => command({ type: 'autopilot', on: e.target.checked })} /> автопилот
        </label>
        {view === 'map' && <button className="link" onClick={() => setHowto(true)}>? как играть</button>}
        <div className="scn muted small">{state.scenario.name}</div>
      </header>

      {ended && (
        <div className="ended">
          Смена окончена: {{ completed: 'все поезда доехали', horizon: 'время вышло', deadlock: 'тупик — поезда сцепились', stalled: 'движение остановилось' }[state.status]}
          <button className="primary" onClick={() => onEnd(state.id)}>Итоги смены →</button>
        </div>
      )}

      {state.alerts?.length > 0 && !ended && (
        <div className="alert">
          ⛔ Сцепка: {state.alerts.map((a) => `${a.trains[0]} (${state.names[a.nodes[0]]}) ↔ ${a.trains[1]} (${state.names[a.nodes[1]]})`).join('; ')}
          {' '}— встречные стоят друг перед другом и не могут двинуться. Разворотов в модели нет, так что этот участок встал до конца смены.
          {view === 'map' && <button className="link" onClick={() => setFocus({ node: state.alerts[0].nodes[0], scale: 0.2, ts: Date.now() })}>показать</button>}
        </div>
      )}

      {view === 'map' ? (
        <div className="mapview">
          <WorldMap state={state} speed={speed} stateTime={stateTime} selected={selected} onSelect={setSelected}
            follow={follow} onFollow={setFollow} junctionPref={junctionPref} onJunction={onJunction} focus={focus} />
          <Notifications state={state} onSelect={(id) => { setSelected(id); setFocus({ train: id, scale: 0.08, ts: Date.now() }) }} />
          <div className="jump">
            {state.layout.nodes.filter((n) => n.type !== 'siding').map((n) => (
              <button key={n.id} onClick={() => { setFollow(null); setFocus({ node: n.id, scale: 0.12, ts: Date.now() }) }}>{n.name}</button>
            ))}
          </div>
          <TrainDrawer state={state} selected={selected} follow={follow}
            onPick={(id) => { setSelected(id); setFollow(id); setFocus({ train: id, scale: 0.08, ts: Date.now() }) }} />
          {state.paused && (
            <div className="map-prompt">
              <PromptPanel key={state.prompt?.t} state={state} onCommand={command} onResume={resume}
                onSelect={(id) => { setSelected(id); setFocus({ train: id, scale: 0.08, ts: Date.now() }) }} />
            </div>
          )}
          {selected && (
            <SimpleCard state={state} tid={selected} onCommand={command} follow={follow}
              onFollow={(id) => { setFollow(id); if (id) setFocus({ train: id, scale: 0.08, ts: Date.now() }) }}
              onClose={() => setSelected(null)} />
          )}
          {howto && <HowTo onClose={closeHowto} />}
        </div>
      ) : (
        <div className="main">
          <div className="left">
            <MapView state={state} selected={selected} onSelect={setSelected} />
            <TrainGraph state={state} selected={selected} onSelect={setSelected} />
          </div>
          <aside className="right">
            {state.paused && <PromptPanel key={state.prompt?.t} state={state} onCommand={command} onResume={resume} onSelect={setSelected} />}
            <TrainCard state={state} tid={selected} onCommand={command} />
            <div className="tabs small-tabs">
              {[['trains', 'Поезда'], ['ports', 'Порты'], ['log', 'Журнал']].map(([k, l]) => (
                <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
              ))}
            </div>
            {tab === 'trains' && <TrainList state={state} selected={selected} onSelect={setSelected} />}
            {tab === 'ports' && <PortPanel state={state} onCommand={command} />}
            {tab === 'log' && <EventFeed state={state} />}
          </aside>
        </div>
      )}
      {toast && <div className={`toast ${toast.bad ? 'bad' : ''}`}>{toast.text}</div>}
    </div>
  )
}
