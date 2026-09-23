import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUp, ChevronRight, FileText, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Typewriter, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { CopilotReply } from '@/lib/types'
import { REASON_LABEL, SCORE_HINT, cn, evidenceRef, explain, fmtValue } from '@/lib/utils'

interface Turn { q: string; a?: CopilotReply }
type Go = (p: Page, id?: string, auto?: boolean) => void

const LINK_LABEL = { cases: 'Open cases', compliance: 'Open compliance gate', analytics: 'Open analytics' } as const
const STAT_TONE = { ok: 'text-emerald-300', bad: 'text-red-300', warn: 'text-amber-300' } as const

function Block({ label, children, i, tone }: { label: string; children: React.ReactNode; i: number; tone?: 'bad' | 'ok' | 'info' }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.12, duration: 0.35, ease }} className={cn('rounded-lg border px-4 py-3', tone === 'bad' ? 'border-bad/20 bg-bad/[0.05]' : tone === 'ok' ? 'border-ok/20 bg-ok/[0.05]' : tone === 'info' ? 'border-sky/20 bg-sky/[0.05]' : 'border-white/[0.09] bg-white/[0.025]')}>
      <div className="eyebrow mb-1">{label}</div>
      <div className="text-[13.5px]">{children}</div>
    </motion.div>
  )
}

function Mismatch({ a, go }: { a: CopilotReply; go: Go }) {
  const issues = a.issues ?? []
  return (
    <div className="space-y-2.5">
      <Block i={0} label={issues.length === 1 ? 'Issue' : `${issues.length} issues`} tone="bad">{issues.map((i) => `${i.label} mismatch`).join(' · ')}</Block>
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.12, duration: 0.35, ease }} className="overflow-hidden rounded-lg border border-white/[0.09]">
        <div className="grid grid-cols-[minmax(110px,0.8fr)_1fr_1fr] gap-3 border-b border-white/[0.07] bg-white/[0.025] px-4 py-2">
          {['Field', 'SI', 'Draft BL'].map((h) => <div key={h} className="eyebrow">{h}</div>)}
        </div>
        {issues.map((i) => (
          <div key={i.key} className="border-b border-white/[0.05] px-4 py-2.5 last:border-0">
            <div className="grid grid-cols-[minmax(110px,0.8fr)_1fr_1fr] gap-3 text-[13px]">
              <div className="font-medium">{i.label}</div>
              <div className="num min-w-0 break-words font-semibold text-emerald-300">{fmtValue(i.key, i.si)}<span className="ml-1.5 text-[11px] font-normal text-ink3">{evidenceRef(i.si_evidence)}</span></div>
              <div className="num min-w-0 break-words font-semibold text-red-300">{fmtValue(i.key, i.bl)}<span className="ml-1.5 text-[11px] font-normal text-ink3">{evidenceRef(i.bl_evidence)}</span></div>
            </div>
            <div className="mt-1 text-[12.5px] text-ink2">{explain(i)}</div>
          </div>
        ))}
      </motion.div>
      <Block i={2} label="Evidence">
        <div className="space-y-1">
          {(a.evidence ?? []).map((f) => (
            <div key={f} className="flex items-center gap-2 text-[12.5px] text-ink2"><FileText className="size-3.5 text-ink3" />{f.replace(/^[a-z]+_\d+_/i, '')}</div>
          ))}
        </div>
      </Block>
      {a.handling && <Block i={3} label="Handling">{a.handling}</Block>}
      <Block i={4} label="Recommendation" tone="info">{a.recommendation}</Block>
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.7 }} className="flex gap-2 pt-1">
        <Button size="sm" variant="primary" onClick={() => go('cases', a.email)}>Open case</Button>
        <Button size="sm" onClick={() => go('verification', a.email, true)}>Replay verification</Button>
      </motion.div>
    </div>
  )
}

function DatasetAnswer({ a, go }: { a: CopilotReply; go: Go }) {
  const max = Math.max(1, ...(a.breakdown ?? []).map((b) => b.count))
  const open = (email: string) => (a.link === 'compliance' ? go('verification', email) : go('cases', email))
  return (
    <div className="space-y-3">
      <div>
        {a.title && <div className="eyebrow mb-1">{a.title}</div>}
        <p className="text-[13.5px]"><Typewriter text={a.message ?? ''} speed={10} /></p>
      </div>
      {a.stats && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {a.stats.map((s, k) => (
            <motion.div key={s.label} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: k * 0.05, duration: 0.3, ease }} className="rounded-lg border border-white/[0.09] bg-white/[0.025] px-3 py-2.5">
              <div className={cn('num text-lg font-semibold', s.tone && STAT_TONE[s.tone])}>{s.value.toLocaleString('en-US')}</div>
              <div className="text-[11.5px] text-ink3">{s.label}</div>
            </motion.div>
          ))}
        </div>
      )}
      {a.breakdown && (
        <div className="space-y-2 rounded-lg border border-white/[0.09] bg-white/[0.025] px-4 py-3">
          {a.breakdown.map((b, k) => (
            <div key={b.label}>
              <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
                <span className="min-w-0 truncate">{b.label}</span>
                <span className="num shrink-0 font-semibold">{b.count}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                <motion.div initial={{ width: 0 }} animate={{ width: `${(b.count / max) * 100}%` }} transition={{ delay: 0.1 + k * 0.05, duration: 0.5, ease }} className="h-full rounded-full bg-sky/70" />
              </div>
              {b.note && <div className="mt-0.5 text-[11.5px] text-ink3">{b.note}</div>}
            </div>
          ))}
        </div>
      )}
      {a.items && a.items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-white/[0.09]">
          {a.items.map((it) => (
            <button key={it.email} onClick={() => open(it.email)} className="group flex w-full items-center gap-3 border-b border-white/[0.05] px-4 py-2 text-left transition last:border-0 hover:bg-white/[0.04]">
              <span className="num w-[84px] shrink-0 text-[12.5px] font-semibold">{it.shipment}</span>
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink2">{it.note}</span>
              <ChevronRight className="size-3.5 shrink-0 text-ink3 transition group-hover:translate-x-0.5 group-hover:text-ink" />
            </button>
          ))}
          {!!a.more && <div className="px-4 py-2 text-[12px] text-ink3">and {a.more} more</div>}
        </div>
      )}
      {a.link && <div className="pt-0.5"><Button size="sm" onClick={() => go(a.link!)}>{LINK_LABEL[a.link]}</Button></div>}
    </div>
  )
}

function Answer({ a, go, send }: { a: CopilotReply; go: Go; send: (q: string) => void }) {
  if (a.kind === 'mismatch' && a.issues) return <Mismatch a={a} go={go} />
  if (a.kind === 'dataset') return <DatasetAnswer a={a} go={go} />
  if (a.kind === 'review')
    return (
      <div className="space-y-2.5">
        <Block i={0} label="Issue">{REASON_LABEL[a.reason as keyof typeof REASON_LABEL] ?? a.reason}</Block>
        {!!a.missing?.length && <Block i={1} label="Not found in the documents">{a.missing.join(' · ')}</Block>}
        <Block i={2} label="Extraction score"><span title={SCORE_HINT}>{Math.round((a.confidence ?? 0) * 100)}% · heuristic; the case is routed by its reason, not by this score</span></Block>
        {a.handling && <Block i={3} label="Handling">{a.handling}</Block>}
        <Block i={4} label="Recommendation" tone="info">{a.recommendation}</Block>
        <div className="pt-1"><Button size="sm" onClick={() => go('cases', a.email)}>Open case</Button></div>
      </div>
    )
  if (a.kind === 'clear')
    return (
      <div className="space-y-2.5">
        <Block i={0} label="Status" tone="ok">{a.message}</Block>
        <div className="pt-1"><Button size="sm" onClick={() => go('compliance', a.email)}>Open compliance gate</Button></div>
      </div>
    )
  return (
    <div className="space-y-3">
      <p className="text-[13.5px] text-ink2"><Typewriter text={a.message ?? ''} speed={12} /></p>
      {!!a.examples?.length && (
        <div className="flex flex-wrap gap-2">
          {a.examples.map((s) => (
            <button key={s} onClick={() => send(s)} className="rounded-full border border-white/12 bg-white/[0.03] px-3 py-1.5 text-[12px] text-ink2 transition hover:border-white/25 hover:text-ink">{s}</button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Copilot({ go }: { go: Go }) {
  const { ask, emails } = useApp()
  const [turns, setTurns] = useState<Turn[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  const mism = emails.find((e) => e.category === 'BL_COMPARISON' && e.status === 'MISMATCH')
  const suggestions = [
    ...(mism ? [`Why is ${mism.shipment} flagged?`] : []),
    'How many mismatches are there?',
    'Which cases need a person?',
    'What amendments did we send today?',
    'Which sender has the most errors?',
    'What is the most common mismatch field?',
  ]

  // The shipment the latest answer was about, so "why is it flagged?" can follow up on it.
  const context = [...turns].reverse().find((t) => t.a?.email)?.a?.email

  const send = async (text: string) => {
    if (!text.trim() || busy) return
    setQ('')
    setBusy(true)
    setTurns((t) => [...t, { q: text }])
    const a = await ask(text, context)
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
        <p className="mt-1 text-[13px] text-ink2">Ask about one shipment or the whole inbox. Answers are read from the verification results and the amendment log — never generated. Questions phrased in other ways are translated by Gemini into a supported query (only your question is sent).</p>
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
                      <span className="ml-2 text-xs text-ink3">Reading the results…</span>
                    </motion.div>
                  ) : (
                    <motion.div key="ans" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                      {t.a.interpreted_as && (
                        <div className="mb-2 text-[11.5px] text-ink3" title="Gemini only chose which query to run; the answer is computed from the results.">
                          Interpreted as <span className="text-ink2">“{t.a.interpreted_as}”</span> · translated by {t.a.via || 'Gemini'}
                        </div>
                      )}
                      {t.a.shipment && <div className="mb-3 flex items-center gap-2"><Badge tone="info">{t.a.shipment}</Badge></div>}
                      <Answer a={t.a} go={go} send={send} />
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
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about a shipment or the inbox, e.g. Which cases need a person?" className="h-9 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-ink3" />
        <Button type="submit" variant="primary" disabled={!q.trim() || busy} aria-label="Send" className="!px-3"><ArrowUp className="size-4" /></Button>
      </form>
    </div>
  )
}
