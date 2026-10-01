export function hm(sec) {
  if (sec === null || sec === undefined) return '—'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export const TYPE_LABEL = { passenger: 'пасс.', container: 'контейн.', freight: 'груз.' }
export const TYPE_COLOR = { passenger: 'var(--pass)', container: 'var(--cont)', freight: 'var(--frei)' }

export const STATUS_LABEL = {
  pending: 'ожидается',
  approaching: 'на подходе к участку',
  running: 'в пути',
  passing: 'проследует',
  at_node: 'стоит',
  delivered: 'сдан в порт',
  done: 'прибыл',
}

export const REJECT_LABEL = {
  segment_busy: 'перегон занят',
  segment_outage: 'перегон закрыт (неисправность)',
  maintenance_window: 'на перегоне окно',
  window_conflict: 'не успевает до окна',
  no_track: 'на станции нет свободного пути подходящей длины',
  track_unavailable: 'выбранный путь недоступен',
  not_on_route: 'не по маршруту',
  must_stop: 'обязательная остановка — проход нельзя',
  loco_failure: 'локомотив неисправен',
  dwell_or_timetable: 'рано: стоянка или расписание',
  already_at_destination: 'уже в пункте назначения',
  unknown_train: 'нет такого поезда',
  unknown_command: 'неизвестная команда',
}

export function rejectText(r) {
  if (!r) return ''
  if (r.startsWith('not_ready')) return 'поезд сейчас не может получить этот приказ (уже тормозит или в пути)'
  return REJECT_LABEL[r] || r
}

export function adjacency(layout) {
  const adj = {}
  for (const n of layout.nodes) adj[n.id] = []
  for (const s of layout.segments) {
    adj[s.a].push(s.b)
    adj[s.b].push(s.a)
  }
  return adj
}

export function pathBetween(adj, from, to) {
  const prev = { [from]: null }
  const q = [from]
  while (q.length) {
    const u = q.shift()
    if (u === to) break
    for (const v of adj[u]) if (!(v in prev)) { prev[v] = u; q.push(v) }
  }
  if (!(to in prev)) return null
  const p = []
  for (let x = to; x !== null; x = prev[x]) p.unshift(x)
  return p
}

/** Для поезда с выбором порта: узел развилки и куда с него ехать к каждому порту. */
export function branchChoices(adj, train) {
  if (train.destinations.length < 2) return null
  const start = train.node || train.run?.to || train.origin
  // назад поезд не поедет: путь через ту станцию, откуда он пришёл, не вариант
  const back = train.node ? train.came_from : train.run?.from
  const paths = train.destinations.map((d) => ({ dest: d, path: pathBetween(adj, start, d) }))
    .filter((p) => p.path && !(back && p.path[1] === back))
  if (paths.length < 2) return null
  let i = 0
  while (paths.every((p) => p.path[i + 1] !== undefined && p.path[i + 1] === paths[0].path[i + 1])) i++
  const at = paths[0].path[i]
  if (train.visited.includes(at) && train.node !== at) return null
  return { at, options: paths.map((p) => ({ dest: p.dest, next: p.path[i + 1] })) }
}

export function trainWhere(t, names) {
  const n = (id) => names[id] || id
  if (t.status === 'pending') return `ожидается ${hm(t.forecast_appear)}${t.waiting_for_track ? ' (нет пути)' : ''}`
  if (t.status === 'approaching') return `подходит к ${n(t.run.to)} ${hm(t.run.eta)}`
  if (t.status === 'running') return `${n(t.run.from)} → ${n(t.run.to)}, ETA ${hm(t.run.eta)}`
  if (t.status === 'at_node' || t.status === 'passing') return `${n(t.node)}${t.track ? ` путь ${t.track}` : ''}`
  return n(t.final_node)
}
