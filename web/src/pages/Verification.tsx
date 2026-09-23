import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Check, Loader2, Play, RotateCcw, ScanLine, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DocCard, type LineState } from '@/components/DocCard'
import { Redline } from '@/components/Redline'
import { Badge, Button, PageHeader, Typewriter, ease, spring } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { EmailDetail } from '@/lib/types'
import { cn, explain, fmtValue, REASON_DETAIL, REASON_LABEL, SCORE_HINT, sleep } from '@/lib/utils'

type Phase = 'idle' | 'received' | 'classified' | 'extracting' | 'comparing' | 'redline' | 'decided'
const STAGES = [
  { k: 'received', label: 'Email received' },
  { k: 'classified', label: 'Classified' },
  { k: 'extracting', label: 'Documents extracted' },
  { k: 'comparing', label: 'AI comparison' },
  { k: 'redline', label: 'Redline' },
  { k: 'decided', label: 'Decision' },
] as const
const ORDER: Phase[] = ['idle', 'received', 'classified', 'extracting', 'comparing', 'redline', 'decided']

type RowState = 'hidden' | 'extracted' | 'comparing' | 'ok' | 'bad' | 'missing'

export default function Verification({ go, id, auto }: { go: (p: Page, id?: string, auto?: boolean) => void; id?: string; auto?: boolean }) {
  const { emails, getDetail } = useApp()
  const comps = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON'), [emails])
  const current = id && comps.some((c) => c.id === id) ? id : (comps.find((c) => c.status === 'MISMATCH') ?? comps[0])?.id ?? ''
  const [d, setD] = useState<EmailDetail | null>(null)
  const [tab, setTab] = useState<'live' | 'redline'>('live')
  const [phase, setPhase] = useState<Phase>('idle')
  const [rows, setRows] = useState<RowState[]>([])
  const [scanKey, setScanKey] = useState(0)
  const [scanning, setScanning] = useState(false)
  const [lines, setLines] = useState<{ si: Record<number, LineState>; bl: Record<number, LineState> }>({ si: {}, bl: {} })
  const [typed, setTyped] = useState(false)
  const run = useRef(0)

  const reset = useCallback((detail: EmailDetail | null) => {
    run.current++
    setPhase('idle')
    setScanning(false)
    setTyped(false)
    setLines({ si: {}, bl: {} })
    setRows((detail?.comparison ?? []).map(() => 'hidden'))
  }, [])

  useEffect(() => {
    let alive = true
    setD(null)
    reset(null)
    getDetail(current).then((x) => {
      if (!alive || !x) return
      setD(x)
      reset(x)
      if (auto) setTimeout(() => alive && start(x), 350)
    })
    return () => {
      alive = false
      run.current++
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, auto])

  const start = useCallback(async (detail: EmailDetail) => {
    const me = ++run.current
    const live = () => run.current === me
    const n = detail.comparison.length
    setTab('live')
    setTyped(false)
    setLines({ si: {}, bl: {} })
    setRows(detail.comparison.map(() => 'hidden'))
    setPhase('received')
    await sleep(650)
    if (!live()) return
    setPhase('classified')
    await sleep(750)
    if (!live()) return

    setPhase('extracting')
    setScanKey((k) => k + 1)
    setScanning(true)
    for (let i = 0; i < n; i++) {
      await sleep(230)
      if (!live()) return
      const c = detail.comparison[i]
      setRows((r) => r.map((x, j) => (j === i ? (c.missing ? 'missing' : 'extracted') : x)))
      setLines((l) => ({
        si: c.si_evidence ? { ...l.si, [c.si_evidence.line_number]: 'lit' } : l.si,
        bl: c.bl_evidence ? { ...l.bl, [c.bl_evidence.line_number]: 'lit' } : l.bl,
      }))
    }
    await sleep(400)
    if (!live()) return
    setScanning(false)

    setPhase('comparing')
    for (let i = 0; i < n; i++) {
      const c = detail.comparison[i]
      setRows((r) => r.map((x, j) => (j === i ? 'comparing' : x)))
      await sleep(460)
      if (!live()) return
      const st: RowState = c.missing ? 'missing' : c.match ? 'ok' : 'bad'
      setRows((r) => r.map((x, j) => (j === i ? st : x)))
      const ls: LineState = c.match ? 'ok' : 'bad'
      setLines((l) => ({
        si: c.si_evidence ? { ...l.si, [c.si_evidence.line_number]: ls } : l.si,
        bl: c.bl_evidence ? { ...l.bl, [c.bl_evidence.line_number]: ls } : l.bl,
      }))
      if (!c.match) await sleep(260)
    }
    setPhase('redline')
    await sleep(600)
    if (!live()) return
    setPhase('decided')
  }, [])

  const decided = phase === 'decided'
  const bad = d?.comparison.filter((c) => !c.match && !c.missing) ?? []
  const nBad = d?.defect_fields.length ?? 0
  const stageIdx = ORDER.indexOf(phase)

  const headline = !d
    ? { text: 'LOADING…', tone: 'text-ink3' }
    : !decided
      ? phase === 'idle'
        ? { text: 'READY TO VERIFY', tone: 'text-ink2' }
        : { text: 'ANALYSING…', tone: 'text-sky' }
      : d.status === 'OK'
        ? { text: 'NO MISMATCH DETECTED (ALL 7 FIELDS VERIFIED)', tone: 'text-ok' }
        : d.status === 'MISMATCH'
          ? { text: `${nBad} DISCREPANC${nBad === 1 ? 'Y' : 'IES'} DETECTED`, tone: 'text-bad' }
          : { text: 'REVIEW REQUIRED', tone: 'text-warn' }

  return (
    <div>
      <PageHeader
        title="Document Verification"
        sub="Receive → understand → compare → explain → resolve. Watch NavisAI reason through a shipment's SI and draft BL."
        right={
          <div className="flex items-center gap-2">
            <select
              value={current}
              onChange={(e) => go('verification', e.target.value)}
              className="h-9 max-w-[280px] rounded-lg border border-white/12 bg-card px-3 text-[13px] text-ink outline-none transition hover:border-white/20 focus:border-sky/50"
              aria-label="Select shipment"
            >
              {comps.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.shipment} · {c.status === 'OK' ? 'Verified' : c.status === 'MISMATCH' ? 'Discrepancy' : 'Needs review'}
                </option>
              ))}
            </select>
            <Button variant="primary" icon={phase === 'idle' || decided ? (decided ? <RotateCcw className="size-3.5" /> : <Play className="size-3.5" />) : undefined} loading={phase !== 'idle' && !decided} disabled={!d} onClick={() => d && start(d)}>
              {decided ? 'Replay' : 'Run verification'}
            </Button>
          </div>
        }
      />

      {/* Pipeline */}
      <div className="panel mb-5 overflow-hidden px-6 py-5">
        <div className="relative flex items-start justify-between">
          <div className="absolute left-[8%] right-[8%] top-[15px] h-px bg-white/10" />
          <motion.div className="absolute left-[8%] top-[15px] h-px bg-sky shadow-[0_0_8px_rgba(56,189,248,0.6)]" initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(5, stageIdx - 1)) * 16.8}%` }} transition={{ duration: 0.6, ease }} />
          {STAGES.map((s, i) => {
            const done = stageIdx > i + 1 || (decided && i === 5)
            const active = stageIdx === i + 1 && !decided
            const end = i === 5 && decided
            const tone = end ? (d?.status === 'OK' ? 'ok' : d?.status === 'MISMATCH' ? 'bad' : 'warn') : done ? 'ok' : active ? 'sky' : 'idle'
            return (
              <div key={s.k} className="relative z-10 flex w-1/6 flex-col items-center gap-2 text-center">
                <motion.div
                  animate={{ scale: active ? 1.08 : 1 }}
                  transition={spring}
                  className={cn(
                    'grid size-[31px] place-items-center rounded-full border bg-card transition-colors duration-300',
                    tone === 'idle' && 'border-white/12 text-ink3',
                    tone === 'sky' && 'border-sky/60 text-sky',
                    tone === 'ok' && 'border-ok/50 bg-ok/10 text-ok',
                    tone === 'bad' && 'border-bad/50 bg-bad/10 text-bad',
                    tone === 'warn' && 'border-warn/50 bg-warn/10 text-warn',
                  )}
                >
                  {active ? <Loader2 className="size-3.5 animate-spin" /> : done || end ? (tone === 'bad' ? <AlertTriangle className="size-3.5" /> : <Check className="size-3.5" strokeWidth={2.6} />) : <span className="num text-[11px]">{i + 1}</span>}
                </motion.div>
                <div className={cn('text-[10.5px] font-semibold uppercase tracking-[0.1em] transition-colors', active ? 'text-sky' : done || end ? 'text-ink2' : 'text-ink3')}>{s.label}</div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Headline + tabs */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <AnimatePresence mode="wait">
            <motion.div key={headline.text} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.25 }} className={cn('flex items-center gap-2.5 text-lg font-semibold tracking-[0.04em]', headline.tone)}>
              {!decided && phase !== 'idle' && d && <ScanLine className="size-5 animate-pulse" />}
              {headline.text}
            </motion.div>
          </AnimatePresence>
          {d && (
            <span className="num text-xs text-ink3">
              {d.shipment} · {d.meta.carrier}
              {d.meta.vessel ? ` · ${d.meta.vessel}` : ''}
            </span>
          )}
        </div>
        <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5">
          {(['live', 'redline'] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={cn('relative rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors', tab === t ? 'text-ink' : 'text-ink3 hover:text-ink2')}>
              {tab === t && <motion.span layoutId="ver-tab" className="absolute inset-0 rounded-md bg-white/[0.08]" transition={spring} />}
              <span className="relative">{t === 'live' ? 'Live analysis' : 'Redline comparison'}</span>
            </button>
          ))}
        </div>
      </div>

      {!d ? (
        <div className="panel grid h-72 place-items-center text-sm text-ink3">Loading shipment…</div>
      ) : tab === 'redline' ? (
        d.comparison.length ? <Redline d={d} /> : <div className="panel p-10 text-center text-sm text-ink2">No comparable documents for this shipment.</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
            <DocCard title="Shipping Instruction" file={d.attachments.find((a) => /_SI/i.test(a))} doc={d.si} scanning={scanning} scanKey={scanKey} lines={lines.si} className="min-h-[360px]" />

            {/* Structured intelligence */}
            <div className="panel order-last col-span-2 overflow-hidden xl:order-none xl:col-span-1">
              <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
                <div className="eyebrow">Structured intelligence</div>
                <span className="num text-[10px] text-ink3">{rows.filter((r) => r !== 'hidden').length}/{d.comparison.length} fields</span>
              </div>
              <div className="divide-y divide-white/[0.05]">
                {d.comparison.map((c, i) => {
                  const st = rows[i] ?? 'hidden'
                  const shown = st !== 'hidden'
                  const res = st === 'ok' || st === 'bad'
                  return (
                    <div key={c.key} className="relative grid min-h-[58px] grid-cols-[1fr_46px_1fr] items-center gap-2 px-4 py-2.5">
                      <AnimatePresence>
                        {st === 'ok' && <motion.span key="fl" className="absolute inset-0 bg-ok/10" initial={{ opacity: 0.9 }} animate={{ opacity: 0 }} transition={{ duration: 0.9 }} />}
                        {st === 'bad' && <motion.span key="fb" className="absolute inset-0 bg-bad/15" initial={{ opacity: 1 }} animate={{ opacity: 0.35 }} transition={{ duration: 0.8 }} />}
                      </AnimatePresence>
                      <div className="relative min-w-0">
                        <div className="eyebrow mb-0.5">{c.label}</div>
                        <motion.div initial={false} animate={shown ? { opacity: 1, x: 0 } : { opacity: 0, x: -22 }} transition={{ duration: 0.4, ease }} className="num line-clamp-2 break-words text-[12px] leading-snug text-ink" title={String(c.si ?? '')}>
                          {fmtValue(c.key, c.si)}
                        </motion.div>
                      </div>
                      <div className="relative flex items-center justify-center">
                        <svg width="46" height="22" viewBox="0 0 46 22" className="overflow-visible">
                          <motion.path d="M0 11 H46" strokeWidth="1.2" strokeDasharray="3 3" fill="none" stroke={st === 'bad' ? '#EF4444' : st === 'ok' ? '#10B981' : '#38BDF8'} initial={false} animate={{ pathLength: st === 'comparing' || res ? 1 : 0, opacity: st === 'comparing' || res ? 1 : 0 }} transition={{ duration: 0.4 }} />
                          {st === 'comparing' && (
                            <motion.circle r="2.5" cy="11" fill="#38BDF8" initial={{ cx: 0 }} animate={{ cx: 46 }} transition={{ duration: 0.45, ease: 'easeInOut' }} />
                          )}
                        </svg>
                        <AnimatePresence>
                          {res && (
                            <motion.span
                              key={st}
                              initial={{ scale: 0, rotate: -30 }}
                              animate={{ scale: 1, rotate: 0, x: st === 'bad' ? [0, -3, 3, -2, 0] : 0 }}
                              transition={{ type: 'spring', stiffness: 500, damping: 18 }}
                              className={cn('absolute grid size-5 place-items-center rounded-full', st === 'ok' ? 'bg-ok text-white' : 'bg-bad text-white')}
                            >
                              {st === 'ok' ? <Check className="size-3" strokeWidth={3} /> : <span className="text-[11px] font-bold leading-none">!</span>}
                            </motion.span>
                          )}
                        </AnimatePresence>
                      </div>
                      <div className="relative min-w-0 text-right">
                        <div className="eyebrow mb-0.5">&nbsp;</div>
                        <motion.div initial={false} animate={shown ? { opacity: 1, x: 0 } : { opacity: 0, x: 22 }} transition={{ duration: 0.4, ease, delay: 0.05 }} className={cn('num line-clamp-2 break-words text-[12px] leading-snug', st === 'bad' ? 'text-red-300' : 'text-ink')} title={String(c.bl ?? '')}>
                          {fmtValue(c.key, c.bl)}
                        </motion.div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            <DocCard title="Draft Bill of Lading" file={d.attachments.find((a) => /_BL/i.test(a))} doc={d.bl} scanning={scanning} scanKey={scanKey} lines={lines.bl} className="min-h-[360px]" />
          </div>

          {/* Decision */}
          <AnimatePresence>
            {decided && (
              <motion.div key="decision" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease }} className="mt-5 grid gap-5 xl:grid-cols-[1.4fr_1fr]">
                <div className="space-y-3">
                  {d.status === 'MISMATCH' &&
                    bad.map((c, i) => (
                      <motion.div key={c.key} initial={{ opacity: 0, x: 36 }} animate={{ opacity: 1, x: 0 }} transition={{ ...spring, delay: i * 0.12 }} className="panel relative overflow-hidden border-bad/25 p-5">
                        <span className="absolute inset-y-0 left-0 w-[3px] bg-bad" />
                        <div className="flex flex-wrap items-center gap-3">
                          <Badge tone="bad" dot>{c.label} mismatch</Badge>
                          <span className="num text-[13px]"><span className="text-emerald-300">SI: {fmtValue(c.key, c.si)}</span><span className="mx-2 text-ink3">→</span><span className="text-red-300">BL: {fmtValue(c.key, c.bl)}</span></span>
                        </div>
                        <div className="eyebrow mb-1 mt-4">AI reasoning</div>
                        <p className="text-[13px] leading-relaxed text-ink2">
                          <Typewriter text={explain(c)} speed={12} start={i === 0 || typed} onDone={() => i === 0 && setTyped(true)} />
                        </p>
                      </motion.div>
                    ))}
                  {d.status === 'OK' && (
                    <div className="panel relative overflow-hidden border-ok/25 p-5">
                      <span className="absolute inset-y-0 left-0 w-[3px] bg-ok" />
                      <Badge tone="ok" dot>Verified</Badge>
                      <p className="mt-3 text-[13px] leading-relaxed text-ink2"><Typewriter text="Shipper, consignee, notify party, ports, container count and gross weight are consistent across the Shipping Instruction and draft Bill of Lading. No amendment is required." speed={10} /></p>
                    </div>
                  )}
                  {d.status === 'NEEDS_REVIEW' && d.review_reason && (
                    <div className="panel relative overflow-hidden border-warn/25 p-5">
                      <span className="absolute inset-y-0 left-0 w-[3px] bg-warn" />
                      <Badge tone="warn" dot>{REASON_LABEL[d.review_reason]}</Badge>
                      <p className="mt-3 text-[13px] leading-relaxed text-ink2"><Typewriter text={`${REASON_DETAIL[d.review_reason]} NavisAI will not guess — this case is escalated to a human reviewer with the evidence attached.`} speed={10} /></p>
                    </div>
                  )}
                </div>

                <div className="panel p-5">
                  <div className="eyebrow mb-2">Recommended action</div>
                  <div className="mb-4 flex items-start gap-3">
                    <div className={cn('mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg', d.status === 'OK' ? 'bg-ok/12 text-ok' : d.status === 'MISMATCH' ? 'bg-bad/12 text-bad' : 'bg-warn/12 text-warn')}>
                      {d.status === 'OK' ? <ShieldCheck className="size-4" /> : <AlertTriangle className="size-4" />}
                    </div>
                    <div className="text-[13.5px] font-medium leading-snug">
                      {d.status === 'OK' ? 'Release to compliance gate' : d.status === 'MISMATCH' ? 'Amendment request to the sender' : 'Route to Human Review Queue'}
                    </div>
                  </div>
                  <div className="num mb-4 text-[11px] text-ink3" title={SCORE_HINT}>Extraction score {Math.round(d.confidence * 100)}% · heuristic</div>
                  <div className="flex flex-wrap gap-2">
                    {d.status === 'MISMATCH' && <Button variant="primary" icon={<ArrowRight className="size-3.5" />} onClick={() => go('cases', d.id)}>Open case</Button>}
                    {d.status === 'OK' && <Button variant="primary" icon={<ArrowRight className="size-3.5" />} onClick={() => go('compliance', d.id)}>Open compliance gate</Button>}
                    {d.status === 'NEEDS_REVIEW' && <Button variant="primary" icon={<ArrowRight className="size-3.5" />} onClick={() => go('cases', d.id)}>Open case</Button>}
                    <Button onClick={() => setTab('redline')}>View redline</Button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
    </div>
  )
}
