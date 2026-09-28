/**
 * A page load that opens on the one-chat first run starts with the session
 * list hidden, so the conversation takes the width and the sessions toggle
 * sits at the top left. The hide is a starting layout, never a stored choice:
 * `mc-sidebar-pinned` stays unwritten until the user toggles the list, and that
 * choice wins from then on. A page whose verdict is not first-run is unchanged.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, act, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { createTestStore } from './helpers'
import type { ChatSlot } from '../types'
import { ThemeProvider } from '../hooks/useTheme'
import { __resetPanelTabs } from '../hooks/usePanelTabs'
import { settleFirstRunLayout, __resetFirstRunLayout } from '../hooks/useFirstRunLayout'
import { sseSlots } from '../store/dashboardSlice'

vi.mock('react-virtuoso', () => ({ Virtuoso: () => null }))
vi.mock('../components/ChatInput', () => ({ default: () => null }))
vi.mock('../components/WelcomeView', () => ({ default: () => null }))
vi.mock('../components/MarkdownPanel', () => ({ default: () => null }))
vi.mock('../components/MarkdownRenderer', () => ({ default: () => null }))
vi.mock('../components/TypewriterText', () => ({ default: () => null }))
// The drawer stands in for `sidebarOpen` itself, so a test can assert the
// session list's real visibility rather than inferring it from the toggle label
// (which is not rendered at all on mobile, or with an empty list).
vi.mock('../components/OverlayDrawer', () => ({
  default: ({ open, children }: { open?: boolean; children?: React.ReactNode }) =>
    (open ? <div data-testid="sessions-drawer">{children}</div> : null),
}))
vi.mock('../components/AgentDropdownList', () => ({ default: () => null }))
vi.mock('../components/ModelDropdownList', () => ({ default: () => null }))
vi.mock('../components/InfoTip', () => ({ default: () => null }))
vi.mock('../components/SegmentedControl', () => ({ default: () => null }))
vi.mock('../pages/chat/CollapsibleToolGroup', () => ({ default: () => null }))
vi.mock('../pages/chat/ActivityViewer', () => ({ default: () => null }))
vi.mock('../pages/chat/SessionColorPicker', () => ({ default: () => null }))
vi.mock('../pages/chat', () => ({ ChatFooter: () => null, AssistantMessage: () => null, McpInfoButton: () => null }))
vi.mock('../pages/ChatSidebar', () => ({ default: () => null, SIDEBAR_MIN: 200, SIDEBAR_MAX: 500 }))
vi.mock('../pages/chat/ChatSettings', () => ({ loadChatConfig: () => ({ contentWidth: 'compact' }), CONTENT_WIDTH: { compact: { messages: '800px', input: '816px' }, comfortable: { messages: '84%', input: '85%' }, full: { messages: '92%', input: '93%' } } }))
vi.mock('../pages/chat/SidePanel', () => ({
  // `expanded` is surfaced so a test can assert the panel stops claiming its
  // maximum once the user reopens the session list.
  default: ({ expanded }: { expanded?: boolean }) =>
    <div data-testid="side-panel" data-expanded={String(!!expanded)} />,
  SIDE_PANEL_MIN_W: 320,
  SIDE_PANEL_RESERVED_W: 560,
  CHAT_PANE_MIN_W: 320,
  sidePanelFillWidth: () => undefined,
}))
vi.mock('../hooks/useBranding', () => ({ useBranding: () => ({ botName: 'Test', avatar: '' }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: () => ({ agents: [], defaultAgent: null }) }))
vi.mock('../hooks/useFilteredDropdown', () => ({ useFilteredDropdown: () => ({ filtered: [], query: '', setQuery: vi.fn(), selectedIndex: 0, setSelectedIndex: vi.fn(), onKeyDown: vi.fn() }) }))
vi.mock('../hooks/useVoiceInput', () => ({ useVoiceInput: () => ({ recording: false, transcribing: false, toggle: vi.fn() }), voiceInputSupported: false }))
// useIsMobile reads matchMedia at module load, so a per-test matchMedia stub
// cannot move it. Mock the hook with a mutable flag instead.
vi.mock('../hooks/useIsMobile', () => ({ useIsMobile: () => false }))
vi.mock('../api/client', () => ({
  api: Object.fromEntries(
    ['sessions', 'chatSlotDetail', 'createChatSlot', 'deleteChatSlot', 'resumeChatSlot',
      'deleteSession', 'agentDetail', 'approveChatSlot', 'chatSlotAgent', 'chatSlotModel',
      'chatSlotWorkspace', 'models', 'planAction', 'planFromChat', 'renameSlot',
      'resolveApproval', 'screenshot', 'slackChannels', 'slackLink', 'spawnList',
      'stopChatSlot', 'uploadFiles', 'voiceSynthesize', 'workspaces', 'chatSlots',
      'notifications', 'status', 'generateTitle'].map(k => [k, vi.fn().mockResolvedValue(
      k === 'chatSlotDetail' ? { messages: [], has_more: false, total: 0 } : {},
    )]),
  ),
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
})
globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }) as unknown as typeof fetch
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver

import ChatPage from '../pages/ChatPage'

const FIRST_RUN = 'chat-1-1790000000'
const listHidden = () => screen.queryByRole('button', { name: 'Show sessions sidebar' }) !== null
const listShown = () => screen.queryByRole('button', { name: 'Hide sessions sidebar' }) !== null

function renderChat({ slots = 1 }: { slots?: number } = {}) {
  const store = createTestStore()
  act(() => {
    store.dispatch(sseSlots(
      Array.from({ length: slots }, (_, i) => ({ key: i === 0 ? FIRST_RUN : `chat-${i + 1}`, title: `Session ${i}` }) as unknown as ChatSlot),
    ))
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Provider store={store}>
        <ThemeProvider>
          <MemoryRouter initialEntries={[`/chat?sid=${FIRST_RUN}`]}>
            <Routes><Route path="/chat/:slug?" element={<ChatPage />} /></Routes>
          </MemoryRouter>
        </ThemeProvider>
      </Provider>
    </QueryClientProvider>,
  )
  return { store, ...view }
}

describe('ChatPage — the first-run chat opens with the session list hidden', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
    localStorage.clear()
    __resetPanelTabs()
    __resetFirstRunLayout()
  })

  it('hides the list when the verdict lands after mount, without writing the preference', () => {
    // The real order: ChatPage lands on the session, then App settles the verdict.
    renderChat()
    expect(listShown()).toBe(true)
    act(() => settleFirstRunLayout(true))
    expect(listHidden()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBeNull()
  })

  it('persists the user\'s expand and keeps it across a remount', () => {
    const first = renderChat()
    act(() => settleFirstRunLayout(true))
    fireEvent.click(screen.getByRole('button', { name: 'Show sessions sidebar' }))
    expect(listShown()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBe('true')

    // Back from another page in the same load: the stored choice wins over the verdict.
    first.unmount()
    renderChat()
    expect(listShown()).toBe(true)
  })

  it('stays hidden from the first render when the chat remounts untouched', () => {
    act(() => settleFirstRunLayout(true))
    renderChat()
    expect(listHidden()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBeNull()
  })

  it('keeps a list the user hid before', () => {
    localStorage.setItem('mc-sidebar-pinned', 'false')
    renderChat()
    act(() => settleFirstRunLayout(true))
    expect(listHidden()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBe('false')
  })

  it('waits for a row, so the empty-list force-open never persists over it', () => {
    const { store } = renderChat({ slots: 0 })
    act(() => settleFirstRunLayout(true))
    expect(localStorage.getItem('mc-sidebar-pinned')).toBeNull()
    act(() => {
      store.dispatch(sseSlots([{ key: FIRST_RUN, title: 'Welcome to Kiro Crew' } as unknown as ChatSlot]))
    })
    expect(listHidden()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBeNull()
  })

  it('leaves the list alone on a page load that is not the first run', () => {
    renderChat()
    act(() => settleFirstRunLayout(false))
    expect(listShown()).toBe(true)
    expect(localStorage.getItem('mc-sidebar-pinned')).toBeNull()
  })
})
