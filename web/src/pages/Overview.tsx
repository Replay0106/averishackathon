import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUpRight, Map } from 'lucide-react'
import { LivePipeline } from '@/components/LivePipeline'
import { Badge, Panel, SectionTitle, StatusBadge, rise, stagger } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import { cn, FIELD_LABEL, greeting, isFlagged, useCountUp } from '@/lib/utils'

function Kpi({ label, value, decimals = 0, suffix = '', hint, tone }: { label: string; value: number; decimals?: number; suffix?: string; hint: string; tone?: string }) {
  const v = useCountUp(value, 1300, decimals)
  return (
    <motion.div variants={rise} className="bezel" whileHover={{ y: -2 }} transition={{ duration: 0.2 }}>
      <div className="core px-4 py-3">
        <div className="eyebrow">{label}</div>
        <div className={cn('num mt-1.5 text-[26px] font-semibold leading-none tracking-tight', tone)}>
          {v}
          {suffix}
        </div>
        <div className="mt-2 text-[11px] text-ink3">{hint}</div>
      </div>
    </motion.div>
  )
}

export default function Overview({ go }: { go: (p: Page, id?: string, auto?: boolean) => void }) {
  const { summary, emails, feed, resolutions } = useApp()
  if (!summary) return null
  const flagged = emails.filter(isFlagged)
  const coverage = summary.comparisons ? ((summary.comparisons - summary.needs_review) / summary.comparisons) * 100 : 0
  const resolvedCount = Object.keys(resolutions).length
  const top = flagged.filter((e) => !resolutions[e.id]).slice(0, 6)

  return (
    <div>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow mb-2 text-brand">NavisAI · Autonomous Trade Intelligence</div>
          <h1 className="text-[28px] font-semibold tracking-tight">{greeting()}, Operations Team</h1>
          <p className="mt-1 text-[13.5px] text-ink2">Your document verification operations are running normally.</p>
        </div>
        <button onClick={() => go('roadmap')} className="group flex items-center gap-2 rounded-lg border border-white/10 px-3.5 py-2 text-xs text-ink2 transition hover:border-white/20 hover:text-ink">
          <Map className="size-3.5" /> Platform roadmap <ArrowUpRight className="size-3.5 transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
        </button>
      </div>

      <motion.div variants={stagger} initial="hidden" animate="show" className="mb-6 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Emails Processed" value={summary.total} hint={`${summary.comparisons} document checks`} />
        <Kpi label="Discrepancies" value={summary.mismatch} tone="text-red-300" hint="SI vs BL field mismatches" />
        <Kpi label="Auto-Verified" value={summary.ok} tone="text-emerald-300" hint="All 7 fields consistent" />
        <Kpi label="Extraction Coverage" value={coverage} decimals={1} suffix="%" hint={`${summary.needs_review} routed to human review`} />
      </motion.div>

      <Panel className="mb-6 px-8 py-6">
        <SectionTitle eyebrow="Live" title="Verification pipeline" right={<Badge tone="info" dot>Processing</Badge>} />
        <LivePipeline inbox={summary.total} classified={summary.total} extracted={summary.comparisons} verified={summary.ok} resolved={resolvedCount} />
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[1fr_1.15fr]">
        <Panel className="p-5">
          <SectionTitle eyebrow="Operations" title="Live activity" right={<span className="flex items-center gap-1.5 text-[11px] text-ink3"><span className="breathe size-1.5 rounded-full bg-ok" /> Streaming</span>} />
          <ul className="space-y-0.5">
            <AnimatePresence initial={false}>
              {feed.slice(0, 8).map((f) => (
                <motion.li key={f.id} layout initial={{ opacity: 0, y: -14, height: 0 }} animate={{ opacity: 1, y: 0, height: 'auto' }} exit={{ opacity: 0 }} transition={{ duration: 0.35 }} className="overflow-hidden">
                  <div className="flex items-start gap-3 py-2">
                    <span className="num w-[62px] shrink-0 pt-px text-[11px] text-ink3">{f.time}</span>
                    <span className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', { ok: 'bg-ok', info: 'bg-sky', warn: 'bg-warn', bad: 'bg-bad' }[f.tone])} />
                    <span className="text-[13px] text-ink2">{f.text}</span>
                  </div>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </Panel>

        <Panel className="p-5">
          <SectionTitle eyebrow="Attention" title="Open items" right={<button onClick={() => go('cases')} className="text-xs text-ink3 transition hover:text-ink">View cases →</button>} />
          <motion.div variants={stagger} initial="hidden" animate="show" className="divide-y divide-white/[0.06]">
            {top.map((e) => (
              <motion.button key={e.id} variants={rise} onClick={() => go('verification', e.id)} className="group flex w-full items-center gap-4 py-3 text-left">
                <span className="num w-[76px] shrink-0 text-[12px] text-ink">{e.shipment}</span>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink2 transition group-hover:text-ink">
                  {e.status === 'MISMATCH' ? e.defect_fields.map((f) => FIELD_LABEL[f]).join(', ') : e.review_reason?.replace(/_/g, ' ')}
                </span>
                <StatusBadge status={e.status} awaiting={e.awaiting_documents} />
              </motion.button>
            ))}
          </motion.div>
        </Panel>
      </div>
    </div>
  )
}
