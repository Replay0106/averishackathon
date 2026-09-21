import { motion } from 'framer-motion'
import { Check } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageHeader, ease } from '@/components/ui'
import { cn } from '@/lib/utils'

const STAGES = [
  { when: 'Today', name: 'Verify', items: ['SI vs BL', 'AI extraction', 'Redline comparison', 'Human review'] },
  { when: '2027', name: 'Understand', items: ['OCR', 'Vision AI', 'Scanned documents', 'Layout intelligence'] },
  { when: '2027–28', name: 'Connect', items: ['Carrier APIs', 'DCSA', 'EDI', 'Automated amendments'] },
  { when: '2028', name: 'Govern', items: ['TMS / ERP', 'Customs validation', 'Compliance intelligence'] },
  { when: '2029+', name: 'Autonomize', items: ['Predictive discrepancy detection', 'e-BL', 'Intelligent resolution', 'Human-approved autonomous workflows'] },
]

export default function Roadmap() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % STAGES.length), 2600)
    return () => clearInterval(t)
  }, [])
  const pos = (i + 0.5) * (100 / STAGES.length)

  return (
    <div>
      <PageHeader title="Roadmap" sub="From verifying documents today to human-approved autonomous workflows." />
      <div className="relative">
        <div className="absolute inset-x-[10%] top-[15px] hidden h-px bg-white/20 xl:block" />
        <motion.div className="absolute top-[11px] hidden size-2 -translate-x-1/2 rounded-full bg-sky shadow-[0_0_14px_4px_rgba(56,189,248,0.6)] xl:block" animate={{ left: `${pos}%` }} transition={{ duration: 1.1, ease }} />
        <div className="grid gap-4 xl:grid-cols-5">
          {STAGES.map((s, k) => {
            const active = k === i
            return (
              <motion.div key={s.name} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: k * 0.09, duration: 0.5, ease }} className="pt-0 xl:pt-9">
                <div className={cn('panel relative h-full p-5 transition-all duration-500', active ? 'border-sky/40 shadow-[0_0_0_1px_rgba(56,189,248,0.15),0_18px_40px_-20px_rgba(56,189,248,0.35)]' : k === 0 ? 'border-brand/30' : '')}>
                  <div className="mb-4 flex items-center justify-between">
                    <span className={cn('num text-[11px] font-semibold uppercase tracking-[0.12em]', k === 0 ? 'text-brand' : 'text-ink3')}>{s.when}</span>
                    {k === 0 && <span className="flex items-center gap-1 rounded-full bg-ok/12 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300"><Check className="size-3" strokeWidth={3} />Live</span>}
                  </div>
                  <div className="mb-4 text-xl font-semibold tracking-tight">{s.name.toUpperCase()}</div>
                  <ul className="space-y-2">
                    {s.items.map((it) => (
                      <li key={it} className="flex gap-2 text-[13px] text-ink2"><span className={cn('mt-[7px] size-1 shrink-0 rounded-full transition-colors', active ? 'bg-sky' : 'bg-white/25')} />{it}</li>
                    ))}
                  </ul>
                </div>
              </motion.div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
