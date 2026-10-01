import { adjacency, pathBetween } from './util'

/** Координата станций: магистраль от границы до узла, затем ветка Актау, затем (ниже) ветка Курык. */
export function rows(layout) {
  const adj = adjacency(layout)
  const seg = Object.fromEntries(layout.segments.flatMap((s) => [[`${s.a}|${s.b}`, s], [`${s.b}|${s.a}`, s]]))
  const east = layout.nodes.find((n) => n.type === 'boundary')
  // ветки: сначала к портам, затем остальные тупики (завод и т. п.)
  const ports = [
    ...layout.nodes.filter((n) => n.type === 'port').map((n) => n.id),
    ...layout.nodes.filter((n) => n.type !== 'port' && n.id !== east.id && adj[n.id].length === 1).map((n) => n.id),
  ]
  const y = {}
  let bottom = 0
  ports.forEach((p, k) => {
    const path = pathBetween(adj, east.id, p) || []
    let km = 0, kmBranch = 0
    path.forEach((id, i) => {
      if (i > 0) km += seg[`${path[i - 1]}|${id}`].length_km
      if (id in y) { kmBranch = km; return }
      // первая ветка продолжает магистраль, следующие рисуются ниже уже нарисованного
      y[id] = k === 0 ? km : bottom + 14 + (km - kmBranch)
    })
    bottom = Math.max(...Object.values(y))
  })
  const max = Math.max(...Object.values(y))
  return { y, max }
}
