import { useEffect, useState } from 'react'
import { api, elapsed, formatTime } from './api'
import type { Config, Stack } from './types'
import { ErrorNotice, PageHeader } from './App'

export function StackDetail({ stack, config }: { stack: Stack; config: Config }) {
  const roomName = (id: string | null) => config.rooms.find(r => r.id === id)?.name || id || '—'
  return <section className="card record-detail"><div className="card-heading"><div><div className="eyebrow">SPECIMEN HISTORY {stack.is_demo ? '· DEMO' : ''}</div><h2>{stack.project_number} / {stack.material_ref}</h2><p>{stack.test_code} · {stack.preconditioning_code} · Registered {formatTime(stack.created_at)} ET</p></div><a className="button secondary" target="_blank" rel="noreferrer" href={`/api/stacks/${stack.id}/labels.pdf`}>Reprint labels ↗</a></div>
    <div className="table-wrap"><table><thead><tr><th>Specimen</th><th>State</th><th>Room</th><th>Current entry</th><th>Elapsed</th><th>Conditioning</th></tr></thead><tbody>{stack.specimens.map(s => <tr key={s.id}><td><b>#{String(s.number).padStart(2, '0')}</b><small className="id-hint">{s.id.slice(0, 8)}</small></td><td><span className={`badge ${s.state === 'IN' ? 'good' : 'muted'}`}>{s.state}</span></td><td>{roomName(s.room_id)}</td><td>{formatTime(s.entered_at)}</td><td>{s.state === 'IN' ? elapsed(s.entered_at) : '—'}</td><td>{s.state !== 'IN' ? '—' : s.due_at && Date.parse(s.due_at) <= Date.now() ? <span className="badge good">24h complete</span> : <span className="badge warning">In progress</span>}</td></tr>)}</tbody></table></div>
    <div className="detail-sections"><div><h3>Conditioning sessions</h3>{stack.specimens.every(s => !s.sessions.length) ? <p className="subtle">No sessions recorded yet.</p> : <div className="table-wrap"><table className="compact"><thead><tr><th>Specimen / Room</th><th>Entered → Exited</th><th>Duration</th><th>Source</th></tr></thead><tbody>{stack.specimens.flatMap(s => s.sessions.map(session => <tr key={session.id}><td><b>#{String(s.number).padStart(2, '0')}</b><small>{roomName(session.room_id)}</small></td><td>{formatTime(session.entered_at)}<small>→ {session.exited_at ? formatTime(session.exited_at) : 'Still in room'}</small></td><td>{elapsed(session.entered_at, session.exited_at)}</td><td>{session.source}{session.corrected && <span className="badge warning">Corrected</span>}<small>Recorded {formatTime(session.recorded_at)}</small></td></tr>))}</tbody></table></div>}</div><div><h3>Audit history</h3>{stack.events?.length ? <ol className="audit-list">{[...stack.events].reverse().map(event => <li key={event.id}><span className={`audit-icon ${event.kind === 'movement' ? 'movement' : ''}`}>{event.kind === 'movement' ? '↔' : '◇'}</span><div><b>{event.kind.replaceAll('_', ' ')} {event.direction || ''}{event.undone && ' · Undone'}</b><small>{formatTime(event.recorded_at)} ET · {event.actor}</small><p>{event.reason || `${event.affected_count} specimens affected`}</p><small className="id-hint">Event {event.id.slice(0, 8)}</small></div></li>)}</ol> : <p className="subtle">No audit events yet.</p>}</div></div>
  </section>
}

export default function Records({ config, healthy }: { config: Config; healthy: boolean }) {
  const [filters, setFilters] = useState({ q: '', project_number: '', material_ref: '', test_code: '', preconditioning_code: '', room_id: '', status: '' })
  const [items, setItems] = useState<Stack[]>([])
  const [selected, setSelected] = useState<Stack | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    if (!healthy) return
    let alive = true
    const timer = setTimeout(() => {
      setLoading(true)
      const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value))
      void api<{ items: Stack[] }>(`/stacks?${query}`).then(result => { if (alive) { setItems(result.items); setError('') } }).catch(err => { if (alive) setError((err as Error).message) }).finally(() => { if (alive) setLoading(false) })
    }, 200)
    return () => { alive = false; clearTimeout(timer) }
  }, [filters, healthy, refresh])
  async function select(id: string) { try { setSelected(await api<Stack>(`/stacks/${id}`)); setError('') } catch (err) { setError((err as Error).message) } }
  const exportQuery = filters.project_number ? `?project_number=${encodeURIComponent(filters.project_number)}` : ''
  return <>
    <PageHeader eyebrow="SPECIMEN RECORDS" title="The full conditioning picture." description="Find a stack, inspect its specimen sessions, and export the recorded history."><button className="button secondary" disabled={!healthy || loading} onClick={() => { setRefresh(v => v + 1); if (selected) void select(selected.id) }}>↻ Refresh</button></PageHeader>
    <ErrorNotice message={error} />
    <section className="card filters"><label className="search-label"><span>⌕</span><input aria-label="Search records" placeholder="Search project, material, test…" value={filters.q} onChange={e => setFilters(f => ({ ...f, q: e.target.value }))} /></label><div className="filter-grid">{(['project_number', 'material_ref', 'test_code', 'preconditioning_code'] as const).map(key => <label key={key}>{({ project_number: 'Project number', material_ref: 'Material reference', test_code: 'Test code', preconditioning_code: 'Preconditioning' })[key]}<input aria-label={`Filter ${key}`} placeholder="Any" value={filters[key]} onChange={e => setFilters(f => ({ ...f, [key]: e.target.value }))} /></label>)}<label>Room<select value={filters.room_id} onChange={e => setFilters(f => ({ ...f, room_id: e.target.value }))}><option value="">All rooms</option>{config.rooms.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label><label>Status<select value={filters.status} onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}><option value="">Any status</option><option value="IN">In room</option><option value="OUT">Out of room</option><option value="MIXED">Mixed locations</option></select></label></div></section>
    <section className="card"><div className="card-heading"><div><h2>Registered stacks <span className="count-pill">{items.length}</span></h2><p>{loading ? 'Loading records…' : 'Select a stack to see every specimen and its history.'}</p></div><div className="export-actions"><a className="button secondary" href={`/api/exports/sessions.csv${exportQuery}`}>Sessions CSV ↓</a><a className="button secondary" href={`/api/exports/history.csv${exportQuery}`}>Audit CSV ↓</a><a className="button secondary" target="_blank" rel="noreferrer" href={`/api/exports/report${exportQuery}`}>Print report ↗</a></div></div>
      <div className="table-wrap"><table className="records-table"><thead><tr><th>Project / Material</th><th>Test</th><th>Treatment</th><th>Specimens</th><th>In room</th><th>Registered</th><th /></tr></thead><tbody>{items.map(stack => <tr key={stack.id} className={selected?.id === stack.id ? 'selected-row' : ''}><td><b>{stack.project_number}</b><small>{stack.material_ref} {stack.is_demo && <span className="badge muted">DEMO</span>}</small></td><td>{stack.test_code}</td><td><span className="badge outline">{stack.preconditioning_code}</span></td><td>{stack.specimens?.length ?? stack.specimen_count}</td><td>{stack.specimens?.filter(s => s.state === 'IN').length ?? '—'}</td><td>{formatTime(stack.created_at)}</td><td><button aria-label={`View ${stack.material_ref}`} className="button text" onClick={() => void select(stack.id)}>View →</button></td></tr>)}</tbody></table></div>
      {!items.length && !loading && <div className="empty-inline"><span>≡</span><h3>No stacks found</h3><p>Try a different filter or register a stack in Prepare specimens.</p></div>}
      <div className="table-foot">Exports use the project number filter{filters.project_number ? `: ${filters.project_number}` : '; currently all projects'}. Other filters apply to the table.</div>
    </section>
    {selected && <StackDetail stack={selected} config={config} />}
  </>
}
