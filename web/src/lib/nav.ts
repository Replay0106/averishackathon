import { useCallback, useEffect, useState } from 'react'

export type Page =
  | 'overview' | 'inbox' | 'verification' | 'cases' | 'compliance'
  | 'analytics' | 'audit' | 'gateway' | 'copilot' | 'settings' | 'roadmap'

export interface Route { page: Page; id?: string; auto?: boolean }

const PAGES: Page[] = ['overview', 'inbox', 'verification', 'cases', 'compliance', 'analytics', 'audit', 'gateway', 'copilot', 'settings', 'roadmap']

function parse(): Route {
  const [, p, id, flag] = window.location.hash.split('/')
  // The old Discrepancy Queue and Carrier Actions pages were merged into Cases; keep their links working.
  const page = p === 'discrepancies' || p === 'carrier' ? 'cases' : (PAGES as string[]).includes(p) ? (p as Page) : 'overview'
  return { page, id: id || undefined, auto: flag === 'auto' }
}

export function useRoute() {
  const [route, setRoute] = useState<Route>(parse)
  useEffect(() => {
    const h = () => setRoute(parse())
    window.addEventListener('hashchange', h)
    return () => window.removeEventListener('hashchange', h)
  }, [])
  const go = useCallback((page: Page, id?: string, auto = false) => {
    window.location.hash = `/${page}${id ? `/${id}` : ''}${auto ? '/auto' : ''}`
  }, [])
  return { route, go }
}
