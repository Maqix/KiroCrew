// Feature: chat-virtualizer — chrome below the transcript changing height
// mid-stream must not disarm follow (P2.23).
//
// The setup-card tray sits below the transcript. When it folds to its one-line
// bar the scroller GROWS by the tray's height; the layout engine lowers the
// maximum scrollTop by the same amount and clamps a flush follower down, with
// no write of ours. The clamp's scroll event is dispatched in the NEXT frame's
// scroll steps, so a streaming append that lands first sees scrollTop below our
// last write with a gap under it -- a scroll-up signature built from two of our
// own layout changes. It released follow, and the answer streamed on below the
// fold behind the "Scroll to bottom" arrow. Seen by the demo recorder when a
// sent message folded the tray while the reply started streaming.
//
// jsdom has no layout, so geometry is faked on a detached scroller (the
// technique of useVirtualChat.layoutShrink.test.tsx) and the pin is driven by
// the append layout effect. The tray's clamp is modelled exactly as the engine
// applies it: scrollTop moves, and the scroll event is dispatched only later.

import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { type RefObject } from 'react'

import { useVirtualChat } from '../hooks/virtualizer/useVirtualChat'
import type { UseVirtualChatOptions } from '../hooks/virtualizer/types'

interface Geom { scrollTop: number; scrollHeight: number; clientHeight: number }

function makeScroller(initial: Geom) {
  const el = document.createElement('div')
  const state: Geom = { ...initial }
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => state.scrollTop,
    set: (v: number) => { state.scrollTop = v },
  })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => state.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => state.clientHeight })
  ;(el as unknown as { scrollTo: (o: { top: number }) => void }).scrollTo = (o) => { state.scrollTop = o.top }
  return { el, state }
}

interface Item { id: string }
const getKey = (it: Item) => it.id
const mkItems = (n: number): Item[] => Array.from({ length: n }, (_, i) => ({ id: `m${i}` }))

const SH = 6000
const CH_TRAY_OPEN = 572          // the pane with the tray at its one-third cap
const TRAY_FOLD = 242             // 286px tray -> 44px bar
const CH_TRAY_FOLDED = CH_TRAY_OPEN + TRAY_FOLD

function mount(sessionId: string, runActive = true) {
  const { el, state } = makeScroller({ scrollTop: 0, scrollHeight: SH, clientHeight: CH_TRAY_OPEN })
  const ref: RefObject<HTMLDivElement | null> = { current: el }
  const props = (n: number): UseVirtualChatOptions<Item> => ({
    items: mkItems(n), sessionId, getKey, externalScrollerRef: ref, runActive,
  })
  const view = renderHook((p: UseVirtualChatOptions<Item>) => useVirtualChat<Item>(p), { initialProps: props(5) })
  return { el, state, view, props }
}

/** The engine's half of a viewport change: resize the box and clamp, no scroll event yet. */
function resizeBox(state: Geom, clientHeight: number) {
  state.clientHeight = clientHeight
  state.scrollTop = Math.min(state.scrollTop, Math.max(0, state.scrollHeight - clientHeight))
}

describe('useVirtualChat: the setup tray changing height mid-stream', () => {
  beforeEach(() => localStorage.clear())

  it('keeps following when the tray folds and output lands before the clamp’s scroll event', () => {
    const { el, state, view, props } = mount('tray-fold')
    expect(el.scrollTop).toBe(SH - CH_TRAY_OPEN)

    act(() => {
      resizeBox(state, CH_TRAY_FOLDED)
      // The reply streams in before the clamp's scroll event dispatches.
      state.scrollHeight = SH + 180
      view.rerender(props(6))
    })
    expect(el.scrollTop).toBe(SH + 180 - CH_TRAY_FOLDED)
    expect(view.result.current.getFollow()).toBe(true)

    // The late scroll event, then more output: still following.
    act(() => { el.dispatchEvent(new Event('scroll')) })
    act(() => {
      state.scrollHeight = SH + 400
      view.rerender(props(7))
    })
    expect(el.scrollTop).toBe(SH + 400 - CH_TRAY_FOLDED)
  })

  it('keeps following when the tray opens mid-stream (the box shrinks)', () => {
    const { el, state, view, props } = mount('tray-open')
    act(() => { resizeBox(state, CH_TRAY_FOLDED) ; el.dispatchEvent(new Event('scroll')) })
    act(() => {
      state.scrollHeight = SH + 100
      view.rerender(props(6))
    })
    expect(el.scrollTop).toBe(SH + 100 - CH_TRAY_FOLDED)

    act(() => {
      resizeBox(state, CH_TRAY_OPEN)
      state.scrollHeight = SH + 300
      view.rerender(props(7))
    })
    expect(el.scrollTop).toBe(SH + 300 - CH_TRAY_OPEN)
    expect(view.result.current.getFollow()).toBe(true)
  })

  it('still lets a reader who scrolled up go, fold or no fold', () => {
    const { el, state, view, props } = mount('tray-fold-read')
    const bottom = SH - CH_TRAY_OPEN
    // A real scroll-up well past anything the fold could explain.
    act(() => { state.scrollTop = bottom - 900; el.dispatchEvent(new Event('scroll')) })
    act(() => {
      resizeBox(state, CH_TRAY_FOLDED)
      state.scrollHeight = SH + 180
      view.rerender(props(6))
    })
    expect(el.scrollTop).toBe(bottom - 900)
    expect(view.result.current.getFollow()).toBe(false)
  })
})
