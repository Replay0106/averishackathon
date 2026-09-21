import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { AuditBlock, CopilotReply, EmailDetail, EmailRow, Resolution, Summary } from './types'
import { clock } from './utils'

type Snapshot = { emails: EmailRow[]; summary: Summary; details: Record<string, EmailDetail> }
export interface Toast { id: number; title: string; body?: string; tone: 'ok' | 'info' | 'warn' | 'bad' }
export interface FeedEvent { id: number; time: string; text: string; tone: 'ok' | 'info' | 'warn' | 'bad' }
export interface DatasetInfo {
  id: string
  name: string
  kind: 'demo' | 'import' | 'gmail'
  created: string
  emails: number
  job: { status: 'ready' | 'processing'; done: number; total: number; failed: { id: string; error: string }[] }
  report: { emails?: number; bundle_emails?: number; eml_emails?: number; attachments?: number; skipped?: string[] }
}
export interface PickedFile { file: File; path: string }
export interface GmailStatus {
  configured: boolean
  configuration_error: string | null
  connected: boolean
  email: string | null
  label: string
  last_sync_at: string | null
  csrf_token: string | null
  dataset_id: string | null
}

let snapshotPromise: Promise<Snapshot> | null = null
const loadSnapshot = () => (snapshotPromise ??= import('../data/snapshot.json').then((m) => m.default as unknown as Snapshot))

async function api<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const ctl = new AbortController()
    const t = setTimeout(() => ctl.abort(), 6000)
    const res = await fetch(path, { credentials: 'same-origin', ...init, signal: ctl.signal })
    clearTimeout(t)
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

async function sha256(s: string) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

interface Ctx {
  ready: boolean
  loading: boolean
  live: boolean
  dataset: string
  datasets: DatasetInfo[]
  switchDataset: (id: string) => void
  importFolder: (files: PickedFile[], name: string, onProgress: (done: number, total: number) => void) => Promise<DatasetInfo>
  deleteDataset: (id: string) => Promise<void>
  gmail: GmailStatus | null
  connectGmail: () => void
  syncGmail: () => Promise<DatasetInfo>
  disconnectGmail: () => Promise<void>
  refreshGmail: () => Promise<GmailStatus | null>
  emails: EmailRow[]
  summary: Summary | null
  resolutions: Record<string, Resolution>
  audit: { blocks: AuditBlock[]; integrity: boolean }
  feed: FeedEvent[]
  toasts: Toast[]
  toast: (t: Omit<Toast, 'id'>) => void
  dismissToast: (id: number) => void
  getDetail: (id: string) => Promise<EmailDetail | null>
  act: (id: string, action: string, details?: Record<string, unknown>) => Promise<void>
  ask: (q: string) => Promise<CopilotReply>
  refreshAudit: () => Promise<void>
}

const C = createContext<Ctx>(null as never)
export const useApp = () => useContext(C)

const DEMO: DatasetInfo = { id: 'demo', name: 'SDOC demo inbox', kind: 'demo', created: '', emails: 0, job: { status: 'ready', done: 0, total: 0, failed: [] }, report: {} }

export function AppProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [loading, setLoading] = useState(false)
  const [live, setLive] = useState(false)
  const [dataset, setDataset] = useState('demo')
  const [datasets, setDatasets] = useState<DatasetInfo[]>([DEMO])
  const [gmailStatus, setGmailStatus] = useState<GmailStatus | null>(null)
  const [emails, setEmails] = useState<EmailRow[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [resolutions, setRes] = useState<Record<string, Resolution>>({})
  const [audit, setAudit] = useState<{ blocks: AuditBlock[]; integrity: boolean }>({ blocks: [], integrity: true })
  const [feed, setFeed] = useState<FeedEvent[]>([])
  const [toasts, setToasts] = useState<Toast[]>([])
  const details = useRef<Record<string, EmailDetail>>({})
  const local = useRef<AuditBlock[]>([])
  const seq = useRef(1)

  const withDs = useCallback((path: string, ds = dataset) => (ds === 'demo' ? path : `${path}${path.includes('?') ? '&' : '?'}ds=${encodeURIComponent(ds)}`), [dataset])

  const refreshAudit = useCallback(async () => {
    const r = await api<{ integrity: boolean; blocks: AuditBlock[] }>('/api/audit')
    if (r) setAudit({ blocks: r.blocks, integrity: r.integrity })
    else setAudit({ blocks: [...local.current].reverse(), integrity: true })
  }, [])

  const refreshDatasets = useCallback(async () => {
    const r = await api<DatasetInfo[]>('/api/datasets')
    setDatasets(r ?? [DEMO])
    return r
  }, [])

  const refreshGmail = useCallback(async () => {
    const status = await api<GmailStatus>('/api/gmail/status')
    setGmailStatus(status)
    return status
  }, [])

  useEffect(() => {
    let alive = true
    setLoading(true)
    details.current = {}
    ;(async () => {
      const [list, sum] = await Promise.all([api<EmailRow[]>(withDs('/api/emails')), api<Summary>(withDs('/api/summary'))])
      if (!alive) return
      if (list && sum) {
        setEmails(list)
        setSummary(sum)
        setLive(true)
        setRes(Object.fromEntries(list.filter((e) => e.resolution).map((e) => [e.id, e.resolution as Resolution])))
      } else if (dataset === 'demo') {
        const snap = await loadSnapshot()
        if (!alive) return
        setEmails(snap.emails)
        setSummary(snap.summary)
        setLive(false)
        setRes({})
      }
      if (!ready) {
        await Promise.all([refreshAudit(), refreshDatasets(), refreshGmail()])
      }
      setReady(true)
      setLoading(false)
    })()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset])

  const toast = useCallback((t: Omit<Toast, 'id'>) => {
    const id = seq.current++
    setToasts((x) => [...x.slice(-3), { ...t, id }])
    setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), 4600)
  }, [])
  const dismissToast = useCallback((id: number) => setToasts((x) => x.filter((y) => y.id !== id)), [])

  const pushFeed = useCallback((text: string, tone: FeedEvent['tone'] = 'info') => {
    setFeed((f) => [{ id: seq.current++, time: clock(), text, tone }, ...f].slice(0, 14))
  }, [])

  // Simulated live operations feed, driven by real shipments in the inbox.
  useEffect(() => {
    if (!emails.length) {
      setFeed([])
      return
    }
    const comps = emails.filter((e) => e.category === 'BL_COMPARISON')
    if (!comps.length) return
    const pick = () => comps[Math.floor(Math.random() * comps.length)]
    const templates: Array<(e: EmailRow) => [string, FeedEvent['tone']]> = [
      (e) => [`SI received — ${e.shipment}`, 'info'],
      (e) => [`BL extracted successfully — ${e.shipment}`, 'info'],
      (e) => [e.status === 'OK' ? `Document verification completed — ${e.shipment}` : `Discrepancy flagged — ${e.shipment}`, e.status === 'OK' ? 'ok' : e.status === 'MISMATCH' ? 'bad' : 'warn'],
      (e) => [`Field comparison finished (7/7) — ${e.shipment}`, 'ok'],
    ]
    const seed = comps.slice(0, 4).reverse()
    setFeed(seed.map((e, i) => ({ id: seq.current++, time: clock(new Date(Date.now() - (4 - i) * 7000)), text: templates[i % 4](e)[0], tone: templates[i % 4](e)[1] })).reverse())
    let n = 0
    const t = setInterval(() => {
      const [text, tone] = templates[n++ % templates.length](pick())
      pushFeed(text, tone)
    }, 5200)
    return () => clearInterval(t)
  }, [emails, pushFeed])

  const getDetail = useCallback(
    async (id: string) => {
      if (details.current[id]) return details.current[id]
      let d = await api<EmailDetail>(withDs(`/api/emails/${id}`))
      if (!d && dataset === 'demo') d = (await loadSnapshot()).details[id] ?? null
      if (d) details.current[id] = d
      return d
    },
    [withDs, dataset],
  )

  const act = useCallback(
    async (id: string, action: string, det: Record<string, unknown> = {}) => {
      const at = new Date().toISOString()
      const res = await api<{ block: AuditBlock }>(withDs(`/api/emails/${id}/action`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(dataset.startsWith('gmail_') && gmailStatus?.csrf_token ? { 'X-CSRF-Token': gmailStatus.csrf_token } : {}),
        },
        body: JSON.stringify({ actor: 'Operations Desk', action, details: det }),
      })
      let block = res?.block
      if (!block) {
        const prev = local.current.at(-1)?.block_hash ?? '0'.repeat(64)
        const index = local.current.length + 1
        const payload = `${index}:${at}:Operations Desk:${id}:${action}:${JSON.stringify(det)}:${prev}`
        block = { index, timestamp: at, actor: 'Operations Desk', email_id: id, action, details: det, previous_hash: prev, block_hash: await sha256(payload) }
        local.current.push(block)
      }
      setRes((r) => ({ ...r, [id]: { action, at, block: block!.index, ...det } }))
      await refreshAudit()
      const row = emails.find((e) => e.id === id)
      pushFeed(`${action.replace(/_/g, ' ').toLowerCase()} — ${row?.shipment ?? id}`, 'ok')
    },
    [dataset, emails, gmailStatus?.csrf_token, pushFeed, refreshAudit, withDs],
  )

  const ask = useCallback(
    async (q: string): Promise<CopilotReply> => {
      const r = await api<CopilotReply>(withDs(`/api/copilot?q=${encodeURIComponent(q)}`))
      if (r) return r
      const m = q.match(/SHP-\d{4}/i)
      const row = m && emails.find((e) => e.shipment.toUpperCase() === m[0].toUpperCase())
      if (!row) return { kind: 'help', message: "Ask about a shipment, e.g. 'Why is SHP-2048 flagged?'" }
      const d = await getDetail(row.id)
      if (!d || d.category !== 'BL_COMPARISON') return { kind: 'not_comparison', shipment: row.shipment, category: row.category, message: `${row.shipment} is classified ${row.category}; no SI/BL comparison applies.` }
      if (d.status === 'NEEDS_REVIEW') return { kind: 'review', shipment: row.shipment, email: row.id, reason: d.review_reason ?? '', confidence: d.confidence, recommendation: 'Escalate to a human reviewer.' }
      const bad = d.comparison.filter((c) => !c.match && !c.missing)
      if (!bad.length) return { kind: 'clear', shipment: row.shipment, email: row.id, message: 'No mismatch detected. All seven fields match between SI and draft BL.' }
      return { kind: 'mismatch', shipment: row.shipment, email: row.id, issues: bad, evidence: d.attachments, recommendation: 'Request corrected draft BL from carrier.' }
    },
    [emails, getDetail, withDs],
  )

  const switchDataset = useCallback((id: string) => {
    setDataset((cur) => {
      if (cur !== id) window.location.hash = '/overview'
      return id
    })
  }, [])

  const importFolder = useCallback(
    async (files: PickedFile[], name: string, onProgress: (done: number, total: number) => void) => {
      const form = new FormData()
      form.append('name', name)
      form.append('paths', JSON.stringify(files.map((f) => f.path)))
      files.forEach((f) => form.append('files', f.file, f.file.name))
      let res: Response
      try {
        res = await fetch('/api/datasets/import', { method: 'POST', body: form })
      } catch {
        throw new Error('Cannot reach the NavisAI API. Start it with: uvicorn api.main:app --port 8000')
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `Import failed (${res.status})`)
      }
      let info = (await res.json()) as DatasetInfo
      onProgress(info.job.done, info.job.total)
      while (info.job.status === 'processing') {
        await new Promise((r) => setTimeout(r, 500))
        const next = await api<DatasetInfo>(`/api/datasets/${info.id}`)
        if (!next) throw new Error('Lost connection to the API while processing')
        info = next
        onProgress(info.job.done, info.job.total)
      }
      await refreshDatasets()
      return info
    },
    [refreshDatasets],
  )

  const deleteDataset = useCallback(
    async (id: string) => {
      await fetch(`/api/datasets/${id}`, { method: 'DELETE' })
      await refreshDatasets()
      if (dataset === id) switchDataset('demo')
    },
    [dataset, refreshDatasets, switchDataset],
  )

  const connectGmail = useCallback(() => {
    window.location.assign('/api/gmail/connect')
  }, [])

  const syncGmail = useCallback(async () => {
    if (!gmailStatus?.csrf_token) throw new Error('Connect Gmail before syncing')
    const res = await fetch('/api/gmail/sync', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': gmailStatus.csrf_token },
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail ?? `Gmail sync failed (${res.status})`)
    }
    let info = (await res.json()) as DatasetInfo
    while (info.job.status === 'processing') {
      await new Promise((resolve) => setTimeout(resolve, 500))
      const next = await api<DatasetInfo>(`/api/datasets/${info.id}`)
      if (!next) throw new Error('Lost connection while processing Gmail messages')
      info = next
    }
    await Promise.all([refreshDatasets(), refreshGmail()])
    switchDataset(info.id)
    return info
  }, [gmailStatus?.csrf_token, refreshDatasets, refreshGmail, switchDataset])

  const disconnectGmail = useCallback(async () => {
    if (!gmailStatus?.csrf_token) return
    const res = await fetch('/api/gmail/disconnect', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': gmailStatus.csrf_token },
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail ?? `Could not disconnect Gmail (${res.status})`)
    }
    if (dataset.startsWith('gmail_')) switchDataset('demo')
    await Promise.all([refreshDatasets(), refreshGmail()])
  }, [dataset, gmailStatus?.csrf_token, refreshDatasets, refreshGmail, switchDataset])

  const value = useMemo(
    () => ({ ready, loading, live, dataset, datasets, switchDataset, importFolder, deleteDataset, gmail: gmailStatus, connectGmail, syncGmail, disconnectGmail, refreshGmail, emails, summary, resolutions, audit, feed, toasts, toast, dismissToast, getDetail, act, ask, refreshAudit }),
    [ready, loading, live, dataset, datasets, switchDataset, importFolder, deleteDataset, gmailStatus, connectGmail, syncGmail, disconnectGmail, refreshGmail, emails, summary, resolutions, audit, feed, toasts, toast, dismissToast, getDetail, act, ask, refreshAudit],
  )
  return <C.Provider value={value}>{children}</C.Provider>
}
