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
const pages: { key: View; title: string; icon: string; caption: string }[] = [
  { key: 'overview', title: 'Stack overview', icon: '▦', caption: 'Readiness & room time' },
  { key: 'prep', title: 'Prep & labels', icon: '▤', caption: 'Import, review, print' },
  { key: 'records', title: 'Specimen records', icon: '≡', caption: 'Entry, exit & history' },
  { key: 'transactions', title: 'Transaction log', icon: '↔', caption: 'Review & correct' },
  { key: 'backups', title: 'Backups', icon: '◫', caption: 'Snapshots & recovery' },
]
function currentView(): View { const value = location.hash.slice(1); if (value === 'supervisor') return 'transactions'; return value === 'kiosk' || pages.some(p => p.key === value) ? value as View : 'overview' }
export default function App() {
  const [view, setView] = useState<View>(currentView)
  const [config, setConfig] = useState<Config>({ rooms: [], stations: [], treatments: [] })
  const [healthy, setHealthy] = useState<boolean | null>(null)
  useEffect(() => { const change = () => setView(currentView()); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change) }, [])
  useEffect(() => { let alive = true; const check = async () => { try { await api('/health', {}, 3500); const value = await api<Config>('/config', {}, 3500); if (alive) { setHealthy(true); setConfig(value) } } catch { if (alive) setHealthy(false) } }; void check(); const timer = setInterval(check, 5000); return () => { alive = false; clearInterval(timer) } }, [])
  const navigate = (next: View) => { location.hash = next; setView(next) }
  if (view === 'kiosk') return <Kiosk config={config} healthy={healthy === true} />
  const page = pages.find(p => p.key === view)!
  return <div className="app-shell"><aside className="sidebar"><a className="brand" href="#overview"><span className="brand-mark">fc</span><div>FABRIC<span>CONDITIONING</span></div></a><div className="side-label">LAB WORKSPACE</div><nav aria-label="Main navigation">{pages.map(p => <button key={p.key} className={`nav-item ${view === p.key ? 'active' : ''}`} onClick={() => navigate(p.key)}><span className="nav-icon">{p.icon}</span><span>{p.title}<small>{p.caption}</small></span></button>)}</nav><div className="side-foot"><a className="station-link" href="#kiosk" target="_blank">Open room station ↗</a><p>One shared database.<br />Every physical piece accounted for.</p><div className="zone">America/New_York</div></div></aside><div className="main-shell"><header className="topbar"><div><span className="breadcrumb">WORKSPACE</span><span className="slash">/</span>{page.title}</div><span className={`connection ${healthy === false ? 'bad' : healthy ? 'good' : ''}`} role="status"><i />{healthy === null ? 'Connecting…' : healthy ? 'Database connected' : 'NOT AVAILABLE'}</span></header>{healthy === false && <div className="offline-banner" role="alert"><strong>NOT AVAILABLE</strong> The database cannot be reached. Changes and printing are unavailable.</div>}<main>{view === 'overview' && <Overview config={config} healthy={healthy === true} />}{view === 'prep' && <Prep config={config} healthy={healthy === true} />}{view === 'records' && <Records config={config} healthy={healthy === true} />}{view === 'transactions' && <Transactions config={config} healthy={healthy === true} />}{view === 'backups' && <Backups healthy={healthy === true} />}</main><footer className="main-foot"><span>FABRIC CONDITIONING</span><span>Eastern Time · Confirmed database records</span></footer></div></div>
}
export function ErrorNotice({ message }: { message: string }) { return message ? <div className="notice error" role="alert">{message}</div> : null }
export function PageHeader({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: React.ReactNode }) { return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{children}</div> }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  const container = useRef<HTMLElement>(null); const close = useRef(onClose); close.current = onClose
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; const focusable = () => Array.from(container.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]') || []); focusable()[0]?.focus(); const handle = (event: KeyboardEvent) => { if (event.key === 'Escape') close.current(); if (event.key !== 'Tab') return; const nodes = focusable(), first = nodes[0], last = nodes[nodes.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() } }; document.addEventListener('keydown', handle); return () => { document.removeEventListener('keydown', handle); previous?.focus() } }, [])
  return <div className="modal-backdrop"><section ref={container} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><header><div><span className="eyebrow">FABRIC CONDITIONING</span><h2>{title}</h2></div><button className="icon-button" aria-label="Close dialog" onClick={onClose}>×</button></header>{children}</section></div>
}
