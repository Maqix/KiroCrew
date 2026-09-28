/**
 * The first-run layout verdict: which page loads start compact, and that the
 * first decided verdict is the one the page keeps.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  firstRunLayoutVerdict, settleFirstRunLayout, firstRunLayoutActive, __resetFirstRunLayout,
  type FirstRunLayoutInputs,
} from '../hooks/useFirstRunLayout'

const FIRST_RUN = 'chat-1-1790000000'
const opening = (over: Partial<FirstRunLayoutInputs> = {}): FirstRunLayoutInputs => ({
  themeBootReady: true, firstRunSlot: FIRST_RUN, mainSlot: null,
  pathname: '/chat', activeSlot: FIRST_RUN, isMobile: false, ...over,
})

describe('firstRunLayoutVerdict', () => {
  it('starts compact when the page opens on the first-run chat', () => {
    expect(firstRunLayoutVerdict(opening())).toBe(true)
    expect(firstRunLayoutVerdict(opening({ pathname: '/chat/welcome-to-kiro-crew' }))).toBe(true)
  })

  it('waits for the boot flags and for the landing session', () => {
    expect(firstRunLayoutVerdict(opening({ themeBootReady: false }))).toBeNull()
    expect(firstRunLayoutVerdict(opening({ activeSlot: null }))).toBeNull()
  })

  it('keeps today\'s layout for any other session, page or install', () => {
    expect(firstRunLayoutVerdict(opening({ activeSlot: 'chat-2-1790000100' }))).toBe(false)
    expect(firstRunLayoutVerdict(opening({ pathname: '/schedule', activeSlot: null }))).toBe(false)
    expect(firstRunLayoutVerdict(opening({ firstRunSlot: null }))).toBe(false)
  })

  it('stops once the first run has graduated into a main chat', () => {
    // Graduation keeps the slot key, so the first-run chat IS the main chat.
    expect(firstRunLayoutVerdict(opening({ mainSlot: FIRST_RUN }))).toBe(false)
  })

  it('leaves mobile to its drawers', () => {
    expect(firstRunLayoutVerdict(opening({ isMobile: true }))).toBe(false)
  })
})

describe('settleFirstRunLayout', () => {
  beforeEach(() => __resetFirstRunLayout())

  it('keeps the first verdict for the page lifetime', () => {
    expect(firstRunLayoutActive()).toBe(false)
    settleFirstRunLayout(true)
    settleFirstRunLayout(false)
    expect(firstRunLayoutActive()).toBe(true)
  })
})
