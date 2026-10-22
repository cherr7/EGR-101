import { useEffect, useState } from 'react'
import type { Config, Stack } from './types'
import { api, formatTime, post } from './api'
import { ErrorNotice, PageHeader } from './App'
import { StackDetail } from './Records'

const browserZone = new Intl.DateTimeFormat().resolvedOptions().timeZone
function localInput(value: string) { const date = new Date(value); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}` }

export default function Supervisor({ config, healthy }: { config: Config; healthy: boolean }) {
  const [auth, setAuth] = useState<{ authenticated: boolean; username?: string }>({ authenticated: false })
  const [username, setUsername] = useState('supervisor')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<Stack[]>([])
  const [stackId, setStackId] = useState('')
  const [stack, setStack] = useState<Stack | null>(null)
  const [kind, setKind] = useState<'edit_session' | 'missed_session' | 'undo'>('edit_session')
  const [sessionId, setSessionId] = useState('')
  const [specimenIds, setSpecimenIds] = useState<string[]>([])
  const [roomId, setRoomId] = useState('shared')
  const [enteredAt, setEnteredAt] = useState('')
  const [exitedAt, setExitedAt] = useState('')
  const [eventId, setEventId] = useState('')
  const [reason, setReason] = useState('')
  useEffect(() => { if (healthy) void api<typeof auth>('/auth/me').then(setAuth).catch(() => {}) }, [healthy])
  useEffect(() => { if (healthy && auth.authenticated) void api<{ items: Stack[] }>('/stacks').then(result => setItems(result.items)).catch(err => setError((err as Error).message)) }, [healthy, auth.authenticated])
  useEffect(() => {
    if (!stackId || !healthy || !auth.authenticated) return
    let alive = true
    void api<Stack>(`/stacks/${stackId}`).then(value => { if (alive) { setStack(value); setSessionId(''); setEventId(''); setSpecimenIds([]); setEnteredAt(''); setExitedAt('') } }).catch(err => setError((err as Error).message))
    return () => { alive = false }
  }, [stackId, healthy, auth.authenticated])
  async function login() {
    setBusy(true); setError('')
    try { await post('/auth/login', { username, password }); setAuth(await api<typeof auth>('/auth/me')); setPassword('') } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const sessions = stack?.specimens.flatMap(s => s.sessions.map(session => ({ ...session, specimen_number: s.number }))) || []
  async function save() {
    setBusy(true); setError(''); setMessage('')
    try {
      const body: Record<string, unknown> = { kind, reason }
      if (kind === 'undo') body.event_id = eventId
      else {
        body.entered_at = new Date(enteredAt).toISOString(); body.exited_at = exitedAt ? new Date(exitedAt).toISOString() : null
        if (kind === 'edit_session') body.session_id = sessionId
        else { body.specimen_ids = specimenIds; body.room_id = roomId }
      }
      await post('/corrections', body)
      setStack(await api<Stack>(`/stacks/${stackId}`)); setReason(''); setMessage('Correction saved. The original observation remains in the audit history.'); setSessionId(''); setEventId(''); setSpecimenIds([])
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const valid = reason.trim().length > 0 && (kind === 'undo' ? !!eventId : !!enteredAt && (kind === 'edit_session' ? !!sessionId : specimenIds.length > 0 && !!roomId))
  return <>
    <PageHeader eyebrow="SUPERVISOR" title="Correct the record. Keep the history." description="Record missed entries, correct session times, or undo a movement with a documented reason.">{auth.authenticated && <button className="button secondary" onClick={async () => { await post('/auth/logout', {}); setAuth({ authenticated: false }); setStack(null) }}>Sign out · {auth.username}</button>}</PageHeader>
    <ErrorNotice message={error} />
    {message && <div className="notice success" role="status">{message}</div>}
    {!auth.authenticated ? <section className="card login-card"><span className="login-mark">◇</span><h2>Supervisor sign in</h2><p>Corrections require a named supervisor account.</p><form onSubmit={e => { e.preventDefault(); void login() }}><label>Username<input aria-label="Supervisor username" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label><label>Password<input aria-label="Supervisor password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" /></label><button className="button primary full" disabled={!healthy || busy || !password}>{busy ? 'Signing in…' : 'Sign in →'}</button></form></section> : <>
      <section className="card correction-card"><div className="card-heading"><div><h2>Document a correction</h2><p>Effective session times and correction recording times remain separate.</p></div><span className="badge good">Signed in as {auth.username}</span></div>
        <label>Stack<select aria-label="Correction stack" value={stackId} onChange={e => { setStackId(e.target.value); setMessage('') }}><option value="">Select a stack</option>{items.map(s => <option key={s.id} value={s.id}>{s.project_number} · {s.material_ref} · {s.test_code}</option>)}</select></label>
        {stack && <><div className="segmented correction-kinds">{([['edit_session', 'Correct timestamps'], ['missed_session', 'Record missed entry'], ['undo', 'Undo movement']] as const).map(([key, label]) => <button key={key} className={kind === key ? 'active' : ''} onClick={() => { setKind(key); setMessage(''); setEnteredAt(''); setExitedAt(''); setSessionId('') }}>{label}</button>)}</div>
          {kind === 'edit_session' && <label>Session<select aria-label="Correction session" value={sessionId} onChange={e => { setSessionId(e.target.value); const session = sessions.find(s => s.id === e.target.value); setEnteredAt(session ? localInput(session.entered_at) : ''); setExitedAt(session?.exited_at ? localInput(session.exited_at) : '') }}><option value="">Select a recorded session</option>{sessions.map(s => <option key={s.id} value={s.id}>Specimen #{String(s.specimen_number).padStart(2, '0')} · {formatTime(s.entered_at)} · {s.exited_at ? 'Closed' : 'Open'}</option>)}</select></label>}
          {kind === 'missed_session' && <><label>Room<select value={roomId} onChange={e => setRoomId(e.target.value)}>{config.rooms.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label><div className="control-label">Affected specimens</div><div className="selection-grid">{stack.specimens.map(s => <label className={`selection-tile ${specimenIds.includes(s.id) ? 'selected' : ''}`} key={s.id}><input type="checkbox" checked={specimenIds.includes(s.id)} onChange={e => setSpecimenIds(ids => e.target.checked ? [...ids, s.id] : ids.filter(id => id !== s.id))} /><b>{String(s.number).padStart(2, '0')}</b><small>{s.state}</small></label>)}</div></>}
          {kind === 'undo' ? <><label>Movement to undo<select aria-label="Movement to undo" value={eventId} onChange={e => setEventId(e.target.value)}><option value="">Select a movement</option>{stack.events?.filter(event => event.kind === 'movement' && !event.undone).reverse().map(event => <option key={event.id} value={event.id}>{event.direction} · {event.affected_count} specimens · {formatTime(event.recorded_at)}</option>)}</select></label><p className="input-help">Undo is allowed only if none of the affected specimens has a later movement or correction.</p></> : <><div className="form-grid"><label>Effective entry time<input aria-label="Effective entry time" type="datetime-local" step="1" value={enteredAt} onChange={e => setEnteredAt(e.target.value)} /></label><label>Effective exit time (optional)<input aria-label="Effective exit time" type="datetime-local" step="1" value={exitedAt} onChange={e => setExitedAt(e.target.value)} /></label></div><p className="input-help">Time inputs use your browser’s zone: {browserZone}. {enteredAt && `Entry will display as ${formatTime(new Date(enteredAt).toISOString())} ET.`} Leave exit blank for a session still in progress.</p></>}
          <label>Reason for correction<textarea aria-label="Correction reason" value={reason} onChange={e => setReason(e.target.value)} rows={3} placeholder="Explain what happened and why this correction is needed." /></label><div className="card-actions"><span className="subtle">Original observations are preserved.</span><button className="button primary" disabled={!healthy || busy || !valid} onClick={() => void save()}>{busy ? 'Saving…' : 'Save correction →'}</button></div>
        </>}
      </section>
      {stack && <StackDetail stack={stack} config={config} />}
    </>}
  </>
}
