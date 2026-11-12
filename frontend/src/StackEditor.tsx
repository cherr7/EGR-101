import { useState } from 'react'
import { api, post, specimenNumber } from './api'
import { ErrorNotice, Modal } from './App'
import LabelDialog from './LabelDialog'
import type { Config, Stack } from './types'

type Fields = { project_number: string; material_ref: string; test_code: string; preconditioning_code: string }

/** Fix a registered stack after its labels are printed. Printed labels keep working: no
 *  specimen's label or number changes. Added specimens get the next numbers and only they
 *  need new labels. Removed specimens keep their number and history. */
export default function StackEditor({ stack, config, healthy, onSaved, onClose }: {
  stack: Stack; config: Config; healthy: boolean; onSaved: (stack: Stack) => void; onClose: () => void
}) {
  const [current, setCurrent] = useState(stack)
  const [fields, setFields] = useState<Fields>({ project_number: stack.project_number, material_ref: stack.material_ref,
    test_code: stack.test_code, preconditioning_code: stack.preconditioning_code })
  const [note, setNote] = useState('')
  const [addCount, setAddCount] = useState(1)
  const [removing, setRemoving] = useState<number[]>([])
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [newLabels, setNewLabels] = useState<number[] | null>(null)
  const changed = (Object.keys(fields) as (keyof Fields)[]).filter(key => fields[key].trim() !== current[key])
  const blank = (Object.keys(fields) as (keyof Fields)[]).some(key => !fields[key].trim())

  async function act(work: () => Promise<Stack & { added_specimen_ids?: number[] }>, done: string) {
    if (busy || !healthy) return
    setBusy(true); setError(''); setMessage('')
    try {
      const value = await work()
      setCurrent(value); onSaved(value); setMessage(done); setNote('')
      setFields({ project_number: value.project_number, material_ref: value.material_ref, test_code: value.test_code, preconditioning_code: value.preconditioning_code })
      if (value.added_specimen_ids?.length) setNewLabels(value.added_specimen_ids)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const saveFields = () => act(() => api<Stack>('/stacks/' + current.id, { method: 'PATCH', body: JSON.stringify({
    ...Object.fromEntries(changed.map(key => [key, fields[key].trim()])), note, expected_version: current.version }) }), 'Stack details saved. Printed labels still scan to this stack.')
  const addSpecimens = () => act(() => post<Stack & { added_specimen_ids: number[] }>('/stacks/' + current.id + '/specimens',
    { count: addCount, note, expected_version: current.version }), `Added ${addCount} specimen(s). Print their labels now.`)
  const removeSpecimens = () => act(async () => {
    const value = await post<Stack>('/stacks/' + current.id + '/specimens/remove', { specimen_ids: removing, note, expected_version: current.version })
    setRemoving([]); setConfirmRemove(false)
    return value
  }, 'Removed. Their labels now scan as removed; set those pieces aside.')

  return <>
    <Modal title={'Edit stack ' + current.id} wide onClose={() => !busy && onClose()}>
      <div className="modal-body stack-editor">
        <p className="modal-intro">Changes keep every printed label valid. Each change is recorded in the stack's history.</p>
        <ErrorNotice message={error} />
        {message && <div className="notice success" role="status">{message}</div>}
        <h3 className="section-title">Stack details</h3>
        <div className="form-grid">
          <label>Project number<input aria-label="Stack project number" value={fields.project_number} disabled={busy} onChange={e => setFields({ ...fields, project_number: e.target.value })} /></label>
          <label>Material / sample<input aria-label="Stack material" value={fields.material_ref} disabled={busy} onChange={e => setFields({ ...fields, material_ref: e.target.value })} /></label>
          <label>Test<input aria-label="Stack test" value={fields.test_code} disabled={busy} onChange={e => setFields({ ...fields, test_code: e.target.value })} /></label>
          <label>Treatment<input aria-label="Stack treatment" list="stack-treatments" value={fields.preconditioning_code} disabled={busy} onChange={e => setFields({ ...fields, preconditioning_code: e.target.value })} /></label>
        </div>
        <datalist id="stack-treatments">{config.treatments.map(code => <option key={code} value={code} />)}</datalist>
        <label>Note (optional)<input aria-label="Stack change note" value={note} disabled={busy} onChange={e => setNote(e.target.value)} placeholder="Why it changed" /></label>
        <div className="card-actions">
          <button className="button primary" disabled={busy || !healthy || !changed.length || blank} onClick={() => void saveFields()}>Save details</button>
        </div>

        <h3 className="section-title">Add specimens</h3>
        <div className="inline-form">
          <label>How many<input aria-label="Specimens to add" type="number" min={1} max={500} value={addCount} disabled={busy}
            onChange={e => setAddCount(Number(e.target.value))} /></label>
          <span className="input-help">They become #{specimenNumber(current.next_number || current.specimen_count + 1)}
            {addCount > 1 ? ' to #' + specimenNumber((current.next_number || current.specimen_count + 1) + addCount - 1) : ''}.</span>
          <button className="button secondary" disabled={busy || !healthy || !Number.isInteger(addCount) || addCount < 1 || addCount > 500}
            onClick={() => void addSpecimens()}>Add & print new labels</button>
        </div>

        <h3 className="section-title">Remove specimens</h3>
        <p className="input-help">Pieces still inside a room must be scanned out first.</p>
        <div className="selection-grid">{current.specimens.map(s => {
          const inside = s.state === 'IN'
          return <label key={s.id} className={['selection-tile', removing.includes(s.id) ? 'selected' : '', inside ? 'disabled' : ''].join(' ')}>
            <input type="checkbox" aria-label={'Remove specimen ' + specimenNumber(s.number)} disabled={busy || inside}
              checked={removing.includes(s.id)} onChange={e => { setConfirmRemove(false); setRemoving(old => e.target.checked ? [...old, s.id] : old.filter(id => id !== s.id)) }} />
            <b>{specimenNumber(s.number)}</b><small>{inside ? 'Inside' : s.status === 'FINISHED' ? 'Finished' : 'Out'}</small>
          </label>
        })}</div>
        <div className="card-actions">
          {!confirmRemove
            ? <button className="button danger-quiet" disabled={busy || !healthy || !removing.length || removing.length >= current.specimens.length}
              onClick={() => setConfirmRemove(true)}>Remove {removing.length || ''} selected</button>
            : <><span>Remove {removing.length} specimen(s) from this stack?</span>
              <button className="button secondary" disabled={busy} onClick={() => setConfirmRemove(false)}>Keep them</button>
              <button className="button danger" disabled={busy || !healthy} onClick={() => void removeSpecimens()}>Yes, remove</button></>}
        </div>
        {!!current.removed_specimens?.length && <p className="input-help">Removed: {current.removed_specimens.map(s => '#' + specimenNumber(s.number)).join(', ')}</p>}
      </div>
      <div className="modal-actions"><span className="grow" /><button className="button secondary" disabled={busy} onClick={onClose}>Done</button></div>
    </Modal>
    {newLabels && <LabelDialog stacks={[current]} initialSelectedIds={newLabels} healthy={healthy} onClose={() => setNewLabels(null)} />}
  </>
}
