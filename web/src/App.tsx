import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { CommandPalette, Sidebar, SystemHealth, TopBar } from './components/Shell'
import { FlyProvider } from './components/Fly'
import { Toasts } from './components/ui'
import { useRoute } from './lib/nav'
import { useApp } from './lib/store'
import { LogoMark } from './components/Logo'
import { ImportModal } from './components/ImportFolder'
import Overview from './pages/Overview'
import Inbox from './pages/Inbox'
import Verification from './pages/Verification'
import Discrepancies from './pages/Discrepancies'
import Compliance from './pages/Compliance'
import Carrier from './pages/Carrier'
import Analytics from './pages/Analytics'
import Audit from './pages/Audit'
import Gateway from './pages/Gateway'
import Copilot from './pages/Copilot'
import Roadmap from './pages/Roadmap'
import Settings from './pages/Settings'

export default function App() {
  const { route, go } = useRoute()
  const { ready } = useApp()
  const [palette, setPalette] = useState(false)
  const [health, setHealth] = useState(false)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [route.page])

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((p) => !p)
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  if (!ready)
    return (
      <div className="grid h-full place-items-center bg-canvas">
        <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="flex flex-col items-center gap-4">
          <LogoMark size={56} animate />
          <div className="eyebrow">Initialising NavisAI</div>
        </motion.div>
      </div>
    )

  const page = (() => {
    switch (route.page) {
      case 'overview': return <Overview go={go} />
      case 'inbox': return <Inbox go={go} initialId={route.id} />
      case 'verification': return <Verification go={go} id={route.id} auto={route.auto} />
      case 'discrepancies': return <Discrepancies go={go} />
      case 'compliance': return <Compliance go={go} id={route.id} />
      case 'carrier': return <Carrier go={go} id={route.id} />
      case 'analytics': return <Analytics />
      case 'audit': return <Audit />
      case 'gateway': return <Gateway go={go} />
      case 'copilot': return <Copilot go={go} />
      case 'roadmap': return <Roadmap />
      case 'settings': return <Settings onHealth={() => setHealth(true)} />
    }
  })()

  return (
    <FlyProvider>
      <div className="min-h-full bg-canvas">
        <Sidebar page={route.page} go={go} onHealth={() => setHealth(true)} />
        <div className="pl-[68px] lg:pl-[248px]">
          <TopBar onPalette={() => setPalette(true)} onHealth={() => setHealth(true)} onImport={() => setImporting(true)} />
          <main className="mx-auto w-full max-w-[1760px] px-6 py-8 xl:px-10">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={route.page}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1] } }}
                exit={{ opacity: 0, y: -6, transition: { duration: 0.15 } }}
              >
                {page}
              </motion.div>
            </AnimatePresence>
          </main>
        </div>
        <CommandPalette open={palette} onClose={() => setPalette(false)} go={go} onImport={() => setImporting(true)} />
        <ImportModal open={importing} onClose={() => setImporting(false)} />
        <SystemHealth open={health} onClose={() => setHealth(false)} />
        <Toasts />
      </div>
    </FlyProvider>
  )
}
