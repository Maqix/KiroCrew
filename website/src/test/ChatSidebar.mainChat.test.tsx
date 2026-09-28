/**
 * The main chat in the sidebar (RFC §6.9): it leads the pinned sessions,
 * whatever pinned order this browser stored, and carries a "Main" chip on its
 * meta line (`data-testid="session-main-badge"`). No other row gets the chip.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'
import { createTestStore } from './helpers'
import { ThemeProvider } from '../hooks/useTheme'
import { PINNED_SESSION_ORDER_KEY } from '../utils/pinnedSessionOrder'
import type { ChatSlot } from '../types'

// Render framer-motion elements as plain DOM (happy-dom cannot run projection).
vi.mock('framer-motion', async () => {
  const React = await import('react')
  const FRAMER_PROPS = new Set([
    'layout', 'layoutId', 'layoutScroll', 'initial', 'animate', 'exit',
    'transition', 'variants', 'whileHover', 'whileTap', 'whileInView',
    'drag', 'dragConstraints', 'dragElastic', 'onAnimationComplete',
  ])
  const make = (tag: string) =>
    React.forwardRef((props: Record<string, unknown>, ref: React.Ref<unknown>) => {
      const clean: Record<string, unknown> = {}
      for (const k of Object.keys(props)) {
        if (k === 'children' || FRAMER_PROPS.has(k)) continue
        clean[k] = props[k]
      }
      return React.createElement(tag, { ...clean, ref }, props.children as React.ReactNode)
    })
  const motion = new Proxy({}, { get: (_t, tag: string) => make(tag) })
  return {
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    LayoutGroup: ({ children }: { children?: React.ReactNode }) => React.createElement(React.Fragment, null, children),
  }
})

vi.mock('../components/ProjectPicker', () => ({ default: () => null }))
vi.mock('../pages/chat/ChatSettings', () => ({
  loadChatConfig: () => ({ tagColumnsEnabled: false, confirmCloseSession: false }),
  saveChatConfig: vi.fn(),
}))
vi.mock('../api/client', () => ({
  SEARCH_MIN_CHARS: 2,
  api: new Proxy({} as Record<string, unknown>, {
    get: () => vi.fn().mockResolvedValue([]),
  }),
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
})

import ChatSidebar from '../pages/ChatSidebar'
import type { RootState } from '../store'

// `modified` is epoch seconds: the pinned `k-pin-new` is the more recent pin,
// so by the ordinary sort it would lead; the main chat is older.
const SLOTS: ChatSlot[] = [
  { key: 'k-plain', title: 'Plain chat', messages: 1, running: false, modified: 5000 },
  { key: 'k-pin-new', title: 'Newer pin', messages: 1, running: false, modified: 4000, pinned: true },
  { key: 'k-main', title: 'Nova', messages: 1, running: false, modified: 1000, pinned: true },
] as unknown as ChatSlot[]

function renderSidebar(mainSlot: string | null) {
  const store = createTestStore({
    dashboard: {
      status: {}, connected: true, slots: SLOTS, approvalMode: 'normal',
      channelTrusted: false, refreshTrigger: 0, unreadSlots: [], updateProgress: null,
      slotsLoaded: true,
      subagentRunning: {}, subagentDetails: {}, subagentText: {},
      sessionDefaultColor: null, sessionColorsMode: 'tint', sessionColorsPalette: 'horizon', sessionColorsIntensity: 'clear',
    } as unknown as RootState['dashboard'],
    chat: { activeSlot: null, slotStatusDetail: {}, subagents: {}, slotActivity: {} } as unknown as RootState['chat'],
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  qc.setQueryData(['chat-folders'], [])
  return render(
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <ThemeProvider>
          <MemoryRouter>
            <ChatSidebar
              slots={SLOTS} activeSlot={null} unreadSlots={[]}
              history={[]} historyHasMore={false} defaultAgent="" installedAgents={[]}
              mainSlot={mainSlot}
            />
          </MemoryRouter>
        </ThemeProvider>
      </Provider>
    </QueryClientProvider>,
  )
}

const rowOrder = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('[data-slot-key]')).map(el => el.getAttribute('data-slot-key'))

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('mc-session-stale-collapse-ms', '0')
})

describe('chat sidebar — the main chat', () => {
  it('control: without a main chat the pinned rows keep their ordinary order and no chip shows', () => {
    const { container, queryAllByTestId } = renderSidebar(null)
    expect(rowOrder(container).slice(0, 2)).toEqual(['k-pin-new', 'k-main'])
    expect(queryAllByTestId('session-main-badge')).toHaveLength(0)
  })

  it('leads the pinned sessions and carries the one "Main" chip', () => {
    const { container, getAllByTestId } = renderSidebar('k-main')
    expect(rowOrder(container)).toEqual(['k-main', 'k-pin-new', 'k-plain'])
    const chips = getAllByTestId('session-main-badge')
    expect(chips).toHaveLength(1)
    expect(chips[0]).toHaveTextContent('Main')
    expect(chips[0].closest('[data-slot-key]')?.getAttribute('data-slot-key')).toBe('k-main')
  })

  it('stays first even when this browser stored a pinned order that puts another pin ahead', () => {
    localStorage.setItem(PINNED_SESSION_ORDER_KEY, JSON.stringify(['k-main', 'k-pin-new']))
    // Control: the stored order is honoured on its own…
    const control = renderSidebar(null)
    expect(rowOrder(control.container).slice(0, 2)).toEqual(['k-main', 'k-pin-new'])
    control.unmount()
    // …and the main chat still overrides it.
    const { container } = renderSidebar('k-pin-new')
    expect(rowOrder(container).slice(0, 2)).toEqual(['k-pin-new', 'k-main'])
  })
})
