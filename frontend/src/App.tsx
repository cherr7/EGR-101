import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Config } from './types'
import Prep from './Prep'
import Kiosk from './Kiosk'
import Records from './Records'
import Supervisor from './Supervisor'

type View = 'kiosk' | 'prep' | 'records' | 'supervisor'
const pages: { key: View; title: string; icon: string; caption: string }[] = [
  { key: 'kiosk', title: 'Room station', icon: '↔', caption: 'Scan in. Scan out.' },
  { key: 'prep', title: 'Prepare specimens', icon: '▦', caption: 'Register & label' },
  { key: 'records', title: 'Specimen records', icon: '≡', caption: 'Sessions & reports' },
  { key: 'supervisor', title: 'Supervisor', icon: '◇', caption: 'Corrections & audit' },
]

export default function App() {
  const [view, setView] = useState<View>(() => (pages.some(p => p.key === location.hash.slice(1)) ? location.hash.slice(1) : 'kiosk') as View)
  const [config, setConfig] = useState<Config>({ rooms: [], stations: [], treatments: [] })
  const [healthy, setHealthy] = useState<boolean | null>(null)
  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        await api('/health', {}, 3500)
        if (alive) setHealthy(true)
        const value = await api<Config>('/config', {}, 3500)
        if (alive) setConfig(value)
      } catch { if (alive) setHealthy(false) }
    }
    void check()
    const timer = setInterval(check, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  useEffect(() => { location.hash = view }, [view])
  const page = pages.find(p => p.key === view)!
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">f<span>c</span></span><div>FABRIC<span>CONDITIONING TRACKER</span></div></div>
      <div className="side-label">LAB WORKSPACE</div>
      <nav aria-label="Main navigation">{pages.map(p => <button key={p.key} className={`nav-item ${view === p.key ? 'active' : ''}`} onClick={() => setView(p.key)}><span className="nav-icon">{p.icon}</span><span>{p.title}<small>{p.caption}</small></span>{view === p.key && <span className="nav-dot" />}</button>)}</nav>
      <div className="side-foot"><span className="badge muted">CLASSROOM PROTOTYPE</span><p>One record for every specimen.<br />Every movement accounted for.</p><div className="zone">America/New_York</div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div><span className="breadcrumb">WORKSPACE</span><span className="slash">/</span>{page.title}</div><span className={`connection ${healthy === false ? 'bad' : healthy ? 'good' : ''}`} role="status"><i />{healthy === null ? 'Connecting…' : healthy ? 'Database connected' : 'NOT AVAILABLE'}</span></header>
      {healthy === false && <div className="offline-banner" role="alert"><strong>NOT AVAILABLE</strong> The central server cannot be reached. Movements are disabled until the connection returns.</div>}
      <main>
        {view === 'kiosk' && <Kiosk config={config} healthy={healthy === true} />}
        {view === 'prep' && <Prep config={config} healthy={healthy === true} />}
        {view === 'records' && <Records config={config} healthy={healthy === true} />}
        {view === 'supervisor' && <Supervisor config={config} healthy={healthy === true} />}
      </main>
      <footer className="main-foot"><span>FABRIC CONDITIONING TRACKER</span><span>Times displayed in Eastern Time · Server-confirmed records</span></footer>
    </div>
  </div>
}

export function ErrorNotice({ message }: { message: string }) { return message ? <div className="notice error" role="alert">{message}</div> : null }
export function PageHeader({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: React.ReactNode }) { return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{children}</div> }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  const container = useRef<HTMLElement>(null)
  const close = useRef(onClose); close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const focusable = () => Array.from(container.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]') || [])
    focusable()[0]?.focus()
    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current()
      if (event.key !== 'Tab') return
      const nodes = focusable(), first = nodes[0], last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', handle)
    return () => { document.removeEventListener('keydown', handle); previous?.focus() }
  }, [])
  return <div className="modal-backdrop"><section ref={container} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}>×</button></header>{children}</section></div>
}
