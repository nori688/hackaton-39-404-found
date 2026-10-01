/**
 * Геометрия «живой карты» в метрах — в реальном масштабе.
 * Перегоны — по длине в км, пути станций — по полезной длине, поезда — по своей длине.
 * Направления берутся из схемы (x, y узлов), расстояния — из данных.
 * Отступление от масштаба одно: поперёк (между путями, ширина платформ) на экране держится
 * минимум в несколько пикселей, иначе на мелком масштабе пути слипаются.
 *
 * Декор (вокзалы, деревья, склады) — только оформление, строится детерминированно
 * из раскладки и на симуляцию не влияет.
 */
import { adjacency, pathBetween } from './util'

const unit = (x, y) => {
  const l = Math.hypot(x, y)
  return l > 1e-9 ? { x: x / l, y: y / l } : null
}
export const add = (p, d, k = 1) => ({ x: p.x + d.x * k, y: p.y + d.y * k })
export const normal = (a) => ({ x: -a.y, y: a.x })

export const PASSENGER_TYPES = new Set(['station', 'junction', 'port'])
export const gapOf = (k) => Math.max(5.3, 9 / k)          // междупутье
export const platOf = (k) => Math.max(7, 11 / k)          // ширина платформы
const islandAt = (node) => node.type === 'station' || node.type === 'junction'

export function buildWorld(layout) {
  const nodes = Object.fromEntries(layout.nodes.map((n) => [n.id, n]))
  const adj = adjacency(layout)
  const segOf = {}
  for (const s of layout.segments) {
    segOf[`${s.a}|${s.b}`] = s
    segOf[`${s.b}|${s.a}`] = s
  }
  const root = (layout.nodes.find((n) => n.type === 'boundary') || layout.nodes[0]).id
  const pos = { [root]: { x: 0, y: 0 } }
  const parent = { [root]: null }
  const queue = [root]
  while (queue.length) {
    const u = queue.shift()
    adj[u].forEach((v, i) => {
      if (v in pos) return
      const d = unit(nodes[v].x - nodes[u].x, nodes[v].y - nodes[u].y)
        || { x: -Math.cos((i - 1) * 0.6), y: Math.sin((i - 1) * 0.6) }
      pos[v] = add(pos[u], d, segOf[`${u}|${v}`].length_km * 1000)
      parent[v] = u
      queue.push(v)
    })
  }
  const axis = {}
  for (const id of Object.keys(pos)) {
    const p = parent[id]
    axis[id] = p ? unit(pos[id].x - pos[p].x, pos[id].y - pos[p].y)
      : unit(pos[adj[id][0]].x - pos[id].x, pos[adj[id][0]].y - pos[id].y) || { x: -1, y: 0 }
  }

  // пути станций: главный по оси, остальные по сторонам; пути порта — с одной стороны
  const tracks = {}, half = {}
  for (const n of layout.nodes) {
    const mains = n.tracks.filter((t) => t.kind !== 'port')
    const ports = n.tracks.filter((t) => t.kind === 'port')
    const list = []
    mains.forEach((t, i) => list.push({ ...t, k: n.type === 'port' ? -i : i === 0 ? 0 : (i % 2 ? 1 : -1) * Math.ceil(i / 2) }))
    ports.forEach((t, i) => list.push({ ...t, k: i + 1 }))
    tracks[n.id] = list
    half[n.id] = n.tracks.length ? Math.max(...n.tracks.map((t) => t.length_m)) / 2 + 90 : 0
  }
  const east = (id) => add(pos[id], axis[id], -half[id])
  const west = (id) => add(pos[id], axis[id], half[id])
  // к какой горловине примыкает перегон: «вперёд» по оси станции — к западной, «назад» — к восточной.
  // Так ветка на узле подходит с нужной стороны и поезд проходит узел насквозь, без разворота
  const side = (u, v) => {
    const d = unit(pos[v].x - pos[u].x, pos[v].y - pos[u].y)
    return d && d.x * axis[u].x + d.y * axis[u].y > 0 ? 'west' : 'east'
  }
  const throat = (u, v) => (side(u, v) === 'west' ? west(u) : east(u))
  // наружу от границы участка (оттуда приходят поезда соседнего участка)
  const outward = (b) => unit(pos[b].x - pos[adj[b][0]].x, pos[b].y - pos[adj[b][0]].y) || { x: 1, y: 0 }

  const segs = {}
  for (const s of layout.segments) {
    const [u, v] = parent[s.b] === s.a ? [s.a, s.b] : [s.b, s.a]
    segs[s.id] = { id: s.id, u, v, p0: throat(u, v), p1: throat(v, u) }
  }
  const boundaries = layout.nodes.filter((n) => n.type === 'boundary').map((n) => n.id)

  const portIds = layout.nodes.filter((n) => n.type === 'port').map((n) => n.id)
  const piers = Object.fromEntries(portIds.map((id) => {
    const start = west(id)
    return [id, { start, coast: add(start, axis[id], 150), berth: add(start, axis[id], 480), axis: axis[id] }]
  }))
  const coast = coastline(portIds.map((id) => piers[id]))

  const pts = [...Object.values(pos), ...boundaries.map((b) => add(pos[b], outward(b), 8000))]
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y)
  const bbox = { x0: Math.min(...xs) - 5000, x1: Math.max(...xs) + 5000, y0: Math.min(...ys) - 5000, y1: Math.max(...ys) + 5000 }
  const w = { nodes, adj, pos, parent, axis, tracks, half, east, west, side, throat, outward, boundaries, segs, piers, coast, root, bbox, segOf }
  w.decor = buildDecor(w)
  return w
}

function coastline(piers) {
  if (!piers.length) return null
  const pts = piers.map((p) => p.coast).sort((a, b) => a.y - b.y)
  const out = [{ x: pts[0].x + 2500, y: pts[0].y - 250000 }, { x: pts[0].x + 1200, y: pts[0].y - 9000 }]
  pts.forEach((p, i) => {
    out.push({ x: p.x + 60, y: p.y - 700 }, { x: p.x, y: p.y }, { x: p.x + 60, y: p.y + 700 })
    const q = pts[i + 1]
    if (q) out.push({ x: Math.max(p.x, q.x) + 3500, y: (p.y + q.y) / 2 })
  })
  const last = pts[pts.length - 1]
  out.push({ x: last.x + 1500, y: last.y + 9000 }, { x: last.x + 2800, y: last.y + 250000 })
  return out
}

/** x береговой линии на широте y (западнее — море). */
export function coastX(w, y) {
  const c = w.coast
  if (!c) return -Infinity
  for (let i = 0; i < c.length - 1; i++) {
    const a = c[i], b = c[i + 1]
    if ((y >= a.y && y <= b.y) || (y <= a.y && y >= b.y)) {
      const f = b.y === a.y ? 0 : (y - a.y) / (b.y - a.y)
      return a.x + (b.x - a.x) * f
    }
  }
  return -Infinity
}

// ------------------------------------------------------------------ поперечная раскладка станции

/** Смещение пути поперёк оси станции, м. На больших станциях между 1-м и 2-м путём — островная платформа. */
export function trackV(w, node, t, k) {
  let v = t.k * gapOf(k)
  if (islandAt(w.nodes[node]) && t.k >= 1) v += platOf(k)
  return v
}

/** Кадр станции на текущем масштабе: края путевого поля, платформы, здание. */
export function frame(w, node, k) {
  const n = w.nodes[node]
  const list = w.tracks[node]
  const g = gapOf(k), P = platOf(k)
  const vs = list.map((t) => trackV(w, node, t, k))
  const vMin = Math.min(...vs), vMax = Math.max(...vs)
  const pass = PASSENGER_TYPES.has(n.type)
  const f = { a: w.axis[node], n: normal(w.axis[node]), c: w.pos[node], half: w.half[node], g, P, vMin, vMax, pass }
  f.sideP = pass ? [vMin - 0.5 * g - P, vMin - 0.5 * g] : null            // боковая платформа у вокзала
  f.island = islandAt(n) && list.some((t) => t.k >= 1) ? [0.5 * g, 0.5 * g + P - 0.0] : null
  f.neg = (pass ? f.sideP[0] : vMin - 0.6 * g)                              // край «со стороны вокзала»
  f.pos = vMax + 0.6 * g
  f.at = (u, v) => add(add(f.c, f.a, u), f.n, v)
  return f
}

/** Линия пути на станции. */
export function trackLine(w, node, trackId, k) {
  const t = w.tracks[node].find((x) => x.id === trackId)
  if (!t) return null
  const a = w.axis[node]
  const c = add(w.pos[node], normal(a), trackV(w, node, t, k))
  return { e: add(c, a, -t.length_m / 2), w: add(c, a, t.length_m / 2), a, track: t }
}

/** Куда смотрит поезд: true — на запад (от границы участка вглубь), false — на восток. */
export function headsWest(w, t, node) {
  // въехал с восточной горловины — смотрит на запад, и наоборот
  if (t.came_from && w.adj[node].includes(t.came_from)) return w.side(node, t.came_from) === 'east'
  const p = pathBetween(w.adj, node, t.destinations[0])
  if (!p || p.length < 2) return true
  return w.side(node, p[1]) === 'west'
}

/**
 * Положение поезда: голова, направление (от хвоста к голове), длина тела.
 * Между тиками сервера поезд достраивается по номинальной скорости типа — только для плавности.
 */
export function placeTrain(w, t, state, clock, k) {
  const L = t.length_m
  const v = ((state.types?.[t.type]?.speed_kmh) || 60) / 3.6
  if ((t.status === 'running' || t.status === 'approaching') && t.run) {
    const r = t.run
    const f = r.eta > r.entered ? Math.max(0, Math.min(1, (clock - r.entered) / (r.eta - r.entered))) : 1
    if (!r.from) {
      const o = w.outward(r.to)
      const d = Math.max(0, (r.eta - clock) * v)
      return { head: add(w.pos[r.to], o, d), dir: { x: -o.x, y: -o.y }, len: L, moving: true, offstage: true }
    }
    const g = w.segs[r.segment]
    const westward = r.from === g.u
    const [p0, p1] = westward ? [g.p0, g.p1] : [g.p1, g.p0]
    const dir = unit(p1.x - p0.x, p1.y - p0.y)
    return { head: add(p0, { x: p1.x - p0.x, y: p1.y - p0.y }, f), dir, len: L, moving: f < 1 }
  }
  if (t.status === 'at_node' && t.node && !t.track) {
    const o = w.outward(t.node)
    const i = state.obs.trains.filter((x) => x.status === 'at_node' && x.node === t.node && !x.track)
      .sort((x, y) => x.since - y.since).findIndex((x) => x.id === t.id)
    return { head: add(w.pos[t.node], o, 300 + i * 1400), dir: { x: -o.x, y: -o.y }, len: L, moving: false, offstage: true }
  }
  const node = t.port ? t.port.node : t.node
  // занятость пути — по табло: ушедший с пути поезд не рисуем
  const trackId = findTrack(state, node, t.id)
  if (!node || !trackId) return null
  const tl = trackLine(w, node, trackId, k)
  if (!tl) return null
  const wb = headsWest(w, t, node)
  const dir = wb ? tl.a : { x: -tl.a.x, y: -tl.a.y }
  const [entry, exit] = wb ? [tl.e, tl.w] : [tl.w, tl.e]
  if (t.port) {
    const len = L * (t.port.remaining / Math.max(1, t.wagons))
    return { head: exit, dir, len, moving: false, wagonsOnly: true }
  }
  if (t.status === 'passing') {
    const rec = (state.paths[t.id] || []).slice().reverse().find((p) => p.node === node)
    const s = Math.min(tl.track.length_m, v * Math.max(0, clock - (rec?.arr ?? clock)))
    return { head: add(entry, dir, s), dir, len: L, moving: true }
  }
  return { head: exit, dir, len: Math.min(L, tl.track.length_m + 40), moving: false }
}

function findTrack(state, node, tid) {
  const n = state.obs.nodes.find((x) => x.id === node)
  return n?.tracks.find((t) => t.occupant === tid)?.id
}

// ------------------------------------------------------------------ декор

function rng(seed) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6D2B79F5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function hashStr(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}

/**
 * Станционный декор задаётся в кадре станции: u — вдоль оси, d — отступ от края путевого поля,
 * side −1 — сторона вокзала, +1 — другая. Остальное — в мировых координатах.
 */
function buildDecor(w) {
  const R = rng(hashStr(Object.keys(w.nodes).join('|')))
  const between = (a, b) => a + (b - a) * R()
  const pick = (arr) => arr[Math.floor(R() * arr.length)]
  const station = []   // {node, kind, u, d, side, ...}
  const world = []     // {kind, x, y, ...}
  const groves = []
  const land = (p) => p.x > coastX(w, p.y) + 600

  for (const id of Object.keys(w.nodes)) {
    const n = w.nodes[id]
    if (!n.tracks.length) continue
    const H = w.half[id]
    const S = (o) => station.push({ node: id, ...o })
    if (PASSENGER_TYPES.has(n.type)) {
      const big = n.type === 'junction' ? 1.3 : n.type === 'port' ? 1.1 : 1
      const bw = 80 * big, bd = 24 * big
      S({ kind: 'building', u: 0, d: 8, side: -1, w: bw, h: bd })
      S({ kind: 'annex', u: -(bw / 2 + 14), d: 10, side: -1, w: 22, h: 20, gear: false })
      S({ kind: 'annex', u: bw / 2 + 14, d: 10, side: -1, w: 22, h: 20, gear: true })
      for (let i = 0; i < 7; i++) S({ kind: 'tree', u: between(-H - 300, H + 300), d: bd + between(70, 300), side: -1, r: between(6, 12) })
      for (let i = 0; i < 5; i++) S({ kind: 'flowers', u: between(-bw, bw), d: between(bd + 12, bd + 30), side: -1, seed: R() })
    } else if (n.type === 'industrial') {
      S({ kind: 'silo', u: -120, d: 25, side: -1, r: 12 })
      S({ kind: 'silo', u: -88, d: 25, side: -1, r: 12 })
      S({ kind: 'silo', u: -56, d: 25, side: -1, r: 12 })
      S({ kind: 'warehouse', u: 60, d: 18, side: -1, w: 150, h: 60 })
      S({ kind: 'reel', u: -220, d: 30, side: -1, r: 14 })
      for (let i = 0; i < 6; i++) S({ kind: 'crate', u: 160 + (i % 3) * 9, d: 22 + Math.floor(i / 3) * 9, side: -1, s: 7 })
    } else {
      S({ kind: 'hut', u: between(-H / 3, H / 3), d: 10, side: -1, w: 10, h: 8 })
      for (let i = 0; i < 4; i++) S({ kind: 'tree', u: between(-H, H), d: between(25, 140), side: pick([-1, 1]), r: between(5, 10) })
    }
    if (n.type === 'port') {
      for (let i = 0; i < 4; i++) S({ kind: 'stack', u: -H + 120 + i * 140, d: 18, side: 1, w: 90, h: 26, seed: R() })
      S({ kind: 'crane', u: -H / 2, d: 4, side: 1, w: 30, h: 70 })
      S({ kind: 'crane', u: H / 3, d: 4, side: 1, w: 30, h: 70 })
      S({ kind: 'warehouse', u: H / 2 + 60, d: 70, side: 1, w: 160, h: 70 })
    } else if (n.type === 'junction' || n.type === 'station') {
      if (n.type === 'junction') {
        // локомотивное депо с поворотным кругом и водонапорная башня
        S({ kind: 'depot', u: -H / 2 - 40, d: 60, side: 1, w: 110, h: 46 })
        S({ kind: 'turntable', u: -H / 2 - 150, d: 62, side: 1, r: 22 })
        S({ kind: 'tower', u: 80 * 1.3 / 2 + 70, d: 14, side: -1, r: 7 })
      }
      S({ kind: 'warehouse', u: H / 2, d: 30, side: 1, w: 120, h: 48 })
      for (let i = 0; i < 4; i++) S({ kind: 'crate', u: H / 2 - 90 + (i % 2) * 9, d: 32 + Math.floor(i / 2) * 9, side: 1, s: 7 })
      S({ kind: 'pond', u: -H / 2, d: between(150, 260), side: 1, rx: between(40, 70), ry: between(22, 34) })
      for (let i = 0; i < 6; i++) S({ kind: 'tree', u: between(-H - 200, H + 200), d: between(80, 280), side: 1, r: between(7, 13) })
    }
  }

  // вдоль перегонов: купы деревьев, кусты; дальше от пути — рощи и пруды
  for (const g of Object.values(w.segs)) {
    const d = { x: g.p1.x - g.p0.x, y: g.p1.y - g.p0.y }
    const L = Math.hypot(d.x, d.y)
    const a = { x: d.x / L, y: d.y / L }, nn = normal(a)
    for (let s = 400; s < L - 400; s += between(500, 1100)) {
      if (R() < 0.35) continue
      const side = R() < 0.5 ? -1 : 1
      const base = add(add(g.p0, a, s), nn, side * between(45, 380))
      const count = 1 + Math.floor(R() * 4)
      for (let i = 0; i < count; i++) {
        const p = add(base, { x: R() - 0.5, y: R() - 0.5 }, 40)
        if (land(p)) world.push({ kind: R() < 0.25 ? 'bush' : 'tree', x: p.x, y: p.y, r: between(5, 12) })
      }
    }
    for (let i = 0; i < Math.max(1, Math.round(L / 9000)); i++) {
      const side = R() < 0.5 ? -1 : 1
      const p = add(add(g.p0, a, between(0.15, 0.85) * L), nn, side * between(1500, 5500))
      if (land(p)) groves.push({ x: p.x, y: p.y, r: between(250, 700), seed: R() })
      if (R() < 0.35) {
        const q = add(add(g.p0, a, between(0.2, 0.8) * L), nn, -side * between(500, 2500))
        if (land(q)) world.push({ kind: 'pond', x: q.x, y: q.y, rx: between(60, 160), ry: between(35, 80), ang: R() * Math.PI })
      }
    }
  }
  // декор не должен лежать на рельсах веток: проверяем на крупном масштабе (k = 1)
  const lines = Object.values(w.segs).map((g) => [g.p0, g.p1])
  const near = (p, m) => lines.some(([a, b]) => distSeg(p, a, b) < m)
  const stationOk = station.filter((it) => {
    const f = frame(w, it.node, 1)
    const ext = it.h || it.ry * 2 || (it.r ? it.r * 2 : 0) || it.s || 0
    const v = it.side < 0 ? f.neg - it.d - ext / 2 : f.pos + it.d + ext / 2
    return !near(f.at(it.u, v), 25 + ext / 2 + (it.w || 0) / 2)
  })
  return { station: stationOk, world: world.filter((it) => !near(it, 35 + (it.rx || 0))), groves }
}

function distSeg(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y
  const l2 = dx * dx + dy * dy || 1
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2))
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
}
