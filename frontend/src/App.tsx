import { Menu, X } from 'lucide-react'
import { useCallback, useState } from 'react'
import { NewResearch } from '@/components/NewResearch'
import { RunPage } from '@/components/RunPage'
import { Sidebar } from '@/components/Sidebar'
import { Button } from '@/components/ui/button'
import { hrefFor, useRoute } from '@/lib/route'

export default function App() {
  const route = useRoute()
  const [menu, setMenu] = useState(false) // narrow screens: the sidebar becomes a drawer opened from the top bar
  const [sidebarVersion, setSidebarVersion] = useState(0) // bump to refresh the recent-runs list after a start
  const refreshSidebar = useCallback(() => setSidebarVersion((v) => v + 1), [])
  const runId = route.page === 'run' ? route.runId : null
  return (
    <div className="min-h-screen md:grid md:grid-cols-[17rem_minmax(0,1fr)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-2">Skip to content</a>
      <header className="flex items-center justify-between bg-sidebar px-3 py-2 text-white md:hidden">
        <span className="font-semibold">DeepDive</span>
        <Button variant="ghost" size="icon" className="text-white hover:bg-white/10 hover:text-white" aria-expanded={menu} aria-controls="sidebar" aria-label={menu ? 'Close navigation' : 'Open navigation'} onClick={() => setMenu((m) => !m)}>
          {menu ? <X aria-hidden /> : <Menu aria-hidden />}
        </Button>
      </header>
      <div id="sidebar" className={`${menu ? 'block' : 'hidden'} md:sticky md:top-0 md:block md:h-screen`}>
        <Sidebar activeRunId={runId} version={sidebarVersion * 100000 + (runId ?? 0)} onNavigate={() => setMenu(false)} />
      </div>
      <main id="main" className="min-w-0">
        {route.page === 'new' ? (
          <NewResearch onStarted={(id) => { setSidebarVersion((v) => v + 1); window.location.hash = hrefFor(id).slice(1) }} />
        ) : (
          <RunPage key={route.runId} runId={route.runId} tab={route.tab} claimId={route.claimId} onTerminal={refreshSidebar} />
        )}
      </main>
    </div>
  )
}
