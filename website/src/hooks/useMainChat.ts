/**
 * The main chat (RFC §6.9) for surfaces outside the chat page's own tree.
 *
 * - `useMainChatSlot()` reads `main_slot` off the SAME `['theme-boot']` query
 *   ThemeProvider owns (same key, same fetcher, so it dedupes and never adds a
 *   request), without requiring the provider — the session menu and the jobs
 *   page render in trees and tests that do not mount one.
 * - `useMakeMainChat()` is the owner's "Make this my main chat": it posts
 *   `/api/setup/main-chat` and re-reads boot, so the sidebar's Main chip and the
 *   landing target move with it.
 * - `useDraftInMainChat()` hands a half-written message to the main chat's
 *   composer — PRE-FILLED, never sent — through the channels the chat page
 *   already honours: the keyed prefill (`writePrefill`, consumed when that slot
 *   becomes active) or, when the main chat is already open on screen, the
 *   `mc:prefill-composer` event (merged into what the composer holds).
 * - `isMainChatEligible()` mirrors the gateway's eligibility rule so the menu
 *   only offers what the server would accept; the server stays authoritative.
 */
import { useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useStore } from 'react-redux'
import { useLocation, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { useAppDispatch, type RootState } from '../store'
import { switchSlot } from '../store/chatSlice'
import type { ChatSlot } from '../types'
import { loadDrafts, mergeIntoDraft } from '../utils/chatDrafts'
import { isChatPageSurface } from '../utils/channelOrigin'
import { writePrefill } from '../utils/navIntent'

/** ThemeProvider's boot query key (hooks/useTheme.tsx). */
export const THEME_BOOT_QUERY_KEY = ['theme-boot'] as const

/** The composer-insert event ChatPage listens for (appends, never replaces). */
export const PREFILL_COMPOSER_EVENT = 'mc:prefill-composer'

function mainSlotOf(boot: unknown): string | null {
  const v = (boot as { main_slot?: unknown } | null | undefined)?.main_slot
  return typeof v === 'string' && v ? v : null
}

/** The main chat's slot key, or null when there is none (yet). */
export function useMainChatSlot(): string | null {
  const q = useQuery({
    queryKey: THEME_BOOT_QUERY_KEY,
    queryFn: () => api.themeBoot(),
    staleTime: Infinity,
    retry: false,
  })
  return mainSlotOf(q.data)
}

/**
 * Whether the gateway would accept this chat as the main chat: one of the
 * owner's own dashboard chats — a `chat-` key, the chat surface, not born in or
 * bound to a messaging channel, not minted by a job or an app.
 */
export function isMainChatEligible(key: string, slot: ChatSlot | undefined): boolean {
  if (!key.startsWith('chat-') || !slot) return false
  if (slot.slack_linked) return false
  if (slot.links?.some(link => link.direction === 'origin')) return false
  if (slot.origin === 'cron' || slot.origin === 'app') return false
  return isChatPageSurface(slot.surface ?? slot.mode)
}

export function useMakeMainChat() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (slot: string) => api.makeMainChat(slot),
    onSuccess: (res, slot) => {
      const main = typeof res?.main_slot === 'string' && res.main_slot ? res.main_slot : slot
      // Paint the move now (useTheme's boot effect and useMainChatSlot both read
      // this), then confirm against the gateway.
      qc.setQueryData(THEME_BOOT_QUERY_KEY, (old: unknown) =>
        old && typeof old === 'object' ? { ...(old as Record<string, unknown>), main_slot: main } : old)
      void qc.invalidateQueries({ queryKey: THEME_BOOT_QUERY_KEY })
    },
  })
}

/** Whether a router path is the chat page (where ChatPage is mounted). */
function isChatPath(pathname: string): boolean {
  return pathname === '/chat' || pathname.startsWith('/chat/')
}

/**
 * Pre-fill the main chat's composer with `text` and take the user there. The
 * text is appended to whatever the main chat's composer already holds, and
 * nothing is sent: the user finishes the sentence.
 */
export function useDraftInMainChat(): (mainSlot: string, text: string) => void {
  const navigate = useNavigate()
  const location = useLocation()
  const dispatch = useAppDispatch()
  const store = useStore<RootState>()
  const pathname = location.pathname
  return useCallback((mainSlot: string, text: string) => {
    const onChat = isChatPath(pathname)
    const active = store.getState().chat.activeSlot
    if (onChat && active === mainSlot) {
      // The main chat is on screen: its live composer merges the text in.
      window.dispatchEvent(new CustomEvent(PREFILL_COMPOSER_EVENT, { detail: { text } }))
      return
    }
    // Seed before the switch, so the chat page's slot-restore applies it when
    // the main chat becomes active (the ?sid / popout hand-off order). The
    // stored draft is merged in here because the restore REPLACES the draft
    // with the seeded text.
    const stored = loadDrafts()[mainSlot] ?? ''
    writePrefill(mainSlot, mergeIntoDraft(stored, text))
    if (active !== mainSlot) void dispatch(switchSlot({ key: mainSlot, announceOnMissing: true }))
    if (!onChat) navigate(`/chat?sid=${encodeURIComponent(mainSlot)}`)
  }, [pathname, store, dispatch, navigate])
}
