import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Check, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, PageHeader, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { resolveApiPath, useApp } from '@/lib/store'
import type { EmailDetail } from '@/lib/types'
import { cn, sleep } from '@/lib/utils'

interface CheckResult { name: string; pass: boolean; detail: string }

interface SenderSecurityReport {
  checked: boolean
  accepted?: boolean
  domain?: string
  failed_gate?: string | null
  reason?: string
  trust_score_after?: number | null
  outcome?: 'accepted' | 'rejected' | 'held'
}

async function fetchSenderSecurity(emailId: string): Promise<SenderSecurityReport> {
  try {
    const res = await fetch(resolveApiPath(`/api/gateway/email-check/${encodeURIComponent(emailId)}`))
    if (!res.ok) return { checked: false }
    return (await res.json()) as SenderSecurityReport
  } catch {
    return { checked: false }
  }
}

function senderSecurityCheck(r: SenderSecurityReport): CheckResult | null {
  if (!r.checked) return null
  if (r.outcome === 'accepted') {
    return {
      name: 'Sender Security (Trust Gateway)',
      pass: true,
      detail: `${r.domain} cleared all 10 gates — authenticated, not a duplicate, trust score ${r.trust_score_after}. Committed to the provable audit ledger.`,
    }
  }
  if (r.outcome === 'held') {
    return {
      name: 'Sender Security (Trust Gateway)',
      pass: false,
      detail: `${r.domain}'s document mismatch was held for corroboration rather than committed outright — awaiting the next email from this sender to confirm or dismiss the pattern.`,
    }
  }
  return {
    name: 'Sender Security (Trust Gateway)',
    pass: false,
    detail: `${r.domain} was quarantined at gate "${r.failed_gate}": ${r.reason}. This email never reached the audit ledger.`,
  }
}

function evaluate(d: EmailDetail): CheckResult[] {
  const row = (k: string) => d.comparison.find((c) => c.key === k)
  const ok = (...ks: string[]) => ks.every((k) => row(k)?.match)
  const text = `${d.si?.raw_text ?? ''}\n${d.bl?.raw_text ?? ''}`
  const dg = /dangerous goods|\bIMDG\b|\bIMO\s?class|\bUN\s?\d{4}\b|hazardous/i.test(text)
  const hs = (t?: string) => t?.match(/HS\s*Code[^:\n]*:\s*([0-9.]{4,})/i)?.[1]?.replace(/\./g, '')
  const hsSi = hs(d.si?.raw_text)
  const hsBl = hs(d.bl?.raw_text)
  const w = row('gross_weight_kg')
  return [
    { name: 'Documentary Consistency', pass: ok('shipper', 'consignee', 'notify_party'), detail: 'Shipper, consignee and notify party agree' },
    { name: 'Shipment Data Consistency', pass: ok('port_of_loading', 'port_of_discharge'), detail: 'Load and discharge ports agree' },
    { name: 'Weight Validation', pass: !!w?.match && Number(w.si) > 0, detail: 'Gross weight agrees between SI and BL' },
    { name: 'Container Validation', pass: ok('container_count'), detail: 'Container count agrees' },
    { name: 'Dangerous Goods Check', pass: !dg, detail: dg ? 'Dangerous-goods markers found in documents' : 'No IMDG / UN-number markers detected' },
    { name: 'Customs Data Check', pass: !!hsSi && (!hsBl || hsBl === hsSi), detail: hsSi ? (hsBl && hsBl !== hsSi ? 'HS code differs between documents' : `HS code ${hsSi} present`) : 'No HS code found on the instruction' },
  ]
}

export default function Compliance({ go, id }: { go: (p: Page, id?: string) => void; id?: string }) {
  const { emails, getDetail } = useApp()
  const comps = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON' && e.status !== 'NEEDS_REVIEW'), [emails])
  const cur = id && comps.some((c) => c.id === id) ? id : comps.find((c) => c.status === 'OK')?.id ?? comps[0]?.id
  const [d, setD] = useState<EmailDetail | null>(null)
  const [checks, setChecks] = useState<CheckResult[]>([])
  const [n, setN] = useState(0)
  const run = useRef(0)

  const start = useCallback(async (detail: EmailDetail) => {
    const me = ++run.current
    const [docChecks, sender] = await Promise.all([evaluate(detail), fetchSenderSecurity(detail.id)])
    if (run.current !== me) return
    const sc = senderSecurityCheck(sender)
    const c = sc ? [...docChecks, sc] : docChecks
    setChecks(c)
    setN(0)
    for (let i = 1; i <= c.length; i++) {
      await sleep(520)
      if (run.current !== me) return
      setN(i)
    }
  }, [])

  useEffect(() => {
    if (!cur) return
    let a = true
    getDetail(cur).then((x) => {
      if (!a || !x) return
      setD(x)
      start(x)
    })
    return () => {
      a = false
      run.current++
    }
  }, [cur, getDetail, start])

  const done = checks.length > 0 && n === checks.length
  const fails = checks.filter((c) => !c.pass).length
  const clear = done && fails === 0
  const R = 92
  const C = 2 * Math.PI * R
  const passed = checks.slice(0, n).filter((c) => c.pass).length

  return (
    <div>
      <PageHeader
        title="Compliance Gate"
        sub="A final, high-confidence gate before documents are released — document checks plus the real Trust Gateway verdict on the sender who sent them."
        right={
          <div className="flex items-center gap-2">
            <select value={cur} onChange={(e) => go('compliance', e.target.value)} className="h-9 max-w-[280px] rounded-lg border border-white/12 bg-card px-3 text-[13px] outline-none">
              {comps.map((c) => <option key={c.id} value={c.id}>{c.shipment} · {c.status === 'OK' ? 'Verified' : 'Discrepancy'}</option>)}
            </select>
            <Button icon={<RotateCcw className="size-3.5" />} onClick={() => d && start(d)}>Re-run</Button>
          </div>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_1.1fr]">
        <div className="panel grid place-items-center px-6 py-12">
          <div className="relative grid size-[240px] place-items-center">
            {clear && <span className="ring-pulse absolute size-[200px] rounded-full border border-ok/40" />}
            <svg width="240" height="240" viewBox="0 0 240 240" className="-rotate-90">
              <circle cx="120" cy="120" r={R} fill="none" stroke="rgba(148,163,184,0.12)" strokeWidth="6" />
              <motion.circle
                cx="120" cy="120" r={R} fill="none" strokeWidth="6" strokeLinecap="round"
                stroke={done ? (clear ? '#10B981' : '#F59E0B') : '#38BDF8'}
                strokeDasharray={C}
                initial={false}
                animate={{ strokeDashoffset: C * (1 - (checks.length ? n / checks.length : 0)) }}
                transition={{ duration: 0.5, ease }}
              />
            </svg>
            <div className="absolute text-center">
              <AnimatePresence mode="wait">
                <motion.div key={done ? (clear ? 'c' : 'r') : 'p'} initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }}>
                  {done ? (
                    <>
                      <div className={cn('mx-auto mb-2 grid size-9 place-items-center rounded-full', clear ? 'bg-ok/15 text-ok' : 'bg-warn/15 text-warn')}>
                        {clear ? <Check className="size-5" strokeWidth={2.6} /> : <AlertTriangle className="size-5" />}
                      </div>
                      <div className={cn('text-[13px] font-semibold uppercase leading-tight tracking-[0.1em]', clear ? 'text-emerald-300' : 'text-amber-300')}>
                        {clear ? <>Documentation<br />clear</> : <>Review<br />required</>}
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="num text-3xl font-semibold">{n}<span className="text-ink3">/{checks.length || 6}</span></div>
                      <div className="eyebrow mt-1">Checks run</div>
                    </>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
          {d && <div className="num mt-6 text-xs text-ink3">{d.shipment} · {passed} passed{done && fails ? ` · ${fails} need attention` : ''}</div>}
        </div>

        <div className="panel divide-y divide-white/[0.06] overflow-hidden">
          {(checks.length ? checks : Array.from({ length: 6 }, (_, i) => ({ name: `Check ${i + 1}`, pass: true, detail: '' }))).map((c, i) => {
            const shown = i < n
            const running = i === n && !done && checks.length > 0
            return (
              <div key={c.name} className="flex items-center gap-4 px-5 py-4">
                <div className={cn('grid size-8 shrink-0 place-items-center rounded-full border transition-colors duration-300', shown ? (c.pass ? 'border-ok/40 bg-ok/12 text-ok' : 'border-warn/40 bg-warn/12 text-warn') : running ? 'border-sky/50 text-sky' : 'border-white/10 text-ink3')}>
                  <AnimatePresence mode="wait" initial={false}>
                    {shown ? (
                      <motion.span key="r" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 500, damping: 20 }}>
                        {c.pass ? <Check className="size-4" strokeWidth={2.6} /> : <X className="size-4" strokeWidth={2.6} />}
                      </motion.span>
                    ) : (
                      <motion.span key="p" className={cn('size-1.5 rounded-full bg-current', running && 'breathe')} />
                    )}
                  </AnimatePresence>
                </div>
                <div className="min-w-0 flex-1">
                  <div className={cn('text-[13.5px] font-medium transition-colors', shown ? 'text-ink' : 'text-ink3')}>{c.name}</div>
                  <AnimatePresence>
                    {shown && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} className="text-xs text-ink3">{c.detail}</motion.div>}
                  </AnimatePresence>
                </div>
              </div>
            )
          })}
          {done && !clear && d?.status === 'MISMATCH' && (
            <div className="flex items-center justify-between bg-warn/[0.05] px-5 py-3.5">
              <span className="text-xs text-amber-200">Resolve the discrepancy before release.</span>
              <Button size="sm" variant="primary" onClick={() => go('cases', d.id)}>Open case</Button>
            </div>
          )}
          {done && !clear && d?.status !== 'MISMATCH' && checks.some((c) => c.name === 'Sender Security (Trust Gateway)' && !c.pass) && (
            <div className="flex items-center justify-between bg-bad/[0.06] px-5 py-3.5">
              <span className="text-xs text-red-200">The sender itself failed security review — not a paperwork problem.</span>
              <Button size="sm" variant="primary" onClick={() => go('gateway')}>Open Trust Gateway</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
