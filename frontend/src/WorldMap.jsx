import { useEffect, useMemo, useRef } from 'react'
import {
  K_DECOR, K_OVER, P, darkness, drawGround, drawGroves, drawSea, drawShip, drawSignal, drawStationDecor,
  drawStationGround, drawStationIcon, drawTrack, drawTrainSprite, drawWorldDecor, label, lampGlow, nightOverlay,
} from './mapArt'
import { hm } from './util'
import { add, buildWorld, frame, normal, placeTrain, trackLine } from './world'

const FERRY_SPEED = 7.7      // м/с, ~15 узлов
const MIN_TRAIN_PX = 18
const MAX_SCALE = 8          // px/м: вагон 15 м ≈ 120 px

function distToSeg(px, py, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y
  const l2 = dx * dx + dy * dy || 1
  const t = Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / l2))
  return Math.hypot(px - (a.x + t * dx), py - (a.y + t * dy))
}

function portBehind(world, start, from) {
  const seen = new Set([from, start])
  const stack = [start]
  while (stack.length) {
    const u = stack.pop()
    if (world.nodes[u].type === 'port') return u
    for (const v of world.adj[u]) if (!seen.has(v)) { seen.add(v); stack.push(v) }
  }
  return null
}

export default function WorldMap({ state, speed, stateTime, selected, onSelect, follow, onFollow,
  junctionPref, onJunction, focus }) {
  const canvasRef = useRef(null)
  const world = useMemo(() => buildWorld(state.layout), [state.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // стрелки — только на развилках, где ветки ведут к разным портам
  const switches = useMemo(() => Object.keys(world.adj).map((id) => {
    const kids = world.adj[id].filter((v) => world.parent[v] === id)
    const ports = new Set(kids.map((v) => portBehind(world, v, id)).filter(Boolean))
    return ports.size >= 2 ? { id, kids: kids.filter((v) => portBehind(world, v, id)) } : null
  }).filter(Boolean), [world])
  const props = useRef(null)
  const cam = useRef(null)
  const hits = useRef({ trains: [], junctions: [] })
  const disp = useRef({ clock: state.clock, id: state.id })
  const drag = useRef(null)

  useEffect(() => {
    props.current = { state, speed, stateTime, selected, follow, junctionPref }
  })

  function fit(W, H) {
    const b = world.bbox
    const scale = Math.min(W / (b.x1 - b.x0), H / (b.y1 - b.y0))
    cam.current = { cx: (b.x0 + b.x1) / 2, cy: (b.y0 + b.y1) / 2, scale, min: scale * 0.6 }
  }

  useEffect(() => {
    if (!focus || !cam.current) return
    const { state: s } = props.current
    if (focus.fit) {
      const c = canvasRef.current
      fit(c.clientWidth, c.clientHeight)
      return
    }
    let p = null
    if (focus.node) p = world.pos[focus.node]
    if (focus.train) {
      const t = s.obs.trains.find((x) => x.id === focus.train)
      p = t && placeTrain(world, t, s, s.clock, cam.current.scale)?.head
    }
    if (p) Object.assign(cam.current, { cx: p.x, cy: p.y, scale: Math.max(cam.current.scale, focus.scale || 0.15) })
  }, [focus]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const canvas = canvasRef.current
    let raf
    const loop = () => {
      render(canvas)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [world]) // eslint-disable-line react-hooks/exhaustive-deps

  function render(canvas) {
    const Pr = props.current
    if (!Pr) return
    const { state: s, speed: spd, stateTime: st0, selected: sel, follow: fol, junctionPref: jp } = Pr
    const dpr = window.devicePixelRatio || 1
    const W = canvas.clientWidth, H = canvas.clientHeight
    if (!W || !H) return
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr)
      canvas.height = Math.round(H * dpr)
    }
    if (!cam.current) fit(W, H)
    const ctx = canvas.getContext('2d')
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    // плавные часы между ответами сервера
    const running = s.status === 'running' && !s.paused && spd > 0
    let clock = s.clock + (running ? Math.min((spd * (performance.now() - st0)) / 1000, spd * 0.5) : 0)
    if (disp.current.id === s.id && clock < disp.current.clock && disp.current.clock - clock < spd) clock = disp.current.clock
    disp.current = { clock, id: s.id }

    const cm = cam.current
    const k = cm.scale
    const trains = s.obs.trains
    const placed = []
    for (const t of trains) {
      const pl = placeTrain(world, t, s, clock, k)
      if (pl) placed.push([t, pl])
    }
    if (fol) {
      const f = placed.find(([t]) => t.id === fol)
      if (f) { cm.cx = f[1].head.x; cm.cy = f[1].head.y }
    }
    const R = {
      ctx, k, W, H, now: performance.now() / 1000, night: darkness(clock),
      X: (p) => (p.x - cm.cx) * k + W / 2, Y: (p) => (p.y - cm.cy) * k + H / 2,
      wx0: cm.cx - W / 2 / k, wx1: cm.cx + W / 2 / k, wy0: cm.cy - H / 2 / k, wy1: cm.cy + H / 2 / k,
    }

    drawGround(R, cm)
    drawSea(R, world)
    drawGroves(R, world.decor.groves)
    drawWorldDecor(R, world.decor.world)

    // перегоны (закрытые — красным, с окном — жёлтым)
    const segObs = Object.fromEntries(s.obs.segments.map((x) => [x.id, x]))
    for (const g of Object.values(world.segs)) {
      const so = segObs[g.id]
      const color = so?.outage ? '#e0483b' : so?.windows?.some((w) => w.state === 'active' || w.state === 'pending') ? '#f0b52f' : null
      drawTrack(R, g.p0, g.p1, color ? { color, ballast: color } : {})
    }
    // подходы с соседних участков
    for (const b of world.boundaries) drawTrack(R, world.pos[b], add(world.pos[b], world.outward(b), 14000), { ballast: '#b8afa4' })

    // станции: подушка и платформы, пути со стрелочными съездами
    const soonPassenger = (node) => trains.some((t) => t.type === 'passenger' && t.stops.some((x) => x.node === node)
      && !t.visited.includes(node) && (t.run?.to === node || (t.run && t.run.eta - clock < 2400)))
    for (const n of s.layout.nodes) {
      if (!n.tracks.length) continue
      drawStationGround(R, world, n.id, soonPassenger(n.id) ? 1 : 0)
      const a = world.axis[n.id]
      for (const t of world.tracks[n.id]) {
        const tl = trackLine(world, n.id, t.id, k)
        drawTrack(R, tl.e, tl.w, t.kind === 'port' ? { ballast: '#c7b48a' } : {})
        const e = world.east(n.id), w = world.west(n.id)
        if (Math.abs(tl.e.x - e.x) + Math.abs(tl.e.y - e.y) > 1) {
          drawTrack(R, e, add(tl.e, a, -25))
          drawTrack(R, add(tl.e, a, -25), tl.e)
          drawTrack(R, add(tl.w, a, 25), w)
          drawTrack(R, tl.w, add(tl.w, a, 25))
        } else {
          drawTrack(R, e, tl.e)
          drawTrack(R, tl.w, w)
        }
      }
      if (n.type === 'port') {
        const pr = world.piers[n.id]
        ctx.strokeStyle = P.pier
        ctx.lineWidth = Math.max(5, 30 * k)
        ctx.beginPath(); ctx.moveTo(R.X(pr.start), R.Y(pr.start)); ctx.lineTo(R.X(pr.berth), R.Y(pr.berth)); ctx.stroke()
      }
    }
    drawStationDecor(R, world, world.decor.station)

    // светофоры на выходах со станций: зелёный — маршрут отсюда задан
    if (k > 0.012) {
      for (const g of Object.values(world.segs)) {
        const so = segObs[g.id]
        for (const [node, at] of [[g.u, g.p0], [g.v, g.p1]]) {
          if (!world.nodes[node].tracks.length) continue
          const res = so.reserved && trains.find((t) => t.id === so.reserved)
          const mine = res && (res.node === node || res.run?.to === node)
          const f = frame(world, node, k)
          const p = add(at, normal(world.axis[node]), f.vMin - f.g * 1.2)
          drawSignal(R, p, Math.atan2(world.axis[node].y, world.axis[node].x), !!mine)
        }
      }
    }

    // паромы
    const byPort = {}
    for (const f of s.obs.ferries) {
      const pr = world.piers[f.port]
      if (!pr) continue
      const idx = (byPort[f.port] = (byPort[f.port] ?? -1) + 1)
      const nn = normal(pr.axis)
      let c, text, alpha = 1, toPort = true
      if (f.status === 'unloading' || f.status === 'loading') {
        c = add(pr.berth, pr.axis, 90)
        text = `${f.id} · ${f.status === 'loading' ? 'погрузка' : 'выгрузка'} ${f.loaded}/${f.capacity}`
      } else if (f.status === 'waiting_berth') {
        c = add(add(pr.berth, pr.axis, 1800), nn, 700 * idx)
        text = `${f.id} · ждёт причала`
      } else if (f.status === 'expected') {
        c = add(add(pr.berth, pr.axis, Math.min(45000, Math.max(2500, (f.eta - clock) * FERRY_SPEED))), nn, 900 * idx)
        text = `${f.id} · ETA ${hm(f.eta)}`
        alpha = 0.9
      } else {
        const dt = clock - f.departed
        if (dt > 3 * 3600) continue
        c = add(pr.berth, pr.axis, 300 + dt * FERRY_SPEED)
        text = `${f.id} · увёз ${f.loaded}`
        alpha = 0.65
        toPort = false
      }
      drawShip(R, R.X(c), R.Y(c), pr.axis, toPort, f, alpha)
      label(R, R.X(c), R.Y(c) - Math.max(14, 20 * k) - 6, text, { font: '600 11px system-ui' })
    }

    // поезда
    const hitT = []
    const blink = Math.floor(performance.now() / 400) % 2 === 0
    for (const [t, pl] of placed) {
      const d = drawTrainSprite(R, t, pl, { minLen: MIN_TRAIN_PX, selected: t.id === sel || t.id === fol, blink })
      const hs = { x: R.X(pl.head), y: R.Y(pl.head) }
      const tail = { x: hs.x - Math.cos(d.ang) * d.len, y: hs.y - Math.sin(d.ang) * d.len }
      hitT.push({ id: t.id, a: hs, b: tail, w: d.wd })
      if (!pl.wagonsOnly && (!pl.offstage || t.id === sel)) {
        const late = Math.round(((t.arrived_at ?? clock) - t.planned_arrival) / 60)
        const txt = `${t.id}${late > 0 ? ` +${late}` : ''}${t.failure ? ' ⚠' : ''}`
        const mx = (hs.x + tail.x) / 2, my = Math.min(hs.y, tail.y) - d.wd / 2 - 8
        label(R, mx, my, txt, {
          font: `600 ${k > 0.01 ? 11 : 10}px system-ui`,
          color: late > 15 ? '#c62d20' : late > 0 ? '#a86400' : P.labelText,
        })
      }
    }

    nightOverlay(R)
    for (const n of s.layout.nodes) lampGlow(R, world, n.id)

    // значки и подписи станций — поверх всего
    for (const n of s.layout.nodes) {
      if (!n.tracks.length) continue
      if (k < K_OVER) drawStationIcon(R, world, n.id)
      const f = frame(world, n.id, k)
      const far = k < K_DECOR ? f.neg - 18 / k : f.neg - 70 - 40 / k
      const p = f.at(0, far)
      const big = n.type !== 'siding'
      label(R, R.X(p), R.Y(p), n.name, { font: `${big ? 700 : 600} ${big ? 13 : 11}px system-ui` })
    }
    for (const b of world.boundaries) {
      const p = add(world.pos[b], world.outward(b), 2500)
      label(R, R.X(p), R.Y(p) - 16, `${s.names[b]} →`, { font: '700 11px system-ui' })
      const q = trains.filter((t) => t.status === 'pending' && t.origin === b)
        .sort((x, y) => x.forecast_appear - y.forecast_appear).slice(0, 3)
      q.forEach((t, i) => label(R, R.X(p), R.Y(p) + 6 + i * 19, `${t.id} ~${hm(t.forecast_appear)}`, { font: '11px system-ui' }))
    }

    // стрелки на развилках
    const hitJ = []
    for (const sw of switches) {
      const id = sw.id
      const wp = world.west(id), ax = world.axis[id], nn = normal(ax)
      const sx = R.X(wp) + ax.x * 30 + nn.x * 44, sy = R.Y(wp) + ax.y * 30 + nn.y * 44
      const pref = jp?.[id]
      ctx.fillStyle = P.label
      ctx.strokeStyle = pref ? '#2f6cc7' : '#5b6670'
      ctx.lineWidth = 2.5
      ctx.beginPath(); ctx.arc(sx, sy, 15, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
      for (const v of sw.kids) {
        const d = { x: world.pos[v].x - world.pos[id].x, y: world.pos[v].y - world.pos[id].y }
        const l = Math.hypot(d.x, d.y)
        ctx.strokeStyle = pref === v ? '#2f6cc7' : '#3b434b'
        ctx.lineWidth = pref === v ? 4 : 2
        ctx.globalAlpha = pref && pref !== v ? 0.3 : 1
        ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + (d.x / l) * 11, sy + (d.y / l) * 11); ctx.stroke()
        ctx.globalAlpha = 1
      }
      const portName = pref ? s.names[portBehind(world, pref, id)] : 'авто'
      label(R, sx, sy - 24, `стрелка: ${portName}`, { font: '600 10px system-ui' })
      hitJ.push({ id, x: sx, y: sy, kids: sw.kids })
    }
    hits.current = { trains: hitT, junctions: hitJ }

    // масштабная линейка
    const nice = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]
    const m = nice.find((v) => v * k >= 80) || 50000
    ctx.fillStyle = 'rgba(255,255,255,.85)'
    ctx.fillRect(10, H - 38, Math.max(m * k + 12, 60), 28)
    ctx.strokeStyle = '#1d2329'
    ctx.fillStyle = '#1d2329'
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.moveTo(16, H - 16); ctx.lineTo(16 + m * k, H - 16); ctx.stroke()
    ctx.font = '11px system-ui'
    ctx.textAlign = 'left'
    ctx.fillText(m >= 1000 ? `${m / 1000} км` : `${m} м`, 16, H - 22)
    if (placed.some(([, pl]) => pl.len * k < MIN_TRAIN_PX)) {
      label(R, 16 + m * k + 200, H - 18, 'поезда укрупнены — приблизьте колесом, чтобы увидеть реальную длину', { font: '11px system-ui' })
    }
  }

  // ------------------------------------------------ ввод
  useEffect(() => {
    const c = canvasRef.current
    const onWheel = (e) => {
      e.preventDefault()
      const cm = cam.current
      if (!cm) return
      const r = c.getBoundingClientRect()
      const mx = e.clientX - r.left - c.clientWidth / 2, my = e.clientY - r.top - c.clientHeight / 2
      const wx = cm.cx + mx / cm.scale, wy = cm.cy + my / cm.scale
      const f = Math.exp(-e.deltaY * 0.0015)
      cm.scale = Math.min(MAX_SCALE, Math.max(cm.min, cm.scale * f))
      if (!props.current.follow) {
        cm.cx = wx - mx / cm.scale
        cm.cy = wy - my / cm.scale
      }
    }
    c.addEventListener('wheel', onWheel, { passive: false })
    return () => c.removeEventListener('wheel', onWheel)
  }, [])

  function pick(e) {
    const r = canvasRef.current.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    for (const j of hits.current.junctions) if (Math.hypot(x - j.x, y - j.y) < 18) return { junction: j }
    let best = null, bd = 12
    for (const h of hits.current.trains) {
      const d = distToSeg(x, y, h.a, h.b) - h.w / 2
      if (d < bd) { bd = d; best = h.id }
    }
    return { train: best }
  }

  return (
    <div className="world">
      <canvas ref={canvasRef}
        onMouseDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, moved: false } }}
        onMouseMove={(e) => {
          const d = drag.current
          if (!d) return
          const dx = e.clientX - d.x, dy = e.clientY - d.y
          if (Math.abs(dx) + Math.abs(dy) > 3) {
            d.moved = true
            if (props.current.follow) onFollow(null)
            cam.current.cx -= dx / cam.current.scale
            cam.current.cy -= dy / cam.current.scale
            d.x = e.clientX; d.y = e.clientY
          }
        }}
        onMouseUp={(e) => {
          const d = drag.current
          drag.current = null
          if (d?.moved) return
          const h = pick(e)
          if (h.junction) onJunction(h.junction.id, h.junction.kids)
          else onSelect(h.train)
        }}
        onMouseLeave={() => { drag.current = null }}
        onDoubleClick={(e) => { const h = pick(e); if (h.train) onFollow(h.train) }}
      />
      <div className="zoom">
        <button onClick={() => { cam.current.scale = Math.min(MAX_SCALE, cam.current.scale * 1.6) }}>+</button>
        <button onClick={() => { cam.current.scale = Math.max(cam.current.min, cam.current.scale / 1.6) }}>−</button>
        <button title="весь участок" onClick={() => { onFollow(null); const c = canvasRef.current; fit(c.clientWidth, c.clientHeight) }}>⤢</button>
      </div>
    </div>
  )
}
