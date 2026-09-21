import { motion } from 'framer-motion'
import {
  AlertTriangle,
  CheckCircle2,
  FileQuestion,
  Loader2,
  Mail,
  Radio,
  Sparkles,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Modal, ease } from './ui'
import { useApp } from '@/lib/store'
import { cn } from '@/lib/utils'

interface SimulateGmailModalProps {
  open: boolean
  onClose: () => void
  onSimulated?: (emailId: string) => void
}

interface PresetOption {
  id: string
  title: string
  subtitle: string
  booking: string
  carrier: string
  tone: 'ok' | 'bad' | 'info'
  badgeText: string
  desc: string
  payload: {
    has_discrepancy: boolean
    booking_ref: string
    shipper: string
    consignee: string
    kind: 'bl_comparison' | 'invoice_query' | 'spam'
  }
}

const PRESETS: PresetOption[] = [
  {
    id: 'clean',
    title: 'Clean Match (7/7 Verified)',
    subtitle: 'MSC Mediterranean Shipping · MEDU-98241',
    booking: 'MEDU-98241',
    carrier: 'MSC',
    tone: 'ok',
    badgeText: 'Status: OK (7/7)',
    desc: 'Simulates inbound carrier email with matching Shipping Instruction and Draft B/L (245,000 kg, 10x40\'HC). Passes all 7 field reconciliation gates.',
    payload: {
      has_discrepancy: false,
      booking_ref: 'MEDU-98241',
      shipper: 'APRIL FAR EAST (M) SDN BHD',
      consignee: 'AL GURG STATIONERY LLC',
      kind: 'bl_comparison',
    },
  },
  {
    id: 'discrepancy',
    title: 'Discrepancy Detected (Audit Flag)',
    subtitle: 'Maersk Line · MAEU-77312',
    booking: 'MAEU-77312',
    carrier: 'Maersk',
    tone: 'bad',
    badgeText: 'Status: MISMATCH',
    desc: 'Simulates draft B/L with 231,000 kg vs SI 245,000 kg and container count mismatch. Triggers automated defect flag, SLA countdown, and audit block.',
    payload: {
      has_discrepancy: true,
      booking_ref: 'MAEU-77312',
      shipper: 'APRIL FAR EAST (M) SDN BHD',
      consignee: 'AL GURG STATIONERY LLC',
      kind: 'bl_comparison',
    },
  },
  {
    id: 'triage',
    title: 'NLP Intent Triage (Non-BL Email)',
    subtitle: 'Transocean Logistics · INV-88391',
    booking: 'INV-88391',
    carrier: 'Transocean',
    tone: 'info',
    badgeText: 'Category: INVOICE_QUERY',
    desc: 'Tests zero-shot intent classifier on an operational billing query without shipping attachments. Filed automatically without triggering document comparison.',
    payload: {
      has_discrepancy: false,
      booking_ref: 'INV-88391',
      shipper: 'Transocean Logistics Ltd',
      consignee: 'Averis Trade Accounts',
      kind: 'invoice_query',
    },
  },
]

export function SimulateGmailModal({ open, onClose, onSimulated }: SimulateGmailModalProps) {
  const { simulateEmail, toast } = useApp()
  const [busyId, setBusyId] = useState<string | null>(null)

  const handleSimulate = async (preset: PresetOption) => {
    setBusyId(preset.id)
    try {
      const res = await simulateEmail(preset.payload)
      if (res && res.status === 'ok') {
        toast({
          title: `📨 Inbound Email Simulated (${res.email_id})`,
          body: `${preset.title} processed & verified via autonomous pipeline.`,
          tone: preset.tone === 'bad' ? 'warn' : 'ok',
        })
        onClose()
        if (onSimulated) {
          onSimulated(res.email_id)
        }
      } else {
        toast({
          title: 'Simulation Failed',
          body: 'Check server connection or API logs.',
          tone: 'bad',
        })
      }
    } catch (err: any) {
      toast({
        title: 'Error Simulating Inbound Email',
        body: err?.message || 'Unknown error occurred.',
        tone: 'bad',
      })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Simulate Inbound Gmail" width={640}>
      <div className="p-6">
        {/* Header */}
        <div className="mb-5 flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl border border-brand/30 bg-brand/10 text-brand">
              <Zap className="size-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-semibold text-ink">Simulate Inbound Gmail</h3>
                <span className="inline-flex items-center gap-1 rounded-full border border-sky/30 bg-sky/10 px-2 py-0.5 text-[10px] font-semibold text-sky">
                  <Radio className="size-2.5 animate-pulse" /> Zero-Credential Mode
                </span>
              </div>
              <p className="text-xs text-ink3">
                Evaluate autonomous ingestion, MIME decoding, zero-shot intent triage, and 7-field compliance checks in 1 click.
              </p>
            </div>
          </div>
        </div>

        {/* Preset Cards */}
        <div className="space-y-3">
          {PRESETS.map((p) => {
            const isBusy = busyId === p.id
            const anyBusy = busyId !== null
            return (
              <motion.div
                key={p.id}
                whileHover={anyBusy ? undefined : { y: -2 }}
                transition={{ duration: 0.18, ease }}
                className={cn(
                  'group relative overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 transition-colors',
                  p.tone === 'ok' && 'hover:border-ok/30 hover:bg-ok/[0.02]',
                  p.tone === 'bad' && 'hover:border-bad/30 hover:bg-bad/[0.02]',
                  p.tone === 'info' && 'hover:border-sky/30 hover:bg-sky/[0.02]',
                )}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="text-[13.5px] font-medium text-ink">{p.title}</span>
                      <Badge tone={p.tone} dot>{p.badgeText}</Badge>
                    </div>
                    <div className="mb-2 flex items-center gap-2 text-[11.5px] text-ink3">
                      <span className="font-mono text-ink2">{p.booking}</span>
                      <span>·</span>
                      <span>{p.subtitle}</span>
                    </div>
                    <p className="text-[12px] leading-relaxed text-ink2">{p.desc}</p>
                  </div>

                  <Button
                    size="sm"
                    variant={p.tone === 'bad' ? 'danger' : p.tone === 'ok' ? 'primary' : 'outline'}
                    loading={isBusy}
                    disabled={anyBusy}
                    icon={p.tone === 'bad' ? <AlertTriangle className="size-3" /> : p.tone === 'ok' ? <CheckCircle2 className="size-3" /> : <FileQuestion className="size-3" />}
                    onClick={() => handleSimulate(p)}
                    className="shrink-0"
                  >
                    Simulate
                  </Button>
                </div>
              </motion.div>
            )
          })}
        </div>

        {/* Footer info banner */}
        <div className="mt-5 flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.015] px-4 py-3 text-[11.5px] text-ink3">
          <div className="flex items-center gap-2">
            <Sparkles className="size-3.5 text-brand" />
            <span>Target dataset: <strong className="font-medium text-ink2">Gmail Live Inbox</strong> (<span className="font-mono">datasets/gmail_live</span>)</span>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  )
}
