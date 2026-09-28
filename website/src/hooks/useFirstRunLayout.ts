import { useSyncExternalStore } from 'react'

/**
 * Whether this page load opened on the one-chat first run, so the dashboard
 * starts compact: the nav rail collapsed to its icons and the session list
 * hidden, leaving the conversation the width of the window.
 *
 * A STARTING layout, not a mode. App settles the verdict once, the first time it
 * can (theme boot read and, on /chat, the landing session known), and it holds
 * for the page's lifetime, so nothing re-lays-out under the conversation: not a
 * session switch, not graduation into the main chat mid-session. Each surface
 * applies it only while its own stored preference is absent (`mc-nav`,
 * `mc-sidebar-pinned`) and never writes that preference. The existing toggles
 * do, so the user's first expand persists the way it always has and wins from
 * then on.
 *
 * Module-level (same shape as useRailWidth) so a ChatPage remounted after a trip
 * to another page reads the verdict App made instead of re-deciding it.
 */
let verdict: boolean | null = null
const listeners = new Set<() => void>()

export interface FirstRunLayoutInputs {
  themeBootReady: boolean
  firstRunSlot: string | null
  mainSlot: string | null
  pathname: string
  activeSlot: string | null
  isMobile: boolean
}

/** `true`/`false` once decidable, `null` while an input is still loading. */
export function firstRunLayoutVerdict({
  themeBootReady, firstRunSlot, mainSlot, pathname, activeSlot, isMobile,
}: FirstRunLayoutInputs): boolean | null {
  if (!themeBootReady) return null
  // A main chat means the first run graduated (or the user picked one by hand):
  // the dashboard is the everyday one now and opens with today's defaults.
  // Mobile keeps its drawers.
  if (!firstRunSlot || mainSlot || isMobile) return false
  // Only the page load that OPENS on the chat. One that opens elsewhere keeps
  // its layout when the user later walks into the chat.
  if (pathname !== '/chat' && !pathname.startsWith('/chat/')) return false
  if (!activeSlot) return null
  return activeSlot === firstRunSlot
}

/** Called by App with a decided verdict. Only the first call counts. */
export function settleFirstRunLayout(value: boolean) {
  if (verdict !== null) return
  verdict = value
  listeners.forEach(l => l())
}

/** Synchronous read for a `useState` initializer. */
export function firstRunLayoutActive(): boolean {
  return verdict === true
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

export function useFirstRunLayout(): boolean {
  return useSyncExternalStore(subscribe, firstRunLayoutActive, firstRunLayoutActive)
}

/** Test seam: forget the verdict between cases. */
export function __resetFirstRunLayout() {
  verdict = null
  listeners.clear()
}
