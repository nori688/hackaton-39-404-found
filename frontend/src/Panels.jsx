import { useState } from 'react'
import { STATUS_LABEL, TYPE_COLOR, TYPE_LABEL, adjacency, branchChoices, hm, rejectText, trainWhere } from './util'

const DIS_LABEL = { loco_failure: 'Неисправность локомотива', segment_outage: 'Закрытие перегона' }

export function PromptPanel({ state, onCommand, onResume, onSelect }) {
  const [done, setDone] = useState({})
  const { prompt, names } = state
  if (!prompt) return null
  const n = (id) => names[id] || id
  const mark = (k, v) => setDone((d) => ({ ...d, [k]: v }))
  return (
    <div className="prompt">
      <div className="prompt-head">⏸ {hm(prompt.t)} — нужно ваше решение</div>
      {prompt.reasons.map((r, i) => {
        const key = `${prompt.t}-${i}`
        if (r.kind === 'approach' || r.kind === 'ready') {
          const opts = state.obs.legal.filter((a) => a.train === r.train)
          return (
            <div key={key} className="reason">
              <div>
                <a onClick={() => onSelect(r.train)}><b>{r.train}</b></a>{' '}
                {r.kind === 'approach' ? `подходит к ${n(r.at)}` : `готов к отправлению с ${n(r.at)}`}
              </div>
              {done[key] ? <div className="muted">→ {done[key]}</div> : (
                <div className="btns">
                  {opts.map((a) => (
                    <button key={a.next} className="go" onClick={() => {
                      onCommand({ type: 'dispatch', train: r.train, next: a.next })
                      mark(key, `${r.kind === 'approach' ? 'проход' : 'отправить'} на ${n(a.next)}`)
                    }}>{r.kind === 'approach' ? 'Пропустить' : 'Отправить'} → {n(a.next)}</button>
                  ))}
                  <button onClick={() => mark(key, r.kind === 'approach' ? 'остановить' : 'ждать')}>
                    {r.kind === 'approach' ? 'Остановить' : 'Ждать'}
                  </button>
                </div>
              )}
            </div>
          )
        }
        if (r.kind === 'disruption') {
          const d = r.disruption
          return (
            <div key={key} className="reason warn">
              ⚠ {DIS_LABEL[d.kind] || d.kind}: {d.train || d.segment} с {hm(d.since)}, обещают до ~{hm(d.est_until)}
            </div>
          )
        }
        return <div key={key} className="reason">⛴ Паром {r.ferry} прибыл в {n(r.port)}</div>
      })}
      <button className="primary" onClick={() => { setDone({}); onResume() }}>Продолжить ▶ <span className="kbd">пробел</span></button>
    </div>
  )
}

export function TrainCard({ state, tid, onCommand }) {
  const { obs, names, orders, clock, layout } = state
  const t = obs.trains.find((x) => x.id === tid)
  if (!t) return <div className="card muted">Выберите поезд на схеме, графике или в списке.</div>
  const n = (id) => names[id] || id
  const legal = obs.legal.filter((a) => a.train === tid)
  const auto = orders.auto_override[tid] ?? orders.autopilot
  const held = tid in orders.holds
  const passAt = orders.pass_next[tid]
  const br = branchChoices(adjacency(layout), t)
  const route = orders.routes.find((r) => r.train === tid && br && r.at === br.at)
  const stopsAt = (node) => t.destinations.includes(node) || t.stops.some((s) => s.node === node)
  const late = Math.max(0, ((t.arrived_at ?? clock) - t.planned_arrival) / 60)
  return (
    <div className="card train-card">
      <div className="tc-head">
        <span className="dot" style={{ background: TYPE_COLOR[t.type] }} />
        <b>{t.id}</b> {TYPE_LABEL[t.type]} · {t.length_m} м · {t.wagons} ваг.
        <span className="muted"> · вес {t.weight}</span>
      </div>
      <div>{STATUS_LABEL[t.status]}: {trainWhere(t, names)}</div>
      <div className="muted small">
        {n(t.origin)} → {t.destinations.map(n).join(' / ')} · план прибытия {hm(t.planned_arrival)}
        {t.target_ferry ? ` · паром ${t.target_ferry}` : ''}
        {late > 0 ? <b className="bad"> · опоздание {Math.round(late)} мин</b> : null}
      </div>
      {t.stops.filter((s) => s.min_dwell_s || s.not_before).map((s) => (
        <div key={s.node} className="muted small">остановка {n(s.node)}{s.min_dwell_s ? ` ${s.min_dwell_s / 60} мин` : ''}{s.not_before ? `, не раньше ${hm(s.not_before)}` : ''}</div>
      ))}
      {t.failure && <div className="bad">⚠ неисправность с {hm(t.failure.since)}, обещают до ~{hm(t.failure.est_until)}</div>}
      {t.status === 'at_node' && t.ready_at_est > clock && <div className="muted small">готов не раньше {hm(t.ready_at_est)}</div>}
      {t.port && <div className="small">в порту: осталось {t.port.remaining} ваг., к погрузке с {hm(t.port.available_at)}</div>}

      {!['done', 'delivered'].includes(t.status) && (
        <div className="actions">
          {legal.map((a) => (
            <button key={a.next} className="go" onClick={() => onCommand({ type: 'dispatch', train: tid, next: a.next })}>
              {a.kind === 'through' ? 'Пропустить' : 'Отправить'} → {n(a.next)}
            </button>
          ))}
          {t.status === 'running' && t.run && !stopsAt(t.run.to) && !t.run.stopping && !t.run.awaiting_decision && (
            passAt === t.run.to
              ? <button className="on" onClick={() => onCommand({ type: 'cancel_pass', train: tid })}>✓ пропуск на {n(t.run.to)} (отменить)</button>
              : <button onClick={() => onCommand({ type: 'pass_next', train: tid, node: t.run.to })}>Пропустить на проход {n(t.run.to)}, если свободно</button>
          )}
          {held
            ? <button className="on" onClick={() => onCommand({ type: 'release', train: tid })}>
                ✋ держим{orders.holds[tid] ? ` до ${hm(orders.holds[tid])}` : ''} — отпустить</button>
            : <>
                <button onClick={() => onCommand({ type: 'hold', train: tid, until: null })}>Держать</button>
                <button onClick={() => onCommand({ type: 'hold', train: tid, until: Math.floor(clock + 1800) })}>Держать 30 мин</button>
              </>}
          {br && br.options.map((o) => (
            <button key={o.dest} className={route?.next === o.next ? 'on' : ''}
              onClick={() => onCommand({ type: 'route', train: tid, at: br.at, next: o.next })}>
              {route?.next === o.next ? '✓ ' : ''}через {n(o.next)} в {n(o.dest)}
            </button>
          ))}
          <label className="check small">
            <input type="checkbox" checked={auto} onChange={(e) => onCommand({ type: 'auto', train: tid, on: e.target.checked })} />
            автопилот для этого поезда
          </label>
        </div>
      )}
    </div>
  )
}

export function TrainList({ state, selected, onSelect }) {
  const { obs, names, clock } = state
  const order = { at_node: 0, passing: 0, running: 1, approaching: 2, pending: 3, delivered: 4, done: 5 }
  const rows = [...obs.trains].sort((a, b) => (order[a.status] - order[b.status]) || a.planned_arrival - b.planned_arrival)
  return (
    <div className="card tlist">
      <table>
        <thead><tr><th>поезд</th><th>где</th><th>план</th><th>±</th></tr></thead>
        <tbody>
          {rows.map((t) => {
            const end = t.arrived_at ?? (t.run?.to && t.destinations.includes(t.run.to) ? t.run.eta : null)
            const late = Math.round(((t.arrived_at ?? Math.max(clock, end ?? clock)) - t.planned_arrival) / 60)
            return (
              <tr key={t.id} className={`${selected === t.id ? 'sel' : ''} ${t.failure ? 'fail' : ''}`} onClick={() => onSelect(t.id)}>
                <td><span className="dot" style={{ background: TYPE_COLOR[t.type] }} />{t.id}</td>
                <td className="small">{trainWhere(t, names)}</td>
                <td>{hm(t.planned_arrival)}</td>
                <td className={late > 0 ? 'bad' : 'muted'}>{late > 0 ? `+${late}` : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function PortPanel({ state, onCommand }) {
  const { obs, names } = state
  const ports = obs.nodes.filter((n) => n.type === 'port')
  return (
    <div className="card">
      {ports.map((p) => {
        const waiting = obs.trains.filter((t) => t.port && t.port.node === p.id && t.port.remaining > 0)
        const order = obs.ferry_order[p.id] || []
        const sorted = [...waiting].sort((a, b) => {
          const ia = order.indexOf(a.id), ib = order.indexOf(b.id)
          return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.port.available_at - b.port.available_at
        })
        return (
          <div key={p.id} className="port">
            <b>{names[p.id]}</b> <span className="muted small">очередь на паром</span>
            {sorted.length === 0 && <div className="muted small">пусто</div>}
            {sorted.map((t, i) => (
              <div key={t.id} className="small row">
                {i + 1}. {t.id} — {t.port.remaining} ваг. (с {hm(t.port.available_at)})
                {i > 0 && <button className="tiny" onClick={() => {
                  const ids = sorted.map((x) => x.id)
                  ids.splice(i, 1); ids.unshift(t.id)
                  onCommand({ type: 'ferry_order', port: p.id, order: ids })
                }}>первым</button>}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

const EV = {
  appear: (e, n) => `${e.train} появился у ${n(e.node)}`,
  appear_blocked: (e, n) => `${e.train}: нет свободного пути на ${n(e.node)} для формирования`,
  arrive: (e, n) => `${e.train} прибыл на ${n(e.node)}, путь ${e.track}`,
  pass: (e, n) => `${e.train} проследовал ${n(e.node)}`,
  depart: (e, n) => `${e.train} отправился с ${n(e.node)}${e.flying ? ' (на ходу)' : ''}`,
  held_at_boundary: (e, n) => `${e.train} остановлен перед участком (${n(e.node)})`,
  terminate: (e, n) => `${e.train} прибыл в пункт назначения ${n(e.node)}`,
  exit: (e) => `${e.train} ушёл с участка`,
  loco_failure: (e) => `⚠ ${e.train}: неисправность локомотива, обещают ~${e.est_min} мин`,
  failure_end: (e) => `${e.train}: локомотив исправен`,
  segment_outage: (e) => `⚠ перегон ${e.segment} закрыт, обещают ~${e.est_min} мин`,
  outage_end: (e) => `перегон ${e.segment} открыт`,
  window_delayed: (e) => `окно на ${e.segment} задерживается: перегон занят`,
  window_start: (e) => `окно на ${e.segment} началось${e.shift_min ? ` (сдвиг ${e.shift_min} мин)` : ''}`,
  window_end: (e) => `окно на ${e.segment} закончилось`,
  ferry_arrive: (e, n) => `⛴ паром ${e.ferry} прибыл в ${n(e.port)}`,
  ferry_berth: (e) => `⛴ паром ${e.ferry} у причала`,
  ferry_depart: (e) => `⛴ паром ${e.ferry} ушёл, ${e.wagons} ваг.`,
  wagons_cleared: (e) => `${e.train}: все вагоны погружены, путь порта свободен`,
  start_blocked: (e) => `${e.train} не может отправиться (сбой) до ~${hm(e.until)}`,
}

export function EventFeed({ state }) {
  const { log, rejections, names } = state
  const n = (id) => names[id] || id
  const items = [
    ...log.map((e) => ({ t: e.t, text: (EV[e.event] || ((x) => x.event))(e, n), bad: false })),
    ...rejections.map((r) => ({ t: r.t, text: `✗ ${r.command.train || ''}: ${rejectText(r.reason)}`, bad: true })),
  ].sort((a, b) => b.t - a.t).slice(0, 40)
  return (
    <div className="card feed">
      {items.map((it, i) => (
        <div key={i} className={it.bad ? 'bad' : ''}><span className="muted">{hm(it.t)}</span> {it.text}</div>
      ))}
    </div>
  )
}
