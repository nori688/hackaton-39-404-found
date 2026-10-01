import { useEffect, useState } from 'react'
import { api } from './api'
import { hm } from './util'

const ROWS = [
  ['total_delay_min', 'Суммарная задержка, мин', true],
  ['weighted_delay_min', 'Задержка с учётом приоритета', true],
  ['wagon_idle_hours', 'Простой вагонов, ваг·ч', true],
  ['unplanned_stops', 'Внеплановые остановки', true],
  ['wagons_shipped', 'Отправлено паромами, ваг.', false],
  ['wagons_on_target_ferry', 'Успели на свой паром, ваг.', false],
  ['rejected_commands', 'Отклонённые приказы', true],
]

export default function Report({ gameId, onMenu, onReplay }) {
  const [rep, setRep] = useState(null)
  const [err, setErr] = useState(null)
  const [truth, setTruth] = useState(false)
  useEffect(() => { api.report(gameId).then(setRep).catch((e) => setErr(e.message)) }, [gameId])
  if (err) return <div className="menu"><p className="bad">{err}</p><button onClick={onMenu}>в меню</button></div>
  if (!rep) return <div className="menu">Считаем автопилот на том же дне…</div>
  const { player: p, autopilot: a, scenario_truth: raw } = rep

  function download() {
    const blob = new Blob([JSON.stringify(raw, null, 2)], { type: 'application/json' })
    const el = document.createElement('a')
    el.href = URL.createObjectURL(blob)
    el.download = `${raw.id}.json`
    el.click()
  }
  const better = p.weighted_delay_min < a.weighted_delay_min
  const tie = p.weighted_delay_min === a.weighted_delay_min
  const late = raw.trains.filter((t) => t.appear_actual && t.appear_actual !== t.appear)
  return (
    <div className="menu report">
      <h1>Итоги смены</h1>
      <p className="sub">{raw.name}</p>
      <p className={better ? 'ok big' : 'big'}>
        {better ? 'Вы провели смену лучше автопилота по взвешенной задержке.'
          : tie ? 'Ничья: результат совпал с автопилотом.' : 'Автопилот на этом дне справился лучше вас.'}
      </p>
      <table className="cmp">
        <thead><tr><th /><th>Вы</th><th>Автопилот</th></tr></thead>
        <tbody>
          {ROWS.map(([k, label, lowerBetter]) => {
            const win = lowerBetter ? p[k] < a[k] : p[k] > a[k]
            const lose = lowerBetter ? p[k] > a[k] : p[k] < a[k]
            return <tr key={k}><td>{label}</td><td className={win ? 'ok' : lose ? 'bad' : ''}>{p[k]}</td><td>{a[k]}</td></tr>
          })}
          <tr><td>Не доехали</td><td>{p.unfinished_trains.length}</td><td>{a.unfinished_trains.length}</td></tr>
          {Object.keys(p.ports).map((port) => (
            <tr key={port}><td>Пик загрузки порта {port}</td><td>{p.ports[port].peak_pct}%</td><td>{a.ports[port].peak_pct}%</td></tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Автопилот — простая эвристика приоритетов на том же симуляторе и с теми же знаниями. Это базовая линия, а не «идеальное решение».</p>

      <button onClick={() => setTruth(!truth)}>{truth ? 'Скрыть' : 'Показать'}, что было скрыто от вас</button>{' '}
      <button onClick={download}>Скачать день (JSON)</button>{' '}
      <button className="primary" onClick={() => onReplay({ game: gameId })}>🌙 Ночной реплей: разобрать мои решения</button>{' '}
      <button onClick={onMenu}>Новая смена</button>
      {truth && (
        <div className="card truth">
          <h3>Опоздания на входе</h3>
          {late.length ? late.map((t) => (
            <div key={t.id}>{t.id}: план {t.appear}, факт {t.appear_actual}
              {t.forecasts?.length ? ` · прогноз в ${t.forecasts[0].at}: ${t.forecasts[0].eta}` : ' · без предупреждения'}</div>
          )) : <div className="muted">нет</div>}
          <h3>Сбои</h3>
          {raw.disruptions.length ? raw.disruptions.map((d) => (
            <div key={d.id}>{d.kind} {d.train || d.segment} в {d.at || d.start}: факт {d.duration_min} мин
              {d.announced_duration_min ? `, обещали ${d.announced_duration_min}` : ''}{d.announce_at ? ` (объявлено в ${d.announce_at})` : ''}</div>
          )) : <div className="muted">нет</div>}
          <h3>Паромы</h3>
          {raw.ferries.map((f) => (
            <div key={f.id}>{f.id} ({f.port}): план {f.planned_arrival}, факт {f.actual_arrival || f.planned_arrival}
              {f.eta_updates?.length ? ` · прогнозы: ${f.eta_updates.map((u) => `${u.at}→${u.eta}`).join(', ')}` : ''}</div>
          ))}
          {raw.generator && <p className="muted small">seed {raw.generator.seed} · {raw.generator.traffic} / {raw.generator.disruptions}
            {raw.generator.storm ? ` · шторм ${raw.generator.storm.from}–${raw.generator.storm.to}` : ''}</p>}
          <p className="muted small">Конец смены: {hm(p.end_time)}</p>
        </div>
      )}
    </div>
  )
}
