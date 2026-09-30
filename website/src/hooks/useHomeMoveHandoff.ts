/** Follow a home move even while the owner is on another local route. */
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { InstanceView } from '../api/client'
import { isTerminalSetupStatus, type SetupCard } from '../api/setupCards'
import { useAppStore } from '../store'
import { useSelectInstance } from './useSelectInstance'

export function useHomeMoveHandoff(instances: InstanceView[], enabled = true) {
  const qc = useQueryClient()
  const store = useAppStore()
  const watched = useRef(new Set<string>())
  const { selectInstance } = useSelectInstance(instances)
  const selectRef = useRef(selectInstance)
  selectRef.current = selectInstance

  useEffect(() => {
    if (!enabled) return
    const observe = (card: SetupCard | undefined) => {
      if (card?.kind !== 'home') return
      if (!isTerminalSetupStatus(card.status)) {
        watched.current.add(card.id)
        return
      }
      // Reading an old confirmation never switches crews. Consuming the event
      // first also prevents a re-fetch or return to Local from switching again.
      const live = watched.current.delete(card.id)
      const home = card.outcome?.home as { instance_id?: string; remote_key?: string } | undefined
      if (live && !store.getState().instances.activeId && card.status === 'committed'
        && card.outcome?.moved && home?.instance_id && home.remote_key) {
        selectRef.current(home.instance_id, home.remote_key)
      }
    }
    const cache = qc.getQueryCache()
    for (const query of cache.findAll({ queryKey: ['setup-card'] })) {
      observe(query.state.data as SetupCard | undefined)
    }
    return cache.subscribe(event => {
      if (event.type === 'removed') {
        watched.current.delete(String(event.query.queryKey[1]))
      } else if (event.type === 'updated' && event.query.queryKey[0] === 'setup-card') {
        observe(event.query.state.data as SetupCard | undefined)
      }
    })
  }, [qc, store, enabled])
}
