import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUp, FileText, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Typewriter, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { CopilotReply } from '@/lib/types'
import { REASON_LABEL, cn, explain, fmtValue, sleep } from '@/lib/utils'

interface Turn { q: string; a?: CopilotReply }

function Block({ label, children, i, tone }: { label: string; children: React.ReactNode; i: number; tone?: 'bad' | 'ok' | 'info' }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.22, duration: 0.4, ease }} className={cn('rounded-lg border px-4 py-3', tone === 'bad' ? 'border-bad/20 bg-bad/[0.05]' : tone === 'ok' ? 'border-ok/20 bg-ok/[0.05]' : tone === 'info' ? 'border-sky/20 bg-sky/[0.05]' : 'border-white/[0.09] bg-white/[0.025]')}>
      <div className="eyebrow mb-1">{label}</div>
      <div className="text-[13.5px]">{children}</div>
    </motion.div>
  )
}

function Answer({ a, go }: { a: CopilotReply; go: (p: Page, id?: string, auto?: boolean) => void }) {
  if (a.kind === 'mismatch' && a.issues) {
    const first = a.issues[0]
    return (
      <div className="space-y-2.5">
        <Block i={0} label="Issue" tone="bad">{a.issues.map((i) => `${i.label} mismatch`).join(' · ')}</Block>
        <div className="grid grid-cols-2 gap-2.5">
          <Block i={1} label="SI"><span className="num font-semibold text-emerald-300">{fmtValue(first.key, first.si)}</span></Block>
          <Block i={2} label="BL"><span className="num font-semibold text-red-300">{fmtValue(first.key, first.bl)}</span></Block>
        </div>
        <Block i={3} label="Evidence">
          <div className="space-y-1">
            {(a.evidence ?? []).map((f, k) => (
              <div key={f} className="flex items-center gap-2 text-[12.5px] text-ink2"><FileText className="size-3.5 text-ink3" />{f.replace(/^email_\d+_/, '')} — Page 1 <span className="num text-ink3">· L{(k === 0 ? first.si_evidence : first.bl_evidence)?.line_number ?? '–'}</span></div>
            ))}
          </div>
        </Block>
        <Block i={4} label="Reasoning"><span className="text-ink2"><Typewriter text={explain(first)} speed={9} /></span></Block>
        <Block i={5} label="Recommendation" tone="info">{a.recommendation}</Block>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.4 }} className="flex gap-2 pt-1">
          <Button size="sm" variant="primary" onClick={() => go('carrier', a.email)}>Prepare amendment</Button>
          <Button size="sm" onClick={() => go('verification', a.email, true)}>Replay verification</Button>
        </motion.div>
      </div>
    )
  }
  if (a.kind === 'review')
    return (
      <div className="space-y-2.5">
        <Block i={0} label="Issue">{REASON_LABEL[a.reason as keyof typeof REASON_LABEL] ?? a.reason}</Block>
        <Block i={1} label="Confidence">{Math.round((a.confidence ?? 0) * 100)}% — below the automation threshold</Block>
        <Block i={2} label="Recommendation" tone="info">{a.recommendation}</Block>
        <div className="pt-1"><Button size="sm" onClick={() => go('discrepancies')}>Open review queue</Button></div>
      </div>
    )
  if (a.kind === 'clear')
    return (
      <div className="space-y-2.5">
        <Block i={0} label="Status" tone="ok">{a.message}</Block>
        <div className="pt-1"><Button size="sm" onClick={() => go('compliance', a.email)}>Open compliance gate</Button></div>
      </div>
    )
  return <p className="text-[13.5px] text-ink2"><Typewriter text={a.message ?? ''} speed={12} /></p>
}

export default function Copilot({ go }: { go: (p: Page, id?: string, auto?: boolean) => void }) {
  const { ask, emails } = useApp()
  const [turns, setTurns] = useState<Turn[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  const mism = emails.filter((e) => e.category === 'BL_COMPARISON' && e.status === 'MISMATCH')
  const okRow = emails.find((e) => e.category === 'BL_COMPARISON' && e.status === 'OK')
  const suggestions = [
    ...(mism[0] ? [`Why is ${mism[0].shipment} flagged?`] : []),
    ...mism.slice(1, 3).map((f) => `What's wrong with ${f.shipment}?`),
    ...(okRow ? [`Is ${okRow.shipment} clear to release?`] : []),
  ]

  const send = async (text: string) => {
    if (!text.trim() || busy) return
    setQ('')
    setBusy(true)
    setTurns((t) => [...t, { q: text }])
    const [a] = await Promise.all([ask(text), sleep(900)])
    setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)))
    setBusy(false)
  }

  useEffect(() => {
    if (turns.length) end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  return (
    <div className="mx-auto flex min-h-[calc(100vh-9rem)] max-w-3xl flex-col">
      <div className="mb-6">
        <div className="eyebrow mb-2 flex items-center gap-1.5 text-sky"><Sparkles className="size-3" /> Operations intelligence</div>
        <h1 className="text-2xl font-semibold tracking-tight">Ask Navis</h1>
        <p className="mt-1 text-[13px] text-ink2">Query any shipment. Answers are assembled from the extracted SI and BL evidence — never guessed.</p>
      </div>

      <div className="flex-1 space-y-6 pb-6">
        {turns.length === 0 && (
          <div className="panel p-6">
            <div className="eyebrow mb-3">Try asking</div>
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <motion.button key={s} whileHover={{ y: -1 }} onClick={() => send(s)} className="rounded-full border border-white/12 bg-white/[0.03] px-3.5 py-2 text-[12.5px] text-ink2 transition hover:border-white/25 hover:text-ink">{s}</motion.button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="space-y-3">
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-md border border-white/10 bg-sub px-4 py-2.5 text-[13.5px]">{t.q}</div>
            </motion.div>
            <div className="flex gap-3">
              <div className="mt-1 grid size-7 shrink-0 place-items-center rounded-lg border border-sky/30 bg-sky/10"><Sparkles className="size-3.5 text-sky" /></div>
              <div className="min-w-0 flex-1">
                <AnimatePresence mode="wait">
                  {!t.a ? (
                    <motion.div key="think" exit={{ opacity: 0 }} className="flex items-center gap-1.5 py-2">
                      {[0, 1, 2].map((d) => <motion.span key={d} className="size-1.5 rounded-full bg-sky" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1, repeat: Infinity, delay: d * 0.18 }} />)}
                      <span className="ml-2 text-xs text-ink3">Reading SI and BL…</span>
                    </motion.div>
                  ) : (
                    <motion.div key="ans" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                      {t.a.shipment && <div className="mb-3 flex items-center gap-2"><Badge tone="info">{t.a.shipment}</Badge></div>}
                      <Answer a={t.a} go={go} />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(q) }} className="sticky bottom-4 flex items-center gap-2 rounded-xl border border-white/12 bg-card/95 p-2 pl-4 shadow-2xl backdrop-blur transition focus-within:border-sky/50">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about a shipment, e.g. Why is SHP-2048 flagged?" className="h-9 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-ink3" />
        <Button type="submit" variant="primary" disabled={!q.trim() || busy} aria-label="Send" className="!px-3"><ArrowUp className="size-4" /></Button>
      </form>
    </div>
  )
}
