import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, CheckCircle2, Filter, Mail, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Drawer, Empty, Modal, PageHeader, Progress, StatusBadge, SuccessCheck, Typewriter, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { EmailDetail, EmailRow, Resolution } from '@/lib/types'
import { cn, evidenceRef, explain, FIELD_LABEL, fmtValue, REASON_DETAIL, REASON_LABEL, SCORE_HINT } from '@/lib/utils'

const FIELDS = Object.keys(FIELD_LABEL)
const DECIDED = ['CONFIRM_AI_RESULT', 'OVERRIDE_RESULT', 'AMENDMENT_CONFIRMED']

type Bucket = 'needs' | 'awaiting' | 'resolved'
type Tab = 'needs' | 'sent' | 'awaiting' | 'resolved'

// Where a flagged comparison sits: decided by a person or the sender, waiting on the sender, or waiting on a person.
function bucketOf(e: EmailRow, res: Record<string, Resolution>): Bucket {
  const r = res[e.id]
  if (r && DECIDED.includes(r.action)) return 'resolved'
  if (e.amendment || r?.action === 'AMENDMENT_DISPATCHED') return 'awaiting'
  return 'needs'
}

function waiting(i: number) {
  const m = 14 + ((i * 37) % 260)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}

const when = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function CaseDrawer({ row, onClose }: { row: EmailRow | null; onClose: () => void }) {
  const { getDetail, act, sendAmendment, toast, resolutions } = useApp()
  const [d, setD] = useState<EmailDetail | null>(null)
  const [sending, setSending] = useState(false)
  const [state, setState] = useState<'idle' | 'loading' | 'done'>('idle')
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
  const bucket = row ? bucketOf(row, resolutions) : 'needs'
  const bad = d?.comparison.filter((c) => !c.match && !c.missing) ?? []
  const missing = d?.comparison.filter((c) => c.missing) ?? []
  const amendment = d?.amendment ?? row?.amendment ?? null

  const received = async () => {
    if (!row) return
    setState('loading')
    await act(row.id, 'AMENDMENT_CONFIRMED', { recipient: amendment?.recipient })
    setState('done')
    toast({ tone: 'ok', title: 'Corrected document received', body: `${row.shipment} · recorded in the audit ledger.` })
  }
  const caseInfo = d?.case ?? row?.case
  const blockReason = caseInfo?.can_send === false ? caseInfo.block_reason ?? 'this case cannot be emailed' : null
  const send = async () => {
    if (!row || blockReason) return
    setSending(true)
    const r = await sendAmendment(row.id)
    setSending(false)
    if (r.ok) toast({ tone: 'ok', title: 'Amendment sent', body: `${row.shipment} · sent to ${r.recipient} and logged in the audit ledger.` })
    else toast({ tone: 'warn', title: 'Not sent', body: r.error ?? 'The amendment could not be sent.' })
  }
  const doOverride = async () => {
    if (!row) return
    setOverride(false)
    setState('loading')
    await act(row.id, 'OVERRIDE_RESULT', { from: row.status, to: decision, note })
    setState('done')
    toast({ tone: 'warn', title: 'Result overridden', body: `${row.shipment} set to ${decision === 'OK' ? 'Verified' : 'Discrepancy'} by reviewer.` })
  }

  const recommended = amendment
    ? `Amendment request sent to ${amendment.recipient}. Waiting for a corrected draft BL.`
    : d?.status === 'MISMATCH'
      ? `${d.case?.reason ?? 'Needs a person'}. Send the amendment to the sender, or override the result if the documents are actually correct.`
      : { missing_attachment: 'Request the missing document from the sender.', wrong_doc_type: 'Ask the sender to resend the correct SI and draft BL.', unreadable: 'Request a legible re-upload.', missing_value: 'Confirm the missing field with the shipper.' }[d?.review_reason ?? 'missing_value']

  return (
    <>
      <Drawer
        open={!!row}
        onClose={onClose}
        eyebrow={row?.shipment}
        title={amendment ? 'Amendment case' : row?.status === 'MISMATCH' ? 'Discrepancy review' : 'Human review case'}
        width={560}
        footer={
          row && (
            <div className="flex items-center gap-3">
              <AnimatePresence mode="wait" initial={false}>
                {state === 'done' || bucket === 'resolved' ? (
                  <motion.div key="done" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="flex items-center gap-3 text-sm text-emerald-300">
                    <SuccessCheck size={32} /> Decision recorded{resolved?.block ? ` · block #${resolved.block}` : ''}
                  </motion.div>
                ) : (
                  <motion.div key="btns" className="flex w-full flex-col gap-2" exit={{ opacity: 0 }}>
                    <div className="flex gap-3">
                      {bucket === 'awaiting' ? (
                        <Button variant="success" className="flex-1" loading={state === 'loading'} onClick={received}>Corrected document received</Button>
                      ) : (
                        <Button variant="primary" className="flex-1" icon={<Mail className="size-3.5" />} loading={sending} disabled={!!blockReason || state !== 'idle'} title={blockReason ? `Cannot send: ${blockReason}` : `Send to ${row.sender}`} onClick={send}>
                          Send amendment to sender
                        </Button>
                      )}
                      <Button className="flex-1" disabled={state !== 'idle' || sending} onClick={() => setOverride(true)}>Override result</Button>
                    </div>
                    {bucket === 'needs' &&
                      (blockReason ? (
                        <div className="flex items-start gap-1.5 text-[11.5px] text-amber-300"><AlertTriangle className="mt-px size-3.5 shrink-0" />Cannot send: {blockReason}.</div>
                      ) : (
                        <div className="num truncate text-[11px] text-ink3">To {row.sender}</div>
                      ))}
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
                <StatusBadge status={d.status} resolved={bucket === 'resolved'} />
                {d.review_reason && <Badge tone="warn">{REASON_LABEL[d.review_reason]}</Badge>}
                {bad.map((c) => <Badge key={c.key} tone="bad">{c.label}</Badge>)}
              </div>
              <div className="mt-3 flex items-center gap-3">
                <span className="num text-[13px]" title={SCORE_HINT}>Extraction score {Math.round(d.confidence * 100)}%</span>
                <Progress value={d.confidence} tone={d.confidence > 0.9 ? 'ok' : 'warn'} className="flex-1" />
              </div>
            </section>

            {amendment && (
              <section>
                <div className="eyebrow mb-2">Amendment request</div>
                <div className="overflow-hidden rounded-lg border border-white/[0.08]">
                  <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] bg-white/[0.025] px-3 py-2 text-[11.5px]">
                    <span className="flex min-w-0 items-center gap-2 text-ink2"><Mail className="size-3.5 shrink-0 text-sky" /><span className="truncate">To {amendment.recipient}</span></span>
                    <span className="num shrink-0 text-ink3">{when(amendment.at)}{amendment.block ? ` · block #${amendment.block}` : ''}</span>
                  </div>
                  <div className="px-3 pb-1 pt-2 text-[12px] font-medium">{amendment.subject}</div>
                  <pre className="num max-h-56 overflow-y-auto whitespace-pre-wrap px-3 pb-3 text-[11px] leading-relaxed text-ink2">{amendment.body}</pre>
                </div>
                {amendment.reason && <div className="mt-2 text-[11px] text-ink3">{amendment.auto ? `Sent automatically: ${amendment.reason}.` : `Sent by a reviewer (${amendment.reason.replace(/^sent by /, '')}).`}</div>}
              </section>
            )}

            <section>
              <div className="eyebrow mb-2">Source evidence</div>
              <div className="space-y-2">
                {bad.length === 0 && missing.length === 0 && <div className="text-xs text-ink3">{d.review_reason ? 'No extractable evidence — the document could not be read reliably.' : 'No conflicting evidence.'}</div>}
                {[...bad, ...missing].map((c) => (
                  <div key={c.key} className="num rounded-lg border border-white/[0.08] bg-white/[0.025] px-3 py-2 text-[11px] text-ink2">
                    <div className="mb-1 text-ink3">{c.label}</div>
                    <div><span className="mr-2 text-emerald-300">SI {evidenceRef(c.si_evidence, d.si?.is_scanned)}</span>{c.si_evidence?.snippet ?? (d.si?.is_scanned && c.si != null ? String(c.si) : 'not found')}</div>
                    <div><span className="mr-2 text-red-300">BL {evidenceRef(c.bl_evidence, d.bl?.is_scanned)}</span>{c.bl_evidence?.snippet ?? (d.bl?.is_scanned && c.bl != null ? String(c.bl) : 'not found')}</div>
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
              <div className="eyebrow mb-2">{amendment ? 'Status' : 'Recommended action'}</div>
              <div className="flex items-center gap-2 rounded-lg border border-sky/20 bg-sky/[0.06] px-3.5 py-3 text-[13px]">
                <ArrowRight className="size-4 shrink-0 text-sky" />
                {recommended}
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

export default function Cases({ go, id }: { go: (p: Page, id?: string, auto?: boolean) => void; id?: string }) {
  const { emails, resolutions } = useApp()
  const [tab, setTab] = useState<Tab>('needs')
  const [field, setField] = useState<string>('all')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<EmailRow | null>(null)

  const flagged = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON' && e.status !== 'OK'), [emails])
  const groups = useMemo(() => {
    const g: Record<Tab, EmailRow[]> = { needs: [], sent: [], awaiting: [], resolved: [] }
    flagged.forEach((e) => {
      const b = bucketOf(e, resolutions)
      g[b].push(e)
      if (e.amendment) g.sent.push(e)
    })
    return g
  }, [flagged, resolutions])

  // Opening a case by link (from the verification page or the compliance gate) shows it on the tab it lives in.
  useEffect(() => {
    if (!id) return
    const e = flagged.find((x) => x.id === id)
    if (e) {
      setTab(bucketOf(e, resolutions))
      setSel(e)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, flagged])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return groups[tab].filter((e) => (field === 'all' || e.defect_fields.includes(field)) && (!s || e.shipment.toLowerCase().includes(s) || e.subject.toLowerCase().includes(s)))
  }, [tab, groups, field, q])

  const tabs: [Tab, string][] = [['needs', 'Needs a person'], ['sent', 'Sent'], ['awaiting', 'Awaiting reply'], ['resolved', 'Resolved']]

  return (
    <div>
      <PageHeader title="Cases" sub="Flagged SI/BL checks in one place. Every field mismatch is sent to the sender automatically; review cases wait for a person." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5">
          {tabs.map(([k, l]) => (
            <button key={k} onClick={() => { setTab(k); setField('all') }} className={cn('relative rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors', tab === k ? 'text-ink' : 'text-ink3 hover:text-ink2')}>
              {tab === k && <motion.span layoutId="cases-tab" className="absolute inset-0 rounded-md bg-white/[0.08]" />}
              <span className="relative">{l} <span className="num text-ink3">{groups[k].length}</span></span>
            </button>
          ))}
        </div>
        <div className="flex h-9 min-w-[220px] items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 focus-within:border-sky/50">
          <Search className="size-4 text-ink3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search shipment…" className="w-full bg-transparent text-[13px] outline-none placeholder:text-ink3" />
        </div>
        <div className="flex items-center gap-1.5 text-ink3"><Filter className="size-3.5" /></div>
        <select value={field} onChange={(e) => setField(e.target.value)} className="h-9 rounded-lg border border-white/12 bg-card px-3 text-[13px] outline-none">
          <option value="all">All fields</option>
          {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
        </select>
        <span className="num ml-auto text-xs text-ink3">{list.length} cases</span>
      </div>

      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[100px_minmax(0,2fr)_110px_minmax(0,1.3fr)_140px_160px] gap-4 border-b border-white/[0.07] px-6 py-3 max-lg:hidden">
          {['Shipment', 'Issue', 'Score', 'Handling', tab === 'needs' ? 'Waiting' : 'Sent', 'Status'].map((h) => <div key={h} className="eyebrow" title={h === 'Score' ? SCORE_HINT : undefined}>{h}</div>)}
        </div>
        {list.length === 0 && <Empty icon={<CheckCircle2 className="size-6" />} title="Nothing here" />}
        <motion.div key={tab + field + q} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.018 } } }}>
          {list.slice(0, 80).map((e, i) => {
            const b = bucketOf(e, resolutions)
            return (
              <motion.button
                key={e.id}
                variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0, transition: { duration: 0.3, ease } } }}
                onClick={() => setSel(e)}
                className={cn('grid w-full grid-cols-[100px_minmax(0,2fr)_110px_minmax(0,1.3fr)_140px_160px] items-center gap-4 border-b border-white/[0.05] px-6 py-3 text-left transition-colors last:border-0 max-lg:grid-cols-[100px_1fr_auto]', sel?.id === e.id ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]')}
              >
                <span className="num text-[12px]">{e.shipment}</span>
                <span className="truncate text-[13px] text-ink2">{e.status === 'MISMATCH' ? e.defect_fields.map((f) => FIELD_LABEL[f]).join(' · ') : REASON_LABEL[e.review_reason ?? 'missing_value']}</span>
                <span className="flex items-center gap-2 max-lg:hidden"><span className="num text-[11.5px] text-ink2">{Math.round(e.confidence * 100)}%</span><Progress value={e.confidence} tone={e.confidence > 0.9 ? 'ok' : 'warn'} className="w-10" /></span>
                <span className="truncate text-[12px] text-ink3 max-lg:hidden">{e.amendment ? `Sent to ${e.amendment.recipient}` : e.status === 'MISMATCH' ? (e.case?.reason ?? 'Needs a person') : (e.review_reason ?? '').replace(/_/g, ' ')}</span>
                <span className="num whitespace-nowrap text-[11.5px] text-ink3 max-lg:hidden">{e.amendment ? when(e.amendment.at) : waiting(i)}</span>
                {b === 'awaiting' ? <Badge tone="brand" dot>Amendment sent</Badge> : <StatusBadge status={e.status} resolved={b === 'resolved'} />}
              </motion.button>
            )
          })}
        </motion.div>
      </div>
      <div className="mt-3 text-[11px] text-ink3">Waiting time is illustrative — the source inbox carries no timestamps. <button onClick={() => go('analytics')} className="text-ink2 underline-offset-2 hover:underline">See analytics</button></div>

      <CaseDrawer row={sel ? emails.find((e) => e.id === sel.id) ?? sel : null} onClose={() => setSel(null)} />
    </div>
  )
}
