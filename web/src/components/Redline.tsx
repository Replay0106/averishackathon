import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Check } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Typewriter, ease } from './ui'
import type { EmailDetail } from '@/lib/types'
import { cn, evidenceRef, explain, fmtValue } from '@/lib/utils'

export function Redline({ d }: { d: EmailDetail }) {
  const firstBad = d.comparison.findIndex((c) => !c.match)
  const [sel, setSel] = useState<number>(firstBad)
  useEffect(() => {
    setSel(d.comparison.findIndex((c) => !c.match))
  }, [d.id, d.comparison])
  const row = sel >= 0 ? d.comparison[sel] : null

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,1fr)]">
      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[1fr_72px_1fr] border-b border-white/[0.07] px-5 py-3">
          <div className="eyebrow">Shipping Instruction</div>
          <div />
          <div className="eyebrow text-right">Draft Bill of Lading</div>
        </div>
        <div>
          {d.comparison.map((c, i) => {
            const bad = !c.match
            const active = sel === i
            return (
              <motion.button
                key={c.key}
                onClick={() => setSel(i)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05, duration: 0.3, ease }}
                className={cn(
                  'relative grid w-full grid-cols-[1fr_72px_1fr] items-center gap-2 border-b border-white/[0.05] px-5 py-3.5 text-left transition-colors last:border-0',
                  active ? 'bg-white/[0.045]' : 'hover:bg-white/[0.025]',
                )}
              >
                {active && <motion.span layoutId="redline-sel" className={cn('absolute inset-y-0 left-0 w-[2px]', bad ? 'bg-bad' : 'bg-ok')} />}
                <div className="min-w-0">
                  <div className="eyebrow mb-1">{c.label}</div>
                  <div className={cn('num truncate text-[12.5px]', bad ? 'text-ink' : 'text-ink2')} title={String(c.si ?? '')}>
                    {bad && !c.missing ? <span className="rounded bg-ok/15 px-1.5 py-0.5 font-semibold text-emerald-300">{fmtValue(c.key, c.si)}</span> : fmtValue(c.key, c.si)}
                  </div>
                </div>
                <div className="relative flex h-full items-center justify-center">
                  <AnimatePresence>
                    {active && bad && !c.missing && (
                      <motion.svg key="line" width="72" height="20" viewBox="0 0 72 20" className="absolute overflow-visible">
                        <motion.path d="M0 10 H72" stroke="#EF4444" strokeWidth="1" strokeDasharray="3 3" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.5, ease }} />
                        <motion.circle cx="0" cy="10" r="2.5" fill="#EF4444" initial={{ scale: 0 }} animate={{ scale: 1 }} />
                        <motion.circle cx="72" cy="10" r="2.5" fill="#EF4444" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.4 }} />
                      </motion.svg>
                    )}
                  </AnimatePresence>
                  {!(active && bad) && (
                    <span className={cn('grid size-6 place-items-center rounded-full border', bad ? 'border-bad/40 bg-bad/10 text-red-300' : 'border-ok/30 bg-ok/10 text-ok')}>
                      {bad ? <span className="text-[11px] font-bold">!</span> : <Check className="size-3.5" strokeWidth={2.6} />}
                    </span>
                  )}
                </div>
                <div className="min-w-0 text-right">
                  <div className="eyebrow mb-1">&nbsp;</div>
                  <div className={cn('num truncate text-[12.5px]', bad ? 'text-ink' : 'text-ink2')} title={String(c.bl ?? '')}>
                    {bad && !c.missing ? <span className="rounded bg-bad/15 px-1.5 py-0.5 font-semibold text-red-300 line-through decoration-red-400/70">{fmtValue(c.key, c.bl)}</span> : fmtValue(c.key, c.bl)}
                  </div>
                </div>
              </motion.button>
            )
          })}
        </div>
      </div>

      <div className="panel h-fit p-5">
        <AnimatePresence mode="wait">
          {row && !row.match ? (
            <motion.div key={row.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
              <div className="mb-4 flex items-center gap-2">
                <AlertTriangle className="size-4 text-bad" />
                <span className="text-[13px] font-semibold uppercase tracking-[0.06em] text-red-300">{row.label} mismatch</span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3">
                  <div className="eyebrow mb-1">SI</div>
                  <div className="num break-words text-lg font-semibold text-emerald-300">{row.key === 'container_count' ? row.si : fmtValue(row.key, row.si)}</div>
                </div>
                <div className="rounded-lg border border-bad/25 bg-bad/[0.06] p-3">
                  <div className="eyebrow mb-1">BL</div>
                  <div className="num break-words text-lg font-semibold text-red-300">{row.key === 'container_count' ? row.bl : fmtValue(row.key, row.bl)}</div>
                </div>
              </div>
              <div className="mt-5">
                <div className="eyebrow mb-1.5">AI Explanation</div>
                <p className="text-[13px] leading-relaxed text-ink2">
                  “<Typewriter key={row.key} text={row.missing ? `The ${row.label.toLowerCase()} could not be located in one of the documents.` : explain(row)} speed={10} />”
                </p>
              </div>
              <div className="mt-5 space-y-2 border-t border-white/[0.07] pt-4">
                <div className="eyebrow">Source evidence</div>
                {[['SI', row.si_evidence], ['BL', row.bl_evidence]].map(([t, ev]) => {
                  const e = ev as typeof row.si_evidence
                  return (
                    <div key={t as string} className="num rounded-md bg-white/[0.03] px-3 py-2 text-[11px] text-ink2">
                      <span className="mr-2 text-ink3">{t as string} · {evidenceRef(e, (t === 'SI' ? d.si : d.bl)?.is_scanned)}</span>
                      {e?.snippet ?? 'not found'}
                    </div>
                  )
                })}
              </div>
            </motion.div>
          ) : (
            <motion.div key="clear" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="py-6 text-center">
              <Badge tone="ok" dot>{row ? `${row.label} verified` : 'All fields verified'}</Badge>
              <p className="mt-3 text-[13px] text-ink2">{row ? `${fmtValue(row.key, row.si)} matches on both documents.` : 'No discrepancies between the Shipping Instruction and the draft Bill of Lading.'}</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
