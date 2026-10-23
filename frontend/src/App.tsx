import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Config } from './types'
import Prep from './Prep'
import Kiosk from './Kiosk'
import Records from './Records'
import Transactions from './Transactions'
import Overview from './Overview'
import Backups from './Backups'

type View = 'overview' | 'prep' | 'records' | 'transactions' | 'backups' | 'kiosk'
const pages = [
  { key: 'overview', title: 'Stacks', icon: '▦' },
  { key: 'specimens', title: 'Specimens', icon: '▤' },
  { key: 'transactions', title: 'Transactions', icon: '↔' },
  { key: 'backups', title: 'Backups', icon: '◫' },
] as const
function currentView(): View {
  const value = location.hash.slice(1).split('?')[0]
  if (value === 'supervisor') return 'transactions'
  if (value === 'specimens') return 'records'
  return ['overview', 'prep', 'records', 'transactions', 'backups', 'kiosk'].includes(value) ? value as View : 'overview'
}
export default function App() {
  const [view, setView] = useState<View>(currentView)
  const [config, setConfig] = useState<Config>({ rooms: [], stations: [], treatments: [] })
  const [healthy, setHealthy] = useState<boolean | null>(null)
  const lastSpecimenPage = useRef<View>(view === 'prep' ? 'prep' : 'records')
  const workspace = view === 'prep' || view === 'records'
  if (workspace) lastSpecimenPage.current = view
  useEffect(() => {
    const change = () => setView(currentView())
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    let alive = true, checking = false
    const check = async () => {
      if (checking) return
      checking = true
      try {
        await api('/health', {}, 3500)
        const value = await api<Config>('/config', {}, 3500)
        if (alive) { setHealthy(true); setConfig(value) }
      } catch { if (alive) setHealthy(false) }
      finally { checking = false }
    }
    void check()
    const timer = setInterval(check, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  function navigate(next: View) { location.hash = next; setView(next) }
  if (view === 'kiosk') return <Kiosk config={config} healthy={healthy === true} />
  const page = pages.find(item => workspace ? item.key === 'specimens' : item.key === view)!
  return <div className="app-shell">
    <aside className="sidebar">
      <nav aria-label="Main navigation">
        {pages.map(item => <button key={item.key}
          className={'nav-item ' + (page.key === item.key ? 'active' : '')} aria-current={page.key === item.key ? 'page' : undefined}
          onClick={() => navigate(item.key === 'specimens' ? lastSpecimenPage.current : item.key)}>
          <span className="nav-icon" aria-hidden="true">{item.icon}</span><span>{item.title}</span>
        </button>)}
      </nav>
      <div className="side-foot">
        <a className="station-link" href="#kiosk" target="_blank" rel="noreferrer">Room station ↗</a>
        <span className={'connection ' + (healthy === false ? 'bad' : healthy ? 'good' : '')} role="status">
          <i aria-hidden="true" />{healthy === null ? 'Connecting…' : healthy ? 'Database connected' : 'Offline'}
        </span>
      </div>
    </aside>
    <div className="main-shell">
      {healthy === false && <div className="offline-banner" role="alert"><strong>NOT AVAILABLE</strong>The database cannot be reached. Changes and printing are unavailable.</div>}
      <main>
        {workspace && <div className="workspace-tabs" role="tablist" aria-label="Specimen workspace">
          <button role="tab" aria-selected={view === 'prep'} onClick={() => navigate('prep')}>Prepare / import</button>
          <button role="tab" aria-selected={view === 'records'} onClick={() => navigate('records')}>Registered specimens</button>
        </div>}
        {view === 'overview' && <Overview config={config} healthy={healthy === true} />}
        {view === 'prep' && <Prep config={config} healthy={healthy === true} />}
        {view === 'records' && <Records config={config} healthy={healthy === true} />}
        {view === 'transactions' && <Transactions config={config} healthy={healthy === true} />}
        {view === 'backups' && <Backups healthy={healthy === true} />}
      </main>
    </div>
  </div>
}
export function ErrorNotice({ message }: { message: string }) {
  return message ? <div className="notice error" role="alert">{message}</div> : null
}
export function PageHeader({
  title, description, children,
}: { eyebrow?: string; title: string; description?: string; children?: React.ReactNode }) {
  return <div className="page-heading"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{children}</div>
}
const modalStack: symbol[] = []
export function Modal({
  title, children, onClose, wide = false,
}: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  const container = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  const identity = useRef(Symbol('modal'))
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const token = identity.current
    modalStack.push(token)
    const focusable = () => Array.from(container.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]',
    ) || [])
    focusable()[0]?.focus()
    const handle = (event: KeyboardEvent) => {
      if (modalStack.at(-1) !== token) return
      if (event.key === 'Escape') { event.preventDefault(); close.current() }
      if (event.key !== 'Tab') return
      const nodes = focusable(), first = nodes[0], last = nodes.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', handle)
    return () => {
      document.removeEventListener('keydown', handle)
      const index = modalStack.indexOf(token)
      if (index >= 0) modalStack.splice(index, 1)
      if (previous?.isConnected) previous.focus()
    }
  }, [])
  return <div className="modal-backdrop">
    <section ref={container} className={'modal ' + (wide ? 'wide' : '')} role="dialog" aria-modal="true" aria-label={title}>
      <header><div><h2>{title}</h2></div>
        <button className="icon-button" aria-label="Close dialog" onClick={onClose}>×</button></header>
      {children}
    </section>
  </div>
}
