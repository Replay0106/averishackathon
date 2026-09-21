import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, CheckCircle2, Filter, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Drawer, Empty, Modal, PageHeader, Progress, StatusBadge, SuccessCheck, Typewriter, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { EmailDetail, EmailRow } from '@/lib/types'
import { cn, explain, FIELD_LABEL, fmtValue, REASON_DETAIL, REASON_LABEL, sleep } from '@/lib/utils'

const FIELDS = Object.keys(FIELD_LABEL)

function waiting(i: number) {
  const m = 14 + ((i * 37) % 260)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}

function ReviewDrawer({ row, onClose }: { row: EmailRow | null; onClose: () => void }) {
  const { getDetail, act, toast, resolutions } = useApp()
  const [d, setD] = useState<EmailDetail | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'verifying' | 'done'>('idle')
  const [override, setOverride] = useState(false)
  const [note, setNote] = useState('')
  const [decision, setDecision] = useState<'OK' | 'MISMATCH'>('OK')

  useEffect(() => {
    setD(null)
    setState('idle')
    if (!row) return
    let a = true
    getDetail(row.id).then((x) => a && setD(x))
    return () => {
      a = false
    }
  }, [row, getDetail])

  const resolved = row ? resolutions[row.id] : undefined
  const bad = d?.comparison.filter((c) => !c.match && !c.missing) ?? []
  const missing = d?.comparison.filter((c) => c.missing) ?? []

  const confirm = async () => {
    if (!row) return
    setState('loading')
    await sleep(700)
    setState('verifying')
    await act(row.id, 'CONFIRM_AI_RESULT', { status: row.status, confidence: row.confidence })
    await sleep(900)
    setState('done')
    toast({ tone: 'ok', title: 'AI result confirmed', body: `${row.shipment} recorded in the audit ledger.` })
  }
  const doOverride = async () => {
    if (!row) return
    setOverride(false)
    setState('loading')
    await act(row.id, 'OVERRIDE_RESULT', { from: row.status, to: decision, note })
    setState('done')
    toast({ tone: 'warn', title: 'Result overridden', body: `${row.shipment} set to ${decision === 'OK' ? 'Verified' : 'Discrepancy'} by reviewer.` })
  }

  return (
    <>
      <Drawer
        open={!!row}
        onClose={onClose}
        eyebrow={row?.shipment}
        title={row?.status === 'MISMATCH' ? 'Discrepancy review' : 'Human review case'}
        width={560}
        footer={
          row && (
            <div className="flex items-center gap-3">
              <AnimatePresence mode="wait" initial={false}>
                {state === 'done' || resolved ? (
                  <motion.div key="done" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="flex items-center gap-3 text-sm text-emerald-300">
                    <SuccessCheck size={32} /> Decision recorded{resolved?.block ? ` · block #${resolved.block}` : ''}
                  </motion.div>
                ) : (
                  <motion.div key="btns" className="flex w-full gap-3" exit={{ opacity: 0 }}>
                    <Button variant="success" className="flex-1" loading={state === 'loading' || state === 'verifying'} onClick={confirm}>
                      {state === 'verifying' ? 'Verifying…' : state === 'loading' ? 'Confirming…' : 'Confirm AI result'}
                    </Button>
                    <Button className="flex-1" disabled={state !== 'idle'} onClick={() => setOverride(true)}>Override result</Button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )
        }
      >
        {!row || !d ? (
          <div className="py-16 text-center text-sm text-ink3">Loading case…</div>
        ) : (
          <div className="space-y-6">
            <section>
              <div className="eyebrow mb-2">AI finding</div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={d.status} resolved={!!resolved} />
                {d.review_reason && <Badge tone="warn">{REASON_LABEL[d.review_reason]}</Badge>}
                {bad.map((c) => <Badge key={c.key} tone="bad">{c.label}</Badge>)}
              </div>
              <div className="mt-3 flex items-center gap-3">
                <span className="num text-[13px]">Confidence {Math.round(d.confidence * 100)}%</span>
                <Progress value={d.confidence} tone={d.confidence > 0.9 ? 'ok' : 'warn'} className="flex-1" />
              </div>
            </section>

            <section>
              <div className="eyebrow mb-2">Source evidence</div>
              <div className="space-y-2">
                {bad.length === 0 && missing.length === 0 && <div className="text-xs text-ink3">{d.review_reason ? 'No extractable evidence — the document could not be read reliably.' : 'No conflicting evidence.'}</div>}
                {[...bad, ...missing].map((c) => (
                  <div key={c.key} className="num rounded-lg border border-white/[0.08] bg-white/[0.025] px-3 py-2 text-[11px] text-ink2">
                    <div className="mb-1 text-ink3">{c.label}</div>
                    <div><span className="mr-2 text-emerald-300">SI L{c.si_evidence?.line_number ?? '–'}</span>{c.si_evidence?.snippet ?? 'not found'}</div>
                    <div><span className="mr-2 text-red-300">BL L{c.bl_evidence?.line_number ?? '–'}</span>{c.bl_evidence?.snippet ?? 'not found'}</div>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <div className="eyebrow mb-2">Extracted values</div>
              <div className="overflow-hidden rounded-lg border border-white/[0.08]">
                {d.comparison.length === 0 && <div className="p-4 text-xs text-ink3">Nothing extracted.</div>}
                {d.comparison.map((c) => (
                  <div key={c.key} className="grid grid-cols-[110px_1fr_1fr] gap-2 border-b border-white/[0.05] px-3 py-2 text-[11.5px] last:border-0">
                    <span className="text-ink3">{c.label}</span>
                    <span className={cn('num truncate', c.match ? 'text-ink2' : 'text-emerald-300')}>{fmtValue(c.key, c.si)}</span>
                    <span className={cn('num truncate', c.match ? 'text-ink2' : c.missing ? 'text-warn' : 'text-red-300')}>{fmtValue(c.key, c.bl)}</span>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <div className="eyebrow mb-2">AI reasoning</div>
              <p className="text-[13px] leading-relaxed text-ink2">
                <Typewriter key={row.id} speed={9} text={d.review_reason ? `${REASON_DETAIL[d.review_reason]} Rather than guess, NavisAI escalated this case with the evidence above.` : bad.map(explain).join(' ') || 'All fields match.'} />
              </p>
            </section>

            <section>
              <div className="eyebrow mb-2">Recommended action</div>
              <div className="flex items-center gap-2 rounded-lg border border-sky/20 bg-sky/[0.06] px-3.5 py-3 text-[13px]">
                <ArrowRight className="size-4 text-sky" />
                {d.status === 'MISMATCH' ? 'Request corrected draft BL from carrier.' : { missing_attachment: 'Request the missing document from the sender.', wrong_doc_type: 'Ask the sender to resend the correct SI and draft BL.', unreadable: 'Request a legible re-upload.', missing_value: 'Confirm the missing field with the shipper.' }[d.review_reason ?? 'missing_value']}
              </div>
            </section>
          </div>
        )}
      </Drawer>

      <Modal open={override} onClose={() => setOverride(false)} title="Override result" width={480}>
        <div className="p-6">
          <h3 className="mb-1 text-base font-semibold">Override AI result</h3>
          <p className="mb-5 text-xs text-ink3">Your decision replaces the AI outcome and is written to the tamper-evident audit ledger.</p>
          <div className="mb-4 grid grid-cols-2 gap-2">
            {([['OK', 'Documents verified'], ['MISMATCH', 'Discrepancy confirmed']] as const).map(([k, l]) => (
              <button key={k} onClick={() => setDecision(k)} className={cn('rounded-lg border px-3 py-3 text-left text-[13px] transition', decision === k ? 'border-brand/50 bg-brand/10' : 'border-white/10 hover:border-white/20')}>{l}</button>
            ))}
          </div>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Reviewer note (e.g. verified against customs declaration)" className="w-full resize-none rounded-lg border border-white/10 bg-white/[0.03] p-3 text-[13px] outline-none transition placeholder:text-ink3 focus:border-sky/50" />
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOverride(false)}>Cancel</Button>
            <Button variant="primary" disabled={!note.trim()} onClick={doOverride}>Apply override</Button>
          </div>
        </div>
      </Modal>
    </>
  )
}

export default function Discrepancies({ go }: { go: (p: Page, id?: string, auto?: boolean) => void }) {
  const { emails, resolutions } = useApp()
  const [tab, setTab] = useState<'mismatch' | 'review'>('mismatch')
  const [field, setField] = useState<string>('all')
  const [carrier, setCarrier] = useState('all')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<EmailRow | null>(null)

  const all = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON'), [emails])
  const mism = useMemo(() => all.filter((e) => e.status === 'MISMATCH'), [all])
  const rev = useMemo(() => all.filter((e) => e.status === 'NEEDS_REVIEW'), [all])
  const carriers = useMemo(() => [...new Set(mism.map((e) => e.meta.carrier))].sort(), [mism])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (tab === 'mismatch' ? mism : rev).filter((e) => (field === 'all' || e.defect_fields.includes(field)) && (carrier === 'all' || e.meta.carrier === carrier) && (!s || e.shipment.toLowerCase().includes(s) || e.subject.toLowerCase().includes(s)))
  }, [tab, mism, rev, field, carrier, q])

  return (
    <div>
      <PageHeader title="Discrepancy Queue" sub="Confirmed SI/BL mismatches and cases NavisAI would rather escalate than guess." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5">
          {([['mismatch', 'Discrepancies', mism.length], ['review', 'Human review queue', rev.length]] as const).map(([k, l, n]) => (
            <button key={k} onClick={() => { setTab(k); setField('all') }} className={cn('relative rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors', tab === k ? 'text-ink' : 'text-ink3 hover:text-ink2')}>
              {tab === k && <motion.span layoutId="dq-tab" className="absolute inset-0 rounded-md bg-white/[0.08]" />}
              <span className="relative">{l} <span className="num text-ink3">{n}</span></span>
            </button>
          ))}
        </div>
        <div className="flex h-9 min-w-[220px] items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 focus-within:border-sky/50">
          <Search className="size-4 text-ink3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search shipment…" className="w-full bg-transparent text-[13px] outline-none placeholder:text-ink3" />
        </div>
        {tab === 'mismatch' && (
          <>
            <div className="flex items-center gap-1.5 text-ink3"><Filter className="size-3.5" /></div>
            <select value={field} onChange={(e) => setField(e.target.value)} className="h-9 rounded-lg border border-white/12 bg-card px-3 text-[13px] outline-none">
              <option value="all">All fields</option>
              {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
            </select>
            <select value={carrier} onChange={(e) => setCarrier(e.target.value)} className="h-9 rounded-lg border border-white/12 bg-card px-3 text-[13px] outline-none">
              <option value="all">All carriers</option>
              {carriers.map((c) => <option key={c}>{c}</option>)}
            </select>
          </>
        )}
        <span className="num ml-auto text-xs text-ink3">{list.length} cases</span>
      </div>

      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[100px_minmax(0,2fr)_110px_minmax(0,1.3fr)_90px_120px] gap-4 border-b border-white/[0.07] px-6 py-3 max-lg:hidden">
          {['Shipment', 'Issue', 'Confidence', 'Reason', 'Waiting', 'Status'].map((h) => <div key={h} className="eyebrow">{h}</div>)}
        </div>
        {list.length === 0 && <Empty icon={<CheckCircle2 className="size-6" />} title="Queue is clear" />}
        <motion.div key={tab + field + carrier + q} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.018 } } }}>
          {list.slice(0, 80).map((e, i) => (
            <motion.button
              key={e.id}
              variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0, transition: { duration: 0.3, ease } } }}
              onClick={() => setSel(e)}
              className={cn('grid w-full grid-cols-[100px_minmax(0,2fr)_110px_minmax(0,1.3fr)_90px_120px] items-center gap-4 border-b border-white/[0.05] px-6 py-3 text-left transition-colors last:border-0 max-lg:grid-cols-[100px_1fr_auto]', sel?.id === e.id ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]')}
            >
              <span className="num text-[12px]">{e.shipment}</span>
              <span className="truncate text-[13px] text-ink2">{e.status === 'MISMATCH' ? e.defect_fields.map((f) => FIELD_LABEL[f]).join(' · ') : REASON_LABEL[e.review_reason ?? 'missing_value']}</span>
              <span className="flex items-center gap-2 max-lg:hidden"><span className="num text-[11.5px] text-ink2">{Math.round(e.confidence * 100)}%</span><Progress value={e.confidence} tone={e.confidence > 0.9 ? 'ok' : 'warn'} className="w-10" /></span>
              <span className="truncate text-[12px] text-ink3 max-lg:hidden">{e.status === 'MISMATCH' ? e.meta.carrier : (e.review_reason ?? '').replace(/_/g, ' ')}</span>
              <span className="num text-[11.5px] text-ink3 max-lg:hidden">{waiting(i)}</span>
              <StatusBadge status={e.status} resolved={!!resolutions[e.id]} />
            </motion.button>
          ))}
        </motion.div>
      </div>
      <div className="mt-3 text-[11px] text-ink3">Waiting time is illustrative — the source inbox carries no timestamps. Carrier is inferred from the booking-reference prefix. <button onClick={() => go('analytics')} className="text-ink2 underline-offset-2 hover:underline">See analytics</button></div>

      <ReviewDrawer row={sel} onClose={() => setSel(null)} />
    </div>
  )
}
