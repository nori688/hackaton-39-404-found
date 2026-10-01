import { useState } from 'react'
import Game from './Game'
import Menu from './Menu'
import Replay from './Replay'
import Report from './Report'

export default function App() {
  const [screen, setScreen] = useState({ name: 'menu' })
  const menu = () => setScreen({ name: 'menu' })
  if (screen.name === 'game') {
    return <Game initial={screen.state} initialView={screen.mode} onQuit={menu}
      onEnd={(id) => setScreen({ name: 'report', id })} />
  }
  if (screen.name === 'report') {
    return <Report gameId={screen.id} onMenu={menu}
      onReplay={(request) => setScreen({ name: 'replay', request, back: { name: 'report', id: screen.id } })} />
  }
  if (screen.name === 'replay') return <Replay request={screen.request} onBack={() => setScreen(screen.back)} />
  return <Menu onStart={(state, mode) => setScreen({ name: 'game', state, mode })}
    onReplay={(request) => setScreen({ name: 'replay', request, back: { name: 'menu' } })} />
}
