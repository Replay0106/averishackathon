import { PageHeader, Kbd, Button, Badge } from '@/components/ui'
import { useApp } from '@/lib/store'

export default function Settings({ onHealth }: { onHealth: () => void }) {
  const { live, emails, audit } = useApp()
  const rows: [string, React.ReactNode][] = [
    ['Data source', <Badge key="s" tone={live ? 'ok' : 'warn'} dot>{live ? 'Live pipeline API' : 'Offline snapshot'}</Badge>],
    ['Emails indexed', <span key="e" className="num">{emails.length}</span>],
    ['Audit ledger', <Badge key="a" tone={audit.integrity ? 'ok' : 'bad'} dot>{audit.integrity ? `Intact · ${audit.blocks.length} blocks` : 'Integrity failure'}</Badge>],
    ['Command palette', <span key="k" className="flex gap-1"><Kbd>Ctrl</Kbd><Kbd>K</Kbd></span>],
    ['Motion', <span key="m" className="text-ink2">Follows your system “reduce motion” setting</span>],
  ]
  return (
    <div className="max-w-2xl">
      <PageHeader title="Settings" sub="Workspace and environment." right={<Button onClick={onHealth}>System health</Button>} />
      <div className="panel divide-y divide-white/[0.06]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between px-5 py-4 text-[13px]">
            <span className="text-ink2">{k}</span>
            {v}
          </div>
        ))}
      </div>
    </div>
  )
}
