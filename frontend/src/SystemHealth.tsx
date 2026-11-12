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
/** RAM in binary units, matching Activity Monitor (8 GB, not 8.6 GB). */
const ram = (value?: number | null) => value == null ? '—' : value >= 2 ** 30 ? `${(value / 2 ** 30).toFixed(1)} GB` : `${Math.round(value / 2 ** 20)} MB`
function since(value?: string | null, now = Date.now()) {
  if (!value) return '—'
  const minutes = Math.max(0, Math.floor((now - Date.parse(value)) / 60000))
  const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60)
  return days ? `${days} d ${hours} h` : hours ? `${hours} h ${minutes % 60} min` : `${minutes} min`
}
const tone = (percent: number | null | undefined, warn: number, bad: number) => percent == null ? '' : percent >= bad ? 'bad' : percent >= warn ? 'warn' : 'good'
const LEVEL: Record<Level, { label: string; icon: string; badge: string }> = {
  critical: { label: 'Critical', icon: '✕', badge: 'critical' },
  warning: { label: 'Warning', icon: '!', badge: 'warning' },
  info: { label: 'Info', icon: 'i', badge: 'blue' },
}
const STATION: Record<Station['status'], { label: string; badge: string }> = {
  ready: { label: 'Ready', badge: 'good' }, no_scanner: { label: 'No scanner', badge: 'warning' },
  offline: { label: 'Offline', badge: 'critical' }, never_seen: { label: 'Not connected', badge: 'muted' },
}

function Meter({ label, value, detail, percent, warn, bad }: { label: string; value: string; detail: React.ReactNode; percent: number | null | undefined; warn: number; bad: number }) {
  return <div className="health-meter">
    <div className="health-meter-head"><span>{label}</span><b>{value}</b></div>
    <div className={'meter ' + tone(percent, warn, bad)} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
      <span style={{ width: `${Math.min(100, Math.max(0, percent ?? 0))}%` }} />
    </div>
    <small>{detail}</small>
  </div>
}

/** One-series trend with a hover readout: the title names the series, so no legend. */
function Sparkline({ label, samples, field, seconds }: { label: string; samples: { at: string; cpu: number | null; memory: number | null }[]; field: 'cpu' | 'memory'; seconds: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const points = samples.map((row, index) => ({ index, at: row.at, value: row[field] })).filter(p => p.value != null) as { index: number; at: string; value: number }[]
  if (points.length < 2) return <div className="sparkline empty"><span>{label}</span><small>Collecting…</small></div>
  const W = 300, H = 56, n = samples.length - 1 || 1
  const x = (i: number) => i / n * W, y = (v: number) => H - 2 - v / 100 * (H - 4)
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.index).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')
  const shown = hover == null ? points.at(-1)! : points.reduce((best, p) => Math.abs(p.index - hover) < Math.abs(best.index - hover) ? p : best)
  const minutes = Math.round(samples.length * seconds / 60)
  return <div className="sparkline">
    <div className="sparkline-head"><span>{label} · last {minutes} min</span><b>{shown.value.toFixed(0)}%{hover != null && <small> at {formatTime(shown.at)}</small>}</b></div>
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${label} over the last ${minutes} minutes, now ${points.at(-1)!.value.toFixed(0)}%`}
      onMouseMove={e => { const box = e.currentTarget.getBoundingClientRect(); setHover(Math.round((e.clientX - box.left) / box.width * n)) }} onMouseLeave={() => setHover(null)}>
      <line x1="0" x2={W} y1={y(50)} y2={y(50)} className="sparkline-grid" />
      <path d={path} className="sparkline-line" vectorEffect="non-scaling-stroke" />
      <line x1={x(shown.index)} x2={x(shown.index)} y1="0" y2={H} className={'sparkline-cursor' + (hover == null ? ' hidden' : '')} vectorEffect="non-scaling-stroke" />
    </svg>
    <div className="sparkline-axis"><span>{minutes} min ago</span><span>50%</span><span>now</span></div>
  </div>
}

const duration = (seconds?: number | null) => seconds == null ? '—' : since(new Date(Date.now() - seconds * 1000).toISOString())
const FLAG_TEXT: Record<string, string> = {
  under_voltage: 'Under-voltage now', frequency_capped: 'CPU frequency capped', throttled: 'Throttled now', soft_temperature_limit: 'Soft temperature limit',
  under_voltage_occurred: 'Under-voltage since boot', frequency_capped_occurred: 'Capped since boot', throttled_occurred: 'Throttled since boot', soft_temperature_limit_occurred: 'Temperature limit since boot',
}
function PiCell({ value, tone: cls, title }: { value: string; tone?: string; title?: string }) {
  return <td className={'pi-cell ' + (cls || '')} title={title}>{value}</td>
}
function PiDetail({ device, expected }: { device: Device; expected?: string }) {
  const flags = device.throttled?.flags || []
  const net = device.network
  return <div className="pi-detail">
    <Facts rows={[
      ['Model', device.model], ['System', <>{device.os}<small> · Linux {device.kernel}</small></>],
      ['Pi up for', duration(device.uptime_seconds)],
      ['Power', flags.length ? <span className="pi-flags">{flags.map(f => <span key={f} className={'badge ' + (f === 'under_voltage' ? 'critical' : 'warning')}>{FLAG_TEXT[f] || f}</span>)}</span> : device.throttled ? 'OK' : 'Unknown'],
      ['CPU load', <>{device.load_average?.join(' / ') ?? '—'}<small> · {device.cores} cores</small></>],
      ['Memory', <>{ram(device.memory?.used_bytes)} of {ram(device.memory?.total_bytes)}{!!device.memory?.swap_used_bytes && <small> · {ram(device.memory.swap_used_bytes)} swap</small>}</>],
      ['SD card', <>{bytes(device.disk?.free_bytes)} free of {bytes(device.disk?.total_bytes)}{device.disk?.read_only && <span className="badge critical"> read-only</span>}</>],
      ['Network', net ? <>{net.interface ?? '—'}{net.wifi_signal_dbm != null && <small> · Wi-Fi {net.wifi_signal_dbm} dBm</small>}{net.wired && <small> · wired</small>}</> : '—'],
      ['Clock', <>{device.clock_skew_seconds == null ? '—' : Math.abs(device.clock_skew_seconds) < 5 ? 'In sync with the Mac' : `${device.clock_skew_seconds > 0 ? 'Ahead' : 'Behind'} by ${Math.abs(device.clock_skew_seconds).toFixed(0)} s`}{device.ntp_synchronized != null && <small> · NTP {device.ntp_synchronized ? 'synced' : 'not synced'}</small>}</>],
      ['Services', device.services ? <span className="pi-flags">{Object.entries(device.services).map(([name, info]) => <span key={name} className={'badge ' + (info.state === 'active' ? 'good' : 'warning')} title={`${info.restarts ?? 0} automatic restarts`}>{name}: {info.state}{info.restarts ? ` · ${info.restarts}↻` : ''}</span>)}</span> : '—'],
      ['Scanner device', device.input ? <>{device.input.scanner_name || device.input.scanner || '—'}{device.input.scanner_error && <small> · {device.input.scanner_error}</small>}</> : '—'],
      ['Version', <>{device.package_version ?? 'Unknown'}{device.package_version && expected && device.package_version !== expected && <span className="badge warning"> behind the Mac</span>}<small> · built {formatTime(device.package_built_at)} · report {device.age_seconds} s ago</small></>],
    ]} />
    <div className="health-trends pi-trends">
      <Sparkline label="Pi CPU" samples={device.history} field="cpu" seconds={30} />
      <Sparkline label="Pi memory" samples={device.history} field="memory" seconds={30} />
    </div>
  </div>
}

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return <dl className="health-facts">{rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value ?? '—'}</dd></div>)}</dl>
}

export default function SystemHealth({ healthy }: { healthy: boolean }) {
  const [report, setReport] = useState<HealthReport | null>(null)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<number>(0)
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api<HealthReport>('/system/health', {}, 8000)
      .then(value => { if (alive) { setReport(value); setUpdated(Date.now()); setError('') } })
      .catch(err => { if (alive) setError((err as Error).message || 'The host did not answer.') })
    void load()
    const timer = setInterval(load, 10000)
    return () => { alive = false; clearInterval(timer) }
  }, [healthy])

  const header = <PageHeader title="System health">
    {report && <span className={'health-overall ' + report.overall} role="status">
      <i aria-hidden="true" />{report.overall === 'ok' ? 'All OK' : report.overall === 'critical' ? 'Problem' : 'Warnings'}
      <small>Updated {new Date(updated).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</small>
    </span>}
  </PageHeader>
  if (!report) return <>{header}<ErrorNotice message={error} />{!error && <div className="notice info">Reading system status…</div>}</>

  const { host, memory, power, database, backups, printer, application } = report
  const disk = report.storage[0]
  const ready = report.stations.filter(s => s.status === 'ready').length
  const problems = report.issues.filter(i => i.level !== 'info').length
  const now = Date.parse(report.server_time)
  const queue = printer.queues.find(q => q.is_default) || printer.queues[0]
  return <>
    {header}
    <ErrorNotice message={error && `Showing the last reading: ${error}`} />
    <section className="health-summary" aria-label="Summary">
      <div><span>Host Mac</span><b>{power?.on_battery ? `On battery · ${power.battery_percent}%` : 'Online'}</b><small>Up {since(host.booted_at, now)}</small></div>
      <div><span>Scan stations</span><b>{ready} of {report.stations.length} ready</b><small>{report.stations.filter(s => s.status === 'offline').length} offline</small></div>
      <div><span>Label printer</span><b>{queue ? queue.state === 'idle' ? 'Ready' : queue.state === 'printing' ? 'Printing' : queue.state === 'disabled' ? 'Paused' : 'Unknown' : 'Not set up'}</b><small>{queue ? queue.name : 'No print queue on the Mac'}</small></div>
      <div><span>Last backup</span><b>{backups.latest_at ? since(backups.latest_at, now) + ' ago' : 'None yet'}</b><small>{backups.count ?? 0} saved · {bytes(backups.total_bytes)}</small></div>
    </section>

    <section className="card" aria-label="Issues">
      <div className="card-heading compact"><div><h2>Issues {problems > 0 && <span className="count-pill">{problems}</span>}</h2></div></div>
      {report.issues.length === 0
        ? <div className="padded"><div className="notice success">No problems.</div></div>
        : <ul className="health-issues">{report.issues.map((issue, index) => <li key={index} className={issue.level}>
            <span className={'badge ' + LEVEL[issue.level].badge}><span aria-hidden="true">{LEVEL[issue.level].icon}</span> {LEVEL[issue.level].label}</span>
            <div><b>{issue.title}</b>{issue.detail && <p>{issue.detail}</p>}</div>
            <small>{issue.area}</small>
          </li>)}</ul>}
    </section>

    <section className="card" aria-label="Host Mac">
      <div className="card-heading compact"><div><h2>Host Mac</h2><p>{host.hostname} · {host.model} · {host.chip} · {host.os}</p></div></div>
      <div className="padded">
        <div className="health-meters">
          {disk ? <Meter label="Storage used" value={`${bytes(disk.used_bytes)} of ${bytes(disk.total_bytes)}`} percent={disk.percent} warn={85} bad={95}
            detail={<><b data-testid="disk-free">{bytes(disk.free_bytes)} free</b> · {disk.percent?.toFixed(0)}% used{report.storage.slice(1).map(v => <span key={v.path}> · {v.label}: {bytes(v.free_bytes)} free</span>)}</>} />
            : <Meter label="Storage used" value="Unknown" percent={null} warn={85} bad={95} detail="Disk usage could not be read." />}
          <Meter label="Memory (RAM)" value={memory ? `${ram(memory.used_bytes)} of ${ram(memory.total_bytes)}` : 'Unknown'} percent={memory?.percent} warn={85} bad={95}
            detail={memory ? <>Pressure <b>{memory.pressure}</b>{memory.cached_bytes != null && <> · {ram(memory.cached_bytes)} cached files</>}{!!memory.swap_used_bytes && <> · {ram(memory.swap_used_bytes)} swap</>}</> : 'Memory could not be read.'} />
          <Meter label="CPU" value={report.cpu.percent == null ? 'Unknown' : `${report.cpu.percent.toFixed(0)}%`} percent={report.cpu.percent} warn={70} bad={90}
            detail={<>{host.cores} cores{host.load_average && <> · load {host.load_average.join(' / ')} (1 / 5 / 15 min)</>}</>} />
        </div>
        <div className="health-trends">
          <Sparkline label="CPU" samples={report.history} field="cpu" seconds={report.sample_seconds} />
          <Sparkline label="Memory" samples={report.history} field="memory" seconds={report.sample_seconds} />
        </div>
        <Facts rows={[
          ['Network', <>{host.ip ?? 'No LAN address'}{host.network_interface && <small> on {host.network_interface}</small>}</>],
          ['Power', power ? <>{power.source ?? '—'}{power.has_battery && <small> · battery {power.battery_percent}% {power.battery_state}</small>}</> : '—'],
          ['System sleep', power?.sleep_minutes == null ? '—' : power.sleep_minutes === 0 ? <>Never{power.sleep_note && <small> · {power.sleep_note}</small>}</> : `After ${power.sleep_minutes} min`],
          ['Temperature', power ? power.thermal_warning ?? 'Normal' : '—'],
          ['Mac started', <>{formatTime(host.booted_at)} <small>· up {since(host.booted_at, now)}</small></>],
        ]} />
      </div>
    </section>

    <div className="prep-bottom">
      <section className="card" aria-label="Application">
        <div className="card-heading compact"><div><h2>Tracker app &amp; database</h2></div></div>
        <div className="padded"><Facts rows={[
          ['Version', <>{application.version ?? 'Unknown'}{application.built_at && <small> · built {formatTime(application.built_at)}</small>}</>],
          ['Last update', application.last_update ? <>{application.last_update.result === 'updated' ? `Updated to ${application.last_update.version}` : application.last_update.result === 'rolled back' ? <span className="badge warning">Update to {application.last_update.version} failed and was rolled back</span> : <span className="badge warning">Update to {application.last_update.version} refused</span>}<small> · {formatTime(application.last_update.at)}{application.last_update.reason ? ` · ${application.last_update.reason}` : ''}</small></> : 'None yet'],
          ['Server running for', <>{since(application.started_at, now)} <small>· since {formatTime(application.started_at)}</small></>],
          ['Database', database.connected ? <>Connected <small>· PostgreSQL {database.version} · {database.connections} connections</small></> : <span className="badge critical">Not reachable</span>],
          ['Database size', bytes(database.size_bytes)],
          ['Records', database.connected ? `${(database.specimens ?? 0).toLocaleString()} specimens · ${(database.audit_events ?? 0).toLocaleString()} audit events` : '—'],
          ['Backups', <>{backups.count ?? 0} {backups.count === 1 ? 'archive' : 'archives'} · {bytes(backups.total_bytes)}{backups.next_run_at && <small> · next {formatTime(backups.next_run_at)}</small>}</>],
          ['Log files', bytes(application.logs_bytes)],
          ['Data folder', <code>{application.state_folder}</code>],
        ]} /></div>
      </section>

      <section className="card" aria-label="Label printer">
        <div className="card-heading compact"><div><h2>Label printer</h2><p>{printer.message}</p></div></div>
        {printer.queues.length === 0
          ? <div className="empty-inline"><h3>No printer queue</h3><p>Connect the Brother label printer by USB and add it in System Settings › Printers &amp; Scanners.</p></div>
          : <div className="table-wrap"><table><thead><tr><th>Queue</th><th>State</th><th>Connection</th><th>Waiting</th></tr></thead><tbody>
            {printer.queues.map(q => <tr key={q.name}><td><b>{q.name}</b>{q.is_default && <small>Default</small>}</td>
              <td><span className={'badge ' + (q.state === 'idle' || q.state === 'printing' ? 'good' : q.state === 'disabled' ? 'warning' : 'muted')}>{q.state}</span>{q.alerts && <small>{q.alerts}</small>}</td>
              <td><small className="id-hint" title={q.device || ''}>{q.device || '—'}</small></td><td>{q.jobs_waiting}</td></tr>)}
          </tbody></table></div>}
        <div className="table-foot">Last 24 h: {Object.entries(printer.jobs_last_24h).map(([s, c]) => `${c} ${s}`).join(' · ') || 'no label jobs'}{printer.last_job && <> · last job {formatTime(printer.last_job.created_at)} ({printer.last_job.status})</>}. “Submitted” means accepted by the Mac’s print queue.</div>
      </section>
    </div>

    <section className="card" aria-label="Scan stations">
      <div className="card-heading compact"><div><h2>Scan stations</h2></div></div>
      <div className="table-wrap"><table className="station-health-table"><thead><tr><th>Station</th><th>Status</th><th>Scanner</th><th>Pi address</th><th>CPU</th><th>RAM</th><th>Temp</th><th>SD free</th><th>Last seen</th><th /></tr></thead><tbody>
        {report.stations.map(s => {
          const d = s.device && !s.device.stale ? s.device : null
          const temp = d?.temperature_c
          return [<tr key={s.id} data-testid={`health-station-${s.id}`}><td><b>{s.name}</b></td>
            <td><span className={'badge ' + STATION[s.status].badge}>{STATION[s.status].label}</span></td>
            <td>{s.status === 'never_seen' ? '—' : <>{s.scanner_ready ? 'Ready' : 'Not ready'}<small>{s.input === 'usb-bridge' ? 'Pi USB reader' : 'Keyboard capture'}</small></>}</td>
            <td>{s.ip ?? s.device?.ip ?? '—'}{s.live_clients.length > 1 && <small>{s.live_clients.length} devices: {s.live_clients.join(', ')}</small>}</td>
            {d ? <>
              <PiCell value={d.cpu_percent == null ? '—' : `${d.cpu_percent.toFixed(0)}%`} tone={tone(d.cpu_percent, 70, 90)} />
              <PiCell value={d.memory?.percent == null ? '—' : `${d.memory.percent.toFixed(0)}%`} tone={tone(d.memory?.percent, 80, 90)} />
              <PiCell value={temp == null ? '—' : `${temp.toFixed(0)} °C`} tone={tone(temp, 70, 80)} title={(d.throttled?.flags || []).map(f => FLAG_TEXT[f] || f).join(', ') || undefined} />
              <PiCell value={bytes(d.disk?.free_bytes)} tone={d.disk?.read_only ? 'bad' : tone(d.disk?.percent, 90, 95)} />
            </> : <td colSpan={4} className="pi-missing">{s.device?.stale ? `No Pi report for ${Math.round(s.device.age_seconds / 60)} min` : 'No Pi stats (reinstall this Pi to add them)'}</td>}
            <td>{formatTime(s.last_seen)}</td>
            <td>{s.device && <button className="button text" aria-expanded={open === s.id} onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? 'Hide' : 'Details'}</button>}</td></tr>,
          open === s.id && s.device && <tr key={s.id + '-detail'} className="pi-detail-row"><td colSpan={10}><PiDetail device={s.device} expected={application.version} /></td></tr>]
        })}
      </tbody></table></div>
    </section>
  </>
}
