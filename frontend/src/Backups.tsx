import { useCallback, useEffect, useRef, useState } from 'react'
import { api, bytes, formatTime, post } from './api'
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
        setError(''); setMessage('Backup restored. Reload the other pages.')
        setRefresh(value => value + 1)
      } else if (result.status === 'validated') {
        localStorage.removeItem(RESTORE_KEY); setPendingRestore(null); setBusy(false); setStaged(result); setConfirmation('')
        setError('The restore didn’t happen. Nothing was changed.')
      }
    } catch {
      if (alive.current) setError('Still checking whether the restore finished.')
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

  async function create() { setBusy(true); setError(''); try { await post('/backups', {}); setRefresh(v => v + 1); setMessage('Backup created.') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }
  async function save() { setBusy(true); setError(''); try { const value = await api<Settings>('/backups/settings', { method: 'PUT', body: JSON.stringify(settings) }); if ('enabled' in value) setSettings(value); setMessage('Settings saved.') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }
  async function stage(file: File) { setBusy(true); setError(''); setStaged(null); try { const form = new FormData(); form.append('file', file); const value = await api<Import>('/backups/import', { method: 'POST', body: form }, 120000); setStaged(value); setConfirmation('') } catch (err) { setError((err as Error).message) } finally { setBusy(false) } }

  async function restore() {
    if (!staged || confirmation !== 'RESTORE' || busy || pendingRestore) return
    const id = staged.id
    try { localStorage.setItem(RESTORE_KEY, id) }
    catch { setError('Turn on browser storage before restoring.'); return }
    setBusy(true); setError(''); setPendingRestore(id); restoreLock.current = true
    try {
      await api('/backups/imports/' + id + '/restore', { method: 'POST', body: JSON.stringify({ confirmation: 'RESTORE' }) }, 120000)
      localStorage.removeItem(RESTORE_KEY)
      if (alive.current) {
        setPendingRestore(null); setStaged(null); setBusy(false)
        setMessage('Backup restored. Reload the other pages.')
        setRefresh(value => value + 1)
      }
    } catch {
      if (alive.current) setError('No answer yet. Checking whether the restore finished…')
    } finally { restoreLock.current = false }
  }

  async function openFolder() { setError(''); try { await post('/backups/open-folder', {}); setMessage('Opened in Finder.') } catch (err) { setError((err as Error).message) } }

  const formatDate = (value: string) => new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
  const totalBytes = storage?.total_bytes ?? items.reduce((sum, item) => sum + (item.size_bytes ?? item.size ?? 0), 0)
  return <><PageHeader title="Backups"><button className="button primary" disabled={!healthy || busy} onClick={() => void create()}>{busy ? 'Working…' : 'Back up now'}</button></PageHeader><ErrorNotice message={error} />{message && <div className="notice success" role="status">{message}</div>}<section className="backup-health" aria-label="Backup health">
      <div><span>Last backup</span><b>{formatTime(scheduler.last_success_at || items[0]?.created_at)}</b></div>
      <div><span>Next backup</span><b>{scheduler.enabled === false ? 'Paused' : scheduler.overdue ? 'Overdue' : formatTime(scheduler.next_run_at)}</b></div>
      <div><span>Extra copy</span><b>{!settings.destination ? 'Off' : items[0]?.cloud_copy_state === 'copy_failed' ? 'Last copy failed' : 'On'}</b></div>
    </section>
    {scheduler.last_error && <div className="notice error" role="alert">The last scheduled backup failed.</div>}
    {pendingRestore && <div className="notice warning" role="status"><b>Checking whether the restore finished…</b><button className="button secondary" disabled={!healthy} onClick={() => void checkRestore()}>Check restore result</button></div>}
    <div className="prep-bottom"><section className="card"><div className="card-heading"><div><h2>Daily backups</h2></div></div><div className="padded"><label className="check-label"><input type="checkbox" checked={settings.enabled} onChange={e => setSettings(s => ({ ...s, enabled: e.target.checked }))} />Back up every day</label><div className="form-grid"><label>Hour (0–23)<input aria-label="Daily backup hour" type="number" min="0" max="23" value={settings.daily_hour} onChange={e => setSettings(s => ({ ...s, daily_hour: Number(e.target.value) }))} /></label><label>Keep for (days)<input aria-label="Backup retention" type="number" min="2" max="3650" value={settings.retention_days} onChange={e => setSettings(s => ({ ...s, retention_days: Number(e.target.value) }))} /></label></div><label>Copy to folder<input aria-label="Backup destination" value={settings.destination || ''} onChange={e => setSettings(s => ({ ...s, destination: e.target.value || null }))} /></label></div><div className="card-actions"><span className="grow" /><button className="button secondary" disabled={!healthy || busy} onClick={() => void save()}>Save settings</button></div></section><section className="card"><div className="card-heading"><div><h2>Restore</h2></div></div><div className="padded"><label className={`button secondary upload ${busy || !healthy ? 'disabled' : ''}`}>Choose file<input aria-label="Import backup archive" type="file" accept=".zip,application/zip" disabled={!healthy || busy} onChange={e => { if (e.target.files?.[0]) void stage(e.target.files[0]); e.target.value = '' }} /></label></div></section></div><section className="card"><div className="card-heading"><div><h2>Saved backups <span className="count-pill">{items.length}</span></h2><p className="backup-storage" aria-label="Backup storage"><span>Total <b data-testid="backup-total-size">{bytes(totalBytes)}</b></span>{storage?.folder && <span className="backup-folder">{storage.can_open ? <a href="#" title={storage.folder} onClick={e => { e.preventDefault(); void openFolder() }}>Open folder</a> : <span><code>{storage.folder}</code></span>}</span>}</p></div><button className="button secondary" disabled={!healthy || busy} onClick={() => setRefresh(v => v + 1)}>Refresh</button></div><div className="table-wrap"><table><thead><tr><th>Created</th><th>Archive</th><th>Size</th><th>Checked</th><th>Extra copy</th><th /></tr></thead><tbody>{items.map(item => <tr key={item.id}><td>{formatTime(item.created_at)}{item.keep_forever ? <small><span className="badge good">Kept</span></small> : item.expires_at ? <small>Expires {formatDate(item.expires_at)}</small> : null}</td><td><b title={'SHA-256 ' + item.sha256}>{item.filename}</b></td><td>{bytes(item.size_bytes ?? item.size ?? 0)}</td><td><span className={`badge ${item.verified ? 'good' : 'warning'}`}>{item.verified ? 'Verified' : 'Not verified'}</span></td><td>{{not_configured:'—',copied:'Copied',copied_to_destination:'Copied',copy_failed:'Failed'}[item.cloud_copy_state || 'not_configured'] || item.cloud_copy_state?.replaceAll('_',' ')}</td><td><a className="button secondary" href={`/api/backups/${item.id}/download`}>Download</a></td></tr>)}</tbody></table></div>{!items.length && <div className="empty-inline"><p>No backups yet.</p></div>}</section>{staged && <Modal title="Restore backup?" onClose={() => !busy && setStaged(null)}><div className="modal-body"><div className="notice success">{staged.status === 'validated' ? 'Backup file is valid.' : 'Check result: ' + staged.status}</div>{staged.snapshot_at || staged.source_created_at || staged.created_at ? <p>Backup created {formatTime(staged.snapshot_at || staged.source_created_at || staged.created_at)}.</p> : null}<div className="source-grid">{Object.entries(staged.counts || {}).filter(([name]) => ['projects','stacks','specimens','audit_events'].includes(name)).map(([name, count]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><b>{count}</b></div>)}</div><details className="backup-detail-counts"><summary>All tables</summary><div className="source-grid">{Object.entries(staged.counts || {}).map(([name,count]) => <div key={name}><span>{name.replaceAll('_',' ')}</span><b>{count}</b></div>)}</div></details><div className="notice warning">This replaces all current records.</div><label>Type RESTORE to confirm<input aria-label="Restore confirmation" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="off" /></label><ErrorNotice message={error} /></div><div className="modal-actions"><button className="button secondary" disabled={busy} onClick={() => setStaged(null)}>Cancel</button><button className="button danger" disabled={!healthy || busy || confirmation !== 'RESTORE' || staged.status !== 'validated'} onClick={() => void restore()}>{busy ? 'Restoring…' : 'Restore backup'}</button></div></Modal>}</>
}
