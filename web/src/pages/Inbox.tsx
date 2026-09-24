import { AnimatePresence, motion } from 'framer-motion'
import { Download, FileText, Mail, Paperclip, Search, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useFly } from '@/components/Fly'
import { SimulateGmailModal } from '@/components/SimulateGmailModal'
import { Badge, Button, Empty, PageHeader, StatusBadge, type Tone, ease } from '@/components/ui'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import type { Category, EmailDetail, EmailRow } from '@/lib/types'
import { CATEGORY_LABEL, cn, senderName } from '@/lib/utils'

const CAT_TONE: Record<Category, Tone> = { BL_COMPARISON: 'info', SI_REQUEST: 'brand', INVOICE_QUERY: 'neutral', GENERAL: 'neutral', SPAM: 'warn' }
const CATS: (Category | 'ALL')[] = ['ALL', 'BL_COMPARISON', 'SI_REQUEST', 'INVOICE_QUERY', 'GENERAL', 'SPAM']

function Expanded({ row, go }: { row: EmailRow; go: (p: Page, id?: string, auto?: boolean) => void }) {
  const { getDetail } = useApp()
  const fly = useFly()
  const [d, setD] = useState<EmailDetail | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    let a = true
    getDetail(row.id).then((x) => a && setD(x))
    return () => {
      a = false
    }
  }, [row.id, getDetail])
  return (
    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.35, ease }} className="overflow-hidden">
      <div className="grid gap-6 border-t border-white/[0.06] bg-white/[0.015] px-6 py-5 lg:grid-cols-[1.6fr_1fr]">
        <motion.div initial={{ x: 18, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 0.08, duration: 0.35, ease }}>
          <div className="eyebrow mb-2">Message</div>
          <div className="mb-3 text-[13px] font-medium">{row.subject}</div>
          <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap font-sans text-[12.5px] leading-relaxed text-ink2">{d?.body ?? 'Loading…'}</pre>
        </motion.div>
        <motion.div initial={{ x: 18, opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ delay: 0.14, duration: 0.35, ease }} className="space-y-5">
          <div>
            <div className="eyebrow mb-2">Classification</div>
            <div className="mb-1.5 flex items-center justify-between">
              <Badge tone={CAT_TONE[row.category]}>{CATEGORY_LABEL[row.category]}</Badge>
              <span className="text-[11px] text-ink3">rule-based</span>
            </div>
            <div className="text-[11.5px] text-ink3">Decided by fixed rules on the subject, body and attachment names.</div>
          </div>
          <div>
            <div className="eyebrow mb-2">Attachments</div>
            {row.attachments.length === 0 && <div className="text-xs text-ink3">No attachments</div>}
            <div className="space-y-2">
              {row.attachments.map((a, i) => (
                <motion.div key={a} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 + i * 0.08 }} className="flex items-center gap-3 rounded-lg border border-white/[0.09] bg-white/[0.03] px-3 py-2.5">
                  <FileText className="size-4 text-ink3" strokeWidth={1.6} />
                  <div className="min-w-0 flex-1">
                    <div className="num truncate text-[12px]">{a}</div>
                    <div className="text-[10.5px] text-ink3">{/_SI/i.test(a) ? 'Shipping Instruction' : /_BL/i.test(a) ? 'Draft Bill of Lading' : 'Attachment'}</div>
                  </div>
                  {/_SI|_BL/i.test(a) && <Badge tone={/_SI/i.test(a) ? 'ok' : 'info'}>{/_SI/i.test(a) ? 'SI' : 'BL'}</Badge>}
                </motion.div>
              ))}
            </div>
          </div>
          {row.category === 'BL_COMPARISON' && (
            <Button
              ref={btn}
              variant="primary"
              className="w-full"
              onClick={() => btn.current && fly(btn.current, 'nav-verification', `${row.shipment} · SI + BL`, () => go('verification', row.id, true))}
            >
              Verify documents
            </Button>
          )}
        </motion.div>
      </div>
    </motion.div>
  )
}

export default function Inbox({ go, initialId }: { go: (p: Page, id?: string, auto?: boolean) => void; initialId?: string }) {
  const { emails, resolutions } = useApp()
  const [q, setQ] = useState('')
  const [cat, setCat] = useState<Category | 'ALL'>('ALL')
  const [open, setOpen] = useState<string | null>(initialId ?? null)
  const [limit, setLimit] = useState(40)
  const [simOpen, setSimOpen] = useState(false)
  const [highlightId, setHighlightId] = useState<string | null>(null)

  useEffect(() => {
    if (initialId) {
      setOpen(initialId)
      setCat('ALL')
      setQ('')
    }
  }, [initialId])

  const counts = useMemo(() => {
    const m: Record<string, number> = { ALL: emails.length }
    emails.forEach((e) => (m[e.category] = (m[e.category] ?? 0) + 1))
    return m
  }, [emails])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return emails.filter((e) => (cat === 'ALL' || e.category === cat) && (!s || e.subject.toLowerCase().includes(s) || e.sender.toLowerCase().includes(s) || e.shipment.toLowerCase().includes(s)))
  }, [emails, q, cat])

  useEffect(() => {
    setLimit(40)
  }, [q, cat])
  const shown = Math.max(limit, (open ? list.findIndex((e) => e.id === open) : -1) + 1)

  const exportCsv = () => {
    const headers = ['Email ID', 'Shipment', 'Sender', 'Subject', 'Category', 'Status', 'Review Reason', 'Defect Fields', 'Attachments']
    const rows = emails.map((e) => [
      e.id,
      e.shipment,
      `"${(e.sender || '').replace(/"/g, '""')}"`,
      `"${(e.subject || '').replace(/"/g, '""')}"`,
      e.category,
      e.status,
      e.review_reason || '',
      `"${(e.defect_fields || []).join('; ')}"`,
      `"${(e.attachments || []).join('; ')}"`,
    ])
    const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
    const encodedUri = encodeURI(csvContent)
    const link = document.createElement('a')
    link.setAttribute('href', encodedUri)
    link.setAttribute('download', `navisai_audit_report_${new Date().toISOString().slice(0, 10)}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  return (
    <div>
      <PageHeader
        title="Inbox Triage"
        sub="Every inbound message is classified on arrival. Select an email to inspect its attachments and send it into verification."
        right={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              icon={<Zap className="size-3.5 text-yellow-300" />}
              onClick={() => setSimOpen(true)}
            >
              Simulate Inbound Gmail
            </Button>
            <Button size="sm" icon={<Download className="size-3.5" />} onClick={exportCsv}>
              Export CSV Report
            </Button>
          </div>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex h-9 min-w-[280px] flex-1 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 transition focus-within:border-sky/50 md:max-w-sm">
          <Search className="size-4 text-ink3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search sender, subject, shipment…" className="w-full bg-transparent text-[13px] outline-none placeholder:text-ink3" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {CATS.map((c) => (
            <button key={c} onClick={() => setCat(c)} className={cn('relative rounded-full border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] transition', cat === c ? 'border-white/25 text-ink' : 'border-white/10 text-ink3 hover:border-white/20 hover:text-ink2')}>
              {cat === c && <motion.span layoutId="inbox-cat" className="absolute inset-0 rounded-full bg-white/[0.08]" />}
              <span className="relative">{c === 'ALL' ? 'All' : CATEGORY_LABEL[c]} <span className="num text-ink3">{counts[c] ?? 0}</span></span>
            </button>
          ))}
        </div>
      </div>

      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,2.4fr)_120px_150px_116px] gap-4 border-b border-white/[0.07] px-6 py-3 max-lg:hidden">
          {['Sender', 'Subject', 'Received', 'Category', 'Status'].map((h) => <div key={h} className="eyebrow">{h}</div>)}
        </div>
        {list.length === 0 && <Empty icon={<Mail className="size-6" />} title="No emails match your filters" />}
        <motion.div key={cat + q} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.02 } } }}>
          {list.slice(0, shown).map((e, i) => {
            const isOpen = open === e.id
            const isHighlighted = highlightId === e.id
            const t = new Date(Date.UTC(2026, 8, 20, 6, 0) - i * 137000)
            return (
              <motion.div key={e.id} variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0 } }} className="border-b border-white/[0.05] last:border-0">
                <button
                  onClick={() => setOpen(isOpen ? null : e.id)}
                  className={cn(
                    'grid w-full grid-cols-[minmax(0,1.1fr)_minmax(0,2.4fr)_120px_150px_116px] items-center gap-4 px-6 py-3 text-left transition-all max-lg:grid-cols-[1fr_auto]',
                    isOpen ? 'bg-white/[0.05]' : 'hover:bg-white/[0.03]',
                    isHighlighted && 'bg-sky/[0.08] ring-1 ring-inset ring-sky/50 shadow-[0_0_15px_rgba(56,189,248,0.15)]'
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className={cn('size-1.5 shrink-0 rounded-full transition-colors', isOpen ? 'bg-brand' : e.category === 'BL_COMPARISON' ? 'bg-sky' : 'bg-white/20')} />
                    <span className="truncate text-[13px] font-medium capitalize">{senderName(e.sender)}</span>
                    {e.id.startsWith('gmail_') && (
                      <span className="rounded border border-sky/30 bg-sky/10 px-1.5 py-0.2 text-[9px] font-bold uppercase tracking-wider text-sky">
                        Live Ingest
                      </span>
                    )}
                  </div>
                  <div className="flex min-w-0 items-center gap-2 max-lg:hidden">
                    <span className="truncate text-[13px] text-ink2">{e.subject}</span>
                    {e.attachments.length > 0 && <Paperclip className="size-3 shrink-0 text-ink3" />}
                  </div>
                  <div className="num text-[11.5px] text-ink3 max-lg:hidden">{t.toISOString().slice(11, 16)} UTC</div>
                  <div className="max-lg:hidden"><Badge tone={CAT_TONE[e.category]}>{CATEGORY_LABEL[e.category]}</Badge></div>
                  <div>{e.category === 'BL_COMPARISON' ? <StatusBadge status={e.status} resolved={!!resolutions[e.id]} awaiting={e.awaiting_documents} /> : <Badge>Filed</Badge>}</div>
                </button>
                <AnimatePresence initial={false}>{isOpen && <Expanded row={e} go={go} />}</AnimatePresence>
              </motion.div>
            )
          })}
        </motion.div>
        {list.length > shown && (
          <div className="border-t border-white/[0.06] p-3 text-center">
            <Button variant="ghost" onClick={() => setLimit((l) => l + 60)}>Show more ({list.length - shown} remaining)</Button>
          </div>
        )}
      </div>

      <SimulateGmailModal
        open={simOpen}
        onClose={() => setSimOpen(false)}
        onSimulated={(id) => {
          setQ('')
          setCat('ALL')
          setLimit((l) => Math.max(l, 40))
          setOpen(id)
          setHighlightId(id)
          setTimeout(() => {
            setOpen(id)
          }, 60)
          setTimeout(() => {
            setHighlightId((cur) => (cur === id ? null : cur))
          }, 3500)
          window.scrollTo({ top: 0, behavior: 'smooth' })
        }}
      />
    </div>
  )
}
