import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown, FolderOpen, FolderPlus, Loader2, Trash2, UploadCloud, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Badge, Button, Modal, Progress, SuccessCheck } from './ui'
import { useApp, type PickedFile } from '@/lib/store'
import { cn } from '@/lib/utils'

const ALLOWED = new Set(['json', 'eml', 'txt', 'pdf', 'docx', 'xlsx', 'xls'])
const ext = (n: string) => n.split('.').pop()?.toLowerCase() ?? ''
const usable = (p: PickedFile) => ALLOWED.has(ext(p.path)) && !p.path.split('/').some((s) => s.startsWith('.') || s.startsWith('~$'))

async function walk(entry: FileSystemEntry, out: PickedFile[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej))
    out.push({ file, path: entry.fullPath.replace(/^\//, '') })
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej))
      if (!batch.length) break
      for (const e of batch) await walk(e, out)
    }
  }
}

export function summarise(files: PickedFile[]) {
  const ok = files.filter(usable)
  const json = ok.filter((f) => ext(f.path) === 'json' && f.path.toLowerCase().split('/').slice(0, -1).includes('inbox')).length
  const eml = ok.filter((f) => ext(f.path) === 'eml').length
  const attachments = ok.filter((f) => !['json', 'eml'].includes(ext(f.path))).length
  return { usable: ok, json, eml, attachments, ignored: files.length - ok.length }
}

export function ImportModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { importFolder, switchDataset, toast } = useApp()
  const [picked, setPicked] = useState<PickedFile[]>([])
  const [name, setName] = useState('')
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState<'idle' | 'upload' | 'process' | 'done'>('idle')
  const [prog, setProg] = useState({ done: 0, total: 0 })
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setPicked([])
      setName('')
      setBusy('idle')
      setError('')
    }
  }, [open])

  const sum = useMemo(() => summarise(picked), [picked])
  const emailCount = sum.json + sum.eml

  const onInput = (fl: FileList | null) => {
    if (!fl) return
    const files = [...fl].map((f) => ({ file: f, path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name }))
    setPicked(files)
    setName((n) => n || files[0]?.path.split('/')[0] || '')
    setError('')
  }

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const items = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry()).filter(Boolean) as FileSystemEntry[]
    const out: PickedFile[] = []
    for (const it of items) await walk(it, out)
    setPicked(out)
    setName((n) => n || items[0]?.name || '')
    setError('')
  }

  const start = async () => {
    setError('')
    setBusy('upload')
    try {
      const info = await importFolder(sum.usable, name.trim(), (done, total) => {
        setBusy('process')
        setProg({ done, total })
      })
      setBusy('done')
      toast({ tone: 'ok', title: 'Folder imported', body: `${info.name} · ${info.emails} emails` })
      if (info.job.failed.length) toast({ tone: 'warn', title: `${info.job.failed.length} emails could not be processed`, body: 'They were routed to human review.' })
      setTimeout(() => {
        switchDataset(info.id)
        onClose()
      }, 900)
    } catch (e) {
      setBusy('idle')
      setError(e instanceof Error ? e.message : 'Import failed')
    }
  }

  const working = busy === 'upload' || busy === 'process'
  return (
    <Modal open={open} onClose={() => !working && onClose()} title="Import folder" width={600}>
      <div className="p-6">
        <div className="mb-1 flex items-center justify-between">
          <h3 className="text-base font-semibold">Import folder</h3>
          <button onClick={onClose} disabled={working} aria-label="Close" className="rounded-md p-1 text-ink3 transition hover:bg-white/[0.06] hover:text-ink disabled:opacity-40"><X className="size-4" /></button>
        </div>
        <p className="mb-5 text-xs text-ink3">Choose a folder containing emails and their attachments. Supported: a bundle (<span className="num">inbox/*.json</span> + <span className="num">attachments/</span>) or <span className="num">.eml</span> exports. Imports are kept as a separate dataset.</p>

        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={cn('grid place-items-center rounded-xl border border-dashed px-6 py-9 text-center transition-colors', drag ? 'border-sky/60 bg-sky/[0.06]' : 'border-white/15 bg-white/[0.02]')}
        >
          {emailCount === 0 && picked.length === 0 ? (
            <>
              <UploadCloud className="mb-3 size-7 text-ink3" strokeWidth={1.5} />
              <div className="text-[13px]">Drag a folder here</div>
              <div className="my-2 text-[11px] text-ink3">or</div>
              <Button icon={<FolderOpen className="size-3.5" />} onClick={() => input.current?.click()}>Choose folder</Button>
            </>
          ) : (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="w-full text-left">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-[13px] font-medium">{emailCount > 0 ? `${emailCount} email${emailCount === 1 ? '' : 's'} found` : 'No emails found in this folder'}</span>
                <button onClick={() => setPicked([])} disabled={working} className="text-xs text-ink3 transition hover:text-ink disabled:opacity-40">Change</button>
              </div>
              {emailCount === 0 && <p className="mb-3 text-xs text-ink3">Expected an <span className="num">inbox</span> folder of .json email records (with an <span className="num">attachments</span> folder), or .eml files.</p>}
              <div className="flex flex-wrap gap-2">
                {sum.json > 0 && <Badge tone="info">{sum.json} bundle records</Badge>}
                {sum.eml > 0 && <Badge tone="info">{sum.eml} .eml files</Badge>}
                <Badge>{sum.attachments} attachment files</Badge>
                {sum.ignored > 0 && <Badge tone="neutral">{sum.ignored} ignored</Badge>}
              </div>
            </motion.div>
          )}
          <input ref={input} type="file" className="hidden" onChange={(e) => onInput(e.target.files)} {...({ webkitdirectory: '', directory: '' } as object)} multiple />
        </div>

        <label className="mt-5 block">
          <span className="eyebrow mb-1.5 block">Dataset name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={working} placeholder="e.g. October mailbox export" className="h-9 w-full rounded-lg border border-white/12 bg-white/[0.03] px-3 text-[13px] outline-none transition placeholder:text-ink3 focus:border-sky/50" />
        </label>

        <AnimatePresence>
          {(busy === 'upload' || busy === 'process') && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0 }} className="mt-5 overflow-hidden">
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="flex items-center gap-2 text-ink2"><Loader2 className="size-3.5 animate-spin text-sky" />{busy === 'upload' ? 'Uploading and normalising…' : 'Classifying, extracting and comparing…'}</span>
                {busy === 'process' && <span className="num text-ink3">{prog.done} / {prog.total}</span>}
              </div>
              <Progress value={busy === 'process' && prog.total ? prog.done / prog.total : 0.08} tone="sky" />
            </motion.div>
          )}
          {busy === 'done' && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-5 flex items-center gap-3 text-sm text-emerald-300"><SuccessCheck size={30} /> Import complete</motion.div>
          )}
          {error && <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-4 rounded-lg border border-bad/25 bg-bad/[0.06] px-3.5 py-2.5 text-[12.5px] text-red-300">{error}</motion.div>}
        </AnimatePresence>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" disabled={working} onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<FolderPlus className="size-3.5" />} loading={working} disabled={emailCount === 0 || busy === 'done'} onClick={start}>Import &amp; verify</Button>
        </div>
      </div>
    </Modal>
  )
}

export function DatasetSwitcher() {
  const { datasets, dataset, switchDataset, deleteDataset, loading } = useApp()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const cur = datasets.find((d) => d.id === dataset) ?? datasets[0]

  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    window.addEventListener('mousedown', h)
    return () => window.removeEventListener('mousedown', h)
  }, [])

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((o) => !o)} className="flex h-9 max-w-[220px] items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 text-[12.5px] text-ink2 transition hover:border-white/20 hover:text-ink" aria-label="Switch dataset">
        {loading ? <Loader2 className="size-3.5 animate-spin text-sky" /> : <span className="size-1.5 rounded-full bg-sky" />}
        <span className="truncate">{cur?.name ?? 'Dataset'}</span>
        <ChevronDown className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-180')} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.15 }} className="panel-glass absolute right-0 top-11 z-50 w-[300px] overflow-hidden bg-card p-1.5">
            <div className="eyebrow px-2.5 py-1.5">Datasets</div>
            {datasets.map((d) => (
              <div key={d.id} className={cn('group flex items-center gap-2 rounded-lg px-2.5 py-2 transition-colors', d.id === dataset ? 'bg-white/[0.07]' : 'hover:bg-white/[0.04]')}>
                <button onClick={() => { switchDataset(d.id); setOpen(false) }} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  {d.id === dataset ? <Check className="size-3.5 shrink-0 text-brand" strokeWidth={2.6} /> : <span className="size-3.5 shrink-0" />}
                  <span className="min-w-0">
                    <span className="block truncate text-[12.5px]">{d.name}</span>
                    <span className="num block text-[10.5px] text-ink3">{d.emails} emails{d.kind === 'demo' ? ' · built-in' : ''}{d.job.status === 'processing' ? ' · processing…' : ''}</span>
                  </span>
                </button>
                {d.kind === 'import' && (
                  <button onClick={() => deleteDataset(d.id)} aria-label={`Delete ${d.name}`} className="rounded p-1 text-ink3 opacity-0 transition hover:text-red-300 group-hover:opacity-100"><Trash2 className="size-3.5" /></button>
                )}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
