import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatTime, post } from './api'
import { ErrorNotice, Modal, PageHeader } from './App'

type Backup = { id: string; created_at: string; size?: number; size_bytes?: number; sha256: string; filename: string; verified: boolean; cloud_copy_state?: string; keep_forever?: boolean; expires_at?: string | null }
type Storage = { folder: string; total_bytes: number; archive_count: number; can_open?: boolean }
type Scheduler = { last_success_at?: string | null; last_error?: string | null; next_run_at?: string | null; overdue?: boolean; enabled?: boolean }
const RESTORE_KEY = 'fabric-tracker.pending-restore.v1'
function savedRestore(): string | null { try { return localStorage.getItem(RESTORE_KEY) } catch { return null } }
type Settings = { destination: string | null; retention_days: number; daily_hour: number; enabled: boolean }
type Import = { id: string; status: string; counts?: Record<string, number>; created_at?: string; source_created_at?: string; snapshot_at?: string; message?: string }
export default function Backups({ healthy }: { healthy: boolean }) {
  const [items, setItems] = useState<Backup[]>([]), [settings, setSettings] = useState<Settings>({ destination: null, retention_days: 30, daily_hour: 2, enabled: true }), [error, setError] = useState(''), [message, setMessage] = useState(''), [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0), [staged, setStaged] = useState<Import | null>(null), [confirmation, setConfirmation] = useState('')

  const [scheduler, setScheduler] = useState<Scheduler>({})
  const [storage, setStorage] = useState<Storage | null>(null)
  const [pendingRestore, setPendingRestore] = useState<string | null>(savedRestore)
  const alive = useRef(true), restoreLock = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!healthy) return
    let active = true
    void api<{ items: Backup[]; settings: Settings; scheduler?: Scheduler; storage?: Storage }>('/backups').then(value => {
      if (active) { setItems(value.items); setSettings(value.settings); setScheduler(value.scheduler || {}); setStorage(value.storage || null); setError('') }
    }).catch(err => { if (active) setError((err as Error).message) })
    return () => { active = false }
  }, [healthy, refresh])
  const checkRestore = useCallback(async () => {
    const id = savedRestore()
    if (!id || !healthy || restoreLock.current) return
    restoreLock.current = true
    try {
      const result = await api<Import>('/backups/imports/' + id)
      if (!alive.current) return
      if (result.status === 'restored') {
        localStorage.removeItem(RESTORE_KEY); setPendingRestore(null); setBusy(false); setStaged(null)
        setError(''); setMessage('Backup restored. Refresh the other pages to load the restored records.')
        setRefresh(value => value + 1)
      } else if (result.status === 'validated') {
        localStorage.removeItem(RESTORE_KEY); setPendingRestore(null); setBusy(false); setStaged(result); setConfirmation('')
        setError('The database replacement was not applied. Review the validated backup before retrying.')
      }
    } catch {
      if (alive.current) setError('Restore result is still unconfirmed. The saved restore will be checked again; no new replacement is submitted.')
    } finally { restoreLock.current = false }
  }, [healthy])
  useEffect(() => {
    if (!pendingRestore) return
    setBusy(true)
    if (!healthy) return
    void checkRestore()
    const timer = setInterval(() => void checkRestore(), 3000)
    return () => clearInterval(timer)
  }, [pendingRestore, healthy, checkRestore])

  async function create() { setBusy(true); setError(''); try { await post('/backups', {}); setRefresh(v => v + 1); setMessage('Database backup created. Download a copy or check the configured destination folder.') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }
  async function save() { setBusy(true); setError(''); try { const value = await api<Settings>('/backups/settings', { method: 'PUT', body: JSON.stringify(settings) }); if ('enabled' in value) setSettings(value); setMessage('Backup settings saved. A destination folder is a local copy target; cloud synchronization is managed separately.') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }
  async function stage(file: File) { setBusy(true); setError(''); setStaged(null); try { const form = new FormData(); form.append('file', file); const value = await api<Import>('/backups/import', { method: 'POST', body: form }, 120000); setStaged(value); setConfirmation('') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }

  async function restore() {
    if (!staged || confirmation !== 'RESTORE' || busy || pendingRestore) return
    const id = staged.id
    try { localStorage.setItem(RESTORE_KEY, id) }
    catch { setError('Enable browser storage before restoring so an interrupted response can be reconciled.'); return }
    setBusy(true); setError(''); setPendingRestore(id); restoreLock.current = true
    try {
      await api('/backups/imports/' + id + '/restore', { method: 'POST', body: JSON.stringify({ confirmation: 'RESTORE' }) }, 120000)
      localStorage.removeItem(RESTORE_KEY)
      if (alive.current) {
        setPendingRestore(null); setStaged(null); setBusy(false)
        setMessage('Backup restored. Refresh the other pages to load the restored records.')
        setRefresh(value => value + 1)
      }
    } catch {
      if (alive.current) setError('Restore result is not confirmed. Keep this page open while the same restore is checked.')
    } finally { restoreLock.current = false }
  }

  async function openFolder() { setError(''); try { await post('/backups/open-folder', {}); setMessage('Backup folder opened in Finder on the host Mac.') } catch (err) { setError((err as Error).message) } }

  const formatDate = (value: string) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
  const bytes = (value: number) => value > 1073741824 ? `${(value / 1073741824).toFixed(2)} GB` : value > 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`
  const totalBytes = storage?.total_bytes ?? items.reduce((sum, item) => sum + (item.size_bytes ?? item.size ?? 0), 0)
  return <><PageHeader title="Backups"><button className="button primary" disabled={!healthy || busy} onClick={() => void create()}>{busy ? 'Working…' : '＋ Create backup now'}</button></PageHeader><ErrorNotice message={error} />{message && <div className="notice success" role="status">{message}</div>}<section className="backup-health" aria-label="Backup health">
      <div><span>Latest successful checkpoint</span><b>{formatTime(scheduler.last_success_at || items[0]?.created_at)}</b></div>
      <div><span>Daily schedule</span><b>{scheduler.enabled === false ? 'Paused' : scheduler.overdue ? 'Checkpoint overdue' : formatTime(scheduler.next_run_at)}</b></div>
      <div><span>Additional copy</span><b>{!settings.destination ? 'Destination not configured' : items[0]?.cloud_copy_state === 'copy_failed' ? 'Latest copy failed' : 'Folder copy configured'}</b></div>
    </section>
    {scheduler.last_error && <div className="notice error" role="alert">The scheduled backup could not finish. Create a checkpoint now and check the host or destination.</div>}
    {pendingRestore && <div className="notice warning" role="status"><b>Checking the saved restore.</b><p>New restore actions are paused until the database confirms the result.</p><button className="button secondary" disabled={!healthy} onClick={() => void checkRestore()}>Check restore result</button></div>}
    <div className="prep-bottom"><section className="card"><div className="card-heading"><div><h2>Daily backups</h2><p>Scheduled on the host Mac. Keep the Mac powered and available.</p></div></div><div className="padded"><label className="check-label"><input type="checkbox" checked={settings.enabled} onChange={e => setSettings(s => ({ ...s, enabled: e.target.checked }))} />Enable daily snapshots</label><div className="form-grid"><label>Hour (America/New_York)<input aria-label="Daily backup hour" type="number" min="0" max="23" value={settings.daily_hour} onChange={e => setSettings(s => ({ ...s, daily_hour: Number(e.target.value) }))} /></label><label>Retention (days)<input aria-label="Backup retention" type="number" min="2" max="3650" value={settings.retention_days} onChange={e => setSettings(s => ({ ...s, retention_days: Number(e.target.value) }))} /></label></div><label>Additional destination folder (optional)<input aria-label="Backup destination" placeholder="Absolute folder path on the host Mac" value={settings.destination || ''} onChange={e => setSettings(s => ({ ...s, destination: e.target.value || null }))} /></label><p className="input-help">Backups are deleted after this many days, except each Monday's 2 a.m. backup (Sunday night), which is kept forever.</p><p className="input-help">A future lab share or locally synchronized OneDrive / SharePoint folder can be selected here. A copied file does not prove it has reached the cloud.</p></div><div className="card-actions"><span className="subtle">The app always keeps its local backup.</span><button className="button secondary" disabled={!healthy || busy} onClick={() => void save()}>Save settings</button></div></section><section className="card"><div className="card-heading"><div><h2>Import & restore</h2><p>The archive is checked and loaded into a separate database before you can replace current records.</p></div></div><div className="padded"><div className="notice warning">Restoring replaces the active dataset. The previous database is retained for recovery.</div><label className={`button secondary upload ${busy || !healthy ? 'disabled' : ''}`}>↑ Validate backup archive<input aria-label="Import backup archive" type="file" accept=".zip,application/zip" disabled={!healthy || busy} onChange={e => { if (e.target.files?.[0]) void stage(e.target.files[0]); e.target.value = '' }} /></label><p className="input-help">Choose an archive previously exported by this app. Uploading and validation do not change the active database.</p></div></section></div><section className="card"><div className="card-heading"><div><h2>Saved backups <span className="count-pill">{items.length}</span></h2><p>Portable archives contain the database snapshot and a checksum manifest.</p><p className="backup-storage" aria-label="Backup storage"><span>Total storage: <b data-testid="backup-total-size">{bytes(totalBytes)}</b></span>{storage?.folder && <span className="backup-folder">{storage.can_open ? <a href="#" title={storage.folder} onClick={e => { e.preventDefault(); void openFolder() }}>📁 Open backup folder</a> : <span title="Open this folder on the host Mac">📁 Saved on the host Mac in <code>{storage.folder}</code></span>}</span>}</p></div><button className="button secondary" disabled={!healthy || busy} onClick={() => setRefresh(v => v + 1)}>↻ Refresh</button></div><div className="table-wrap"><table><thead><tr><th>Created</th><th>Archive</th><th>Size</th><th>Integrity</th><th>Additional copy</th><th /></tr></thead><tbody>{items.map(item => <tr key={item.id}><td>{formatTime(item.created_at)}{item.keep_forever ? <small><span className="badge good" title="First backup of a Monday (Sunday night, 2 a.m.): never deleted">Weekly · kept forever</span></small> : item.expires_at ? <small>Deleted after {formatDate(item.expires_at)}</small> : null}</td><td><b>{item.filename}</b><small className="id-hint" title={item.sha256}>SHA-256 {item.sha256?.slice(0, 16)}…</small></td><td>{bytes(item.size_bytes ?? item.size ?? 0)}</td><td><span className={`badge ${item.verified ? 'good' : 'warning'}`}>{item.verified ? 'Checked at creation' : 'Check needed'}</span></td><td>{{not_configured:'Not configured',copied:'Copied to destination',copied_to_destination:'Copied to destination',copy_failed:'Copy failed'}[item.cloud_copy_state || 'not_configured'] || item.cloud_copy_state?.replaceAll('_',' ')}</td><td><a className="button secondary" href={`/api/backups/${item.id}/download`}>Download ↓</a></td></tr>)}</tbody></table></div>{!items.length && <div className="empty-inline"><h3>No backup archives yet</h3><p>Create the first snapshot to enable a portable recovery copy.</p></div>}</section>{staged && <Modal title="Restore validated backup?" onClose={() => !busy && setStaged(null)}><div className="modal-body"><div className="notice success">Archive validation: {staged.status}</div>{staged.snapshot_at || staged.source_created_at || staged.created_at ? <p>Backup created {formatTime(staged.snapshot_at || staged.source_created_at || staged.created_at)}.</p> : null}<div className="source-grid">{Object.entries(staged.counts || {}).filter(([name]) => ['projects','stacks','specimens','audit_events'].includes(name)).map(([name, count]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><b>{count}</b></div>)}</div><details className="backup-detail-counts"><summary>All database table counts</summary><div className="source-grid">{Object.entries(staged.counts || {}).map(([name,count]) => <div key={name}><span>{name.replaceAll('_',' ')}</span><b>{count}</b></div>)}</div></details><div className="notice warning">Restore replaces the current database records with this validated dataset. The existing database is retained separately.</div><label>Type RESTORE to replace the active dataset<input aria-label="Restore confirmation" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="off" /></label><ErrorNotice message={error} /></div><div className="modal-actions"><button className="button secondary" disabled={busy} onClick={() => setStaged(null)}>Cancel</button><button className="button danger" disabled={!healthy || busy || confirmation !== 'RESTORE' || staged.status !== 'validated'} onClick={() => void restore()}>{busy ? 'Restoring…' : 'Restore backup'}</button></div></Modal>}</>
}
