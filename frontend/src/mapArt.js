/**
 * Рисование «живой карты» в мультяшном стиле (вид сверху): трава, щебень и шпалы,
 * вокзалы с красной крышей, платформы, деревья, вагоны разных типов.
 * Только картинка: всё положение берётся из world.js и состояния симулятора.
 *
 * R — контекст кадра: { ctx, k (px/м), X(p), Y(p), W, H, now (с), night (0..1) }.
 */
import { PASSENGER_TYPES, add, frame, normal, trackLine } from './world'

export const K_OVER = 0.03     // мельче — обзор: значки станций
export const K_DECOR = 0.07    // крупнее — здания, платформы, деревья
export const K_FINE = 0.6      // крупнее — скамейки, люди, детали вагонов

export const P = {
  grass: '#74c04f', tuft: '#5fa83f', grassDark: '#68b247',
  ballast: '#a39a8f', ballastDot: '#857c72', sleeper: '#7b4a2b', rail: '#d3d8dc', railDark: '#7b8187',
  railOver: '#5b4b3c',
  platform: '#ddd7cc', tile: '#cbc4b8', edge: '#f2cc2e',
  roof: '#cc4f39', roofDark: '#a23b2a', roofLight: '#da6550', annex: '#a5a8ac', annexIn: '#8f9296',
  tree: '#3f8f2f', treeDark: '#2f7424', treeLight: '#5db544',
  water: '#4aa3df', waterLight: '#71bdf0', sea: '#3b8fd0', seaLight: '#5aa6e0', sand: '#e9d49b',
  crate: '#c98b4f', crateDark: '#8b5a2c', ware: '#8d9197', wareRoof: '#7c8087', sky: '#bfe0f5',
  silo: '#b8bbbf', crane: '#f0b52f', bench: '#8b5a2f', lamp: '#1e1e1e', lampOn: '#ffd84a',
  label: 'rgba(255,255,255,.92)', labelText: '#1d2329', pier: '#9b8467',
}

const PEOPLE = ['#e05555', '#3b7dd8', '#3fa66b', '#e08a2e', '#8e5fc9', '#2bb3b3', '#c43c7a']
const CONTAINERS = ['#d94b3d', '#3b7dd8', '#e3a33a', '#4caf6a', '#8e5fc9', '#e6e6e6', '#2a8a9c']

export function hash(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}

function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath()
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2)))
  else ctx.rect(x, y, w, h)
}

/** Выполнить fn в локальной системе: начало в мировой точке p, ось x — вдоль ang, единица — пиксель. */
function local(R, p, ang, fn) {
  const { ctx } = R
  ctx.save()
  ctx.translate(R.X(p), R.Y(p))
  ctx.rotate(ang)
  fn(ctx)
  ctx.restore()
}

function onScreen(R, p, padPx = 200) {
  const x = R.X(p), y = R.Y(p)
  return x > -padPx && y > -padPx && x < R.W + padPx && y < R.H + padPx
}

// ------------------------------------------------------------------ земля, море, рощи

let grassTile = null
function grassPattern(ctx) {
  if (grassTile) return ctx.createPattern(grassTile, 'repeat')
  const c = document.createElement('canvas')
  c.width = c.height = 96
  const g = c.getContext('2d')
  g.fillStyle = P.grass
  g.fillRect(0, 0, 96, 96)
  g.strokeStyle = P.tuft
  g.lineWidth = 1.4
  const tufts = [[10, 14], [44, 8], [78, 22], [24, 46], [62, 52], [88, 70], [8, 78], [40, 86], [70, 90], [52, 30]]
  for (const [x, y] of tufts) {
    g.beginPath(); g.moveTo(x - 3, y + 3); g.lineTo(x, y - 2); g.lineTo(x + 3, y + 3); g.stroke()
  }
  grassTile = c
  return ctx.createPattern(c, 'repeat')
}

export function drawGround(R, cam) {
  const { ctx, W, H, k } = R
  if (k >= K_OVER) {
    const pat = grassPattern(ctx)
    const ox = (-cam.cx * k + W / 2) % 96, oy = (-cam.cy * k + H / 2) % 96
    if (pat.setTransform) pat.setTransform(new DOMMatrix([1, 0, 0, 1, ox, oy]))
    ctx.fillStyle = pat
  } else {
    ctx.fillStyle = P.grass
  }
  ctx.fillRect(0, 0, W, H)
}

export function drawSea(R, w) {
  if (!w.coast) return
  const { ctx, k } = R
  const path = () => {
    ctx.beginPath()
    w.coast.forEach((p, i) => (i ? ctx.lineTo(R.X(p), R.Y(p)) : ctx.moveTo(R.X(p), R.Y(p))))
    const last = w.coast[w.coast.length - 1], first = w.coast[0]
    ctx.lineTo(R.X({ x: last.x - 600000, y: last.y }), R.Y(last))
    ctx.lineTo(R.X({ x: first.x - 600000, y: first.y }), R.Y(first))
    ctx.closePath()
  }
  path()
  ctx.strokeStyle = P.sand
  ctx.lineWidth = Math.max(6, 120 * k)
  ctx.lineJoin = 'round'
  ctx.stroke()
  ctx.fillStyle = P.sea
  ctx.fill()
  ctx.strokeStyle = P.seaLight
  ctx.lineWidth = Math.max(2, 30 * k)
  ctx.stroke()
  // волны: сетка в мировых координатах, чтобы не «плыли» при прокрутке
  if (k > 0.004) {
    const cell = k > 0.1 ? 300 : k > 0.02 ? 2000 : 8000
    const x0 = R.wx0 - cell, x1 = R.wx1 + cell, y0 = R.wy0 - cell, y1 = R.wy1 + cell
    ctx.strokeStyle = 'rgba(255,255,255,.55)'
    ctx.lineWidth = 2
    for (let gx = Math.floor(x0 / cell) * cell; gx < x1; gx += cell) {
      for (let gy = Math.floor(y0 / cell) * cell; gy < y1; gy += cell) {
        const h = hash(`${gx}|${gy}`)
        const p = { x: gx + (h % 1000) / 1000 * cell, y: gy + ((h >>> 10) % 1000) / 1000 * cell }
        if (p.x > coastXCached(w, p.y) - 300) continue
        const r = 9
        const drift = Math.sin(R.now * 0.8 + (h % 7)) * 2
        ctx.beginPath()
        ctx.arc(R.X(p) + drift, R.Y(p), r, Math.PI * 1.15, Math.PI * 1.85)
        ctx.stroke()
      }
    }
  }
  if (k < 0.02) {
    ctx.fillStyle = 'rgba(255,255,255,.75)'
    ctx.font = '600 15px system-ui'
    ctx.textAlign = 'center'
    const p = w.coast[Math.floor(w.coast.length / 2)]
    ctx.fillText('Каспийское море', R.X({ x: p.x - 25000, y: p.y }), R.Y(p))
  }
}

const coastMemo = new Map()
function coastXCached(w, y) {
  const key = Math.round(y / 200)
  let v = coastMemo.get(key)
  if (v === undefined) {
    const c = w.coast
    v = -Infinity
    for (let i = 0; i < c.length - 1; i++) {
      const a = c[i], b = c[i + 1]
      if ((y >= a.y && y <= b.y) || (y <= a.y && y >= b.y)) {
        v = a.x + (b.x - a.x) * (b.y === a.y ? 0 : (y - a.y) / (b.y - a.y))
        break
      }
    }
    coastMemo.set(key, v)
  }
  return v
}

export function drawGroves(R, groves) {
  const { ctx, k } = R
  for (const g of groves) {
    if (!onScreen(R, g, g.r * k + 50)) continue
    const n = 7
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + g.seed * 6
      const rr = g.r * (0.35 + ((hash(`${g.seed}${i}`) % 100) / 100) * 0.3)
      const p = { x: g.x + Math.cos(a) * g.r * 0.45, y: g.y + Math.sin(a) * g.r * 0.45 }
      treeBlob(ctx, R.X(p), R.Y(p), Math.max(3, rr * k))
    }
    treeBlob(ctx, R.X(g), R.Y(g), Math.max(4, g.r * 0.5 * k))
  }
}

function treeBlob(ctx, x, y, r) {
  ctx.fillStyle = P.treeDark
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill()
  ctx.fillStyle = P.tree
  ctx.beginPath(); ctx.arc(x, y, r * 0.84, 0, Math.PI * 2); ctx.fill()
  if (r > 4) {
    ctx.fillStyle = P.treeLight
    ctx.beginPath(); ctx.arc(x - r * 0.32, y - r * 0.32, r * 0.3, 0, Math.PI * 2); ctx.fill()
  }
}

// ------------------------------------------------------------------ рельсы

/** Путь от a до b: щебень, шпалы, рельсы — по масштабу. */
export function drawTrack(R, a, b, opts = {}) {
  const { ctx, k } = R
  const ax = R.X(a), ay = R.Y(a), bx = R.X(b), by = R.Y(b)
  const minx = Math.min(ax, bx), maxx = Math.max(ax, bx), miny = Math.min(ay, by), maxy = Math.max(ay, by)
  if (maxx < -50 || minx > R.W + 50 || maxy < -50 || miny > R.H + 50) return
  const line = () => { ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke() }
  ctx.lineCap = 'butt'
  if (k < K_OVER) {
    ctx.strokeStyle = opts.color || P.railOver
    ctx.lineWidth = 2.5
    line()
    return
  }
  ctx.strokeStyle = opts.ballast || P.ballast
  ctx.lineWidth = Math.max(4, 5.2 * k)
  line()
  if (k >= 1.2) {
    ctx.strokeStyle = P.sleeper
    ctx.lineWidth = 2.8 * k
    ctx.setLineDash([0.7 * k, 1.3 * k])
    line()
    ctx.setLineDash([])
  }
  const d = { x: bx - ax, y: by - ay }, l = Math.hypot(d.x, d.y) || 1
  const nx = -d.y / l, ny = d.x / l
  if (k >= 0.9) {
    const off = 0.76 * k
    for (const s of [-1, 1]) {
      ctx.strokeStyle = P.railDark
      ctx.lineWidth = Math.max(1.5, 0.32 * k)
      ctx.beginPath(); ctx.moveTo(ax + nx * off * s, ay + ny * off * s); ctx.lineTo(bx + nx * off * s, by + ny * off * s); ctx.stroke()
      ctx.strokeStyle = opts.color || P.rail
      ctx.lineWidth = Math.max(1, 0.18 * k)
      ctx.beginPath(); ctx.moveTo(ax + nx * off * s, ay + ny * off * s); ctx.lineTo(bx + nx * off * s, by + ny * off * s); ctx.stroke()
    }
  } else {
    ctx.strokeStyle = opts.color || P.railDark
    ctx.lineWidth = Math.max(1.5, 1.6 * k)
    line()
  }
}

// ------------------------------------------------------------------ станции

/** Площадка станции: щебёночная подушка, платформы, навес, скамейки, фонари, люди. */
export function drawStationGround(R, w, node, crowd) {
  const { k } = R
  const n = w.nodes[node]
  const f = frame(w, node, k)
  if (!onScreen(R, f.c, f.half * k + 400)) return
  const ang = Math.atan2(f.a.y, f.a.x)
  const L = f.half * 2 - 120
  if (k >= K_DECOR) {
    // щебёночная подушка под путевым развитием
    local(R, f.c, ang, (c) => {
      c.fillStyle = P.ballast
      c.globalAlpha = 0.55
      c.fillRect(-L / 2 * k, (f.vMin - 0.6 * f.g) * k, L * k, (f.vMax - f.vMin + 1.2 * f.g) * k)
      c.globalAlpha = 1
    })
  }
  if (!PASSENGER_TYPES.has(n.type) || k < K_DECOR) return
  const pl = n.type === 'junction' ? 620 : n.type === 'port' ? 420 : 520
  const plats = [f.sideP, f.island].filter(Boolean)
  local(R, f.c, ang, (c) => {
    for (const [v0, v1] of plats) {
      c.fillStyle = P.platform
      c.fillRect(-pl / 2 * k, v0 * k, pl * k, (v1 - v0) * k)
      if (k >= K_FINE) {
        c.strokeStyle = P.tile
        c.lineWidth = 1
        for (let u = -pl / 2; u < pl / 2; u += 4) {
          c.beginPath(); c.moveTo(u * k, v0 * k); c.lineTo(u * k, v1 * k); c.stroke()
        }
        c.beginPath(); c.moveTo(-pl / 2 * k, (v0 + v1) / 2 * k); c.lineTo(pl / 2 * k, (v0 + v1) / 2 * k); c.stroke()
      }
      c.fillStyle = P.edge
      const e = Math.max(1.5, 0.45 * k)
      c.fillRect(-pl / 2 * k, v0 * k, pl * k, e)
      c.fillRect(-pl / 2 * k, v1 * k - e, pl * k, e)
    }
    if (f.island && k >= K_DECOR) {
      const [v0, v1] = f.island
      c.fillStyle = '#6f7f91'
      c.fillRect(-140 * k, (v0 + 1) * k, 280 * k, (v1 - v0 - 2) * k)
      if (k >= 0.25) {
        c.strokeStyle = '#5b6a7b'
        c.lineWidth = 1
        for (let u = -140; u <= 140; u += 14) { c.beginPath(); c.moveTo(u * k, (v0 + 1) * k); c.lineTo(u * k, (v1 - 1) * k); c.stroke() }
        c.beginPath(); c.moveTo(-140 * k, (v0 + v1) / 2 * k); c.lineTo(140 * k, (v0 + v1) / 2 * k); c.stroke()
      }
    }
    if (k >= K_FINE) {
      for (const [v0, v1] of plats) {
        const vm = (v0 + v1) / 2
        for (let u = -pl / 2 + 30; u < pl / 2 - 20; u += 60) {
          c.fillStyle = P.bench
          c.fillRect((u - 2.5) * k, (vm - 0.5) * k, 5 * k, 1.1 * k)
          const lu = u + 30
          c.fillStyle = P.lamp
          c.beginPath(); c.arc(lu * k, (vm + 0.2) * k, 0.9 * k, 0, Math.PI * 2); c.fill()
          c.fillStyle = P.lampOn
          c.beginPath(); c.arc(lu * k, (vm + 0.2) * k, 0.5 * k, 0, Math.PI * 2); c.fill()
        }
      }
      // пассажиры: больше, когда скоро пассажирский
      const base = hash(node)
      const count = 6 + crowd * 14
      for (let i = 0; i < count; i++) {
        const plat = plats[i % plats.length]
        const h = hash(`${node}|p${i}`)
        const u = ((h % 1000) / 1000 - 0.5) * (pl - 40) + Math.sin(R.now * 0.3 + i) * 1.2
        const v = plat[0] + 1.2 + ((h >>> 10) % 1000) / 1000 * (plat[1] - plat[0] - 2.4)
        c.fillStyle = PEOPLE[(base + i) % PEOPLE.length]
        c.beginPath(); c.arc(u * k, v * k, 0.75 * k, 0, Math.PI * 2); c.fill()
        c.fillStyle = '#f1c9a0'
        c.beginPath(); c.arc(u * k, v * k, 0.42 * k, 0, Math.PI * 2); c.fill()
      }
    }
  })
}

function stationPoint(f, it, along = 0, extent = 0) {
  const v = it.side < 0 ? f.neg - it.d - extent / 2 : f.pos + it.d + extent / 2
  return f.at(it.u + along, v)
}

/** Здания, деревья и прочее вокруг станций. */
export function drawStationDecor(R, w, items) {
  const { k } = R
  if (k < K_DECOR) return
  const frames = {}
  for (const it of items) {
    const f = frames[it.node] || (frames[it.node] = frame(w, it.node, k))
    const ang = Math.atan2(f.a.y, f.a.x)
    const ext = it.h || it.ry * 2 || (it.r ? it.r * 2 : 0) || it.s || 0
    const p = stationPoint(f, it, 0, ext)
    if (!onScreen(R, p, 300)) continue
    local(R, p, ang, (c) => decorItem(c, it, k, R))
  }
}

export function drawWorldDecor(R, items) {
  const { k } = R
  if (k < K_DECOR) {
    for (const it of items) if (it.kind === 'pond' && onScreen(R, it, 200)) local(R, it, it.ang || 0, (c) => decorItem(c, it, k, R))
    return
  }
  for (const it of items) {
    if (!onScreen(R, it, 120)) continue
    local(R, it, it.ang || 0, (c) => decorItem(c, it, k, R))
  }
}

function decorItem(c, it, k, R) {
  switch (it.kind) {
    case 'tree': {
      const r = Math.max(2.5, it.r * k)
      treeBlob(c, 0, 0, r)
      return
    }
    case 'bush': {
      const r = Math.max(2, it.r * 0.6 * k)
      c.fillStyle = P.treeDark
      for (const [dx, dy] of [[-0.6, 0], [0.6, 0.1], [0, -0.5]]) { c.beginPath(); c.arc(dx * r, dy * r, r * 0.8, 0, Math.PI * 2); c.fill() }
      c.fillStyle = P.tree
      c.beginPath(); c.arc(0, 0, r * 0.75, 0, Math.PI * 2); c.fill()
      return
    }
    case 'flowers': {
      if (k < K_FINE) return
      const cols = ['#ff6b8b', '#ffd23f', '#ffffff', '#b88cff']
      for (let i = 0; i < 4; i++) {
        c.fillStyle = cols[(i + Math.floor(it.seed * 4)) % 4]
        c.beginPath(); c.arc((i * 2.3 - 3) * k, ((i % 2) * 1.6) * k, 0.55 * k, 0, Math.PI * 2); c.fill()
      }
      return
    }
    case 'pond': {
      const rx = Math.max(4, it.rx * k), ry = Math.max(3, it.ry * k)
      c.fillStyle = P.water
      c.beginPath(); c.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); c.fill()
      c.fillStyle = P.waterLight
      c.beginPath(); c.ellipse(rx * 0.08, ry * 0.08, rx * 0.8, ry * 0.72, 0, 0, Math.PI * 2); c.fill()
      if (rx > 30) {
        c.strokeStyle = 'rgba(255,255,255,.85)'
        c.lineWidth = 2
        c.beginPath(); c.arc(-rx * 0.25, -ry * 0.1, rx * 0.12, Math.PI * 1.15, Math.PI * 1.85); c.stroke()
        c.beginPath(); c.arc(rx * 0.25, ry * 0.25, rx * 0.12, Math.PI * 1.15, Math.PI * 1.85); c.stroke()
        c.fillStyle = '#3e9a3a'
        c.beginPath(); c.ellipse(-rx * 0.55, -ry * 0.25, rx * 0.08, ry * 0.08, 0, 0, Math.PI * 2); c.fill()
      }
      return
    }
    case 'building': {
      const w = it.w * k, h = it.h * k
      c.fillStyle = P.roof
      c.fillRect(-w / 2, -h / 2, w, h)
      // вальмовая крыша: скаты и конёк
      c.fillStyle = P.roofDark
      c.beginPath(); c.moveTo(-w / 2, -h / 2); c.lineTo(-w / 2 + h / 2, 0); c.lineTo(-w / 2, h / 2); c.fill()
      c.beginPath(); c.moveTo(-w / 2, h / 2); c.lineTo(-w / 2 + h / 2, 0); c.lineTo(w / 2 - h / 2, 0); c.lineTo(w / 2, h / 2); c.fill()
      c.fillStyle = P.roofLight
      c.beginPath(); c.moveTo(w / 2, -h / 2); c.lineTo(w / 2 - h / 2, 0); c.lineTo(w / 2, h / 2); c.fill()
      if (k >= 0.25) {
        c.strokeStyle = 'rgba(0,0,0,.12)'
        c.lineWidth = 1
        for (let y = -h / 2 + 3; y < 0; y += Math.max(3, 1.6 * k)) { c.beginPath(); c.moveTo(-w / 2 + (y + h / 2), y); c.lineTo(w / 2 - (y + h / 2), y); c.stroke() }
        c.fillStyle = '#2c5f86'
        const s = h * 0.45
        c.fillRect(-s / 2, -s / 2, s, s)
        c.fillStyle = '#f2cc2e'
        c.beginPath(); c.arc(0, 0, Math.max(1.5, 0.9 * k), 0, Math.PI * 2); c.fill()
        c.fillStyle = '#3c3f43'
        c.fillRect(-w * 0.28, -h * 0.38, Math.max(2, 2.2 * k), Math.max(2, 2.2 * k))
        c.fillRect(w * 0.24, h * 0.18, Math.max(2, 2.2 * k), Math.max(2, 2.2 * k))
      }
      if (R.night > 0.15) {
        c.fillStyle = `rgba(255,214,90,${0.6 * R.night})`
        c.fillRect(-w / 2 + 2, h / 2 - 2, w - 4, 2)
      }
      return
    }
    case 'annex': {
      const w = it.w * k, h = it.h * k
      c.fillStyle = P.annex
      c.fillRect(-w / 2, -h / 2, w, h)
      c.fillStyle = P.annexIn
      c.fillRect(-w / 2 + w * 0.1, -h / 2 + h * 0.1, w * 0.8, h * 0.8)
      if (k >= 0.3) {
        if (it.gear) {
          c.strokeStyle = '#7b7e82'; c.lineWidth = Math.max(1.5, 0.8 * k)
          c.beginPath(); c.arc(0, 0, w * 0.18, 0, Math.PI * 2); c.stroke()
        } else {
          c.fillStyle = '#7b7e82'
          c.fillRect(-w * 0.2, -h * 0.12, w * 0.4, h * 0.24)
        }
      }
      return
    }
    case 'hut': {
      const w = it.w * k, h = it.h * k
      c.fillStyle = '#b5543f'
      c.fillRect(-w / 2, -h / 2, w, h)
      c.fillStyle = '#8e3e2e'
      c.fillRect(-w / 2, 0, w, h / 2)
      return
    }
    case 'warehouse': {
      const w = it.w * k, h = it.h * k
      c.fillStyle = P.ware
      c.fillRect(-w / 2, -h / 2, w, h)
      c.strokeStyle = P.wareRoof
      c.lineWidth = 1
      for (let y = -h / 2; y < h / 2; y += Math.max(2.5, 3 * k)) { c.beginPath(); c.moveTo(-w / 2, y); c.lineTo(w / 2, y); c.stroke() }
      c.fillStyle = '#6b6f75'
      c.fillRect(-w / 2, -1, w, Math.max(1.5, 1.2 * k))
      if (k >= 0.2) {
        c.fillStyle = P.sky
        c.fillRect(-w * 0.3, -h * 0.35, w * 0.12, h * 0.2)
        c.fillRect(w * 0.15, h * 0.12, w * 0.12, h * 0.2)
        c.fillStyle = '#b5ab9a'
        c.fillRect(-w / 2, h / 2, w, Math.max(2, 6 * k))
        c.fillStyle = '#c47a2c'
        c.fillRect(-w * 0.2, h / 2, w * 0.12, Math.max(2, 5 * k))
        c.fillStyle = '#3b6fc4'
        c.fillRect(-w * 0.04, h / 2, w * 0.08, Math.max(2, 5 * k))
      }
      return
    }
    case 'crate': {
      const s = Math.max(2, it.s * k)
      c.fillStyle = P.crate
      c.fillRect(-s / 2, -s / 2, s, s)
      c.strokeStyle = P.crateDark
      c.lineWidth = Math.max(1, 0.4 * k)
      c.strokeRect(-s / 2, -s / 2, s, s)
      if (s > 8) { c.beginPath(); c.moveTo(-s / 2, -s / 2); c.lineTo(s / 2, s / 2); c.stroke() }
      return
    }
    case 'silo': {
      const r = Math.max(2.5, it.r * k)
      c.fillStyle = P.silo
      c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fill()
      c.strokeStyle = '#9a9da1'; c.lineWidth = Math.max(1, 0.6 * k)
      c.beginPath(); c.arc(0, 0, r * 0.65, 0, Math.PI * 2); c.stroke()
      c.fillStyle = '#8b8e92'
      c.beginPath(); c.arc(0, 0, r * 0.2, 0, Math.PI * 2); c.fill()
      return
    }
    case 'reel': {
      const r = Math.max(3, it.r * k)
      c.fillStyle = '#8f6440'
      c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fill()
      c.strokeStyle = '#6e4a2c'; c.lineWidth = Math.max(1, 0.5 * k)
      for (const f of [0.75, 0.5]) { c.beginPath(); c.arc(0, 0, r * f, 0, Math.PI * 2); c.stroke() }
      c.fillStyle = '#4b321e'
      c.beginPath(); c.arc(0, 0, r * 0.13, 0, Math.PI * 2); c.fill()
      return
    }
    case 'stack': {
      const w = it.w * k, h = it.h * k
      const cols = 6, rows = 3
      for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
        c.fillStyle = CONTAINERS[hash(`${it.seed}${i}${j}`) % CONTAINERS.length]
        c.fillRect(-w / 2 + (i * w) / cols + 0.5, -h / 2 + (j * h) / rows + 0.5, w / cols - 1, h / rows - 1)
      }
      return
    }
    case 'depot': {
      // сарай депо: три въездных пути и полосы на крыше
      const w = it.w * k, h = it.h * k
      c.strokeStyle = P.railDark
      c.lineWidth = Math.max(1, 0.9 * k)
      for (let j = 0; j < 3; j++) {
        const y = -h / 2 + ((j + 0.5) * h) / 3
        c.beginPath(); c.moveTo(-w / 2 - 40 * k, y); c.lineTo(w / 2, y); c.stroke()
      }
      c.fillStyle = '#7d8590'
      c.fillRect(-w / 2, -h / 2, w, h)
      c.strokeStyle = '#6b727c'
      c.lineWidth = 1
      for (let x = -w / 2; x < w / 2; x += Math.max(3, 4 * k)) { c.beginPath(); c.moveTo(x, -h / 2); c.lineTo(x, h / 2); c.stroke() }
      c.fillStyle = '#5c636c'
      c.fillRect(-w / 2, -h / 2, Math.max(2, 3 * k), h)
      if (k >= 0.2) {
        c.fillStyle = P.sky
        for (let j = 0; j < 3; j++) c.fillRect(-w * 0.1, -h / 2 + ((j + 0.3) * h) / 3, w * 0.2, h / 3 * 0.4)
      }
      return
    }
    case 'turntable': {
      const r = Math.max(4, it.r * k)
      c.fillStyle = '#9a8f84'
      c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fill()
      c.strokeStyle = '#6e655b'
      c.lineWidth = Math.max(1, 0.8 * k)
      c.stroke()
      c.fillStyle = '#5b4b3c'
      c.save(); c.rotate(R.now * 0.15)
      c.fillRect(-r, -Math.max(1.5, 1.6 * k), r * 2, Math.max(3, 3.2 * k))
      c.restore()
      c.fillStyle = '#3a3f45'
      c.beginPath(); c.arc(0, 0, Math.max(1.5, 1.4 * k), 0, Math.PI * 2); c.fill()
      return
    }
    case 'tower': {
      const r = Math.max(3, it.r * k)
      c.fillStyle = '#7b8794'
      c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fill()
      c.fillStyle = '#c9503a'
      c.beginPath(); c.arc(0, 0, r * 0.72, 0, Math.PI * 2); c.fill()
      c.fillStyle = '#a23b2a'
      c.beginPath(); c.arc(0, 0, r * 0.25, 0, Math.PI * 2); c.fill()
      return
    }
    case 'crane': {
      const w = it.w * k, h = it.h * k
      c.strokeStyle = P.crane
      c.lineWidth = Math.max(2, 1.6 * k)
      c.strokeRect(-w / 2, -h / 2, w, h)
      c.beginPath(); c.moveTo(-w / 2, -h / 2); c.lineTo(w / 2, h / 2); c.moveTo(w / 2, -h / 2); c.lineTo(-w / 2, h / 2); c.stroke()
      c.fillStyle = '#d99a1f'
      c.fillRect(-w / 2 - 2, -h * 0.05, w + 4, Math.max(3, 4 * k))
      return
    }
    default:
  }
}

// ------------------------------------------------------------------ светофоры, значки, подписи

export function drawSignal(R, p, ang, green) {
  const { k } = R
  local(R, p, ang, (c) => {
    const w = Math.max(5, 2.4 * k), h = Math.max(7, 3.2 * k)
    c.fillStyle = '#1e2226'
    rrect(c, -w / 2, -h / 2, w, h, 2)
    c.fill()
    c.fillStyle = green ? '#3ee06b' : '#ff4b3e'
    c.beginPath(); c.arc(0, 0, w * 0.32, 0, Math.PI * 2); c.fill()
    if (R.night > 0.2) {
      c.fillStyle = green ? 'rgba(62,224,107,.25)' : 'rgba(255,75,62,.25)'
      c.beginPath(); c.arc(0, 0, w * 1.4, 0, Math.PI * 2); c.fill()
    }
  })
}

export function drawStationIcon(R, w, node) {
  const { ctx } = R
  const n = w.nodes[node]
  const f = frame(w, node, R.k)
  const p = f.at(0, f.neg)
  const x = R.X(p), y = R.Y(p) - 4
  if (PASSENGER_TYPES.has(n.type)) {
    const s = n.type === 'junction' ? 16 : 12
    ctx.fillStyle = P.roof
    ctx.fillRect(x - s / 2, y - s / 2, s, s * 0.75)
    ctx.fillStyle = P.roofDark
    ctx.beginPath(); ctx.moveTo(x - s / 2, y + s * 0.25); ctx.lineTo(x, y - s * 0.1); ctx.lineTo(x + s / 2, y + s * 0.25); ctx.fill()
  } else if (n.type === 'industrial') {
    ctx.fillStyle = P.silo
    ctx.fillRect(x - 7, y - 5, 14, 9)
    ctx.fillStyle = '#7b7e82'
    ctx.fillRect(x + 3, y - 11, 3, 7)
  } else {
    ctx.fillStyle = '#b5543f'
    ctx.fillRect(x - 3, y - 3, 6, 6)
  }
}

export function label(R, x, y, text, opts = {}) {
  const { ctx } = R
  ctx.font = opts.font || '600 12px system-ui'
  const tw = ctx.measureText(text).width
  ctx.fillStyle = opts.bg || P.label
  rrect(ctx, x - tw / 2 - 6, y - 12, tw + 12, 17, 5)
  ctx.fill()
  ctx.fillStyle = opts.color || P.labelText
  ctx.textAlign = 'center'
  ctx.fillText(text, x, y + 1)
}

// ------------------------------------------------------------------ поезда

const LOCO = { passenger: '#d83a2c', freight: '#2e8a55', container: '#2f63c7' }

/**
 * Поезд в локальной системе: голова в (0,0), хвост — в сторону −x. Возвращает длину на экране.
 * Длина и ширина — по масштабу; на мелком масштабе поезд укрупняется до заметного размера.
 */
export function drawTrainSprite(R, t, pl, opts) {
  const { k } = R
  const minLen = opts.minLen
  let len = pl.len * k
  const enlarged = len < minLen
  if (enlarged) len = minLen
  const wd = Math.max(enlarged ? 7 : 6, 3.3 * k)
  const ang = Math.atan2(pl.dir.y, pl.dir.x)
  let hitLen = len
  local(R, pl.head, ang, (c) => {
    c.globalAlpha = pl.offstage ? 0.55 : 1
    const locoL = pl.wagonsOnly ? 0 : enlarged ? Math.min(7, len * 0.3) : 24 * k
    const n = pl.wagonsOnly ? Math.max(1, t.port.remaining) : Math.max(1, t.wagons)
    const wagL = (len - locoL) / n
    // рама/сцепки
    c.fillStyle = '#26292d'
    c.fillRect(-len, -wd * 0.22, len, wd * 0.44)
    if (enlarged || wagL < 2.5) {
      c.fillStyle = t.type === 'passenger' ? '#eef2f6' : t.type === 'container' ? '#d94b3d' : '#2a8a9c'
      rrect(c, -len, -wd / 2, len - locoL, wd, 2)
      c.fill()
      if (t.type === 'passenger') { c.strokeStyle = '#2f6cc7'; c.lineWidth = 1.5; c.stroke() }
    } else {
      const gap = Math.max(0.8, 1.1 * k)
      for (let i = 0; i < n; i++) {
        const x = -locoL - (i + 1) * wagL
        wagon(c, t, i, x + gap / 2, wagL - gap, wd, k)
      }
    }
    if (locoL) loco(c, t.type, locoL, wd, k, enlarged)
    if (opts.selected) {
      c.strokeStyle = '#ffffff'
      c.lineWidth = 2.5
      rrect(c, -len - 3, -wd / 2 - 3, len + 6, wd + 6, 5)
      c.stroke()
    }
    if (t.failure && opts.blink) {
      c.strokeStyle = '#ff3b30'
      c.lineWidth = 3
      rrect(c, -len - 2, -wd / 2 - 2, len + 4, wd + 4, 5)
      c.stroke()
    }
    if (R.night > 0.15 && !pl.wagonsOnly) {
      const g = c.createRadialGradient(wd, 0, 0, wd, 0, wd * 5)
      g.addColorStop(0, `rgba(255,240,180,${0.55 * R.night})`)
      g.addColorStop(1, 'rgba(255,240,180,0)')
      c.fillStyle = g
      c.beginPath(); c.moveTo(0, 0); c.arc(0, 0, wd * 6, -0.45, 0.45); c.fill()
    }
    c.globalAlpha = 1
  })
  hitLen = len
  return { len: hitLen, wd, ang }
}

function loco(c, type, L, wd, k, enlarged) {
  const body = LOCO[type] || '#666'
  c.fillStyle = body
  rrect(c, -L, -wd / 2, L, wd, Math.min(4, wd * 0.25))
  c.fill()
  if (enlarged || L < 14) {
    c.fillStyle = '#ffd23f'
    c.fillRect(-Math.max(2, L * 0.22), -wd / 2, Math.max(2, L * 0.22), wd)
    return
  }
  // кабина спереди: жёлтая рамка, тёмное окно
  const cab = L * 0.2
  c.strokeStyle = '#f4cc2a'
  c.lineWidth = Math.max(1.5, wd * 0.09)
  c.strokeRect(-cab - wd * 0.1, -wd * 0.36, cab, wd * 0.72)
  c.fillStyle = '#23384f'
  c.fillRect(-cab * 0.55, -wd * 0.24, cab * 0.22, wd * 0.48)
  if (type === 'passenger') {
    c.fillStyle = '#9aa0a6'
    c.fillRect(-L * 0.72, -wd * 0.28, L * 0.35, wd * 0.56)
    c.strokeStyle = '#2b2f33'
    c.lineWidth = Math.max(1, wd * 0.06)
    c.beginPath()
    c.moveTo(-L * 0.72, 0); c.lineTo(-L * 0.545, -wd * 0.24); c.lineTo(-L * 0.37, 0); c.lineTo(-L * 0.545, wd * 0.24); c.closePath()
    c.stroke()
    c.strokeStyle = 'rgba(0,0,0,.35)'
    for (let i = 0; i < 4; i++) { c.beginPath(); c.moveTo(-L * 0.93 + i * L * 0.04, -wd * 0.32); c.lineTo(-L * 0.93 + i * L * 0.04, wd * 0.32); c.stroke() }
  } else if (type === 'freight') {
    c.strokeStyle = '#1f5f3a'
    c.lineWidth = Math.max(1, wd * 0.08)
    for (const fx of [-L * 0.78, -L * 0.55]) {
      c.beginPath(); c.arc(fx, 0, wd * 0.28, 0, Math.PI * 2); c.stroke()
      c.beginPath(); c.moveTo(fx - wd * 0.2, 0); c.lineTo(fx + wd * 0.2, 0); c.moveTo(fx, -wd * 0.2); c.lineTo(fx, wd * 0.2); c.stroke()
    }
  } else {
    c.fillStyle = '#ffd23f'
    c.fillRect(-L, -wd * 0.08, L * 0.78, wd * 0.16)
  }
  c.fillStyle = '#ffffff'
  c.beginPath(); c.arc(-1, -wd * 0.36, Math.max(1, wd * 0.08), 0, Math.PI * 2); c.fill()
  c.beginPath(); c.arc(-1, wd * 0.36, Math.max(1, wd * 0.08), 0, Math.PI * 2); c.fill()
}

const FREIGHT_KINDS = ['logs', 'box', 'tank', 'hopper']

function wagon(c, t, i, x, L, wd, k) {
  const detail = L >= 10
  if (t.type === 'passenger') {
    c.fillStyle = '#eef2f6'
    rrect(c, x, -wd / 2, L, wd, Math.min(3, wd * 0.15))
    c.fill()
    c.strokeStyle = '#2f6cc7'
    c.lineWidth = Math.max(1, wd * 0.11)
    c.stroke()
    if (detail) {
      c.fillStyle = '#9ba3ab'
      for (const fx of [0.18, 0.62]) {
        c.fillRect(x + L * fx, -wd * 0.28, L * 0.2, wd * 0.56)
      }
      c.fillStyle = '#7d858c'
      c.beginPath(); c.arc(x + L * 0.5, 0, Math.max(1, wd * 0.08), 0, Math.PI * 2); c.fill()
    }
    return
  }
  if (t.type === 'container') {
    c.fillStyle = '#3a3f45'
    c.fillRect(x, -wd * 0.42, L, wd * 0.84)
    const col = CONTAINERS[hash(t.id + i) % CONTAINERS.length]
    c.fillStyle = col
    c.fillRect(x + L * 0.04, -wd * 0.46, L * 0.92, wd * 0.92)
    if (detail) {
      c.strokeStyle = 'rgba(0,0,0,.22)'
      c.lineWidth = 1
      for (let j = 1; j < 8; j++) { c.beginPath(); c.moveTo(x + L * 0.04 + (j * L * 0.92) / 8, -wd * 0.44); c.lineTo(x + L * 0.04 + (j * L * 0.92) / 8, wd * 0.44); c.stroke() }
    }
    return
  }
  const kind = FREIGHT_KINDS[hash(t.id + '|' + i) % FREIGHT_KINDS.length]
  if (kind === 'logs') {
    c.fillStyle = '#7d5232'
    c.fillRect(x, -wd / 2, L, wd)
    c.fillStyle = '#b07a4a'
    for (let j = 0; j < 3; j++) {
      rrect(c, x + 1, -wd / 2 + (j + 0.12) * wd / 3, L - 2, wd / 3 * 0.76, wd / 6)
      c.fill()
    }
    if (detail) {
      c.fillStyle = '#d9a774'
      for (let j = 0; j < 3; j++) { c.beginPath(); c.arc(x + L - 2, -wd / 2 + (j + 0.5) * wd / 3, wd / 9, 0, Math.PI * 2); c.fill() }
      c.fillStyle = '#2b2f33'
      c.fillRect(x + L * 0.47, -wd / 2, L * 0.05, wd)
    }
  } else if (kind === 'box') {
    c.fillStyle = '#2a8a9c'
    c.fillRect(x, -wd / 2, L, wd)
    if (detail) {
      c.strokeStyle = '#3aa3b6'
      c.lineWidth = 1
      for (let j = 1; j < 14; j++) { c.beginPath(); c.moveTo(x + (j * L) / 14, -wd / 2 + 1); c.lineTo(x + (j * L) / 14, wd / 2 - 1); c.stroke() }
    }
  } else if (kind === 'tank') {
    c.fillStyle = '#2b2f33'
    c.fillRect(x, -wd / 2, L, wd)
    c.fillStyle = '#f1f3f5'
    rrect(c, x + 1, -wd * 0.44, L - 2, wd * 0.88, wd * 0.44)
    c.fill()
    if (detail) {
      c.fillStyle = '#d63a2f'
      c.fillRect(x + L * 0.2, -wd * 0.44, Math.max(1, L * 0.03), wd * 0.88)
      c.fillRect(x + L * 0.77, -wd * 0.44, Math.max(1, L * 0.03), wd * 0.88)
      c.fillStyle = '#9aa0a6'
      c.beginPath(); c.arc(x + L * 0.5, 0, wd * 0.16, 0, Math.PI * 2); c.fill()
    }
  } else {
    c.fillStyle = '#3d2d22'
    c.fillRect(x, -wd / 2, L, wd)
    c.fillStyle = '#1c1c1c'
    c.fillRect(x + 1, -wd * 0.4, L / 2 - 1.5, wd * 0.8)
    c.fillRect(x + L / 2 + 0.5, -wd * 0.4, L / 2 - 1.5, wd * 0.8)
    if (detail) {
      c.fillStyle = '#3a3a3a'
      for (let j = 0; j < 4; j++) {
        const h = hash(`${t.id}${i}${j}`)
        c.beginPath(); c.arc(x + 2 + ((h % 100) / 100) * (L - 4), -wd * 0.25 + (((h >>> 8) % 100) / 100) * wd * 0.5, wd * 0.1, 0, Math.PI * 2); c.fill()
      }
    }
  }
}

// ------------------------------------------------------------------ паромы

export function drawShip(R, x, y, axis, toPort, f, alpha) {
  const { ctx, k } = R
  const Lp = Math.max(34, 150 * k), Wp = Math.max(12, 24 * k)
  ctx.save()
  ctx.globalAlpha = alpha
  ctx.translate(x, y)
  ctx.rotate(Math.atan2(axis.y, axis.x) + (toPort ? Math.PI : 0))
  ctx.fillStyle = 'rgba(255,255,255,.35)'
  ctx.beginPath(); ctx.ellipse(-Lp * 0.55, 0, Lp * 0.2, Wp * 0.35, 0, 0, Math.PI * 2); ctx.fill()
  ctx.fillStyle = '#f4f6f8'
  ctx.strokeStyle = '#25384a'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(Lp / 2, 0)
  ctx.lineTo(Lp / 2 - Wp * 0.9, -Wp / 2)
  ctx.lineTo(-Lp / 2, -Wp / 2)
  ctx.lineTo(-Lp / 2, Wp / 2)
  ctx.lineTo(Lp / 2 - Wp * 0.9, Wp / 2)
  ctx.closePath()
  ctx.fill()
  ctx.stroke()
  ctx.fillStyle = '#2f63c7'
  ctx.fillRect(-Lp / 2, -Wp / 2, Lp * 0.85, Math.max(1.5, Wp * 0.1))
  const fill = f.capacity ? f.loaded / f.capacity : 0
  const rows = 3, cols = 10
  const filled = Math.round(fill * rows * cols)
  for (let i = 0; i < rows * cols; i++) {
    const cx = -Lp / 2 + Wp * 0.4 + (i % cols) * ((Lp - Wp * 1.6) / cols)
    const cy = -Wp * 0.33 + Math.floor(i / cols) * (Wp * 0.66 / rows)
    ctx.fillStyle = i < filled ? CONTAINERS[i % CONTAINERS.length] : 'rgba(0,0,0,.06)'
    ctx.fillRect(cx, cy, (Lp - Wp * 1.6) / cols - 1, Wp * 0.66 / rows - 1)
  }
  ctx.fillStyle = '#d9dde1'
  ctx.fillRect(Lp / 2 - Wp * 1.5, -Wp * 0.3, Wp * 0.5, Wp * 0.6)
  ctx.restore()
}

// ------------------------------------------------------------------ ночь

/** Насколько темно в этот час игровых суток (0 — день, ~0.5 — ночь). */
export function darkness(clock) {
  const h = ((clock / 3600) % 24 + 24) % 24
  if (h >= 22 || h < 4.5) return 0.5
  if (h >= 19.5) return ((h - 19.5) / 2.5) * 0.5
  if (h < 7) return (1 - (h - 4.5) / 2.5) * 0.5
  return 0
}

export function nightOverlay(R) {
  if (R.night <= 0.01) return
  const { ctx } = R
  ctx.fillStyle = `rgba(12,22,58,${R.night})`
  ctx.fillRect(0, 0, R.W, R.H)
}

/** Свет фонарей платформ ночью (поверх затемнения). */
export function lampGlow(R, w, node) {
  if (R.night <= 0.15 || R.k < K_DECOR) return
  const n = w.nodes[node]
  if (!PASSENGER_TYPES.has(n.type)) return
  const { ctx, k } = R
  const f = frame(w, node, k)
  if (!onScreen(R, f.c, f.half * k + 100)) return
  const pl = n.type === 'junction' ? 620 : n.type === 'port' ? 420 : 520
  const plats = [f.sideP, f.island].filter(Boolean)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  for (const [v0, v1] of plats) {
    const vm = (v0 + v1) / 2
    for (let u = -pl / 2 + 60; u < pl / 2 - 20; u += 60) {
      const p = f.at(u, vm)
      const r = Math.max(6, 14 * k)
      const g = ctx.createRadialGradient(R.X(p), R.Y(p), 0, R.X(p), R.Y(p), r)
      g.addColorStop(0, `rgba(255,214,110,${0.55 * R.night})`)
      g.addColorStop(1, 'rgba(255,214,110,0)')
      ctx.fillStyle = g
      ctx.beginPath(); ctx.arc(R.X(p), R.Y(p), r, 0, Math.PI * 2); ctx.fill()
    }
  }
  ctx.restore()
}

export { normal, add, trackLine }
