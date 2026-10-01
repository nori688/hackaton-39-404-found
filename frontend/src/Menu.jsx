import { useEffect, useState } from 'react'
import { api } from './api'

const PAUSES = [
  ['approach', 'поезд подходит к станции (проход или остановка)'],
  ['ready', 'поезд стоит и готов к отправлению'],
  ['disruption', 'сбой (поломка, закрытие перегона)'],
  ['ferry', 'паром пришёл'],
]

export default function Menu({ onStart, onReplay }) {
  const [mode, setMode] = useState('map')
  const [tab, setTab] = useState('random')
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 99999) + 1)
  const [traffic, setTraffic] = useState('normal')
  const [dis, setDis] = useState('normal')
  const [storm, setStorm] = useState('auto')
  const [list, setList] = useState([])
  const [picked, setPicked] = useState('day1')
  const [imported, setImported] = useState(null)
  const [importInfo, setImportInfo] = useState(null)
  const [autopilot, setAutopilot] = useState(false)
  const [pauseOn, setPauseOn] = useState(['approach', 'ready', 'disruption', 'ferry'])
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.scenarios().then(setList).catch((e) => setErr(e.message)) }, [])

  async function onFile(e) {
    const f = e.target.files?.[0]
    setImported(null); setImportInfo(null)
    if (!f) return
    try {
      const raw = JSON.parse(await f.text())
      const v = await api.validate(raw)
      setImportInfo(v)
      if (v.ok) setImported(raw)
    } catch (ex) {
      setImportInfo({ ok: false, error: `не JSON: ${ex.message}` })
    }
  }

  async function downloadTemplate() {
    const raw = await api.scenario('day1')
    const blob = new Blob([JSON.stringify(raw, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'шаблон-сценария.json'
    a.click()
  }

  async function start() {
    setBusy(true); setErr(null)
    // простой режим: поезда едут сами, игра встаёт на паузу только при сбоях
    const base = mode === 'map' ? { autopilot: true, pause_on: ['disruption'] } : { autopilot, pause_on: pauseOn }
    let body
    if (tab === 'random') {
      body = { ...base, generate: { seed: Number(seed), traffic, disruptions: dis,
        storm: storm === 'auto' ? null : storm === 'yes' } }
    } else if (tab === 'ready') body = { ...base, scenario: picked }
    else body = { ...base, scenario_data: imported }
    try {
      onStart(await api.newGame(body), mode)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const canStart = tab !== 'import' || imported
  return (
    <div className="menu">
      <h1>Диспетчер однопутного участка</h1>
      <p className="sub">Восток → узел (≈Бейнеу) → порты Актау и Курык. Вы — поездной диспетчер на смену.
        Симулятор не подсказывает будущего: вы видите только то, что видел бы диспетчер.</p>

      <div className="modes">
        <button className={`mode ${mode === 'map' ? 'on' : ''}`} onClick={() => setMode('map')}>
          <span className="mode-ico">🗺</span>
          <b>Простой — живая карта</b>
          <span>Поезда в реальную длину едут по карте сами. Вы придерживаете, пропускаете и переводите стрелку на порт.</span>
        </button>
        <button className={`mode ${mode === 'dispatcher' ? 'on' : ''}`} onClick={() => setMode('dispatcher')}>
          <span className="mode-ico">📈</span>
          <b>Диспетчер — схема и график</b>
          <span>Как у настоящего ДНЦ: график движения, ручные решения на каждом подходе, паузы по выбору.</span>
        </button>
      </div>

      <div className="tabs">
        <button className={tab === 'random' ? 'on' : ''} onClick={() => setTab('random')}>Случайный день</button>
        <button className={tab === 'ready' ? 'on' : ''} onClick={() => setTab('ready')}>Готовый сценарий</button>
        <button className={tab === 'import' ? 'on' : ''} onClick={() => setTab('import')}>Данные станции</button>
      </div>

      <div className="card">
        {tab === 'random' && (
          <div className="grid2">
            <label>Seed
              <span className="row">
                <input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
                <button onClick={() => setSeed(Math.floor(Math.random() * 99999) + 1)}>🎲</button>
              </span>
            </label>
            <label>Трафик
              <select value={traffic} onChange={(e) => setTraffic(e.target.value)}>
                <option value="low">низкий (~14 поездов)</option>
                <option value="normal">обычный (~21)</option>
                <option value="high">плотный (~31)</option>
              </select>
            </label>
            <label>Сбои
              <select value={dis} onChange={(e) => setDis(e.target.value)}>
                <option value="none">нет</option>
                <option value="low">мало</option>
                <option value="normal">обычно</option>
                <option value="high">много</option>
              </select>
            </label>
            <label>Шторм на Каспии
              <select value={storm} onChange={(e) => setStorm(e.target.value)}>
                <option value="auto">как выпадет</option>
                <option value="yes">да</option>
                <option value="no">нет</option>
              </select>
            </label>
            <p className="hint full">Один и тот же seed с теми же настройками даёт тот же день: можно сыграть его повторно или дать сыграть коллеге.</p>
          </div>
        )}
        {tab === 'ready' && (
          <div className="list">
            {list.map((s) => (
              <label key={s.id} className="radio">
                <input type="radio" checked={picked === s.id} onChange={() => setPicked(s.id)} />
                <b>{s.id}</b> — {s.name} <span className="muted">({s.trains} поездов, {s.ferries} паромов)</span>
              </label>
            ))}
          </div>
        )}
        {tab === 'import' && (
          <div>
            <p>Загрузите JSON со станции в формате сценария: инфраструктура, поезда, паромы, сбои.
              Данные проверяются тем же загрузчиком, что и синтетика.</p>
            <input type="file" accept=".json,application/json" onChange={onFile} />
            <button className="link" onClick={downloadTemplate}>скачать шаблон формата</button>
            {importInfo && (importInfo.ok
              ? <p className="ok">✓ {importInfo.summary.name || importInfo.summary.id}: {importInfo.summary.trains} поездов, {importInfo.summary.ferries} паромов, {importInfo.summary.disruptions} сбоев</p>
              : <p className="bad">✗ {importInfo.error}</p>)}
          </div>
        )}
      </div>

      {mode === 'dispatcher' && <div className="card">
        <label className="check"><input type="checkbox" checked={autopilot} onChange={(e) => setAutopilot(e.target.checked)} />
          Автопилот для всех поездов (вы вмешиваетесь, когда хотите)</label>
        <div className="muted small">Ставить игру на паузу, когда:</div>
        {PAUSES.map(([k, label]) => (
          <label key={k} className="check">
            <input type="checkbox" checked={pauseOn.includes(k)}
              onChange={(e) => setPauseOn(e.target.checked ? [...pauseOn, k] : pauseOn.filter((x) => x !== k))} />
            {label}
          </label>
        ))}
      </div>}

      {err && <p className="bad">{err}</p>}
      <div className="row start-row">
        <button className="primary big" disabled={!canStart || busy} onClick={start}>
          {busy ? 'Готовим смену…' : 'Принять смену ▶'}
        </button>
        {tab === 'ready' && (
          <button className="big" onClick={() => onReplay({ scenario: picked, dispatcher: 'scripted' })}>
            🌙 Ночной реплей записанного диспетчера</button>
        )}
        {tab === 'random' && (
          <button className="big" onClick={() => onReplay({ generate: { seed: Number(seed), traffic, disruptions: dis,
            storm: storm === 'auto' ? null : storm === 'yes' }, dispatcher: 'priority' })}>
            🌙 Разобрать этот день (вёл автопилот)</button>
        )}
      </div>
    </div>
  )
}
