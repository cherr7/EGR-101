let serverOffsetMs = 0
export const serverNow = () => Date.now() + serverOffsetMs
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export async function api<T>(path: string, options: RequestInit = {}, timeout = 10000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(`/api${path}`, { credentials: 'include', ...options, headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers }, signal: controller.signal })
    const body = await response.json().catch(() => ({}))
    const serverTime = body.server_time || body.items?.[0]?.server_time
    if (serverTime && Number.isFinite(Date.parse(serverTime))) serverOffsetMs = Date.parse(serverTime) - Date.now()
    if (!response.ok) {
      const detail = body.detail
      throw new ApiError(response.status, typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { msg: string; loc?: string[] }) => `${item.loc?.join('.') || 'Input'}: ${item.msg}`).join('; ') : detail?.message || body.message || `Request failed (${response.status})`)
    }
    return body as T
  } finally { clearTimeout(timer) }
}
export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })
export async function apiBlob(path: string, body: unknown, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch('/api' + path, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) {
    const value = await response.json().catch(() => ({}))
    throw new ApiError(response.status, typeof value.detail === 'string' ? value.detail : value.detail?.message || 'Could not generate the label preview.')
  }
  return response.blob()
}
export const formatTime = (value?: string | null) => value ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(value)) : '—'
export const elapsed = (value?: string | null, end?: string | null) => {
  if (!value) return '—'
  const minutes = Math.max(0, Math.floor(((end ? Date.parse(end) : serverNow()) - Date.parse(value)) / 60000))
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
export const entryOf = (s: import('./types').Specimen) => s.ENTRY ?? s.entry_at ?? s.sessions?.[0]?.entered_at ?? s.entered_at
export const exitOf = (s: import('./types').Specimen) => s.EXIT ?? s.exit_at ?? s.sessions?.[0]?.exited_at ?? null
export const specimenNumber = (value: number) => String(value).padStart(2, '0')
export const specimenNumbers = (values: number[]) => values.map(specimenNumber).join(', ')
export const specimenStatus = (s: import('./types').Specimen) => exitOf(s) ? 'Finished' : entryOf(s) ? 'Conditioning' : 'Awaiting entry'
export const isReady = (s: import('./types').Specimen, now = serverNow()) => typeof s.ready === 'boolean' ? s.ready : s.state === 'IN' && !!s.due_at && Date.parse(s.due_at) <= now
export const localTimeInput = (value: string) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)).replace(' ', 'T')
export function easternToUTC(value: string) { const target = Date.parse(`${value}Z`); let guess = target; for (let i = 0; i < 4; i++) { const represented = Date.parse(`${localTimeInput(new Date(guess).toISOString())}Z`); guess += target - represented } if (localTimeInput(new Date(guess).toISOString()).slice(0, value.length) !== value) throw new Error('This Eastern Time does not exist. Check the daylight-saving change.'); return new Date(guess).toISOString() }
export function requestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
/** Compact duration since/until an ISO time, e.g. "45 min", "3 h 10 min", "2 d 4 h". */
export function duration(value?: string | null, until = serverNow()) {
  if (!value) return '—'
  const minutes = Math.max(0, Math.round((until - Date.parse(value)) / 60000))
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return hours % 24 ? `${Math.floor(hours / 24)} d ${hours % 24} h` : `${Math.floor(hours / 24)} d`
}
/** Hash-route query parameters, e.g. #dashboard?room=shared -> {room: 'shared'}. */
export const hashParams = () => new URLSearchParams(location.hash.split('?')[1] || '')
