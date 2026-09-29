/**
 * What the "needs your decision" tray (PendingSetupCards) reads from the
 * transcript it sits under, kept out of the component so both hosts (the
 * single-chat page and every grid pane) and the tests share one definition.
 */
import { useSyncExternalStore } from 'react'

import { setupCardRefOf } from '../../api/setupCards'
import { REASONING_ROLES } from '../../pages/chat/groupDisplayItems'
import type { ChatMessage } from '../../types'

/** Rows a reader does not stop at: tool lines, reasoning, queued sends. */
const QUIET_ROLES: ReadonlySet<string> = new Set<string>(['tool', 'queued', ...REASONING_ROLES])

/**
 * The card whose transcript row is the newest thing to read, or `null` when the
 * transcript has something newer (the agent kept writing, the user replied, a
 * notice landed). Walks back from the end past quiet rows and empty
 * placeholders, so a card followed only by tool lines or an empty streaming row
 * still counts as newest. With two cards in a row, the later one is returned.
 */
export function setupCardAtTail(messages: readonly ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    const ref = setupCardRefOf(m.meta)
    if (ref) return ref.id
    if (m.role !== 'user' && (QUIET_ROLES.has(m.role) || !m.content?.trim())) continue
    return null
  }
  return null
}

// ── The row highlight the tray's "find it in the chat" asks for ───────────────
// Module-level (the shape useRailWidth uses) so the tray can light up a
// transcript row it does not render, and the row re-renders alone rather than
// the renderer set rebuilding.

/** How long a located row keeps its highlight: the msg-highlight utility's run. */
export const SETUP_CARD_ROW_HIGHLIGHT_MS = 2000

let highlighted: string | null = null
let clearTimer: ReturnType<typeof setTimeout> | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach(l => l())

/** Highlight `cardId`'s transcript row for {@link SETUP_CARD_ROW_HIGHLIGHT_MS}. */
export function highlightSetupCardRow(cardId: string) {
  if (clearTimer) clearTimeout(clearTimer)
  highlighted = cardId
  notify()
  clearTimer = setTimeout(() => {
    clearTimer = null
    highlighted = null
    notify()
  }, SETUP_CARD_ROW_HIGHLIGHT_MS)
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

export function useSetupCardRowHighlighted(cardId: string): boolean {
  return useSyncExternalStore(subscribe, () => highlighted === cardId, () => false)
}
