import { AnimatePresence, motion } from 'framer-motion'
import { RadioTower, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Page } from '@/lib/nav'
import { Badge, Button, Empty, PageHeader, Panel, SectionTitle } from '@/components/ui'
import { resolveApiPath, useApp } from '@/lib/store'
import { cn, sleep } from '@/lib/utils'

const REVEAL_STEP_MS = 220

type GateStep = { gate: number; name: string; passed: boolean; reason: string }
type Outcome = 'accepted' | 'rejected' | 'held'
type Decision = {
  accepted: boolean
  email_id: string
  domain: string
  failed_gate: string | null
  reason: string
  trust_score_after: number | null
  record_id: number | null
  resolved_hold: { email_id: string; outcome: string; record_id?: number } | null
  trace: GateStep[]
  outcome: Outcome
}
type DomainTag = 'known' | 'suspicious' | 'unclassified'
type DomainState = {
  domain: string
  tag: DomainTag
  trust: number
  revoked: boolean
  accepted_count: number
  quarantine_count: number
  last_email_id: string | null
  last_subject: string | null
  has_last: boolean
}
type LedgerSummary = { total_committed: number; pending_flush: number; batches: number; last_root: string | null }
type LogEntry = { id: number; domain: string | null; kind: 'pass' | 'fail' | 'hold'; text: string }
type GwState = { domains: DomainState[]; ledger: LedgerSummary; log: LogEntry[]; accepted_count: number; quarantine_count: number; bootstrapped: boolean }
type LedgerRecord = { record_id: number; payload: Record<string, unknown>; batch_index: number | null }

const GATE_DEFS: [string, string][] = [
  ['Rate limit (domain)', 'flood protection, before real work'],
  ['Structural parse', 'malformed intake record check'],
  ['Sender trust', 'continuous score, not a binary flag'],
  ['Correspondence check', 'first-time vs. known sender — informational'],
  ['Authentication', "NavisAI's real spoofing / domain-reputation heuristics"],
  ['Adaptive rate limit', 'budget scales with trust score'],
  ['Duplicate check', 'blocks resubmission of an already-processed email'],
  ['Extraction integrity', "did NavisAI's own pipeline actually succeed"],
  ['Discrepancy plausibility', 'a single mismatch is held, not instant fraud'],
  ['Commit + Merkle proof', 'provable against a published root'],
]

const TAG_LABEL: Record<DomainTag, string> = { known: 'known correspondent', suspicious: 'suspicious pattern', unclassified: 'unclassified' }

type VerifyDetail = { verified: boolean; root?: string; leaf_hash?: string; proof?: { side: string; hash: string }[] }
type ChainStep = { label: string; hash: string }

async function api<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(resolveApiPath(path), init)
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

// Recomputes the same SHA-256 Merkle chain the Python ledger uses, live in
// the browser — this is a real independent computation, not a replay of
// what the server said. See security_layer/ledger.py: _node_hash(l, r) =
// sha256(b"node:" + l + r) over the raw bytes.
function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2)
  for (let i = 0; i < hex.length; i += 2) out[i / 2] = parseInt(hex.slice(i, i + 2), 16)
  return out
}
function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
}
async function nodeHashHex(leftHex: string, rightHex: string): Promise<string> {
  const prefix = new TextEncoder().encode('node:')
  const left = hexToBytes(leftHex)
  const right = hexToBytes(rightHex)
  const buf = new Uint8Array(prefix.length + left.length + right.length)
  buf.set(prefix, 0)
  buf.set(left, prefix.length)
  buf.set(right, prefix.length + left.length)
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return bytesToHex(new Uint8Array(digest))
}
async function buildChain(detail: VerifyDetail): Promise<{ steps: ChainStep[]; matches: boolean }> {
  const steps: ChainStep[] = [{ label: "This record's own fingerprint", hash: detail.leaf_hash ?? '' }]
  let current = detail.leaf_hash ?? ''
  for (const p of detail.proof ?? []) {
    current = p.side === 'R' ? await nodeHashHex(current, p.hash) : await nodeHashHex(p.hash, current)
    steps.push({ label: `Combined with a neighboring record's fingerprint (${p.side === 'R' ? 'added on the right' : 'added on the left'})`, hash: current })
  }
  return { steps, matches: current === detail.root }
}

export default function Gateway({ go }: { go: (p: Page, id?: string) => void }) {
  const { emails } = useApp()
  const openEmail = useCallback((emailId: string | null) => { if (emailId) go('inbox', emailId) }, [go])

  const [state, setState] = useState<GwState | null>(null)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [revealCount, setRevealCount] = useState(0)
  const [animating, setAnimating] = useState(false)
  const revealRun = useRef(0)
  const [records, setRecords] = useState<LedgerRecord[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [verifyState, setVerifyState] = useState<Record<number, 'ok' | 'bad' | undefined>>({})
  const [expandedRecord, setExpandedRecord] = useState<number | null>(null)
  const [chainSteps, setChainSteps] = useState<ChainStep[]>([])
  const [chainRoot, setChainRoot] = useState('')
  const [chainMatches, setChainMatches] = useState(false)
  const [chainReveal, setChainReveal] = useState(0)
  const chainRun = useRef(0)

  const refresh = useCallback(async () => {
    const s = await api<GwState>('/api/gateway/state')
    if (s) setState(s)
  }, [])

  const refreshLedger = useCallback(async () => {
    const r = await api<{ records: LedgerRecord[]; summary: LedgerSummary }>('/api/gateway/ledger')
    if (r) setRecords(r.records)
  }, [])

  useEffect(() => {
    refresh()
    refreshLedger()
    const t = window.setInterval(refresh, 4000)
    return () => window.clearInterval(t)
  }, [refresh, refreshLedger])

  const resubmit = useCallback(
    async (domain: string, mode: 'replay' | 'flood' | 'send_test') => {
      setBusy(domain)
      try {
        const r = await api<{ decision: Decision; state: GwState }>('/api/gateway/resubmit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ domain, mode }),
        })
        if (r) {
          setState(r.state)
          setDecision(r.decision)
          const me = ++revealRun.current
          setAnimating(true)
          setRevealCount(0)
          for (let i = 1; i <= r.decision.trace.length; i++) {
            await sleep(REVEAL_STEP_MS)
            if (revealRun.current !== me) return
            setRevealCount(i)
          }
          if (revealRun.current === me) setAnimating(false)
        }
        await refreshLedger()
      } finally {
        setBusy(null)
      }
    },
    [refreshLedger],
  )

  const flush = useCallback(async () => {
    const r = await api<{ batch: unknown; state: GwState }>('/api/gateway/flush', { method: 'POST' })
    if (r) setState(r.state)
    await refreshLedger()
  }, [refreshLedger])

  const verify = useCallback(async (id: number, tampered = false) => {
    const r = await api<VerifyDetail>(`/api/gateway/ledger/verify${tampered ? '-tampered' : ''}/${id}`)
    if (!r) return
    const { steps, matches } = await buildChain(r)
    setExpandedRecord(id)
    setChainSteps(steps)
    setChainRoot(r.root ?? '')
    setChainMatches(matches)
    setVerifyState((v) => ({ ...v, [id]: matches ? 'ok' : 'bad' }))
    const me = ++chainRun.current
    setChainReveal(0)
    for (let i = 1; i <= steps.length + 1; i++) {
      await sleep(REVEAL_STEP_MS)
      if (chainRun.current !== me) return
      setChainReveal(i)
    }
  }, [])

  const reset = useCallback(async () => {
    revealRun.current++
    await api('/api/gateway/reset', { method: 'POST' })
    await refresh()
    await refreshLedger()
    setDecision(null)
    setRevealCount(0)
    setAnimating(false)
    setVerifyState({})
    chainRun.current++
    setExpandedRecord(null)
    setChainSteps([])
    setChainReveal(0)
  }, [refresh, refreshLedger])

  if (!state) {
    return (
      <Empty
        icon={<ShieldCheck className="size-6" />}
        title="Connecting to the gateway…"
        sub="Start the API with: uvicorn api.main:app --reload --port 8000"
      />
    )
  }

  const outcomeLabel = (d: Decision) => {
    if (d.outcome === 'accepted') return d.record_id != null ? `COMMITTED — record #${d.record_id}` : 'WOULD COMMIT — drill only, not written to the ledger'
    if (d.outcome === 'held') return 'HELD — awaiting the next email from this sender'
    return `QUARANTINED — ${d.failed_gate}`
  }

  return (
    <div>
      <PageHeader
        title="Trust Gateway"
        sub="NavisAI's real email-intake cybersecurity layer — every one of the 520 emails in the demo inbox has actually been run through this 10-gate pipeline. Classification (elsewhere in NavisAI) reads what an email is about; this decides whether the email and its sender can be trusted at all before that content is acted on."
        right={
          <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={reset}>
            Reset &amp; re-bootstrap
          </Button>
        }
      />

      <Panel className="mb-6 overflow-hidden">
        <div className="border-b border-white/[0.07] px-6 py-4">
          <SectionTitle
            eyebrow="Real sender domains"
            title={`${state.accepted_count} emails committed · ${state.quarantine_count} quarantined`}
            right={<span className="text-[11px] text-ink3">counts are the real inbox only — drills below never change them</span>}
          />
        </div>
        <div className="divide-y divide-white/[0.06]">
          {state.domains.map((d) => (
            <div key={d.domain} className="flex flex-wrap items-center gap-3 px-6 py-3">
              <button
                onClick={() => openEmail(d.last_email_id)}
                disabled={!d.last_email_id}
                className="w-44 shrink-0 text-left transition enabled:hover:text-sky disabled:opacity-60"
                title={d.last_subject ?? undefined}
              >
                <span className="num block text-[13px]">{d.domain}</span>
                <span className="block text-[11px] text-ink3">{TAG_LABEL[d.tag]}</span>
              </button>
              <Badge tone={d.trust < 20 ? 'bad' : d.trust < 50 ? 'warn' : 'ok'} dot>
                {d.trust}
              </Badge>
              <span className="w-32 shrink-0 text-[11px] text-ink3">
                {d.accepted_count} ok &middot; {d.quarantine_count} blocked
              </span>
              <div className="ml-auto flex flex-wrap gap-1.5">
                <Button size="sm" variant="primary" loading={busy === d.domain} onClick={() => resubmit(d.domain, 'send_test')}>
                  Send test email
                </Button>
                <Button size="sm" variant="ghost" disabled={!d.has_last} loading={busy === d.domain} onClick={() => resubmit(d.domain, 'replay')}>
                  Replay drill
                </Button>
                <Button size="sm" variant="ghost" disabled={!d.has_last} loading={busy === d.domain} onClick={() => resubmit(d.domain, 'flood')}>
                  Flood drill
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="mb-6 grid gap-6 xl:grid-cols-[1.15fr_1fr]">
        <Panel className="p-5">
          <SectionTitle eyebrow="Live" title="Gate-by-gate result" />
          {decision && (
            <div className="num mb-3 overflow-x-auto whitespace-pre rounded-md border border-white/10 bg-black/20 px-3 py-2 text-[11px] text-ink3">
              {decision.email_id} · {decision.domain}
            </div>
          )}
          {!decision && <Empty icon={<RadioTower className="size-6" />} title="Send a test email or run a drill to see it here" />}
          {decision && (
            <>
              <ol className="mb-3 space-y-1.5">
                {GATE_DEFS.map(([name, sub], i) => {
                  const reached = i < decision.trace.length
                  const revealed = i < revealCount
                  const isActive = i === revealCount && animating && reached
                  const step = revealed ? decision.trace[i] : undefined
                  const tone: 'ok' | 'bad' | 'warn' | 'active' | 'idle' = revealed
                    ? step!.passed
                      ? 'ok'
                      : decision.outcome === 'held' && i === 8
                        ? 'warn'
                        : 'bad'
                    : isActive
                      ? 'active'
                      : 'idle'
                  const statusWord = revealed ? (step!.passed ? 'pass' : 'fail') : isActive ? 'checking…' : reached ? 'pending' : 'skipped'
                  return (
                    <li
                      key={i}
                      className={cn(
                        'flex items-center gap-3 rounded-lg border px-3 py-2 transition-colors duration-200',
                        tone === 'ok' && 'border-ok/30 bg-ok/[0.06]',
                        tone === 'bad' && 'border-bad/30 bg-bad/[0.06]',
                        tone === 'warn' && 'border-warn/30 bg-warn/[0.06]',
                        tone === 'active' && 'border-sky/40 bg-sky/[0.08] animate-pulse',
                        tone === 'idle' && 'border-white/[0.06] opacity-40',
                      )}
                    >
                      <span className="num w-7 shrink-0 rounded bg-white/[0.07] py-0.5 text-center text-[10px] font-bold">G{i}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] font-medium">{name}</span>
                        <span className="block truncate text-[11px] text-ink3">{step?.reason ?? sub}</span>
                      </span>
                      <span className={cn('shrink-0 text-[10px] uppercase tracking-wide', tone === 'active' ? 'text-sky' : 'text-ink3')}>{statusWord}</span>
                    </li>
                  )
                })}
              </ol>
              {revealCount >= decision.trace.length && (
                <>
                  <div
                    className={cn(
                      'rounded-lg border px-3 py-2 text-center text-[13px] font-semibold',
                      decision.outcome === 'accepted' && 'border-ok/30 bg-ok/10 text-emerald-300',
                      decision.outcome === 'rejected' && 'border-bad/30 bg-bad/10 text-red-300',
                      decision.outcome === 'held' && 'border-warn/30 bg-warn/10 text-amber-300',
                    )}
                  >
                    {outcomeLabel(decision)}
                  </div>
                  {decision.resolved_hold && (
                    <div className="mt-2 text-[11px] text-ink3">
                      Also resolved an earlier hold: {decision.resolved_hold.email_id} → {decision.resolved_hold.outcome}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </Panel>

        <Panel className="p-5">
          <SectionTitle
            eyebrow="Live"
            title="Event log"
            right={
              <span className="flex items-center gap-1.5 text-[11px] text-ink3">
                <span className="breathe size-1.5 rounded-full bg-ok" /> polling
              </span>
            }
          />
          <ul className="max-h-[420px] space-y-0.5 overflow-y-auto">
            {state.log.length === 0 && <Empty icon={<RadioTower className="size-6" />} title="Nothing logged yet" />}
            <AnimatePresence initial={false}>
              {[...state.log].reverse().map((l) => (
                <motion.li key={l.id} layout initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="flex items-start gap-2.5 py-1.5">
                  <span className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', l.kind === 'pass' && 'bg-ok', l.kind === 'fail' && 'bg-bad', l.kind === 'hold' && 'bg-warn')} />
                  <span className="text-[12px] text-ink2">
                    <span className="num text-ink3">{l.domain ?? '—'}</span> — {l.text}
                  </span>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </Panel>
      </div>

      <Panel className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/[0.07] px-6 py-4">
          <SectionTitle eyebrow="Provable" title="Audit ledger" />
          <Button size="sm" variant="primary" onClick={flush}>
            Flush batch
          </Button>
        </div>
        <div className="border-b border-white/[0.07] px-6 py-3 text-[12px] text-ink3">
          <b className="text-ink">{state.ledger.total_committed}</b> total committed &middot; <b className="text-ink">{state.ledger.pending_flush}</b> pending flush &middot;{' '}
          <b className="text-ink">{state.ledger.batches}</b> batch(es) anchored
          {state.ledger.last_root && <span className="num ml-2">root {state.ledger.last_root.slice(0, 20)}…</span>}
        </div>
        <div className="grid grid-cols-[70px_140px_100px_100px_70px_minmax(0,1fr)] gap-4 border-b border-white/[0.07] px-6 py-2 max-lg:hidden">
          {['Record', 'Email', 'Domain', 'Status', 'Batch', 'Proof'].map((h) => (
            <div key={h} className="eyebrow">{h}</div>
          ))}
        </div>
        {records.length === 0 && <Empty icon={<ShieldCheck className="size-6" />} title="No committed records yet" />}
        {records.slice(0, 25).map((r) => (
          <div key={r.record_id} className="border-b border-white/[0.05] last:border-0">
          <div
            className="grid grid-cols-[70px_140px_100px_100px_70px_minmax(0,1fr)] items-center gap-4 px-6 py-2.5 max-lg:grid-cols-1"
          >
            <span className="num text-[12px]">#{r.record_id}</span>
            <span className="num truncate text-[12px]">{String(r.payload.email_id)}</span>
            <span className="num truncate text-[12px] text-ink3">{String(r.payload.domain)}</span>
            <span className="num text-[12px]">{String(r.payload.status)}</span>
            <span className="num text-[12px]">{r.batch_index == null ? 'pending' : `#${r.batch_index}`}</span>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" disabled={r.batch_index == null} onClick={() => verify(r.record_id)}>
                Verify
              </Button>
              <Button size="sm" variant="ghost" disabled={r.batch_index == null} onClick={() => verify(r.record_id, true)}>
                Tamper &amp; reverify
              </Button>
              {verifyState[r.record_id] === 'ok' && <Badge tone="ok" dot>verified</Badge>}
              {verifyState[r.record_id] === 'bad' && <Badge tone="bad" dot>rejected</Badge>}
            </div>
          </div>
          {expandedRecord === r.record_id && chainSteps.length > 0 && (
            <div className="mx-6 mb-4 rounded-lg border border-white/10 bg-black/20 p-4">
              <div className="mb-3 text-[11px] text-ink3">
                Recomputed live in your browser, independently of the server — SHA-256, the same fingerprint function run twice. If even one character of the original record changed, every fingerprint from that point on comes out completely different.
              </div>
              <div className="space-y-0">
                {chainSteps.map((s, i) => {
                  const revealed = i < chainReveal
                  return (
                    <div key={i} className={cn('transition-opacity duration-300', revealed ? 'opacity-100' : 'opacity-0')}>
                      <div className="flex items-start gap-3">
                        <span className="num mt-0.5 w-6 shrink-0 rounded bg-white/[0.07] py-0.5 text-center text-[10px] font-bold text-ink3">{i}</span>
                        <div className="min-w-0 flex-1">
                          <div className="text-[11px] text-ink3">{s.label}</div>
                          <div className="num truncate text-[12px] text-ink" title={s.hash}>{s.hash}</div>
                        </div>
                      </div>
                      {i < chainSteps.length - 1 && <div className="ml-3 h-4 w-px bg-white/10" />}
                    </div>
                  )
                })}
              </div>
              <div className={cn('mt-3 flex items-start gap-3 border-t border-white/10 pt-3 transition-opacity duration-300', chainReveal > chainSteps.length ? 'opacity-100' : 'opacity-0')}>
                <span className={cn('mt-0.5 w-6 shrink-0 rounded py-0.5 text-center text-[10px] font-bold', chainMatches ? 'bg-ok/20 text-ok' : 'bg-bad/20 text-bad')}>=</span>
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] text-ink3">Published root {chainMatches ? '— matches what your browser just computed' : '— does NOT match: this record is forged'}</div>
                  <div className={cn('num truncate text-[12px]', chainMatches ? 'text-emerald-300' : 'text-red-300')} title={chainRoot}>{chainRoot}</div>
                </div>
                <Badge tone={chainMatches ? 'ok' : 'bad'} dot>{chainMatches ? 'verified' : 'forged'}</Badge>
              </div>
            </div>
          )}
          </div>
        ))}
        {records.length > 25 && <div className="px-6 py-3 text-center text-[11px] text-ink3">showing 25 of {records.length} committed records</div>}
      </Panel>
    </div>
  )
}
