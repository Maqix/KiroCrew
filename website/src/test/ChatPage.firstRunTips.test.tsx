/**
 * The dashboard's generic feature tips are held while the one-chat first run is
 * under way (its slot known, no main chat yet), in every chat: there the setup
 * cards and notices are the only guidance, and a tip lands under a live card.
 * They resume once graduation sets the main chat. An existing install, with no
 * first-run slot, is unchanged. The user's "Turn off tips" opt-out is the
 * backend's (`tipsNext` serves nothing), so it still wins either way.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { createTestStore } from './helpers'
import type { ChatSlot } from '../types'
import type { RootState } from '../store'

const themeState = { mainSlot: null as string | null, firstRunSlot: null as string | null, themeBootReady: true }
vi.mock('../hooks/useTheme', () => ({
  useOptionalTheme: () => themeState,
  useTheme: () => themeState,
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
}))

// The hook's own `blocked` contract (no fetch, nothing marked shown, hidden on
// the same render, eligible again once unblocked) is pinned in TipCard.test.tsx.
// Here the stub records what ChatPage hands it.
const tipCalls = vi.hoisted(() => ({ blocked: [] as boolean[] }))
vi.mock('../components/TipCard', () => ({
  useTipTrigger: (_running: boolean, _suppressed: boolean, _slot: string | null, blocked = false) => {
    tipCalls.blocked.push(blocked)
    return { tip: null, dismiss: () => {} }
  },
  TipCard: () => null,
}))

// --- Stub child components ---
vi.mock('react-virtuoso', () => ({ Virtuoso: () => null }))
vi.mock('../components/ChatInput', () => ({
  default: ({ value }: { value: string }) => (
    <textarea aria-label="test chat input" value={value} readOnly />
  ),
}))
vi.mock('../components/PendingQuestionCard', () => ({
  default: ({ onFallbackSend }: { onFallbackSend: (text: string) => void }) => (
    <button aria-label="send stale question fallback" onClick={() => onFallbackSend('Public only')}>
      Send fallback
    </button>
  ),
}))
vi.mock('../components/WelcomeView', () => ({ default: () => null }))
vi.mock('../components/MarkdownPanel', () => ({ default: () => null }))
vi.mock('../components/MarkdownRenderer', () => ({ default: () => null }))
vi.mock('../components/TypewriterText', () => ({ default: () => null }))
vi.mock('../components/OverlayDrawer', () => ({ default: () => null }))
vi.mock('../components/AgentDropdownList', () => ({ default: () => null }))
vi.mock('../components/ModelDropdownList', () => ({ default: () => null }))
vi.mock('../components/InfoTip', () => ({ default: () => null }))
vi.mock('../components/SegmentedControl', () => ({ default: () => null }))
vi.mock('../pages/chat/CollapsibleToolGroup', () => ({ default: () => null }))
vi.mock('../pages/chat/ActivityViewer', () => ({ default: () => null }))
vi.mock('../pages/chat/SessionColorPicker', () => ({ default: () => null }))
vi.mock('../pages/chat', () => ({ ChatFooter: () => null, AssistantMessage: () => null, McpInfoButton: () => null }))
vi.mock('../pages/ChatSidebar', () => ({ default: () => null, SIDEBAR_MIN: 200, SIDEBAR_MAX: 500 }))
vi.mock('../pages/chat/ChatSettings', () => ({ loadChatConfig: () => ({ contentWidth: 'compact' }), CONTENT_WIDTH: { compact: { messages: '900px', input: '916px' }, comfortable: { messages: '84%', input: '85%' }, full: { messages: '92%', input: '93%' } } }))

// --- Stub hooks ---
vi.mock('../hooks/useBranding', () => ({ useBranding: () => ({ botName: 'Test', avatar: '' }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: () => ({ agents: [], defaultAgent: null }) }))
vi.mock('../hooks/useFilteredDropdown', () => ({ useFilteredDropdown: () => ({ filtered: [], query: '', setQuery: vi.fn(), selectedIndex: 0, setSelectedIndex: vi.fn(), onKeyDown: vi.fn() }) }))
vi.mock('../hooks/useVoiceInput', () => ({ useVoiceInput: () => ({ recording: false, transcribing: false, toggle: vi.fn() }), voiceInputSupported: false }))

// --- Stub API ---
vi.mock('../api/client', () => ({
  api: Object.fromEntries(
    ['sessions', 'chatSlotDetail', 'createChatSlot', 'deleteChatSlot', 'resumeChatSlot',
     'deleteSession', 'agentDetail', 'approveChatSlot', 'chatSlotAgent', 'chatSlotModel',
     'chatSlotWorkspace', 'models', 'planAction', 'planFromChat', 'renameSlot',
     'resolveApproval', 'screenshot', 'slackChannels', 'slackLink', 'spawnList',
     'stopChatSlot', 'uploadFiles', 'voiceSynthesize', 'workspaces', 'chatSlots',
     'notifications', 'status', 'sendChat'].map(k => [k, vi.fn().mockResolvedValue(k === 'chatSlotDetail' ? { messages: [], has_more: false } : {})])
  ),
}))

// --- Browser APIs ---
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
})
globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }) as unknown as typeof fetch

import ChatPage from '../pages/ChatPage'

const slot = (key: string): ChatSlot => ({
  key, title: key, messages: 0, running: false, mode: '', created: '', last_ts: '',
  pending_approval: false, waiting_for_input: false, last_activity_ts: undefined,
})
const SLOTS = [slot('chat-1'), slot('chat-main'), slot('chat-3')]

function renderLanding(route = '/chat') {
  const store = createTestStore({
    dashboard: {
      status: { platform: 'darwin' }, connected: true, slots: SLOTS, slotsLoaded: true, approvalMode: 'normal',
      channelTrusted: false, refreshTrigger: 0, unreadSlots: [], updateProgress: null,
      subagentRunning: {}, subagentDetails: {}, subagentText: {},
      sessionDefaultColor: null, sessionColorsMode: 'tint', sessionColorsPalette: 'horizon', sessionColorsIntensity: 'clear',
    } as unknown as RootState['dashboard'],
    chat: {
      activeSlot: null, messages: [], slotRunning: false, slotStopping: false, slotState: 'idle',
      slotStatusDetail: {}, slotHasMore: false, slotOldestIndex: 0, loadingOlder: false,
      lastChunkSeq: undefined, history: [], historyHasMore: false, historyOffset: 0,
      pendingInput: null, slotContextPct: {}, voicePlaying: false, voiceAudio: null,
      subagents: {}, toolLog: [], activityOpen: false, activityTab: 'tools', slotActivity: {}, slotHistory: [],
      slotMessages: {}, slotLoading: false,
    } as unknown as RootState['chat'],
  })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <MemoryRouter initialEntries={[route]}>
          <ChatPage />
        </MemoryRouter>
      </Provider>
    </QueryClientProvider>,
  )
  return { store, ...utils }
}


const lastBlocked = () => tipCalls.blocked[tipCalls.blocked.length - 1]

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  tipCalls.blocked = []
  themeState.mainSlot = null
  themeState.firstRunSlot = null
  themeState.themeBootReady = true
})

describe('ChatPage holds generic tips during the first run', () => {
  it('holds them in the first-run chat', async () => {
    themeState.firstRunSlot = 'chat-1'
    const { store } = renderLanding('/chat?sid=chat-1')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
    expect(lastBlocked()).toBe(true)
  })

  it('holds them in any other chat too, until graduation', async () => {
    themeState.firstRunSlot = 'chat-1'
    const { store } = renderLanding('/chat?sid=chat-3')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-3'))
    expect(lastBlocked()).toBe(true)
  })

  it('lets them resume when graduation sets the main chat mid-session', async () => {
    themeState.firstRunSlot = 'chat-1'
    const { store, rerender } = renderLanding('/chat?sid=chat-1')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
    expect(lastBlocked()).toBe(true)
    // Graduation keeps the slot key: the first-run chat becomes the main chat.
    themeState.mainSlot = 'chat-1'
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <Provider store={store}>
          <MemoryRouter initialEntries={['/chat?sid=chat-1']}>
            <ChatPage />
          </MemoryRouter>
        </Provider>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(lastBlocked()).toBe(false))
  })

  it('shows them after graduation on a fresh load', async () => {
    themeState.firstRunSlot = 'chat-1'
    themeState.mainSlot = 'chat-1'
    const { store } = renderLanding('/chat?sid=chat-1')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
    expect(lastBlocked()).toBe(false)
  })

  it('control: an existing install with no first-run chat is unchanged', async () => {
    const { store } = renderLanding('/chat?sid=chat-3')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-3'))
    expect(lastBlocked()).toBe(false)
  })
})
