import { AnimatePresence, motion } from 'framer-motion'
import { ShieldCheck, ShieldAlert } from 'lucide-react'
import { useEffect } from 'react'
import { Badge, Empty, PageHeader } from '@/components/ui'
import { useApp } from '@/lib/store'

export default function Audit() {
  const { audit, refreshAudit } = useApp()
  useEffect(() => {
    refreshAudit()
  }, [refreshAudit])
  return (
    <div>
      <PageHeader
        title="Audit Trail"
        sub="Every human decision and dispatch is appended to a SHA-256 hash-chained ledger. Any tampering breaks the chain."
        right={
          <Badge tone={audit.integrity ? 'ok' : 'bad'} dot>
            {audit.integrity ? <><ShieldCheck className="size-3" /> Ledger intact</> : <><ShieldAlert className="size-3" /> Integrity failure</>}
          </Badge>
        }
      />
      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[70px_170px_minmax(0,1.3fr)_120px_minmax(0,1.4fr)] gap-4 border-b border-white/[0.07] px-6 py-3 max-lg:hidden">
          {['Block', 'Timestamp (UTC)', 'Action', 'Reference', 'Hash'].map((h) => <div key={h} className="eyebrow">{h}</div>)}
        </div>
        {audit.blocks.length === 0 && <Empty icon={<ShieldCheck className="size-6" />} title="No entries yet" />}
        <AnimatePresence initial={false}>
          {audit.blocks.map((b) => (
            <motion.div key={b.index} layout initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="grid grid-cols-[70px_170px_minmax(0,1.3fr)_120px_minmax(0,1.4fr)] items-center gap-4 border-b border-white/[0.05] px-6 py-3 last:border-0 max-lg:grid-cols-[60px_1fr]">
              <span className="num text-[12px] text-ink3">#{b.index}</span>
              <span className="num text-[11.5px] text-ink2 max-lg:hidden">{b.timestamp.replace('T', ' ').slice(0, 19)}</span>
              <span className="text-[13px]">
                {b.action.replace(/_/g, ' ')}
                <span className="ml-2 text-xs text-ink3">{b.actor}</span>
              </span>
              <span className="num text-[12px] text-ink2 max-lg:hidden">{b.email_id}</span>
              <span className="num truncate text-[11px] text-ink3 max-lg:hidden" title={b.block_hash}>{b.block_hash.slice(0, 28)}…</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
