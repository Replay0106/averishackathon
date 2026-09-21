import { useState, type ReactNode } from 'react'
import { LockKeyhole, Mail, RefreshCw, Unplug } from 'lucide-react'
import { PageHeader, Kbd, Button, Badge } from '@/components/ui'
import { useApp } from '@/lib/store'

export default function Settings({ onHealth }: { onHealth: () => void }) {
  const { live, emails, audit, gmail, connectGmail, syncGmail, disconnectGmail, toast } = useApp()
  const [busy, setBusy] = useState<'sync' | 'disconnect' | null>(null)
  const rows: [string, ReactNode][] = [
    ['Data source', <Badge key="s" tone={live ? 'ok' : 'warn'} dot>{live ? 'Live pipeline API' : 'Offline snapshot'}</Badge>],
    ['Emails indexed', <span key="e" className="num">{emails.length}</span>],
    ['Audit ledger', <Badge key="a" tone={audit.integrity ? 'ok' : 'bad'} dot>{audit.integrity ? `Intact · ${audit.blocks.length} blocks` : 'Integrity failure'}</Badge>],
    ['Command palette', <span key="k" className="flex gap-1"><Kbd>Ctrl</Kbd><Kbd>K</Kbd></span>],
    ['Motion', <span key="m" className="text-ink2">Follows your system “reduce motion” setting</span>],
  ]

  async function sync() {
    setBusy('sync')
    try {
      const dataset = await syncGmail()
      toast({ tone: 'ok', title: 'Gmail synced', body: `${dataset.emails} labelled emails are ready for classification.` })
    } catch (error) {
      toast({ tone: 'bad', title: 'Gmail sync failed', body: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy(null)
    }
  }

  async function disconnect() {
    if (!window.confirm('Disconnect Gmail and erase its encrypted token and cached messages from NavisAI?')) return
    setBusy('disconnect')
    try {
      await disconnectGmail()
      toast({ tone: 'ok', title: 'Gmail disconnected', body: 'The stored token and encrypted Gmail cache were removed.' })
    } catch (error) {
      toast({ tone: 'bad', title: 'Could not disconnect Gmail', body: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="max-w-3xl">
      <PageHeader title="Settings" sub="Workspace, environment, and secure integrations." right={<Button onClick={onHealth}>System health</Button>} />

      <div className="mb-6 panel overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/[0.06] px-5 py-5">
          <div className="flex min-w-0 gap-3">
            <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-white/10 bg-white/[0.04]">
              <Mail className="size-5 text-sky" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-[15px] font-semibold">Gmail inbox</h2>
                <Badge tone={gmail?.connected ? 'ok' : gmail?.configured ? 'neutral' : 'warn'} dot>
                  {gmail?.connected ? 'Connected' : gmail?.configured ? 'Not connected' : 'Setup required'}
                </Badge>
              </div>
              <p className="mt-1 max-w-xl text-xs leading-5 text-ink2">
                Imports only messages carrying the <span className="font-medium text-ink">{gmail?.label ?? 'NavisAI'}</span> label.
                {' '}Google access is read-only; refresh tokens and cached messages are encrypted with AES-256-GCM.
              </p>
            </div>
          </div>

          <div className="flex gap-2">
            {gmail?.connected ? (
              <>
                <Button icon={<RefreshCw className="size-3.5" />} loading={busy === 'sync'} disabled={busy !== null} onClick={sync}>Sync labelled mail</Button>
                <Button variant="danger" icon={<Unplug className="size-3.5" />} loading={busy === 'disconnect'} disabled={busy !== null} onClick={disconnect}>Disconnect</Button>
              </>
            ) : (
              <Button variant="primary" icon={<Mail className="size-3.5" />} disabled={!gmail?.configured} onClick={connectGmail}>Connect Gmail</Button>
            )}
          </div>
        </div>

        <div className="grid gap-px bg-white/[0.06] sm:grid-cols-3">
          <div className="bg-card px-5 py-4">
            <div className="eyebrow mb-1">Account</div>
            <div className="truncate text-[13px] text-ink2">{gmail?.email ?? 'No account connected'}</div>
          </div>
          <div className="bg-card px-5 py-4">
            <div className="eyebrow mb-1">Last sync</div>
            <div className="text-[13px] text-ink2">{gmail?.last_sync_at ? new Date(gmail.last_sync_at).toLocaleString() : 'Never'}</div>
          </div>
          <div className="bg-card px-5 py-4">
            <div className="eyebrow mb-1">Protection</div>
            <div className="flex items-center gap-1.5 text-[13px] text-ink2"><LockKeyhole className="size-3.5 text-ok" /> Encrypted at rest</div>
          </div>
        </div>

        {!gmail?.configured && (
          <div className="border-t border-warn/20 bg-warn/[0.06] px-5 py-4 text-xs leading-5 text-amber-200">
            Gmail is disabled until the OAuth client and encryption key are added to <code>.env</code> and the API is restarted.
            {gmail?.configuration_error && <span className="mt-1 block text-amber-300/80">{gmail.configuration_error}</span>}
          </div>
        )}
      </div>

      <div className="panel divide-y divide-white/[0.06]">
        {rows.map(([key, value]) => (
          <div key={key} className="flex items-center justify-between px-5 py-4 text-[13px]">
            <span className="text-ink2">{key}</span>
            {value}
          </div>
        ))}
      </div>
    </div>
  )
}
