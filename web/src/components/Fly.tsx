import { motion } from 'framer-motion'
import { Mail } from 'lucide-react'
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

interface Job { from: DOMRect; to: DOMRect; label: string; done: () => void }
const C = createContext<(fromEl: Element, toId: string, label: string, done: () => void) => void>(() => {})
export const useFly = () => useContext(C)

export function FlyProvider({ children }: { children: ReactNode }) {
  const [job, setJob] = useState<Job | null>(null)
  const fly = useCallback((fromEl: Element, toId: string, label: string, done: () => void) => {
    const target = document.getElementById(toId)
    if (!target) return done()
    setJob({ from: fromEl.getBoundingClientRect(), to: target.getBoundingClientRect(), label, done })
  }, [])
  return (
    <C.Provider value={fly}>
      {children}
      {job && (
        <motion.div
          className="pointer-events-none fixed z-[95] flex items-center gap-2 rounded-lg border border-sky/40 bg-card px-3 py-2 text-xs shadow-xl"
          style={{ left: 0, top: 0, maxWidth: 260 }}
          initial={{ x: job.from.left, y: job.from.top, opacity: 1, scale: 1 }}
          animate={{ x: job.to.left + 12, y: job.to.top + 4, opacity: [1, 1, 0.2], scale: [1, 1, 0.7] }}
          transition={{ duration: 0.75, ease: [0.65, 0, 0.35, 1] }}
          onAnimationComplete={() => {
            const d = job.done
            setJob(null)
            d()
          }}
        >
          <Mail className="size-3.5 shrink-0 text-sky" />
          <span className="truncate">{job.label}</span>
        </motion.div>
      )}
    </C.Provider>
  )
}
