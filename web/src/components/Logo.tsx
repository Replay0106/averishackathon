import { motion } from 'framer-motion'

export function LogoMark({ size = 32, animate = false }: { size?: number; animate?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-label="NavisAI">
      <rect width="32" height="32" rx="8" fill="#0F172A" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" stroke="rgba(148,163,184,0.22)" />
      <g strokeLinecap="round" strokeLinejoin="round">
        <motion.circle
          cx="16" cy="16" r="9.5" stroke="#334155" strokeWidth="1.4"
          initial={animate ? { pathLength: 0 } : false}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1.1, ease: 'easeOut' }}
        />
        <path d="M16 6.5v3M16 22.5v3M6.5 16h3M22.5 16h3" stroke="#64748B" strokeWidth="1.4" />
        <path d="M16 9.5 20.2 20.5 16 18l-4.2 2.5z" stroke="#F37021" strokeWidth="1.6" fill="#F37021" fillOpacity=".16" />
        <path d="M14.2 14.6h3.6M14.2 16.4h3.6" stroke="#F37021" strokeWidth="0.9" opacity=".7" />
      </g>
      <circle cx="16" cy="16" r="1.5" fill="#38BDF8" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="leading-none">
      <div className="text-[15px] font-semibold tracking-[0.14em]">
        NAVIS<span className="text-brand">AI</span>
      </div>
      <div className="mt-1 text-[9.5px] uppercase tracking-[0.16em] text-ink3">Autonomous Trade Intelligence</div>
    </div>
  )
}
