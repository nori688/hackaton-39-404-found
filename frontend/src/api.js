async function req(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
  return data
}

export const api = {
  scenarios: () => req('GET', '/api/scenarios'),
  scenario: (id) => req('GET', `/api/scenarios/${id}`),
  validate: (raw) => req('POST', '/api/scenarios/validate', raw),
  newGame: (body) => req('POST', '/api/game', body),
  advance: (id, dt) => req('POST', `/api/game/${id}/advance`, { dt }),
  resume: (id) => req('POST', `/api/game/${id}/resume`),
  command: (id, cmd) => req('POST', `/api/game/${id}/command`, cmd),
  report: (id) => req('GET', `/api/game/${id}/report`),
}

export const replayApi = {
  start: (body) => req('POST', '/api/replay', body),
  status: (id) => req('GET', `/api/replay/${id}`),
  compare: (id, cid) => req('GET', `/api/replay/${id}/compare/${cid}`),
}
