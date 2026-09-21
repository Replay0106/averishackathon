import { motion } from 'framer-motion'
import { CheckCheck, FileSearch, Inbox, ScanText, Tags } from 'lucide-react'
import { useCountUp } from '@/lib/utils'

function Node({ icon: Icon, label, value, tone }: { icon: typeof Inbox; label: string; value: number; tone: string }) {
  const v = useCountUp(value, 1200)
  return (
    <div className="relative z-10 flex flex-col items-center gap-2">
      <div className={`grid size-11 place-items-center rounded-xl border bg-card ${tone}`}>
        <Icon className="size-[18px]" strokeWidth={1.7} />
      </div>
      <div className="num text-lg font-semibold leading-none">{v}</div>
      <div className="eyebrow">{label}</div>
    </div>
  )
}

function Link({ delay }: { delay: number }) {
  return (
    <div className="relative mx-1 mt-[22px] h-px flex-1 self-start bg-white/[0.09]">
      {[0, 1].map((k) => (
        <motion.span
          key={k}
          className="absolute -top-[1.5px] size-1 rounded-full bg-sky shadow-[0_0_8px_2px_rgba(56,189,248,0.55)]"
          initial={{ left: '0%', opacity: 0 }}
          animate={{ left: '100%', opacity: [0, 1, 1, 0] }}
          transition={{ duration: 2.4, repeat: Infinity, repeatDelay: 2.4 + delay, delay: delay + k * 3.1, ease: 'easeInOut' }}
        />
      ))}
    </div>
  )
}

export function LivePipeline({ inbox, classified, extracted, verified, resolved }: { inbox: number; classified: number; extracted: number; verified: number; resolved: number }) {
  return (
    <div className="flex items-start">
      <Node icon={Inbox} label="Email inbox" value={inbox} tone="border-white/12 text-ink2" />
      <Link delay={0} />
      <Node icon={Tags} label="Classified" value={classified} tone="border-sky/30 text-sky" />
      <Link delay={0.6} />
      <Node icon={ScanText} label="Extracted" value={extracted} tone="border-sky/30 text-sky" />
      <Link delay={1.2} />
      <Node icon={FileSearch} label="Verified" value={verified} tone="border-ok/30 text-ok" />
      <Link delay={1.8} />
      <Node icon={CheckCheck} label="Resolved" value={resolved} tone="border-brand/40 text-brand" />
    </div>
  )
}
