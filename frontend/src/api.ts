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
    if (!response.ok) {
      const detail = body.detail
      throw new ApiError(response.status, typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: { msg: string; loc?: string[] }) => `${item.loc?.join('.') || 'Input'}: ${item.msg}`).join('; ') : detail?.message || body.message || `Request failed (${response.status})`)
    }
    return body as T
  } finally { clearTimeout(timer) }
}
export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })
export const formatTime = (value?: string | null) => value ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date(value)) : '—'
export const elapsed = (value?: string | null, end?: string | null) => {
  if (!value) return '—'
  const minutes = Math.max(0, Math.floor(((end ? Date.parse(end) : Date.now()) - Date.parse(value)) / 60000))
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
export function codeFor(id: string, stack = false) { return `UL1:${stack ? 'S' : 'P'}:${id}` }
export function requestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
