import { useEffect, useState } from 'react'
import { api, ApiError } from './api'
import { ErrorNotice } from './App'

type UpdateStatus = { running: string; latest: string | null; available: boolean; in_progress: boolean; can_update: boolean; check_error: string | null }
type Outside = { outside: boolean; enabled?: boolean; has_passcode?: boolean; url?: string | null; error?: string | null }

export const passcodeNeeded = (error: unknown) => error instanceof ApiError && error.status === 401 && error.message === 'Passcode required.'

function usePoll<T>(path: string, every: number): [T | null, (value: T) => void] {
  const [value, setValue] = useState<T | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api<T>(path, {}, 8000).then(next => { if (alive) setValue(next) }).catch(() => undefined)
    void load()
    const timer = setInterval(load, every)
    return () => { alive = false; clearInterval(timer) }
  }, [path, every])
  return [value, setValue]
}

export function UpdateControl() {
  const [status, setStatus] = usePoll<UpdateStatus>('/system/update', 5000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!status) return null
  async function run(path: string) {
    setBusy(true); setError('')
    try { setStatus(await api<UpdateStatus>(path, { method: 'POST', body: '{}' }, 30000)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  if (status.in_progress) return <span className="badge warning">Updating…</span>
  if (!status.can_update) return null
  return <span className="update-control">
    {status.available && status.latest
      ? <button className="button small" disabled={busy} onClick={() => {
          if (window.confirm(`Update to ${status.latest}? The app stops for about a minute. A backup is made first.`)) void run('/system/update')
        }}>Update to {status.latest}</button>
      : <button className="button text" disabled={busy} onClick={() => void run('/system/update/check')}>{busy ? 'Checking…' : 'Check for updates'}</button>}
    {(error || status.check_error) && <small className="error-text">{error || status.check_error}</small>}
  </span>
}

export function OutsideCard() {
  const [status, setStatus] = usePoll<Outside>('/outside/status', 5000)
  const [passcode, setPasscode] = useState('')
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  if (!status || status.outside) return null
  async function save(body: { enabled?: boolean; passcode?: string }) {
    setError('')
    try { setStatus(await api<Outside>('/outside', { method: 'PUT', body: JSON.stringify(body) })); setPasscode('') }
    catch (err) { setError((err as Error).message) }
  }
  return <section className="card" aria-label="Outside link">
    <div className="card-heading compact"><div><h2>Outside link</h2></div>
      {status.has_passcode && <button className="button small secondary" onClick={() => void save({ enabled: !status.enabled })}>{status.enabled ? 'Turn off' : 'Turn on'}</button>}
    </div>
    <div className="padded outside-link">
      {status.enabled
        ? status.url
          ? <div className="outside-url"><a href={status.url} target="_blank" rel="noreferrer">{status.url}</a>
              <button className="button small secondary" onClick={() => { void navigator.clipboard?.writeText(status.url!); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>{copied ? 'Copied' : 'Copy'}</button></div>
          : <p>{status.error ?? 'Starting…'}</p>
        : <p>Off</p>}
      <form className="outside-passcode" onSubmit={event => { event.preventDefault(); void save({ passcode }) }}>
        <label>{status.has_passcode ? 'New passcode' : 'Passcode'}<input type="password" autoComplete="new-password" minLength={6} value={passcode} onChange={event => setPasscode(event.target.value)} /></label>
        <button className="button small" disabled={passcode.length < 6}>Save</button>
      </form>
      <ErrorNotice message={error} />
    </div>
  </section>
}

export function PasscodeScreen({ onDone }: { onDone: () => void }) {
  const [passcode, setPasscode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try { await api('/outside/login', { method: 'POST', body: JSON.stringify({ passcode }) }); onDone() }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <main className="passcode-screen">
    <form className="card padded" onSubmit={submit}>
      <h1>Fabric Conditioning</h1>
      <label>Passcode<input type="password" autoFocus autoComplete="current-password" value={passcode} onChange={event => setPasscode(event.target.value)} /></label>
      <ErrorNotice message={error} />
      <button className="button" disabled={busy || !passcode}>Open</button>
    </form>
  </main>
}
