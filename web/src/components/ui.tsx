import { AnimatePresence, motion, type HTMLMotionProps } from 'framer-motion'
import { Check, Loader2, X } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { Status } from '@/lib/types'

export const spring = { type: 'spring', stiffness: 380, damping: 32 } as const
export const ease = [0.16, 1, 0.3, 1] as const

export const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.045, delayChildren: 0.04 } } }
export const rise = { hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: { duration: 0.35, ease } } }

/* ---------- Buttons ---------- */
type BtnProps = HTMLMotionProps<'button'> & {
  variant?: 'primary' | 'ghost' | 'outline' | 'danger' | 'success'
  size?: 'sm' | 'md'
  loading?: boolean
  icon?: ReactNode
}
export function Button({ variant = 'outline', size = 'md', loading, icon, className, children, disabled, ...p }: BtnProps) {
  const v = {
    primary: 'bg-brand text-white hover:bg-brand-deep shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]',
    outline: 'border border-white/12 bg-white/[0.03] text-ink hover:bg-white/[0.07] hover:border-white/20',
    ghost: 'text-ink2 hover:text-ink hover:bg-white/[0.06]',
    danger: 'border border-bad/30 bg-bad/10 text-red-300 hover:bg-bad/20',
    success: 'bg-ok/90 text-white hover:bg-ok',
  }[variant]
  return (
    <motion.button
      whileHover={disabled || loading ? undefined : { y: -1 }}
      whileTap={disabled || loading ? undefined : { scale: 0.975 }}
      transition={{ duration: 0.15 }}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
        size === 'sm' ? 'h-8 px-3 text-xs' : 'h-9 px-4 text-[13px]',
        v,
        className,
      )}
      {...p}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      {children as ReactNode}
    </motion.button>
  )
}

/* ---------- Badges ---------- */
const tones = {
  ok: 'bg-ok/12 text-emerald-300 border-ok/25',
  bad: 'bg-bad/12 text-red-300 border-bad/25',
  warn: 'bg-warn/12 text-amber-300 border-warn/25',
  info: 'bg-sky/10 text-sky border-sky/25',
  neutral: 'bg-white/[0.04] text-ink2 border-white/10',
  brand: 'bg-brand/12 text-orange-300 border-brand/30',
}
export type Tone = keyof typeof tones
export function Badge({ tone = 'neutral', children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <motion.span
      layout
      className={cn('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[10.5px] font-bold uppercase tracking-[0.08em] whitespace-nowrap transition-colors duration-300', tones[tone], className)}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </motion.span>
  )
}

export function statusMeta(s: Status | 'RESOLVED' | 'AWAITING') {
  return {
    OK: { tone: 'ok' as Tone, label: 'Verified' },
    AWAITING: { tone: 'neutral' as Tone, label: 'Awaiting docs' },
    MISMATCH: { tone: 'bad' as Tone, label: 'Discrepancy' },
    NEEDS_REVIEW: { tone: 'warn' as Tone, label: 'Needs review' },
    RESOLVED: { tone: 'info' as Tone, label: 'Resolved' },
  }[s]
}
// awaiting: a request to send the draft BL with nothing attached yet, so nothing was verified.
export function StatusBadge({ status, resolved, awaiting }: { status: Status; resolved?: boolean; awaiting?: boolean }) {
  const m = statusMeta(resolved ? 'RESOLVED' : awaiting ? 'AWAITING' : status)
  return (
    <Badge tone={m.tone} dot>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={m.label} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
          {m.label}
        </motion.span>
      </AnimatePresence>
    </Badge>
  )
}

/* ---------- Surfaces ---------- */
export function Panel({ className, children, hover, ...p }: HTMLMotionProps<'div'> & { hover?: boolean }) {
  return (
    <motion.div
      whileHover={hover ? { y: -2, borderColor: 'rgba(148,163,184,0.28)' } : undefined}
      transition={{ duration: 0.2 }}
      className={cn('panel', className)}
      {...p}
    >
      {children as ReactNode}
    </motion.div>
  )
}

export function SectionTitle({ eyebrow, title, right }: { eyebrow?: string; title: string; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <div>
        {eyebrow && <div className="eyebrow mb-0.5">{eyebrow}</div>}
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
      </div>
      {right}
    </div>
  )
}

export function PageHeader({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 max-w-2xl text-[13px] text-ink2">{sub}</p>}
      </div>
      {right}
    </div>
  )
}

export function Progress({ value, tone = 'sky', className }: { value: number; tone?: 'sky' | 'ok' | 'brand' | 'warn' | 'bad'; className?: string }) {
  const c = { sky: 'bg-sky', ok: 'bg-ok', brand: 'bg-brand', warn: 'bg-warn', bad: 'bg-bad' }[tone]
  return (
    <div className={cn('h-1 overflow-hidden rounded-full bg-white/[0.07]', className)}>
      <motion.div className={cn('h-full rounded-full', c)} initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} transition={{ duration: 0.9, ease }} />
    </div>
  )
}

export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 translate-y-1 whitespace-nowrap rounded-md border border-white/10 bg-sub px-2 py-1 text-[11px] text-ink opacity-0 shadow-lg transition-all duration-150 group-hover/tip:translate-y-0 group-hover/tip:opacity-100">
        {label}
      </span>
    </span>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="num rounded border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-ink2">{children}</kbd>
}

export function Empty({ icon, title, sub }: { icon: ReactNode; title: string; sub?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center text-ink3">
      <div className="text-ink3">{icon}</div>
      <div className="text-sm text-ink2">{title}</div>
      {sub && <div className="text-xs">{sub}</div>}
    </div>
  )
}

/* ---------- Overlays ---------- */
function useEsc(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
}

export function Drawer({ open, onClose, title, eyebrow, children, footer, width = 520 }: { open: boolean; onClose: () => void; title: string; eyebrow?: string; children: ReactNode; footer?: ReactNode; width?: number }) {
  useEsc(open, onClose)
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div key="scrim" className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside
            key="drawer"
            role="dialog"
            aria-label={title}
            className="fixed right-0 top-0 z-50 flex h-full max-w-full flex-col border-l border-white/10 bg-card shadow-2xl"
            style={{ width }}
            initial={{ x: width }}
            animate={{ x: 0 }}
            exit={{ x: width }}
            transition={{ type: 'spring', stiffness: 320, damping: 36 }}
          >
            <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-6 py-4">
              <div>
                {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
                <h3 className="text-base font-semibold tracking-tight">{title}</h3>
              </div>
              <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-ink3 transition hover:bg-white/[0.06] hover:text-ink">
                <X className="size-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
            {footer && <div className="border-t border-white/[0.08] px-6 py-4">{footer}</div>}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

export function Modal({ open, onClose, title, children, width = 560 }: { open: boolean; onClose: () => void; title: string; children: ReactNode; width?: number }) {
  useEsc(open, onClose)
  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
          <motion.div className="absolute inset-0 bg-black/60 backdrop-blur-[3px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.div
            role="dialog"
            aria-label={title}
            className="panel-glass relative max-h-[90vh] w-full overflow-y-auto bg-card"
            style={{ maxWidth: width }}
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.22, ease }}
          >
            {children}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}

export function SuccessCheck({ size = 44 }: { size?: number }) {
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <motion.span className="absolute inset-0 rounded-full bg-ok/20" initial={{ scale: 0.6, opacity: 0.8 }} animate={{ scale: 1.5, opacity: 0 }} transition={{ duration: 0.9 }} />
      <motion.span className="grid size-full place-items-center rounded-full border border-ok/40 bg-ok/15" initial={{ scale: 0.5 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 400, damping: 18 }}>
        <Check className="text-ok" style={{ width: size * 0.5, height: size * 0.5 }} strokeWidth={2.6} />
      </motion.span>
    </div>
  )
}

/* ---------- Typewriter ---------- */
import { useState } from 'react'
export function Typewriter({ text, speed = 14, start = true, onDone, className }: { text: string; speed?: number; start?: boolean; onDone?: () => void; className?: string }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!start) return
    setN(0)
    let i = 0
    const t = setInterval(() => {
      i += 1
      setN(i)
      if (i >= text.length) {
        clearInterval(t)
        onDone?.()
      }
    }, speed)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, start, speed])
  return <span className={cn(n < text.length && start && 'caret', className)}>{text.slice(0, n)}</span>
}

/* ---------- Toasts ---------- */
import { useApp } from '@/lib/store'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'
export function Toasts() {
  const { toasts, dismissToast } = useApp()
  const icon = { ok: <CheckCircle2 className="size-4 text-ok" />, info: <Info className="size-4 text-sky" />, warn: <AlertTriangle className="size-4 text-warn" />, bad: <XCircle className="size-4 text-bad" /> }
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[80] flex w-[340px] flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, x: 40, scale: 0.98 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40 }}
            transition={spring}
            className="panel-glass pointer-events-auto flex items-start gap-3 bg-card px-4 py-3"
            onClick={() => dismissToast(t.id)}
          >
            <div className="mt-0.5">{icon[t.tone]}</div>
            <div className="min-w-0">
              <div className="text-[13px] font-medium">{t.title}</div>
              {t.body && <div className="mt-0.5 text-xs text-ink2">{t.body}</div>}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
