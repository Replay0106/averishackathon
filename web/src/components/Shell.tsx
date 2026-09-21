import { AnimatePresence, motion } from 'framer-motion'
import {
  Activity, BarChart3, Bot, CheckCircle2, ChevronRight, FileSearch, Gauge, GitCompareArrows, History, Inbox, Map, FolderPlus, RadioTower, Search, Settings, ShieldCheck, Sparkles, type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { LogoMark, Wordmark } from './Logo'
import { Badge, Button, Kbd, Modal, ease } from './ui'
import { DatasetSwitcher } from './ImportFolder'
import type { Page } from '@/lib/nav'
import { useApp } from '@/lib/store'
import { cn, CATEGORY_LABEL } from '@/lib/utils'

const MAIN: { page: Page; label: string; icon: LucideIcon }[] = [
  { page: 'overview', label: 'Overview', icon: Gauge },
  { page: 'inbox', label: 'Inbox Triage', icon: Inbox },
  { page: 'verification', label: 'Document Verification', icon: FileSearch },
  { page: 'cases', label: 'Cases', icon: GitCompareArrows },
  { page: 'compliance', label: 'Compliance Gate', icon: ShieldCheck },
  { page: 'analytics', label: 'Analytics', icon: BarChart3 },
  { page: 'audit', label: 'Audit Trail', icon: History },
  { page: 'gateway', label: 'Trust Gateway', icon: RadioTower },
]

function NavItem({ active, label, icon: Icon, onClick, id, badge }: { active: boolean; label: string; icon: LucideIcon; onClick: () => void; id?: string; badge?: number }) {
  return (
    <button
      id={id}
      onClick={onClick}
      title={label}
      className={cn('group relative flex h-9 w-full items-center gap-3 rounded-lg px-3 text-[13px] transition-colors', active ? 'text-ink' : 'text-ink2 hover:text-ink')}
    >
      {active && (
        <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg border border-white/[0.09] bg-white/[0.06]" transition={{ type: 'spring', stiffness: 420, damping: 34 }}>
          <span className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-brand" />
        </motion.span>
      )}
      {!active && <span className="absolute inset-0 rounded-lg opacity-0 transition-opacity group-hover:opacity-100 group-hover:bg-white/[0.035]" />}
      <motion.span className="relative" whileHover={{ scale: 1.1 }} animate={active ? { rotate: [0, -8, 0] } : {}} transition={{ duration: 0.35 }}>
        <Icon className={cn('size-[17px]', active && 'text-brand')} strokeWidth={1.8} />
      </motion.span>
      <span className="relative hidden truncate lg:block">{label}</span>
      {badge ? <span className="num relative ml-auto hidden rounded-full bg-white/[0.07] px-1.5 text-[10px] text-ink2 lg:block">{badge}</span> : null}
    </button>
  )
}

export function Sidebar({ page, go, onHealth }: { page: Page; go: (p: Page) => void; onHealth: () => void }) {
  const { emails, resolutions } = useApp()
  // cases waiting for a person: flagged comparisons with no automatic amendment and no decision yet
  const flagged = emails.filter((e) => e.category === 'BL_COMPARISON' && e.status !== 'OK' && !e.amendment && !resolutions[e.id]).length
  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-[68px] flex-col border-r border-white/[0.07] bg-[#0a0f1d] px-3 py-4 lg:w-[248px]">
      <button onClick={() => go('overview')} className="mb-6 flex items-center gap-3 px-1.5">
        <LogoMark size={32} animate />
        <span className="hidden lg:block"><Wordmark /></span>
      </button>
      <div className="eyebrow mb-2 hidden px-3 lg:block">Operations</div>
      <nav className="flex flex-col gap-0.5">
        {MAIN.map((n) => (
          <NavItem
            key={n.page}
            id={`nav-${n.page}`}
            active={page === n.page}
            label={n.label}
            icon={n.icon}
            onClick={() => go(n.page)}
            badge={n.page === 'cases' ? flagged : undefined}
          />
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-0.5 border-t border-white/[0.07] pt-3">
        <NavItem active={page === 'copilot'} label="Ask Navis Copilot" icon={Bot} onClick={() => go('copilot')} />
        <NavItem active={false} label="System Health" icon={Activity} onClick={onHealth} />
        <NavItem active={page === 'roadmap'} label="Roadmap" icon={Map} onClick={() => go('roadmap')} />
        <NavItem active={page === 'settings'} label="Settings" icon={Settings} onClick={() => go('settings')} />
      </div>
    </aside>
  )
}

export function StatusPill({ onClick }: { onClick: () => void }) {
  const { live } = useApp()
  return (
    <button onClick={onClick} className="group flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 transition hover:border-white/20 hover:bg-white/[0.06]" aria-label="System health">
      <span className="relative flex size-2">
        <span className="ring-pulse absolute inset-0 rounded-full bg-ok" />
        <span className="breathe relative size-2 rounded-full bg-ok" />
      </span>
      <span className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink2 group-hover:text-ink">NavisAI Online</span>
      {!live && <Badge tone="warn" className="!py-0">Snapshot</Badge>}
    </button>
  )
}

export function TopBar({ onPalette, onHealth, onImport }: { onPalette: () => void; onHealth: () => void; onImport: () => void }) {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-white/[0.06] bg-canvas/80 px-6 backdrop-blur-xl xl:px-10">
      <button onClick={onPalette} className="flex h-9 w-full max-w-md items-center gap-2.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 text-left text-[13px] text-ink3 transition hover:border-white/20 hover:bg-white/[0.05]">
        <Search className="size-4" />
        <span className="flex-1">Search shipments, emails, commands…</span>
        <Kbd>Ctrl</Kbd>
        <Kbd>K</Kbd>
      </button>
      <div className="ml-auto flex items-center gap-3">
        <DatasetSwitcher />
        <Button size="sm" className="!h-9" icon={<FolderPlus className="size-3.5" />} onClick={onImport}>Import folder</Button>
        <StatusPill onClick={onHealth} />
        <div className="grid size-8 place-items-center rounded-full border border-white/10 bg-sub text-[11px] font-semibold text-ink2">OP</div>
      </div>
    </header>
  )
}

export function SystemHealth({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { live, emails, audit } = useApp()
  const services = [
    ['AI Engine', live ? 'Operational' : 'Operational (snapshot)'],
    ['Document Extraction', 'Operational'],
    ['Verification Engine', 'Operational'],
    ['Carrier Gateway', 'Operational'],
    ['Database', audit.integrity ? 'Operational — ledger intact' : 'Degraded — ledger check failed'],
  ]
  return (
    <Modal open={open} onClose={onClose} title="System Health" width={460}>
      <div className="p-6">
        <div className="mb-1 flex items-center gap-2">
          <span className="relative flex size-2.5"><span className="ring-pulse absolute inset-0 rounded-full bg-ok" /><span className="breathe relative size-2.5 rounded-full bg-ok" /></span>
          <h3 className="text-base font-semibold">System Health</h3>
        </div>
        <p className="mb-5 text-xs text-ink3">{emails.length} emails indexed · data source: {live ? 'live pipeline API' : 'offline snapshot'}</p>
        <motion.ul initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }} className="divide-y divide-white/[0.06] rounded-lg border border-white/[0.08]">
          {services.map(([n, s]) => (
            <motion.li key={n} variants={{ hidden: { opacity: 0, x: -8 }, show: { opacity: 1, x: 0 } }} className="flex items-center justify-between px-4 py-3 text-[13px]">
              <span>{n}</span>
              <span className="flex items-center gap-2 text-emerald-300"><CheckCircle2 className="size-4" />{s}</span>
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </Modal>
  )
}

interface Cmd { id: string; label: string; hint: string; icon: LucideIcon; run: () => void; group: string }

export function CommandPalette({ open, onClose, go, onImport }: { open: boolean; onClose: () => void; go: (p: Page, id?: string, auto?: boolean) => void; onImport: () => void }) {
  const { emails } = useApp()
  const hero = emails.find((e) => e.status === 'MISMATCH')?.id ?? emails.find((e) => e.category === 'BL_COMPARISON')?.id
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)

  useEffect(() => {
    if (open) {
      setQ('')
      setSel(0)
    }
  }, [open])

  const commands: Cmd[] = useMemo(
    () => [
      { id: 'c1', group: 'Commands', label: 'Search shipment', hint: 'Type a SHP- id', icon: Search, run: () => setQ('SHP-') },
      { id: 'c2', group: 'Commands', label: 'Search email', hint: 'Open inbox triage', icon: Inbox, run: () => go('inbox') },
      { id: 'c3', group: 'Commands', label: 'Open cases', hint: 'Go to', icon: GitCompareArrows, run: () => go('cases') },
      { id: 'c4', group: 'Commands', label: 'Verify document', hint: 'Run live verification', icon: FileSearch, run: () => go('verification', hero, true) },
      { id: 'c10', group: 'Commands', label: 'Import folder', hint: 'Emails + attachments', icon: FolderPlus, run: onImport },
      { id: 'c5', group: 'Commands', label: 'Ask Navis', hint: 'Open copilot', icon: Sparkles, run: () => go('copilot') },
      { id: 'c6', group: 'Commands', label: 'Open analytics', hint: 'Go to', icon: BarChart3, run: () => go('analytics') },
      { id: 'c7', group: 'Commands', label: 'View audit trail', hint: 'Go to', icon: History, run: () => go('audit') },
      { id: 'c8', group: 'Commands', label: 'View roadmap', hint: 'Go to', icon: Map, run: () => go('roadmap') },
      { id: 'c9', group: 'Commands', label: 'Compliance gate', hint: 'Go to', icon: ShieldCheck, run: () => go('compliance') },
      { id: 'c11', group: 'Commands', label: 'Open trust gateway', hint: 'Go to', icon: RadioTower, run: () => go('gateway') },
    ],
    [go, hero, onImport],
  )

  const results: Cmd[] = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return commands
    const cmds = commands.filter((c) => c.label.toLowerCase().includes(s))
    const ships: Cmd[] = emails
      .filter((e) => e.shipment.toLowerCase().includes(s) || e.subject.toLowerCase().includes(s) || e.sender.toLowerCase().includes(s) || e.id.includes(s))
      .slice(0, 8)
      .map((e) => ({
        id: e.id,
        group: e.category === 'BL_COMPARISON' ? 'Shipments' : 'Emails',
        label: `${e.shipment} — ${e.subject.slice(0, 64)}`,
        hint: CATEGORY_LABEL[e.category],
        icon: e.category === 'BL_COMPARISON' ? FileSearch : Inbox,
        run: () => (e.category === 'BL_COMPARISON' ? go('verification', e.id) : go('inbox', e.id)),
      }))
    return [...cmds, ...ships]
  }, [q, commands, emails])

  useEffect(() => {
    setSel(0)
  }, [q])

  const choose = (c?: Cmd) => {
    if (!c) return
    if (c.id !== 'c1') onClose()
    c.run()
  }

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[90] flex items-start justify-center px-4 pt-[14vh]">
          <motion.div className="absolute inset-0 bg-black/60 backdrop-blur-[3px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.div
            className="panel-glass relative w-full max-w-xl overflow-hidden bg-card"
            initial={{ opacity: 0, scale: 0.96, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.2, ease }}
            role="dialog"
            aria-label="Command palette"
          >
            <div className="flex items-center gap-3 border-b border-white/[0.08] px-4">
              <Search className="size-4 text-ink3" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)) }
                  if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
                  if (e.key === 'Enter') choose(results[sel])
                  if (e.key === 'Escape') onClose()
                }}
                placeholder="Type a command or search a shipment…"
                className="h-12 flex-1 bg-transparent text-[14px] outline-none placeholder:text-ink3"
              />
              <Kbd>Esc</Kbd>
            </div>
            <div className="max-h-[52vh] overflow-y-auto p-2">
              {results.length === 0 && <div className="px-3 py-8 text-center text-sm text-ink3">No results for “{q}”</div>}
              {results.map((c, i) => {
                const showGroup = i === 0 || results[i - 1].group !== c.group
                return (
                  <div key={c.id + c.group}>
                    {showGroup && <div className="eyebrow px-3 pb-1 pt-2">{c.group}</div>}
                    <motion.button
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: Math.min(i, 8) * 0.025, duration: 0.2 }}
                      onMouseEnter={() => setSel(i)}
                      onClick={() => choose(c)}
                      className={cn('flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[13px] transition-colors', sel === i ? 'bg-white/[0.07] text-ink' : 'text-ink2')}
                    >
                      <c.icon className="size-4 shrink-0 text-ink3" strokeWidth={1.8} />
                      <span className="flex-1 truncate">{c.label}</span>
                      <span className="text-[11px] text-ink3">{c.hint}</span>
                      {sel === i && <ChevronRight className="size-3.5 text-ink3" />}
                    </motion.button>
                  </div>
                )
              })}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}
