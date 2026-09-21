import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, Check, Send } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Modal, PageHeader, StatusBadge, SuccessCheck, ease, spring } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { EmailDetail } from '@/lib/types'
import { cn, fmtValue, sleep } from '@/lib/utils'

const STAGES = ['Discrepancy detected', 'AI recommendation', 'Amendment prepared', 'Carrier review', 'Resolved']

export default function Carrier({ go, id }: { go: (p: Page, id?: string) => void; id?: string }) {
  const { emails, getDetail, act, resolutions, toast } = useApp()
  const mism = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON' && e.status === 'MISMATCH'), [emails])
  const cur = id && mism.some((m) => m.id === id) ? id : mism[0]?.id ?? ''
  const [d, setD] = useState<EmailDetail | null>(null)
  const [prepared, setPrepared] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [modal, setModal] = useState(false)
  const [sending, setSending] = useState(false)
  const res = resolutions[cur]
  const dispatched = res?.action === 'AMENDMENT_DISPATCHED'
  const resolved = res?.action === 'AMENDMENT_CONFIRMED'

  useEffect(() => {
    let a = true
    setD(null)
    setPrepared(false)
    getDetail(cur).then((x) => a && setD(x))
    return () => {
      a = false
    }
  }, [cur, getDetail])

  const bad = d?.comparison.filter((c) => !c.match && !c.missing) ?? []
  const stage = resolved ? 6 : dispatched ? 4 : prepared ? 3 : 2

  const prepare = async () => {
    setPreparing(true)
    await sleep(900)
    setPrepared(true)
    setPreparing(false)
  }
  const dispatch = async () => {
    setSending(true)
    await sleep(900)
    await act(cur, 'AMENDMENT_DISPATCHED', { fields: bad.map((b) => b.key), to: d?.meta.carrier })
    setSending(false)
    setModal(false)
    toast({ tone: 'ok', title: 'Amendment sent', body: `${d?.shipment} · request logged in the audit ledger.` })
  }

  const draft = d
    ? `Subject: Amendment request — Draft B/L ${d.meta.booking ?? ''} (${d.shipment})\n\nDear ${/^(Other|Unassigned)/.test(d.meta.carrier) ? 'Carrier' : d.meta.carrier} documentation team,\n\nOur Shipping Instruction differs from the draft Bill of Lading on the following fields. Please issue a corrected draft:\n\n${bad.map((b) => `  • ${b.label}: BL shows "${fmtValue(b.key, b.bl)}" → should read "${fmtValue(b.key, b.si)}"`).join('\n')}\n\nKindly confirm once amended.\n\nRegards,\nOperations Desk`
    : ''

  return (
    <div>
      <PageHeader title="Carrier Actions" sub="Turn a confirmed discrepancy into a carrier amendment request — reviewed by you before anything leaves the building." />
      <div className="grid gap-6 xl:grid-cols-[300px_1fr]">
        <div className="panel max-h-[640px] overflow-y-auto p-2">
          <div className="eyebrow px-3 py-2">Open discrepancies · {mism.length}</div>
          {mism.slice(0, 40).map((m) => (
            <button key={m.id} onClick={() => go('carrier', m.id)} className={cn('relative flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left transition-colors', cur === m.id ? 'bg-white/[0.07]' : 'hover:bg-white/[0.035]')}>
              {cur === m.id && <motion.span layoutId="carrier-sel" className="absolute inset-y-2 left-0 w-[2px] rounded-full bg-brand" />}
              <div>
                <div className="num text-[12.5px]">{m.shipment}</div>
                <div className="text-[11px] text-ink3">{m.meta.carrier}</div>
              </div>
              <StatusBadge status="MISMATCH" resolved={resolutions[m.id]?.action === 'AMENDMENT_CONFIRMED'} />
            </button>
          ))}
        </div>

        <div className="space-y-5">
          {/* Stage rail */}
          <div className="panel px-6 py-5">
            <div className="flex items-center">
              {STAGES.map((s, i) => {
                const done = stage > i + 1
                const active = stage === i + 1
                return (
                  <div key={s} className="flex flex-1 items-center last:flex-none">
                    <div className="flex flex-col items-center gap-2">
                      <motion.div animate={{ scale: active ? 1.1 : 1 }} transition={spring} className={cn('grid size-8 place-items-center rounded-full border transition-colors duration-300', done || (i === 4 && resolved) ? 'border-ok/50 bg-ok/12 text-ok' : active ? 'border-brand/60 text-brand' : 'border-white/10 text-ink3')}>
                        {done || (i === 4 && resolved) ? <Check className="size-4" strokeWidth={2.6} /> : <span className={cn('num text-[11px]', active && i === 3 && 'breathe')}>{i + 1}</span>}
                      </motion.div>
                      <div className={cn('w-24 text-center text-[10px] font-semibold uppercase leading-tight tracking-[0.08em]', active ? 'text-ink' : 'text-ink3')}>{s}</div>
                    </div>
                    {i < 4 && (
                      <div className="relative mx-2 -mt-6 h-px flex-1 bg-white/10">
                        <motion.div className="absolute inset-y-0 left-0 bg-ok" initial={false} animate={{ width: done ? '100%' : '0%' }} transition={{ duration: 0.6, ease }} />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {!d ? (
            <div className="panel grid h-56 place-items-center text-sm text-ink3">Loading…</div>
          ) : (
            <motion.div layout className="panel overflow-hidden" transition={spring}>
              <AnimatePresence mode="wait" initial={false}>
                {!prepared && !dispatched && !resolved && (
                  <motion.div key="disc" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, y: -10 }} className="p-6">
                    <div className="mb-1 flex items-center gap-3"><Badge tone="bad" dot>Discrepancy detected</Badge><span className="num text-xs text-ink3">{d.shipment} · {d.meta.carrier}</span></div>
                    <div className="mt-4 space-y-2">
                      {bad.map((b) => (
                        <div key={b.key} className="flex items-center justify-between rounded-lg border border-bad/20 bg-bad/[0.05] px-4 py-3">
                          <span className="text-[13px] font-medium">{b.label}</span>
                          <span className="num text-[12.5px]"><span className="text-emerald-300">SI {fmtValue(b.key, b.si)}</span><span className="mx-2 text-ink3">≠</span><span className="text-red-300">BL {fmtValue(b.key, b.bl)}</span></span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-5 flex items-center gap-2 rounded-lg border border-sky/20 bg-sky/[0.06] px-4 py-3 text-[13px]"><ArrowRight className="size-4 text-sky" /> Request corrected draft BL from carrier.</div>
                    <Button variant="primary" className="mt-5" loading={preparing} onClick={prepare}>Prepare amendment</Button>
                  </motion.div>
                )}
                {prepared && !dispatched && !resolved && (
                  <motion.div key="amend" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="p-6">
                    <div className="mb-4 flex items-center gap-3"><Badge tone="brand" dot>Amendment prepared</Badge><span className="text-xs text-ink3">Draft is ready for your review</span></div>
                    <div className="space-y-2">
                      {bad.map((b, i) => (
                        <motion.div key={b.key} initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.1, ease }} className="flex items-center justify-between rounded-lg border border-white/[0.09] bg-white/[0.03] px-4 py-3.5">
                          <span className="text-[13px] font-medium">{b.label}</span>
                          <span className="num flex items-center gap-3 text-[15px] font-semibold">
                            <motion.span initial={{ opacity: 1 }} animate={{ opacity: 0.5 }} className="text-red-300 line-through decoration-red-400/70">{fmtValue(b.key, b.bl)}</motion.span>
                            <ArrowRight className="size-4 text-ink3" />
                            <span className="text-emerald-300">{fmtValue(b.key, b.si)}</span>
                          </span>
                        </motion.div>
                      ))}
                    </div>
                    <Button variant="primary" className="mt-5" icon={<Send className="size-3.5" />} onClick={() => setModal(true)}>Review &amp; dispatch</Button>
                  </motion.div>
                )}
                {dispatched && (
                  <motion.div key="sent" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center gap-3 px-6 py-12 text-center">
                    <SuccessCheck size={52} />
                    <div className="text-sm font-semibold uppercase tracking-[0.14em] text-emerald-300">Amendment sent</div>
                    <p className="max-w-sm text-[13px] text-ink2">Awaiting corrected draft BL from the carrier. Logged as ledger block #{res?.block}.</p>
                    <Button className="mt-2" onClick={async () => { await act(cur, 'AMENDMENT_CONFIRMED', {}); toast({ tone: 'ok', title: 'Carrier confirmed correction', body: d.shipment }) }}>Mark carrier confirmation received</Button>
                  </motion.div>
                )}
                {resolved && (
                  <motion.div key="res" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center gap-3 px-6 py-12 text-center">
                    <SuccessCheck size={52} />
                    <div className="text-sm font-semibold uppercase tracking-[0.14em] text-sky">Resolved</div>
                    <p className="max-w-sm text-[13px] text-ink2">The carrier reissued the corrected draft. {d.shipment} is ready for the compliance gate.</p>
                    <Button variant="primary" onClick={() => go('compliance', cur)}>Open compliance gate</Button>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          )}
        </div>
      </div>

      <Modal open={modal} onClose={() => !sending && setModal(false)} title="Review and dispatch" width={600}>
        <div className="p-6">
          <h3 className="mb-1 text-base font-semibold">Review &amp; dispatch</h3>
          <p className="mb-4 text-xs text-ink3">Simulated dispatch — the message is recorded in the audit ledger; no email is actually sent.</p>
          <pre className="num max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg border border-white/[0.08] bg-white/[0.02] p-4 text-[11.5px] leading-relaxed text-ink2">{draft}</pre>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" disabled={sending} onClick={() => setModal(false)}>Cancel</Button>
            <Button variant="primary" loading={sending} icon={<Send className="size-3.5" />} onClick={dispatch}>Confirm &amp; send</Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
