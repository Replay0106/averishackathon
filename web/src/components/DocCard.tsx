import { motion } from 'framer-motion'
import { FileText } from 'lucide-react'
import { useMemo } from 'react'
import type { DocFields } from '@/lib/types'
import { cn } from '@/lib/utils'

export type LineState = 'lit' | 'ok' | 'bad'

export function DocCard({
  title, file, doc, scanning, scanKey, lines = {}, className, maxLines = 24,
}: {
  title: string
  file?: string
  doc: DocFields | null
  scanning?: boolean
  scanKey?: number
  lines?: Record<number, LineState>
  className?: string
  maxLines?: number
}) {
  const rows = useMemo(() => (doc?.raw_text ?? '').split('\n').slice(0, maxLines), [doc, maxLines])
  return (
    <div className={cn('panel relative flex flex-col overflow-hidden', className)}>
      <div className="flex items-center gap-2.5 border-b border-white/[0.07] px-4 py-3">
        <FileText className="size-4 text-ink3" strokeWidth={1.6} />
        <div className="min-w-0">
          <div className="eyebrow">{title}</div>
          {file && <div className="num truncate text-[11px] text-ink3">{file}</div>}
        </div>
        {doc && <span className="num ml-auto text-[10px] text-ink3">{doc.is_scanned ? 'SCANNED' : 'TEXT'}</span>}
      </div>
      <div className="relative flex-1 overflow-hidden px-2 py-3">
        {!doc && <div className="grid h-40 place-items-center text-xs text-ink3">No document available</div>}
        {rows.map((t, i) => {
          const st = lines[i + 1]
          return (
            <div
              key={i}
              className={cn(
                'num relative min-h-[19px] whitespace-pre-wrap break-words border-l-2 px-2.5 text-[11px] leading-[19px] transition-colors duration-300',
                st === 'lit' && 'border-sky bg-sky/[0.09] text-ink',
                st === 'ok' && 'border-ok bg-ok/[0.09] text-ink',
                st === 'bad' && 'border-bad bg-bad/[0.12] text-ink',
                !st && 'border-transparent text-ink3',
              )}
            >
              {t || ' '}
            </div>
          )
        })}
        {scanning && (
          <motion.div
            key={scanKey}
            className="pointer-events-none absolute inset-x-0 top-0 z-10"
            initial={{ top: '0%', opacity: 1 }}
            animate={{ top: '100%', opacity: [1, 1, 0] }}
            transition={{ duration: 1.7, ease: 'easeInOut' }}
          >
            <div className="absolute inset-x-0 -top-10 h-10 bg-gradient-to-b from-transparent to-sky/[0.14]" />
            <div className="h-px w-full bg-sky shadow-[0_0_10px_1px_rgba(56,189,248,0.7)]" />
          </motion.div>
        )}
      </div>
    </div>
  )
}
