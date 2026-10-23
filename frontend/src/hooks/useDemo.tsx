import { createContext, useContext, type ReactNode } from 'react'
import type { DemoInfo } from '@/api/types'
import { useResource } from './useApi'

interface DemoState {
  /** False until GET /demo has answered (success or failure). */
  ready: boolean
  /** Set only for the hosted, read-only recorded demo; null for the normal application. */
  info: DemoInfo | null
}

const DemoContext = createContext<DemoState>({ ready: true, info: null })

/** Asks the backend once whether this is the read-only recorded demo. A failed or non-demo answer leaves the full application unchanged. */
export function DemoProvider({ children }: { children: ReactNode }) {
  const demo = useResource<DemoInfo>('/demo')
  const value: DemoState = { ready: !demo.loading, info: demo.data?.read_only ? demo.data : null }
  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>
}

export const useDemo = () => useContext(DemoContext)
