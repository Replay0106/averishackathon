import { motion } from 'framer-motion'
import { useMemo } from 'react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { PageHeader, Panel, SectionTitle, rise, stagger } from '@/components/ui'
import { useApp } from '@/lib/store'
import { pct, useCountUp } from '@/lib/utils'

const SHORT: Record<string, string> = { shipper: 'Shipper', consignee: 'Consignee', notify_party: 'Notify', port_of_loading: 'POL', port_of_discharge: 'POD', container_count: 'Containers', gross_weight_kg: 'Weight' }

const AX = { stroke: '#64748B', fontSize: 11, tickLine: false, axisLine: false } as const
const TIP = { contentStyle: { background: '#0F172A', border: '1px solid rgba(148,163,184,0.2)', borderRadius: 8, fontSize: 12, color: '#F8FAFC' }, cursor: { fill: 'rgba(148,163,184,0.06)' }, itemStyle: { color: '#F8FAFC' }, labelStyle: { color: '#94A3B8' } }

function Stat({ label, value, suffix, decimals = 1, hint }: { label: string; value: number; suffix: string; decimals?: number; hint: string }) {
  const v = useCountUp(value, 1200, decimals)
  return (
    <motion.div variants={rise} className="bezel">
      <div className="core px-4 py-3.5">
        <div className="eyebrow">{label}</div>
        <div className="num mt-1.5 text-[26px] font-semibold leading-none">{v}<span className="ml-0.5 text-base text-ink3">{suffix}</span></div>
        <div className="mt-2 text-[11px] text-ink3">{hint}</div>
      </div>
    </motion.div>
  )
}

export default function Analytics() {
  const { summary, emails } = useApp()
  const comps = useMemo(() => emails.filter((e) => e.category === 'BL_COMPARISON'), [emails])

  const byField = useMemo(() => Object.entries(summary?.defect_fields ?? {}).map(([k, v]) => ({ name: SHORT[k] ?? k, v })).sort((a, b) => b.v - a.v), [summary])
  const byCarrier = useMemo(() => {
    const m: Record<string, number> = {}
    comps.filter((e) => e.status === 'MISMATCH').forEach((e) => (m[e.meta.carrier] = (m[e.meta.carrier] ?? 0) + 1))
    return Object.entries(m).map(([name, v]) => ({ name, v })).sort((a, b) => b.v - a.v)
  }, [comps])
  const volume = useMemo(() => {
    const size = Math.ceil(emails.length / 10)
    return Array.from({ length: 10 }, (_, i) => {
      const slice = emails.slice(i * size, (i + 1) * size).filter((e) => e.category === 'BL_COMPARISON')
      return { batch: `B${i + 1}`, checks: slice.length, flagged: slice.filter((e) => e.status !== 'OK').length }
    })
  }, [emails])
  const outcome = useMemo(() => volume.map((b) => ({ batch: b.batch, rate: b.checks ? +(((b.checks - b.flagged) / b.checks) * 100).toFixed(1) : 0 })), [volume])

  if (!summary) return null
  const auto = +pct(summary.ok, summary.comparisons)
  const human = +pct(summary.needs_review, summary.comparisons)

  return (
    <div>
      <PageHeader title="Analytics" sub="Computed live from the processed inbox. Batches are consecutive groups of ~52 emails in arrival order." />
      <motion.div variants={stagger} initial="hidden" animate="show" className="mb-6 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat label="Auto-resolution rate" value={auto} suffix="%" hint="Checks verified with no action needed" />
        <Stat label="Human review rate" value={human} suffix="%" hint="Escalated instead of guessed" />
        <Stat label="Discrepancy rate" value={+pct(summary.mismatch, summary.comparisons)} suffix="%" hint={`${summary.mismatch} of ${summary.comparisons} checks`} />
        <Stat label="Avg verification time" value={summary.avg_verify_ms ?? 0} suffix=" ms" decimals={1} hint="Per shipment, extraction + comparison" />
      </motion.div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel className="p-5">
          <SectionTitle eyebrow="Discrepancies" title="By field" />
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={byField} margin={{ left: -18, top: 8 }}>
                <CartesianGrid vertical={false} stroke="rgba(148,163,184,0.08)" />
                <XAxis dataKey="name" {...AX} interval={0} tick={{ fontSize: 10 }} />
                <YAxis {...AX} allowDecimals={false} />
                <Tooltip {...TIP} />
                <Bar dataKey="v" name="Discrepancies" radius={[4, 4, 0, 0]} animationDuration={900} maxBarSize={38}>
                  {byField.map((_, i) => <Cell key={i} fill={i === 0 ? '#F37021' : '#38BDF8'} fillOpacity={i === 0 ? 1 : 0.7} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel className="p-5">
          <SectionTitle eyebrow="Discrepancies" title="By carrier" right={<span className="text-[11px] text-ink3">inferred from booking prefix</span>} />
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={byCarrier} layout="vertical" margin={{ left: 20, right: 12, top: 8 }}>
                <CartesianGrid horizontal={false} stroke="rgba(148,163,184,0.08)" />
                <XAxis type="number" {...AX} allowDecimals={false} />
                <YAxis type="category" dataKey="name" {...AX} width={100} tick={{ fontSize: 11, fill: '#94A3B8' }} />
                <Tooltip {...TIP} />
                <Bar dataKey="v" name="Discrepancies" fill="#38BDF8" fillOpacity={0.75} radius={[0, 4, 4, 0]} animationDuration={900} maxBarSize={18} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel className="p-5">
          <SectionTitle eyebrow="Volume" title="Verification volume by batch" />
          <div className="h-64">
            <ResponsiveContainer>
              <AreaChart data={volume} margin={{ left: -18, top: 8 }}>
                <defs>
                  <linearGradient id="vg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#38BDF8" stopOpacity={0.25} /><stop offset="100%" stopColor="#38BDF8" stopOpacity={0} /></linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="rgba(148,163,184,0.08)" />
                <XAxis dataKey="batch" {...AX} />
                <YAxis {...AX} allowDecimals={false} />
                <Tooltip {...TIP} />
                <Area type="monotone" dataKey="checks" name="Document checks" stroke="#38BDF8" strokeWidth={2} fill="url(#vg)" animationDuration={1400} />
                <Area type="monotone" dataKey="flagged" name="Flagged" stroke="#EF4444" strokeWidth={1.5} fill="none" animationDuration={1400} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel className="p-5">
          <SectionTitle eyebrow="Rate" title="Auto-resolution rate by batch" />
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={outcome} margin={{ left: -12, top: 8, right: 8 }}>
                <CartesianGrid vertical={false} stroke="rgba(148,163,184,0.08)" />
                <XAxis dataKey="batch" {...AX} />
                <YAxis {...AX} domain={[0, 100]} unit="%" />
                <Tooltip {...TIP} />
                <Line type="monotone" dataKey="rate" name="Verified %" stroke="#10B981" strokeWidth={2} dot={{ r: 3, fill: '#0F172A', stroke: '#10B981', strokeWidth: 2 }} animationDuration={1600} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>
    </div>
  )
}
