import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Config, Draft, PrepRow, Stack } from './types'
import { api, post } from './api'
import { ErrorNotice, Modal, PageHeader } from './App'
import LabelDialog from './LabelDialog'

const REVIEW_KEY = 'fabric-tracker.prep-review.v3'
const OLD_KEY = 'fabric-tracker.prep-draft.v2'
const fields = ['project_number', 'material_ref', 'test_code', 'preconditioning_code', 'specimen_count'] as const
const labels = {
  project_number: 'Project number', material_ref: 'Material / sample', test_code: 'Test',
  preconditioning_code: 'Treatment', specimen_count: 'Pieces to condition',
}
const emptyRow = (): PrepRow => ({
  project_number: '', material_ref: '', test_code: '', preconditioning_code: 'AR', specimen_count: 1,
})
type Review = { draft: Draft | null; rows: PrepRow[] }
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    const object = value as Record<string, unknown>
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + canonical(object[key])).join(',') + '}'
  }
  return JSON.stringify(value) ?? 'null'
}
function restoredReview(): Review {
  try {
    const current = JSON.parse(localStorage.getItem(REVIEW_KEY) || 'null') as Review | null
    if (current && Array.isArray(current.rows)) return current
    const old = JSON.parse(localStorage.getItem(OLD_KEY) || 'null') as Draft | null
    if (old) return { draft: old, rows: old.rows }
  } catch { /* An invalid local cache must not prevent a fresh import. */ }
  return { draft: null, rows: [emptyRow()] }
}
function missingFields(row: PrepRow) {
  return fields.filter(field => field === 'specimen_count'
    ? !Number.isInteger(row.specimen_count) || row.specimen_count < 1 || row.specimen_count > 500
    : !row[field]?.trim())
}
function describe(value: unknown): string {
  if (value == null) return '—'
  if (Array.isArray(value)) return value.map(describe).join(', ')
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>)
    .map(([key, content]) => key.replaceAll('_', ' ') + ': ' + describe(content)).join(' · ')
  return String(value)
}
function SourceDetails({ row }: { row: PrepRow }) {
  const metadata = row.metadata || {}
  const source = metadata.source as Record<string, unknown> | undefined
  const cells = source?.cells as Record<string, Record<string, unknown>> | undefined
  const warnings = metadata.warnings as string[] | undefined
  return <>
    <div className="source-grid">
      {source && Object.entries(source).filter(([key]) => key !== 'cells').map(([key, value]) =>
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{describe(value)}</b></div>)}
      {Object.entries(metadata).filter(([key]) => key !== 'source' && key !== 'warnings').map(([key, value]) =>
        <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{describe(value)}</b></div>)}
    </div>
    {cells && <div className="table-wrap source-cells"><table>
      <thead><tr><th>Source field</th><th>Excel cell</th><th>Workbook value</th></tr></thead>
      <tbody>{Object.entries(cells).map(([name, value]) => <tr key={name}>
        <td>{name.replaceAll('_', ' ')}</td><td><b>{describe(value.cell)}</b></td>
        <td>{describe(value.value ?? value.raw ?? value)}</td>
      </tr>)}</tbody>
    </table></div>}
    {warnings?.map((warning, index) => <div className="notice warning" key={index}>{warning}</div>)}
    {!Object.keys(metadata).length && <p className="subtle">Manually entered row. No workbook source cells.</p>}
  </>
}

export default function Prep({ config, healthy }: { config: Config; healthy: boolean }) {
  const initial = useRef(restoredReview()).current
  const [rows, setRows] = useState(initial.rows)
  const [draft, setDraft] = useState<Draft | null>(initial.draft)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [sourceRow, setSourceRow] = useState<number | null>(null)
  const [labelStacks, setLabelStacks] = useState<Stack[] | null>(null)
  const [selectedSheets, setSelectedSheets] = useState<string[]>(() =>
    initial.draft?.sheets?.filter(sheet => (sheet as { selected?: boolean }).selected).map(sheet => sheet.name) || [])
  const uploadFile = useRef<File | null>(null)
  const alive = useRef(true)
  const operation = useRef(0)
  const workLock = useRef(false)
  const current = useRef<Review>({ rows, draft })
  current.current = { rows, draft }
  const registered = draft?.committed ? draft.stack_ids : []
  const unresolved = rows.map((row, index) => ({ row, index, missing: missingFields(row) })).filter(row => row.missing.length)
  const valid = rows.length > 0 && unresolved.length === 0
  const total = rows.reduce((sum, row) => sum + (Number.isFinite(row.specimen_count) ? row.specimen_count : 0), 0)

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; operation.current++ }
  }, [])
  // Save on every edit before a navigation paint. No extra Save step is required.
  // The uncommitted server import is only replaced on registration, avoiding
  // background writes over another person's review of the same source file.
  useLayoutEffect(() => {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify({ rows, draft })) }
    catch { setError('This browser cannot preserve your review. Keep this page open until registration finishes.') }
  }, [rows, draft])
  useEffect(() => {
    const saved = current.current
    if (!healthy || !saved.draft || workLock.current) return
    const id = saved.draft.id
    let active = true
    void api<Draft>('/prep-drafts/' + id).then(value => {
      if (!active || !alive.current || current.current.draft?.id !== id || workLock.current) return
      if (value.committed) {
        setDraft(value)
        setRows(value.rows)
      } else {
        const local = current.current
        const hasEdits = canonical(local.rows) !== canonical(local.draft?.rows)
        if (hasEdits && canonical(value.rows) !== canonical(local.draft?.rows)
          && canonical(value.rows) !== canonical(local.rows)) {
          setError('Another browser changed this preparation. Your edits are kept here; review the latest workbook before registering.')
        } else {
          setDraft(value)
          if (!hasEdits) setRows(value.rows)
        }
      }
    }).catch(err => {
      if (active && alive.current) setError('The saved review is still available on this computer. Could not check its registration: ' + (err as Error).message)
    })
    return () => { active = false }
  }, [healthy])

  async function run<T>(work: () => Promise<T>, success: (value: T) => void) {
    if (workLock.current || !healthy) return
    const id = ++operation.current
    workLock.current = true
    setBusy(true)
    setError('')
    try {
      const value = await work()
      if (alive.current && id === operation.current) success(value)
    } catch (err) {
      if (alive.current && id === operation.current) setError((err as Error).message)
    } finally {
      workLock.current = false
      if (alive.current && id === operation.current) setBusy(false)
    }
  }
  function update(index: number, field: typeof fields[number], value: string) {
    setRows(previous => previous.map((row, at) => at === index
      ? { ...row, [field]: field === 'specimen_count' ? Number(value) : value } : row))
  }
  function jumpToMissing() {
    const first = unresolved[0]
    if (!first) return
    setOnlyMissing(true)
    requestAnimationFrame(() => {
      const input = document.getElementById('prep-' + first.index + '-' + first.missing[0])
      input?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
      input?.focus({ preventScroll: true })
    })
  }
  async function upload(file: File, sheets?: string[], newBatch = false) {
    await run(async () => {
      const data = new FormData()
      data.append('file', file)
      if (sheets?.length) data.append('sheet_names', JSON.stringify(sheets))
      if (newBatch) data.append('new_batch', 'true')
      return api<Draft>(file.name.toLowerCase().endsWith('.csv') ? '/prep-drafts/csv' : '/prep-drafts/workbook',
        { method: 'POST', body: data }, 30000)
    }, value => {
      uploadFile.current = file
      setDraft(value)
      setRows(value.rows.length ? value.rows : [emptyRow()])
      setSelectedSheets(value.sheets?.filter(sheet => (sheet as { selected?: boolean }).selected).map(sheet => sheet.name) || [])
      setOnlyMissing(false)
      setLabelStacks(null)
      setMessage(value.committed ? 'This workbook is already registered. Its original identities and labels have been recovered.' :
        'Workbook imported. Confirm the physical piece counts and resolve highlighted fields.')
    })
  }
  async function openLabels(ids = registered) {
    await run(() => Promise.all(ids.map(id => api<Stack>('/stacks/' + id))), value => setLabelStacks(value))
  }
  async function register() {
    const reviewed = current.current
    await run(async () => {
      const recovered = reviewed.draft ? await api<Draft>('/prep-drafts/' + reviewed.draft.id) : null
      let saved: Draft
      if (recovered?.committed) saved = recovered
      else {
        // Refuse an unseen edit from another browser rather than replacing it
        // with this browser's older source review.
        if (recovered && reviewed.draft && canonical(recovered.rows) !== canonical(reviewed.draft.rows)
          && canonical(recovered.rows) !== canonical(reviewed.rows)) {
          throw new Error('This imported preparation changed in another browser. Import the workbook again to review the latest values before registering.')
        }
        const value = recovered
          ? await api<Draft>('/prep-drafts/' + recovered.id, { method: 'PUT', body: JSON.stringify({ rows: reviewed.rows, expected_rows: recovered.rows }) })
          : await post<Draft>('/prep-drafts', { rows: reviewed.rows })
        // Preserve the authoritative import ID before the commit can lose its response.
        localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft: value, rows: reviewed.rows }))
        if (alive.current) setDraft(value)
        const result = await post<{ stack_ids: string[] }>('/prep-drafts/' + value.id + '/commit', { expected_rows: value.rows })
        saved = { ...value, committed: true, stack_ids: result.stack_ids }
        localStorage.setItem(REVIEW_KEY, JSON.stringify({ draft: saved, rows: saved.rows }))
      }
      const stacks = await Promise.all(saved.stack_ids.map(id => api<Stack>('/stacks/' + id)))
      return { saved, stacks }
    }, ({ saved, stacks }) => {
      setDraft(saved)
      setRows(saved.rows)
      setLabelStacks(stacks)
      setMessage('Registered ' + saved.stack_ids.length + ' stacks. Reprinting will keep these permanent identities.')
    })
  }
  function startNew() {
    operation.current++
    setDraft(null)
    setRows([emptyRow()])
    uploadFile.current = null
    setSelectedSheets([])
    setOnlyMissing(false)
    setLabelStacks(null)
    setMessage('')
    setError('')
  }
  const shown = rows.map((row, index) => ({ row, index })).filter(({ row }) => !onlyMissing || missingFields(row).length)

  return <>
    <PageHeader eyebrow="PREP & LABELS" title="Prepare the next batch."
      description="Import a prep workbook, check its physical stacks, then preview and print the specimen labels.">
      <button className="button secondary" disabled={busy} onClick={startNew}>＋ New preparation</button>
    </PageHeader>
    <ErrorNotice message={error} />
    {message && <div className="notice success" role="status">{message}</div>}
    <section className="card">
      <div className="card-heading">
        <div>
          <h2>Prep review {draft?.source && <span className="badge outline">{draft.source.filename}</span>}</h2>
          <p>One row per physical stack. Count all pieces sent to conditioning, including extras.</p>
        </div>
        <label className={'button primary upload ' + (busy || !healthy ? 'disabled' : '')}>↑ Import Excel / CSV
          <input aria-label="Import prep workbook" type="file" accept=".xlsx,.xlsm,.csv"
            disabled={busy || !healthy} onChange={e => {
              if (e.target.files?.[0]) void upload(e.target.files[0])
              e.target.value = ''
            }} />
        </label>
      </div>
      {draft?.warnings?.length ? <details className="import-warnings">
        <summary>Workbook review notes · {draft.warnings.length}</summary>
        {draft.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
      </details> : null}
      {draft?.sheets?.length ? <details className="sheet-inventory">
        <summary>{draft.sheets.length} workbook sheets inspected</summary>
        <div className="sheet-list">{draft.sheets.map(sheet => <label className="check-label" key={sheet.name}>
          <input type="checkbox" checked={selectedSheets.includes(sheet.name)}
            disabled={!uploadFile.current || busy || registered.length > 0}
            onChange={e => setSelectedSheets(previous => e.target.checked ? [...previous, sheet.name] : previous.filter(name => name !== sheet.name))} />
          <span><b>{sheet.name}</b> · {sheet.kind.replaceAll('_', ' ')} · {sheet.candidate_count} candidates</span>
        </label>)}</div>
        {uploadFile.current && !registered.length && <button className="button secondary"
          disabled={busy || !selectedSheets.length}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets)}>Review selected sheets</button>}
        <p className="input-help">{uploadFile.current ? 'Only selected sheets are imported.' : 'Choose the workbook again to change the sheet selection.'}</p>
      </details> : null}
      {!registered.length && <div className="prep-exceptions">
        <span className={'badge ' + (unresolved.length ? 'warning' : 'good')}>
          {unresolved.length ? unresolved.length + ' rows need values' : 'All required values present'}
        </span>
        <label className="check-label"><input type="checkbox" checked={onlyMissing}
          onChange={e => setOnlyMissing(e.target.checked)} />Only rows needing attention</label>
        {unresolved.length > 0 && <button className="button text" onClick={jumpToMissing}>Go to first missing value →</button>}
        <small>Review changes are saved automatically on this computer.</small>
      </div>}
      <datalist id="treatments">{config.treatments.map(treatment => <option key={treatment} value={treatment} />)}</datalist>
      <div className="table-wrap"><table className="prep-table">
        <thead><tr><th>#</th>{fields.map(field => <th key={field}>{labels[field]}</th>)}<th>Source</th><th /></tr></thead>
        <tbody>{shown.map(({ row, index }) => <tr key={index}>
          <td className="row-number">{String(index + 1).padStart(2, '0')}</td>
          {fields.map(field => <td key={field}>
            <input id={'prep-' + index + '-' + field} aria-label={field + ' row ' + (index + 1)}
              className={missingFields(row).includes(field) ? 'unresolved' : ''}
              list={field === 'preconditioning_code' ? 'treatments' : undefined}
              type={field === 'specimen_count' ? 'number' : 'text'} min={1} max={field === 'specimen_count' ? 500 : undefined}
              value={row[field] ?? ''} placeholder="Required" disabled={busy || registered.length > 0}
              onChange={e => update(index, field, e.target.value)} />
          </td>)}
          <td><button className="button text" aria-label={'Source row ' + (index + 1)} onClick={() => setSourceRow(index)}>Review ↗</button></td>
          <td><button className="icon-button" aria-label={'Remove row ' + (index + 1)}
            disabled={busy || registered.length > 0 || rows.length === 1}
            onClick={() => setRows(previous => previous.filter((_, at) => at !== index))}>×</button></td>
        </tr>)}</tbody>
      </table></div>
      {!shown.length && <div className="empty-inline"><h3>No unresolved rows</h3><p>Turn off the attention filter to review every stack.</p></div>}
      <div className="card-actions">
        <button className="button text" disabled={busy || registered.length > 0}
          onClick={() => setRows(previous => [...previous, emptyRow()])}>＋ Add stack</button>
        <span className="summary-count">{rows.length} stacks · {total} physical specimens</span>
        <button className="button primary" disabled={!valid || !healthy || busy}
          onClick={() => registered.length ? void openLabels() : void register()}>
          {busy ? 'Working…' : registered.length ? 'Preview / reprint labels →' : 'Register & preview labels →'}
        </button>
      </div>
    </section>
    {registered.length > 0 && <section className="card">
      <div className="card-heading">
        <div><h2>Registered specimens</h2><p>Labels are permanent. Choose a stack or individual numbers to reprint without creating new records.</p></div>
        {uploadFile.current && <button className="button secondary" disabled={busy || !healthy}
          onClick={() => uploadFile.current && void upload(uploadFile.current, selectedSheets, true)}>New physical batch from this workbook</button>}
      </div>
      {registered.map((id, index) => <div className="export-row" key={id}>
        <div><b>{rows[index]?.project_number} / {rows[index]?.material_ref}</b>
          <small>{rows[index]?.test_code} · {rows[index]?.preconditioning_code} · {rows[index]?.specimen_count} specimens</small></div>
        <button className="button secondary" disabled={!healthy || busy} onClick={() => void openLabels([id])}>Preview / reprint</button>
        <a className="button text" href={'#records?stack=' + id}>Open records →</a>
      </div>)}
    </section>}
    {sourceRow !== null && <Modal title={'Workbook source · Stack ' + (sourceRow + 1)} wide onClose={() => setSourceRow(null)}>
      <div className="modal-body"><SourceDetails row={rows[sourceRow]} /></div>
      <div className="modal-actions"><button className="button secondary" onClick={() => setSourceRow(null)}>Back to review</button></div>
    </Modal>}
    {labelStacks && <LabelDialog stacks={labelStacks} healthy={healthy} onClose={() => setLabelStacks(null)} />}
  </>
}
