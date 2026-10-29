import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiBlob, ApiError, post, requestId } from './api'
import type { PrinterInfo, PrintJob, Stack } from './types'
import { ErrorNotice, Modal } from './App'

const PRINT_KEY = 'fabric-tracker.pending-print.v2'
const OLD_PRINT_KEY = 'fabric-tracker.pending-print.v1'
// Brother QL-810W, 62 mm continuous roll at a constant 25.4 mm per label (2.44 x 1 in).
const LABEL_WIDTH_MM = 62
const LABEL_HEIGHT_MM = 25.4
type LabelSelection = {
  stack_ids: number[]
  selected_specimen_ids: number[]
  width_mm: number
  height_mm: number
}
type PrintIntent = LabelSelection & {
  request_id: string
  printer_queue: string
  copies: number
}
function readPending(): PrintIntent | null {
  try {
    const value = localStorage.getItem(PRINT_KEY)
    if (value) return JSON.parse(value) as PrintIntent
    const legacy = localStorage.getItem(OLD_PRINT_KEY)
    return legacy ? { request_id: legacy } as PrintIntent : null
  } catch { return null }
}
function forgetPending(identifier?: string) {
  if (identifier && readPending()?.request_id !== identifier) return
  localStorage.removeItem(PRINT_KEY)
  localStorage.removeItem(OLD_PRINT_KEY)
}
export default function LabelDialog({
  stacks, initialSelectedIds, healthy, onClose,
}: {
  stacks: Stack[]
  initialSelectedIds?: number[]
  healthy: boolean
  onClose: () => void
}) {
  const [selected, setSelected] = useState<number[]>(() => initialSelectedIds || stacks.flatMap(stack => stack.specimens.map(s => s.id)))
  const [expanded, setExpanded] = useState<number | null>(() => initialSelectedIds ? stacks[0]?.id ?? null : null)
  const [copies, setCopies] = useState(1)
  const [queue, setQueue] = useState('')
  const [printers, setPrinters] = useState<PrinterInfo | null>(null)
  const [previewURL, setPreviewURL] = useState('')
  const [previewFingerprint, setPreviewFingerprint] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<PrintJob | null>(null)
  const [pending, setPending] = useState<PrintIntent | null>(readPending)
  const alive = useRef(true)
  const serial = useRef(0)
  const printLock = useRef(false)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; serial.current++ }
  }, [])

  const selection = useMemo<LabelSelection>(() => ({
    stack_ids: stacks.filter(stack => stack.specimens.some(s => selected.includes(s.id))).map(stack => stack.id).sort((a, b) => a - b),
    selected_specimen_ids: [...selected].sort((a, b) => a - b),
    width_mm: LABEL_WIDTH_MM,
    height_mm: LABEL_HEIGHT_MM,
  }), [stacks, selected])
  const fingerprint = JSON.stringify(selection)
  const valid = selected.length > 0
  useEffect(() => {
    if (!healthy) return
    let current = true
    void api<PrinterInfo>('/printers').then(value => {
      if (current) {
        setPrinters(value)
        // Prefer the label printer over a default office printer.
        setQueue(previous => previous || value.configured_queue || value.printers.find(p => /QL/i.test(p.name))?.name ||
          value.printers.find(p => p.is_default)?.name || '')
      }
    }).catch(() => { if (current) setPrinters(null) })
    return () => { current = false }
  }, [healthy])

  useEffect(() => {
    const requestSerial = ++serial.current
    const controller = new AbortController()
    let objectURL = ''
    setPreviewURL('')
    setPreviewFingerprint('')
    setPreviewing(false)
    if (!healthy || !valid) return () => controller.abort()
    setPreviewing(true)
    const timer = setTimeout(() => {
      void apiBlob('/labels/preview', selection, controller.signal).then(blob => {
        if (controller.signal.aborted || requestSerial !== serial.current || !alive.current) return
        objectURL = URL.createObjectURL(blob)
        setPreviewURL(objectURL)
        setPreviewFingerprint(fingerprint)
        setError('')
      }).catch(err => {
        if (!controller.signal.aborted && requestSerial === serial.current && alive.current) setError((err as Error).message)
      }).finally(() => {
        if (!controller.signal.aborted && requestSerial === serial.current && alive.current) setPreviewing(false)
      })
    }, 180)
    return () => {
      clearTimeout(timer)
      controller.abort()
      if (objectURL) URL.revokeObjectURL(objectURL)
    }
  }, [healthy, valid, fingerprint, selection])

  function acceptJob(value: PrintJob) {
    if (alive.current) setJob(value)
    if (value.status === 'submitted' || value.status === 'failed') {
      forgetPending(value.request_id)
      if (alive.current && !readPending()) setPending(null)
    }
  }
  async function checkPrevious() {
    const intent = readPending()
    if (!intent) { setPending(null); return }
    if (printLock.current || !healthy) return
    printLock.current = true
    setBusy(true)
    setError('')
    try {
      const value = await api<PrintJob>('/print-jobs/' + intent.request_id)
      if (alive.current) acceptJob(value)
    } catch (err) {
      if (alive.current) {
        if (err instanceof ApiError && err.status === 404) {
          if (Array.isArray(intent.stack_ids) && Array.isArray(intent.selected_specimen_ids) && intent.printer_queue) {
            // The original request may still be rendering/claiming its job.
            // Reuse its ID and body; never replace an ambiguous request with a new ID.
            try { acceptJob(await post<PrintJob>('/print-jobs', intent)) }
            catch (retryError) { setError('The same saved print request is still unconfirmed. ' + (retryError as Error).message) }
          } else {
            setJob({ request_id: intent.request_id, status: 'unknown',
              message: 'This older saved request has no recoverable selection. Check the Mac queue before allowing a deliberate reprint.' })
          }
        } else setError('The previous print result is still unknown. Check again before submitting a new job.')
      }
    } finally {
      printLock.current = false
      if (alive.current) setBusy(false)
    }
  }
  const checkRef = useRef(checkPrevious)
  checkRef.current = checkPrevious
  useEffect(() => {
    if (!pending || !healthy) return
    void checkRef.current()
    const timer = setInterval(() => void checkRef.current(), 5000)
    return () => clearInterval(timer)
  }, [pending, healthy])

  async function print() {
    if (!healthy || printLock.current || readPending() || !valid || !queue || previewFingerprint !== fingerprint) return
    const intent: PrintIntent = {
      ...selection, request_id: requestId(), printer_queue: queue, copies,
    }
    try { localStorage.setItem(PRINT_KEY, JSON.stringify(intent)) }
    catch { setError('This browser cannot preserve a pending print request. Enable local storage before printing.'); return }
    printLock.current = true
    setPending(intent)
    setBusy(true)
    setError('')
    setJob(null)
    try {
      const result = await post<PrintJob>('/print-jobs', intent)
      if (alive.current) acceptJob(result)
    } catch (err) {
      if (alive.current) {
        if (err instanceof ApiError && err.status < 500) {
          try { acceptJob(await api<PrintJob>('/print-jobs/' + intent.request_id)) }
          catch (statusError) {
            if (statusError instanceof ApiError && statusError.status === 404) {
              forgetPending(intent.request_id); if (!readPending()) setPending(null)
              setJob({ request_id: intent.request_id, status: 'failed', error: err.message })
            }
          }
        }
        setError('Print result not confirmed. Check the saved request before creating another print job. ' + (err as Error).message)
      }
    } finally {
      printLock.current = false
      if (alive.current) setBusy(false)
    }
  }
  const allIds = stacks.flatMap(stack => stack.specimens.map(s => s.id))
  function toggle(ids: number[], checked: boolean) {
    if (busy) return
    setSelected(previous => checked ? [...new Set([...previous, ...ids])] : previous.filter(id => !ids.includes(id)))
  }
  const canPrint = healthy && valid && !busy && !pending && !previewing && previewFingerprint === fingerprint &&
    !!queue && printers?.available && Number.isInteger(copies) && copies >= 1 && copies <= 10

  return <Modal title="Preview & print labels" wide onClose={() => !busy && onClose()}>
    <div className="modal-body label-dialog">
      <section className="label-selection-panel">
        <div className="selection-heading">
          <h3>Labels to print</h3>
          <span data-testid="label-scope">{selected.length} selected</span>
        </div>
        <div className="selection-tools">
          <button className="button text" disabled={busy} onClick={() => setSelected(allIds)}>Select all</button>
          <button className="button text" disabled={busy} onClick={() => setSelected([])}>Clear</button>
        </div>
        {stacks.map(stack => {
          const ids = stack.specimens.map(s => s.id)
          const count = ids.filter(id => selected.includes(id)).length
          return <div className="label-stack-choice" key={stack.id}>
            <div className="label-stack-heading">
              <label className="check-label">
                <input type="checkbox" aria-label={'Print stack ' + stack.material_ref + ' ' + stack.preconditioning_code}
                  checked={count === ids.length} disabled={busy}
                  onChange={e => toggle(ids, e.target.checked)} />
                <span><b>{stack.project_number} / {stack.material_ref}</b>
                  <small>{stack.test_code} · {stack.preconditioning_code} · {count} of {ids.length} labels</small></span>
              </label>
              <button className="button text" disabled={busy} aria-expanded={expanded === stack.id}
                onClick={() => setExpanded(expanded === stack.id ? null : stack.id)}>Numbers {expanded === stack.id ? '−' : '+'}</button>
            </div>
            {expanded === stack.id && <div className="selection-grid label-number-grid">
              {stack.specimens.map(specimen => <label key={specimen.id}
                className={'selection-tile ' + (selected.includes(specimen.id) ? 'selected' : '')}>
                <input type="checkbox" aria-label={'Label specimen ' + String(specimen.number).padStart(2, '0') + ' ' + stack.material_ref}
                  disabled={busy} checked={selected.includes(specimen.id)}
                  onChange={e => toggle([specimen.id], e.target.checked)} />
                <b>{String(specimen.number).padStart(2, '0')}</b>
              </label>)}
            </div>}
          </div>
        })}
      </section>
      <section className="label-preview-panel">
        <div className="preview-toolbar">
          <label>Printer
            <select aria-label="Printer queue" value={queue} disabled={busy} onChange={e => setQueue(e.target.value)}>
              <option value="">Choose a printer</option>
              {printers?.printers.map(printer => <option key={printer.name} value={printer.name}>{printer.name}{printer.is_default ? ' (default)' : ''}</option>)}
            </select>
          </label>
          <label>Copies<input aria-label="Print copies" type="number" min="1" max="10"
            disabled={busy} value={copies} onChange={e => setCopies(Number(e.target.value))} /></label>
        </div>
        {previewURL ? <iframe className="pdf-preview" title="Actual label PDF preview" src={previewURL} /> :
          <div className="pdf-preview preview-empty" role="status">{previewing ? 'Loading preview…' : !selected.length ? 'Select labels to preview.' : !healthy ? 'Preview unavailable offline.' : 'Preview unavailable.'}</div>}
        {printers && !printers.available && <p className="input-help">No printer available. Open the PDF to print.</p>}
        {job && <div className={'notice ' + (job.status === 'submitted' ? 'success' : job.status === 'failed' ? 'error' : 'warning')} role="status">
          {job.status === 'submitted' ? 'Sent to printer.' : job.message || job.error || job.status}
        </div>}
        {pending && <div className="notice warning">
          <b>Print result unconfirmed. Check the printer before retrying.</b>
          <button className="button secondary" disabled={!healthy || busy} onClick={() => void checkPrevious()}>Check previous print job</button>
          {job?.status === 'unknown' && <button className="button text" disabled={busy} onClick={() => {
            forgetPending(); setPending(null); setJob(null)
          }}>I checked the queue; allow a deliberate reprint</button>}
        </div>}
        <ErrorNotice message={error} />
      </section>
    </div>
    <div className="modal-actions">
      {previewURL && <a className="button secondary" href={previewURL} target="_blank" rel="noreferrer">Open PDF ↗</a>}
      <span className="grow" />
      <button className="button secondary" disabled={busy} onClick={onClose}>Close</button>
      <button className="button primary" disabled={!canPrint} onClick={() => void print()}>
        {busy ? 'Checking print request…' : 'Print ' + selected.length * copies + (selected.length * copies === 1 ? ' label' : ' labels')}
      </button>
    </div>
  </Modal>
}
