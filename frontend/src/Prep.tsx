import { useEffect, useState } from 'react'
import type { Config, Draft, PrepRow } from './types'
import { api, post } from './api'
import { ErrorNotice, PageHeader } from './App'

const emptyRow = (): PrepRow => ({ project_number: '', material_ref: '', test_code: '', preconditioning_code: 'AR', specimen_count: 20 })
const DRAFT_KEY = 'fabric-tracker.prep-draft.v1'
function savedDraft(): Draft | null { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null') as Draft | null } catch { return null } }
function saveDraft(value: Draft) { localStorage.setItem(DRAFT_KEY, JSON.stringify(value)); return value }
export default function Prep({ config, healthy }: { config: Config; healthy: boolean }) {
  const [rows, setRows] = useState<PrepRow[]>(() => savedDraft()?.rows || [emptyRow()])
  const [draft, setDraft] = useState<Draft | null>(savedDraft)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [registered, setRegistered] = useState<string[]>(() => savedDraft()?.stack_ids || [])
  const [width, setWidth] = useState(54)
  const [height, setHeight] = useState(25)
  const [header, setHeader] = useState(true)
  const labelsValid = width >= 40 && width <= 150 && height >= 25 && height <= 150
  useEffect(() => {
    const current = savedDraft(); if (!healthy || !current) return
    let alive = true; setBusy(true)
    void api<Draft>(`/prep-drafts/${current.id}`).then(value => { if (alive) { setDraft(saveDraft(value)); setRows(value.rows); setRegistered(value.stack_ids || []); setError('') } }).catch(err => { if (alive) setError(`Could not recover this prep draft: ${(err as Error).message}. Retry registration to reconcile its status.`) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [healthy])
  const valid = rows.length > 0 && rows.every(r => r.project_number.trim() && r.material_ref.trim() && r.test_code.trim() && r.preconditioning_code.trim() && Number.isInteger(r.specimen_count) && r.specimen_count > 0 && r.specimen_count <= 500)
  const update = (index: number, field: keyof PrepRow, value: string) => { setRows(current => current.map((r, i) => i === index ? { ...r, [field]: field === 'specimen_count' ? Number(value) : value } : r)); setRegistered([]) }
  async function upload(file: File) {
    setBusy(true); setError('')
    try { const data = new FormData(); data.append('file', file); const value = await api<Draft>('/prep-drafts/csv', { method: 'POST', body: data }); setDraft(saveDraft(value)); setRows(value.rows); setRegistered([]) }
    catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function register() {
    setBusy(true); setError('')
    try {
      const recovered = draft ? await api<Draft>(`/prep-drafts/${draft.id}`) : null
      if (recovered?.committed) { setDraft(saveDraft(recovered)); setRows(recovered.rows); setRegistered(recovered.stack_ids); return }
      const value = recovered ? await api<Draft>(`/prep-drafts/${recovered.id}`, { method: 'PUT', body: JSON.stringify({ rows }) }) : await post<Draft>('/prep-drafts', { rows })
      setDraft(saveDraft(value))
      const result = await post<{ stack_ids: string[] }>(`/prep-drafts/${value.id}/commit`, {})
      setDraft(saveDraft({ ...value, committed: true, stack_ids: result.stack_ids })); setRegistered(result.stack_ids)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const labelUrl = (id: string, format: string) => `/api/stacks/${id}/labels.${format}?width_mm=${width}&height_mm=${height}&include_header=${header}`
  return <>
    <PageHeader eyebrow="PREPARATION" title="A label. A specimen. A record." description="Review your stacks, register each specimen, then print its permanent identity." />
    <div className="step-strip"><div className="done"><b>01</b> Enter & review</div><div className={registered.length ? 'done' : ''}><b>02</b> Register specimens</div><div><b>03</b> Print labels</div></div>
    <ErrorNotice message={error} />
    <section className="card"><div className="card-heading"><div><h2>Prep review</h2><p>Each row creates one fixed stack. Project numbers preserve leading zeroes.</p></div><label className={`button secondary upload ${busy || !healthy ? 'disabled' : ''}`}>↑ Import CSV<input aria-label="Import CSV" type="file" accept=".csv,text/csv" disabled={busy || !healthy} onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]); e.target.value = '' }} /></label></div>
      <datalist id="treatments">{config.treatments.map(t => <option key={t} value={t} />)}</datalist>
      <div className="table-wrap"><table className="prep-table"><thead><tr><th>#</th><th>Project number</th><th>Material reference</th><th>Test code</th><th>Preconditioning</th><th>Specimens</th><th /></tr></thead><tbody>{rows.map((row, i) => <tr key={i}><td className="row-number">{String(i + 1).padStart(2, '0')}</td>{(['project_number', 'material_ref', 'test_code', 'preconditioning_code', 'specimen_count'] as const).map(field => <td key={field}><input aria-label={`${field} row ${i + 1}`} list={field === 'preconditioning_code' ? 'treatments' : undefined} type={field === 'specimen_count' ? 'number' : 'text'} min={field === 'specimen_count' ? 1 : undefined} max={field === 'specimen_count' ? 500 : undefined} value={Number.isNaN(row[field]) ? '' : row[field]} onChange={e => update(i, field, e.target.value)} disabled={busy || registered.length > 0} /></td>)}<td><button className="icon-button" aria-label={`Remove row ${i + 1}`} disabled={busy || registered.length > 0 || rows.length === 1} onClick={() => setRows(current => current.filter((_, j) => j !== i))}>×</button></td></tr>)}</tbody></table></div>
      {!valid && <p className="input-help">Fill every field. Specimen counts must be whole numbers between 1 and 500.</p>}
      <div className="card-actions"><button className="button text" disabled={busy || registered.length > 0} onClick={() => setRows(current => [...current, emptyRow()])}>＋ Add stack</button><span className="summary-count">{rows.length} {rows.length === 1 ? 'stack' : 'stacks'} <span>·</span> {rows.reduce((total, r) => total + (Number.isFinite(r.specimen_count) ? r.specimen_count : 0), 0)} specimens</span><button className="button primary" disabled={!valid || !healthy || busy || registered.length > 0} onClick={() => void register()}>{busy ? 'Registering…' : registered.length ? '✓ Registered' : 'Register specimens →'}</button></div>
    </section>
    <div className="prep-bottom"><section className="card"><div className="card-heading"><div><h2>Print settings</h2><p>Reprint anytime. The specimen IDs stay the same.</p></div><span className="mini-icon">▤</span></div><div className="form-grid"><label>Label width (mm)<input type="number" min="40" max="150" value={width} onChange={e => setWidth(Number(e.target.value))} /></label><label>Label height (mm)<input type="number" min="25" max="150" value={height} onChange={e => setHeight(Number(e.target.value))} /></label></div><label className="check-label"><input type="checkbox" checked={header} onChange={e => setHeader(e.target.checked)} />Include a stack header label</label><div className="label-preview"><span className="mock-qr" aria-hidden="true">▦</span><div><b>{rows[0]?.project_number || 'PROJECT'} · {rows[0]?.material_ref || 'MATERIAL'}</b><span>{rows[0]?.test_code || 'TEST'} / {rows[0]?.preconditioning_code || 'AR'}</span><small>SPECIMEN 01 OF {rows[0]?.specimen_count || '20'}</small></div></div><p className="input-help">{!labelsValid && 'Use width 40–150 mm and height 25–150 mm. '}Layout preview only. Download the PDF for actual scannable labels.</p></section>
      <section className="card"><div className="card-heading"><div><h2>{registered.length ? 'Your labels are ready' : 'Ready when you are'}</h2><p>{registered.length ? 'All specimens have permanent identities in the database.' : 'Register your reviewed stacks to enable PDF and printer CSV downloads.'}</p></div></div>{registered.length ? <><div className="notice success" role="status">✓ Registered {registered.length} {registered.length === 1 ? 'stack' : 'stacks'} successfully.</div>{registered.map((id, index) => <div className="export-row" key={id}><div><b>Stack {String(index + 1).padStart(2, '0')}</b><small>{rows[index]?.material_ref} · {rows[index]?.specimen_count} specimens</small></div><a className={`button secondary ${!labelsValid ? 'disabled-link' : ''}`} aria-disabled={!labelsValid} href={labelsValid ? labelUrl(id, 'pdf') : undefined} target="_blank" rel="noreferrer">PDF ↗</a><a className={`button secondary ${!labelsValid ? 'disabled-link' : ''}`} aria-disabled={!labelsValid} href={labelsValid ? labelUrl(id, 'csv') : undefined}>CSV ↓</a></div>)}<button className="button text" onClick={() => { localStorage.removeItem(DRAFT_KEY); setRows([emptyRow()]); setDraft(null); setRegistered([]) }}>Prepare another batch →</button></> : <div className="empty-inline"><span>▧</span><p>Your printable labels will appear here.</p></div>}</section></div>
  </>
}
