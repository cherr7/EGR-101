import { useEffect, useState } from 'react'
import { api, formatTime } from './api'
import { ErrorNotice, PageHeader } from './App'

type Level = 'critical' | 'warning' | 'info'
type Issue = { level: Level; area: string; title: string; detail: string }
type Volume = { label: string; path: string; total_bytes: number; used_bytes: number; free_bytes: number; percent: number | null }
type Memory = { total_bytes: number; used_bytes: number; cached_bytes: number | null; percent: number; pressure: string; available_percent: number | null; swap_used_bytes: number | null }
type Power = { source: string | null; on_battery: boolean; has_battery: boolean; battery_percent: number | null; battery_state: string | null; time_remaining: string | null; sleep_minutes: number | null; sleep_note: string | null; display_sleep_minutes: number | null; thermal_warning: string | null }
type Device = {
  received_at: string; age_seconds: number; stale: boolean; ip: string; clock_skew_seconds: number | null
  hostname: string | null; model: string | null; os: string | null; kernel: string | null; uptime_seconds: number | null
  cpu_percent: number | null; cores: number | null; load_average: number[] | null; temperature_c: number | null
  memory: { total_bytes: number | null; used_bytes: number | null; percent: number | null; swap_used_bytes: number | null } | null
  disk: { total_bytes: number | null; free_bytes: number | null; percent: number | null; read_only: boolean | null } | null
  throttled: { value: number; flags: string[] } | null
  network: { interface: string | null; wifi_signal_dbm: number | null; wired: boolean | null } | null
  ntp_synchronized: boolean | null; services: Record<string, { state: string; restarts: number | null }> | null
  input: { scanner: string | null; scanner_name: string | null; scanner_error: string | null; nfc: string | null } | null
  package_built_at: string | null; package_version?: string | null; history: { at: string; cpu: number | null; memory: number | null; temperature: number | null }[]
}
type Station = { id: string; name: string; status: 'ready' | 'no_scanner' | 'offline' | 'never_seen'; ip: string | null; last_seen: string | null; scanner_ready: boolean; input: string | null; live_clients: string[]; device: Device | null }
type Queue = { name: string; is_default: boolean; state: string; alerts: string | null; device: string | null; jobs_waiting: number }
type Sample = { at: string; cpu: number | null; memory: number | null }
export type HealthReport = {
  server_time: string; overall: 'ok' | 'warning' | 'critical'; issues: Issue[]
  host: { hostname: string; ip: string | null; network_interface: string | null; model: string | null; chip: string | null; os: string; cores: number | null; load_average: number[] | null; booted_at: string | null; python: string }
  cpu: { percent: number | null }; memory: Memory | null; storage: Volume[]; power: Power | null
  application: { version?: string; commit?: string | null; built_at?: string | null; last_update?: { result: string; version: string; previous?: string; at: string; reason?: string } | null; started_at: string; state_folder: string; logs_bytes: number | null; pid: number }
  database: { connected: boolean; size_bytes?: number; version?: string; started_at?: string; connections?: number; specimens?: number; audit_events?: number }
  backups: { available: boolean; count?: number; total_bytes?: number; folder?: string; latest_at?: string | null; latest_verified?: boolean | null; enabled?: boolean; overdue?: boolean; last_error?: string | null; next_run_at?: string | null }
  stations: Station[]
  printer: { cups_available: boolean; configured_queue: string | null; message: string; queues: Queue[]; jobs_last_24h: Record<string, number>; last_job: { status: string; queue: string | null; created_at: string; error: string | null } | null }
  history: Sample[]; sample_seconds: number
}

export const bytes = (value?: number | null) => value == null ? '—' : value >= 1e12 ? `${(value / 1e12).toFixed(2)} TB` : value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : value >= 1e6 ? `${(value / 1e6).toFixed(1)} MB` : `${Math.ceil(value / 1e3)} KB`
// Binary units, as Activity Monitor shows them.
const ram = (value?: number | null) => value == null ? '—' : value >= 2 ** 30 ? `${(value / 2 ** 30).toFixed(1)} GB` : `${Math.round(value / 2 ** 20)} MB`
function since(value?: string | null, now = Date.now()) {
  if (!value) return '—'
  const minutes = Math.max(0, Math.floor((now - Date.parse(value)) / 60000))
  const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60)
  return days ? `${days} d ${hours} h` : hours ? `${hours} h ${minutes % 60} min` : `${minutes} min`
}
const duration = (seconds?: number | null) => seconds == null ? '—' : since(new Date(Date.now() - seconds * 1000).toISOString())
const tone = (percent: number | null | undefined, warn: number, bad: number) => percent == null ? '' : percent >= bad ? 'bad' : percent >= warn ? 'warn' : 'good'
const LEVEL: Record<Level, { label: string; badge: string }> = {
  critical: { label: 'Critical', badge: 'critical' },
  warning: { label: 'Warning', badge: 'warning' },
  info: { label: 'Info', badge: 'blue' },
}
const STATION: Record<Station['status'], { label: string; badge: string }> = {
  ready: { label: 'Ready', badge: 'good' }, no_scanner: { label: 'No scanner', badge: 'warning' },
  offline: { label: 'Offline', badge: 'critical' }, never_seen: { label: 'Not connected', badge: 'muted' },
}
const FLAG_TEXT: Record<string, string> = {
  under_voltage: 'Under-voltage', frequency_capped: 'CPU capped', throttled: 'Throttled', soft_temperature_limit: 'Hot',
  under_voltage_occurred: 'Under-voltage since boot', frequency_capped_occurred: 'Capped since boot', throttled_occurred: 'Throttled since boot', soft_temperature_limit_occurred: 'Hot since boot',
}

function Meter({ label, value, percent, warn, bad }: { label: string; value: string; percent: number | null | undefined; warn: number; bad: number }) {
  return <div className="health-meter">
    <div className="health-meter-head"><span>{label}</span><b>{value}</b></div>
    <div className={'meter ' + tone(percent, warn, bad)} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
      <span style={{ width: `${Math.min(100, Math.max(0, percent ?? 0))}%` }} />
    </div>
  </div>
}

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return <dl className="health-facts">{rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value ?? '—'}</dd></div>)}</dl>
}

function PiDetail({ device, expected }: { device: Device; expected?: string }) {
  const flags = device.throttled?.flags || []
  const skew = device.clock_skew_seconds
  return <div className="pi-detail">
    <Facts rows={[
      ['Version', <>{device.package_version ?? 'Unknown'}{device.package_version && expected && device.package_version !== expected && <span className="badge warning"> Behind the Mac</span>}</>],
      ['Up for', duration(device.uptime_seconds)],
      ['Power', flags.length ? <span className="pi-flags">{flags.map(f => <span key={f} className={'badge ' + (f === 'under_voltage' ? 'critical' : 'warning')}>{FLAG_TEXT[f] || f}</span>)}</span> : device.throttled ? 'OK' : '—'],
      ['Memory', `${ram(device.memory?.used_bytes)} of ${ram(device.memory?.total_bytes)}`],
      ['SD card', <>{bytes(device.disk?.free_bytes)} free{device.disk?.read_only && <span className="badge critical"> Read-only</span>}</>],
      ['Wi-Fi', device.network?.wifi_signal_dbm != null ? `${device.network.wifi_signal_dbm} dBm` : device.network?.wired ? 'Wired' : '—'],
      ['Clock', skew == null ? '—' : Math.abs(skew) < 5 ? 'OK' : `${skew > 0 ? 'Ahead' : 'Behind'} ${Math.abs(skew).toFixed(0)} s`],
      ['Services', device.services ? <span className="pi-flags">{Object.entries(device.services).map(([name, info]) => <span key={name} className={'badge ' + (info.state === 'active' ? 'good' : 'warning')}>{name}</span>)}</span> : '—'],
      ['Scanner', device.input?.scanner_error || device.input?.scanner_name || device.input?.scanner || '—'],
    ]} />
  </div>
}

function printerState(queue?: Queue) {
  if (!queue) return 'Not set up'
  return { idle: 'Ready', printing: 'Printing', disabled: 'Paused' }[queue.state] || 'Unknown'
}
const connection = (device: string | null) => !device ? '—' : device.startsWith('ippusb:') || device.startsWith('usb:') ? 'USB' : 'Network'

export default function SystemHealth({ healthy }: { healthy: boolean }) {
  const [report, setReport] = useState<HealthReport | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api<HealthReport>('/system/health', {}, 8000)
      .then(value => { if (alive) { setReport(value); setError('') } })
      .catch(err => { if (alive) setError((err as Error).message || 'No answer from the host.') })
    void load()
    const timer = setInterval(load, 10000)
    return () => { alive = false; clearInterval(timer) }
  }, [healthy])

  const header = <PageHeader title="System health">
    {report && <span className={'health-overall ' + report.overall} role="status">
      <i aria-hidden="true" />{report.overall === 'ok' ? 'All OK' : report.overall === 'critical' ? 'Problem' : 'Warnings'}
    </span>}
  </PageHeader>
  if (!report) return <>{header}<ErrorNotice message={error} />{!error && <div className="notice info">Loading…</div>}</>

  const { host, memory, power, database, backups, printer, application } = report
  const disk = report.storage[0]
  const ready = report.stations.filter(s => s.status === 'ready').length
  const problems = report.issues.filter(i => i.level !== 'info').length
  const now = Date.parse(report.server_time)
  const queue = printer.queues.find(q => q.is_default) || printer.queues[0]
  const update = application.last_update
  return <>
    {header}
    <ErrorNotice message={error} />
    <section className="health-summary" aria-label="Summary">
      <div><span>Host Mac</span><b>{power?.on_battery ? `On battery · ${power.battery_percent}%` : 'Online'}</b></div>
      <div><span>Scan stations</span><b>{ready} of {report.stations.length} ready</b></div>
      <div><span>Label printer</span><b>{printerState(queue)}</b></div>
      <div><span>Last backup</span><b>{backups.latest_at ? since(backups.latest_at, now) + ' ago' : 'None'}</b></div>
    </section>

    <section className="card" aria-label="Issues">
      <div className="card-heading compact"><div><h2>Issues {problems > 0 && <span className="count-pill">{problems}</span>}</h2></div></div>
      {report.issues.length === 0
        ? <div className="padded"><div className="notice success">No problems.</div></div>
        : <ul className="health-issues">{report.issues.map((issue, index) => <li key={index} className={issue.level}>
            <span className={'badge ' + LEVEL[issue.level].badge}>{LEVEL[issue.level].label}</span>
            <div><b>{issue.title}</b>{issue.detail && <p>{issue.detail}</p>}</div>
            <small>{issue.area}</small>
          </li>)}</ul>}
    </section>

    <section className="card" aria-label="Host Mac">
      <div className="card-heading compact"><div><h2>Host Mac</h2></div></div>
      <div className="padded">
        <div className="health-meters">
          <Meter label="Storage" value={disk ? `${bytes(disk.free_bytes)} free` : 'Unknown'} percent={disk?.percent} warn={85} bad={95} />
          <Meter label="Memory" value={memory ? `${ram(memory.used_bytes)} of ${ram(memory.total_bytes)}` : 'Unknown'} percent={memory?.percent} warn={85} bad={95} />
          <Meter label="CPU" value={report.cpu.percent == null ? 'Unknown' : `${report.cpu.percent.toFixed(0)}%`} percent={report.cpu.percent} warn={70} bad={90} />
        </div>
        <Facts rows={[
          ['Address', host.ip ?? '—'],
          ['Power', power ? power.on_battery ? `Battery ${power.battery_percent}%` : 'Charger' : '—'],
          ['Sleep', power?.sleep_minutes == null ? '—' : power.sleep_minutes === 0 ? 'Never' : `After ${power.sleep_minutes} min`],
          ['Up for', since(host.booted_at, now)],
        ]} />
      </div>
    </section>

    <div className="prep-bottom">
      <section className="card" aria-label="Application">
        <div className="card-heading compact"><div><h2>App</h2></div></div>
        <div className="padded"><Facts rows={[
          ['Version', application.version ?? 'Unknown'],
          ['Last update', !update ? '—' : update.result === 'updated' ? `${update.version} · ${formatTime(update.at)}`
            : <span className="badge warning">{update.version} {update.result === 'rolled back' ? 'failed' : 'refused'}</span>],
          ['Database', database.connected ? 'Connected' : <span className="badge critical">Not reachable</span>],
          ['Specimens', database.connected ? (database.specimens ?? 0).toLocaleString() : '—'],
          ['Data folder', <code>{application.state_folder}</code>],
        ]} /></div>
      </section>

      <section className="card" aria-label="Label printer">
        <div className="card-heading compact"><div><h2>Label printer</h2></div></div>
        {printer.queues.length === 0
          ? <div className="empty-inline"><p>No printer. Connect the Brother printer by USB.</p></div>
          : <div className="table-wrap"><table><thead><tr><th>Printer</th><th>State</th><th>Connection</th><th>Waiting</th></tr></thead><tbody>
            {printer.queues.map(q => <tr key={q.name}><td><b>{q.name}</b>{q.is_default && <small>Default</small>}</td>
              <td><span className={'badge ' + (q.state === 'idle' || q.state === 'printing' ? 'good' : q.state === 'disabled' ? 'warning' : 'muted')}>{q.state}</span>{q.alerts && <small>{q.alerts}</small>}</td>
              <td>{connection(q.device)}</td><td>{q.jobs_waiting}</td></tr>)}
          </tbody></table></div>}
      </section>
    </div>

    <section className="card" aria-label="Scan stations">
      <div className="card-heading compact"><div><h2>Scan stations</h2></div></div>
      <div className="table-wrap"><table className="station-health-table"><thead><tr><th>Station</th><th>Status</th><th>Scanner</th><th>Address</th><th>CPU</th><th>Temp</th><th>SD free</th><th>Last seen</th><th /></tr></thead><tbody>
        {report.stations.map(s => {
          const d = s.device && !s.device.stale ? s.device : null
          const temp = d?.temperature_c
          return [<tr key={s.id} data-testid={`health-station-${s.id}`}><td><b>{s.name}</b></td>
            <td><span className={'badge ' + STATION[s.status].badge}>{STATION[s.status].label}</span></td>
            <td>{s.status === 'never_seen' ? '—' : s.scanner_ready ? 'Ready' : 'Not ready'}</td>
            <td>{s.ip ?? s.device?.ip ?? '—'}</td>
            {d ? <>
              <td className={'pi-cell ' + tone(d.cpu_percent, 70, 90)}>{d.cpu_percent == null ? '—' : `${d.cpu_percent.toFixed(0)}%`}</td>
              <td className={'pi-cell ' + tone(temp, 70, 80)}>{temp == null ? '—' : `${temp.toFixed(0)} °C`}</td>
              <td className={'pi-cell ' + (d.disk?.read_only ? 'bad' : tone(d.disk?.percent, 90, 95))}>{bytes(d.disk?.free_bytes)}</td>
            </> : <td colSpan={3} className="pi-missing">—</td>}
            <td>{formatTime(s.last_seen)}</td>
            <td>{s.device && <button className="button text" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? 'Hide' : 'Details'}</button>}</td></tr>,
          open === s.id && s.device && <tr key={s.id + '-detail'} className="pi-detail-row"><td colSpan={9}><PiDetail device={s.device} expected={application.version} /></td></tr>]
        })}
      </tbody></table></div>
    </section>
  </>
}
