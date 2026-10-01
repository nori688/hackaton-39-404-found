import { useEffect, useState } from 'react'
import { replayApi } from './api'
import ReplayGraph from './ReplayGraph'

const SECTIONS = [
  ['missed', 'Упущенные возможности', 'По тому, что было известно в момент решения, другой вариант был лучше.'],
  ['tradeoffs', 'Жертва ради нескольких', 'Один поезд ждёт — несколько других выигрывают больше, чем он теряет.'],
  ['good', 'Удачные решения', 'Все проверенные альтернативы оказались хуже.'],
  ['better_than_algo', 'Лучше алгоритма', 'Здесь автопилот поступил бы иначе — и проиграл бы.'],
  ['bad_luck', 'Невезение', 'Лучше было бы другое — но только с учётом того, чего тогда знать было нельзя.'],
]

/** В подписях из движка — коды станций; показываем человеческие названия. */
function humanize(text, names) {
  if (!text) return text
  const ids = Object.keys(names).sort((a, b) => b.length - a.length)
  const re = new RegExp('(^|[^A-Za-z0-9_])(' + ids.join('|') + ')(?![A-Za-z0-9_])', 'g')
  return text.replace(re, (m, pre, id) => pre + (names[id] || id))
}

const fmt = (x) => `${x > 0 ? '+' : ''}${Math.round(x)}`
const cls = (x) => (x < -0.5 ? 'ok' : x > 0.5 ? 'bad' : 'muted')
const agreeText = (a, n = 4) => `${Math.round(a * n)} из ${n}`

function TrainChips({ trains, limit = 6 }) {
  const items = Object.entries(trains || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, limit)
  if (!items.length) return null
  return (
    <div className="chips">
      {items.map(([tid, d]) => <span key={tid} className={`chip-d ${d < 0 ? 'win' : 'lose'}`}>{tid} {fmt(d)}</span>)}
    </div>
  )
}

function Card({ c, onCompare, active, names }) {
  const h = (x) => humanize(x, names)
  return (
    <div className={`rcard ${c.kind} ${active ? 'active' : ''}`}>
      <div className="rc-head">
        <span className="rc-time">{c.t_str}</span>
        <span className="muted">{c.node_name}</span>
      </div>
      <div className="small muted">Сделано: {c.fact}</div>
      {c.kind === 'good'
        ? <div className="rc-label">Решение оказалось удачным</div>
        : c.kind === 'better_than_algo'
          ? <div className="rc-label">Автопилот на этом месте: {h(c.label.replace('Как поступил бы автопилот: ', ''))}</div>
          : <div className="rc-label">Можно было: {h(c.label)}</div>}
      {c.kind === 'good' ? (
        <div className="small">
          {c.alternatives.map((a) => (
            <div key={a.label}>«{h(a.label)}» — <b className={cls(a.delta_honest)}>{fmt(a.delta_honest)}</b> мин</div>
          ))}
        </div>
      ) : (
        <div className="deltas">
          <div>
            <b className={cls(c.delta_honest)}>{fmt(c.delta_honest)}</b> взв. мин
            <span className="muted small"> — по тому, что знали тогда ({agreeText(c.agree_honest)} вариантов будущего)</span>
          </div>
          <div>
            <b className={cls(c.delta_true)}>{fmt(c.delta_true)}</b> взв. мин
            <span className="muted small"> — по тому, как на самом деле прошёл день</span>
          </div>
        </div>
      )}
      {c.kind === 'tradeoff' && (
        <div className="small trade">
          Жертва: {Object.entries(c.sacrificed).map(([k, v]) => `${k} ${fmt(v)} мин`).join(', ')}
          {' → '}выигрывают: {Object.entries(c.winners).map(([k, v]) => `${k} ${fmt(v)}`).join(', ')}
        </div>
      )}
      {c.kind === 'bad_luck' && c.hidden?.length > 0 && (
        <div className="small hidden-list">
          Чего нельзя было знать в {c.t_str}:
          <ul>{c.hidden.map((h, i) => <li key={i}>{h.text}</li>)}</ul>
        </div>
      )}
      {c.kind !== 'good' && <TrainChips trains={c.kind === 'bad_luck' ? c.trains_true : c.trains_honest} />}
      {(c.gridlock_risk?.honest?.[1] > 0 || c.gridlock_risk?.true?.[1] > 0) && (
        <div className="small warnc">⚠ в части вариантов будущего этот путь ведёт к сцепке</div>
      )}
      <button onClick={() => onCompare(c)}>📈 Сравнить «было / могло быть»</button>
    </div>
  )
}

function Compare({ job, report, card, onClose }) {
  const [data, setData] = useState(null)
  const [world, setWorld] = useState(card.kind === 'bad_luck' ? 'true' : 'honest')
  const [full, setFull] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => {
    replayApi.compare(job, card.compare_id).then(setData).catch((e) => setErr(e.message))
  }, [job, card.compare_id])
  const w = data?.[world]
  const changed = Object.keys((world === 'honest' ? card.trains_honest : card.trains_true) || {})
  const rowsM = [['weighted', 'Задержка с учётом приоритета, мин'], ['total', 'Суммарная задержка, мин'],
    ['wagon_hours', 'Простой вагонов, ваг·ч'], ['shipped', 'Отправлено паромами, ваг.'], ['unfinished', 'Не доехали']]
  return (
    <div className="compare card">
      <div className="row cmp-head">
        <b>{card.t_str} · {card.node_name}: {humanize(card.label, report.names)}</b>
        <button className="link" onClick={onClose}>закрыть ×</button>
      </div>
      <div className="row">
        <div className="tabs small-tabs">
          <button className={world === 'honest' ? 'on' : ''} onClick={() => setWorld('honest')}>Как ожидал диспетчер</button>
          <button className={world === 'true' ? 'on' : ''} onClick={() => setWorld('true')}>Как прошёл день</button>
        </div>
        <label className="check small"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> весь день</label>
        <span className="muted small legend"><span className="lg base" /> решение смены <span className="lg alt" /> альтернатива</span>
      </div>
      {err && <p className="bad">{err}</p>}
      {!w ? <p className="muted">Загружаем графики…</p> : (
        <>
          <ReplayGraph layout={report.layout} names={report.names} types={report.types} horizon={report.horizon}
            base={w.base} alt={w.alt} t={data.t} node={data.node} involved={data.involved} changed={changed} full={full} />
          <table className="cmp">
            <thead><tr><th /><th>Решение смены</th><th>Альтернатива</th><th>Разница</th></tr></thead>
            <tbody>
              {rowsM.map(([k, label]) => (
                <tr key={k}><td>{label}</td><td>{w.base_m[k]}</td><td>{w.alt_m[k]}</td>
                  <td className={k === 'shipped' ? cls(w.base_m[k] - w.alt_m[k]) : cls(w.alt_m[k] - w.base_m[k])}>
                    {fmt(w.alt_m[k] - w.base_m[k])}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            {world === 'honest'
              ? 'Первый из вариантов будущего, построенных из того, что диспетчер знал в момент решения: прогнозы, объявленные сроки ремонтов, без сбоев, о которых ещё не сообщили.'
              : 'Настоящее будущее дня: все сбои, реальные опоздания и паромы. После развилки день доигрывает одна и та же политика — и в «было», и в «могло быть».'}
          </p>
        </>
      )}
    </div>
  )
}

export default function Replay({ request, onBack }) {
  const [job, setJob] = useState(null)
  const [st, setSt] = useState(null)
  const [err, setErr] = useState(null)
  const [tab, setTab] = useState('missed')
  const [card, setCard] = useState(null)

  useEffect(() => {
    let alive = true
    let timer
    replayApi.start(request).then(({ id }) => {
      setJob(id)
      const poll = async () => {
        try {
          const s = await replayApi.status(id)
          if (!alive) return
          setSt(s)
          if (s.status === 'running') timer = setTimeout(poll, 700)
        } catch (e) { if (alive) setErr(e.message) }
      }
      poll()
    }).catch((e) => setErr(e.message))
    return () => { alive = false; clearTimeout(timer) }
  }, [request])

  if (err || st?.status === 'error') {
    return <div className="menu"><p className="bad">{err || st.error}</p><button onClick={onBack}>назад</button></div>
  }
  if (!st || st.status === 'running') {
    const pct = st?.total ? Math.round((100 * st.done) / st.total) : 0
    return (
      <div className="menu">
        <h1>🌙 Ночной реплей</h1>
        <p className="sub">Ищем точки решений и перепрогоняем остаток дня для каждой альтернативы — в нескольких вариантах будущего.</p>
        <div className="progress"><div style={{ width: `${pct}%` }} /></div>
        <p className="muted">{st?.total ? `Развилок проверено: ${st.done} из ${st.total}` : 'Воспроизводим смену и собираем точки решений…'}</p>
      </div>
    )
  }
  const r = st.result
  const fm = r.fact_metrics
  const list = r[tab] || []
  return (
    <div className="replay">
      <header className="bar">
        <button className="link" onClick={onBack}>← назад</button>
        <b>🌙 Ночной реплей</b>
        <span className="muted small">{r.scenario.name} · вёл: {r.source}</span>
      </header>
      <div className="replay-body">
        <div className="tiles">
          <div className="tile"><div className="muted small">Факт смены</div>
            <div className="big">{fm.weighted_delay_min} <span className="small">взв. мин</span></div>
            <div className="small muted">суммарно {fm.total_delay_min} мин · {fm.wagons_shipped} ваг. на паромах</div></div>
          <div className="tile"><div className="muted small">Лучшая найденная возможность</div>
            {r.best ? (<>
              <div className="big ok">{fmt(r.best.delta_honest)} <span className="small">взв. мин</span></div>
              <div className="small">{r.best.t_str} · {r.best.node_name}: {humanize(r.best.label, r.names)}</div></>)
              : <div className="big">—</div>}</div>
          <div className="tile"><div className="muted small">Проверено</div>
            <div className="big">{r.stats.points} <span className="small">развилок</span></div>
            <div className="small muted">{r.stats.alternatives} альтернатив · {r.stats.runs} прогонов дня · {r.stats.seconds} с</div></div>
          <div className="tile"><div className="muted small">Удачных решений</div>
            <div className="big ok">{r.good.length + r.better_than_algo.length}</div>
            <div className="small muted">исход зависит от случая: {r.stats.uncertain}</div></div>
        </div>
        <p className="muted small blame">Это разбор для обучения, а не оценка работы: решения сравниваются с тем, что было известно
          в тот момент. «Невезение» отделено от упущенных возможностей.</p>

        <div className="replay-main">
          <div className="replay-list">
            <div className="tabs">
              {SECTIONS.map(([k, label]) => (
                <button key={k} className={tab === k ? 'on' : ''} onClick={() => { setTab(k); setCard(null) }}>
                  {label} <span className="count">{r[k].length}</span></button>
              ))}
              <button className={tab === 'nodes' ? 'on' : ''} onClick={() => setTab('nodes')}>Проблемные разъезды</button>
            </div>
            {tab === 'nodes' ? (
              <div className="card">
                <p className="muted small">Сколько взвешенных минут упущено на каждой станции (по честной оценке).</p>
                {r.problem_nodes.length === 0 && <p className="muted">Нет.</p>}
                {r.problem_nodes.map((n) => {
                  const maxM = r.problem_nodes[0].minutes || 1
                  return (
                    <div key={n.node} className="nbar">
                      <span>{n.name}</span>
                      <div className="nbar-track"><div style={{ width: `${(100 * n.minutes) / maxM}%` }} /></div>
                      <span className="small">{Math.round(n.minutes)} мин · {n.count}</span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <>
                <p className="muted small">{SECTIONS.find((s) => s[0] === tab)[2]}</p>
                {list.length === 0 && <p className="muted">Таких развилок не нашлось.</p>}
                <div className="rcards">
                  {list.map((c) => <Card key={`${c.compare_id}-${c.kind}`} c={c} names={r.names} active={card === c} onCompare={setCard} />)}
                </div>
              </>
            )}
          </div>
          {card && <Compare key={card.compare_id + card.kind} job={job} report={r} card={card} onClose={() => setCard(null)} />}
        </div>
        <details className="method">
          <summary>Как считали</summary>
          <ul className="small">
            <li>Смена воспроизведена тем же симулятором. В каждом моменте, где был выбор, построены 2–3 альтернативы — только из того, что было видно на табло.</li>
            <li>Остаток дня перепрогнан для решения смены и для каждой альтернативы; {r.method.continuation}.</li>
            <li>«Как ожидал диспетчер» — будущее по прогнозам и объявленным срокам, без сбоев, о которых ещё не знали; «как прошёл день» — настоящие события.</li>
            <li>В каждом режиме — {r.method.samples} варианта будущего (разброс времени хода и погрешность прогнозов). Вывод делается, только если согласны не меньше {Math.round(r.method.agree * r.method.samples)} из {r.method.samples}, по медиане.</li>
            <li>Метрика — {r.method.metric}. Порог значимости — {r.method.threshold_min} мин.</li>
          </ul>
        </details>
      </div>
    </div>
  )
}
