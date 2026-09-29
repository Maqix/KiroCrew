/**
 * What the "needs your decision" tray (PendingSetupCards) reads from the
 * transcript it sits under, kept out of the component so both hosts (the
 * single-chat page and every grid pane) and the tests share one definition.
 */
import { useSyncExternalStore } from 'react'

import { setupCardRefOf } from '../../api/setupCards'
import { isSystemNoticeKind } from '../../lib/systemNotice'
import { TURN_OPENER_ROLES } from '../../pages/chat/groupDisplayItems'
import { injectOpensTurn } from '../../pages/chat/RecoveryCard'
import type { ChatMessage } from '../../types'

/**
 * Whether a row moves the conversation past a card proposed before it: the
 * user writing (a steer included), a row that opens a later turn (the same
 * openers the turn grouping uses: a nudge, a sub-agent completion, a cron or
 * setup-result inject...), or a notice. Rows of the proposing turn itself do
 * not, whatever they say: the agent nearly always follows a card with "I've put
 * a card on screen", then tool lines and its done marker.
 */
function movesPastCard(m: ChatMessage): boolean {
  if (m.role === 'user' || m.role === 'notice') return true
  if (TURN_OPENER_ROLES.has(m.role) || injectOpensTurn(m)) return true
  return m.role === 'assistant' && isSystemNoticeKind(m.kind ?? (m.meta?.kind as string | undefined))
}

/**
 * The card proposed in the transcript's latest turn, or `null` once the
 * conversation has moved past it (see {@link movesPastCard}). With two cards in
 * that turn, the later one is returned.
 */
export function setupCardAtTail(messages: readonly ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    const ref = setupCardRefOf(m.meta)
    if (ref) return ref.id
    if (movesPastCard(m)) return null
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
