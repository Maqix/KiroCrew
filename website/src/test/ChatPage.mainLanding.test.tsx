/**
 * Landing on the main chat (RFC §6.9): when the chat page opens with no
 * session in the URL and none remembered, it opens the main chat if there is
 * one; otherwise the old choice (the first row) stands. A remembered session
 * still wins, and with nothing remembered the page waits for the boot flags
 * that carry `mainSlot` rather than landing on the first row a moment early.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
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
import { api } from '../api/client'
import { switchSlot } from '../store/chatSlice'
import { writePrefill } from '../utils/navIntent'

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

beforeEach(() => {
  localStorage.clear()
  themeState.mainSlot = null
  themeState.firstRunSlot = null
  themeState.themeBootReady = true
  sessionStorage.clear()
})

describe('ChatPage lands on the main chat', () => {
  it('opens the main chat when nothing is named and nothing is remembered', async () => {
    themeState.mainSlot = 'chat-main'
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-main'))
  })

  it('control: with no main chat it keeps choosing the first row', async () => {
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
  })

  it('a remembered session still wins over the main chat', async () => {
    themeState.mainSlot = 'chat-main'
    localStorage.setItem('mc-active-slot-chat', 'chat-3')
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-3'))
  })

  it('a session named in the URL wins over the main chat', async () => {
    themeState.mainSlot = 'chat-main'
    const { store } = renderLanding('/chat?sid=chat-3')
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-3'))
  })

  it('waits for the boot flags before choosing, then lands on the main chat', async () => {
    themeState.themeBootReady = false
    const { store, rerender } = renderLanding()
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(store.getState().chat.activeSlot).toBeNull()
    themeState.mainSlot = 'chat-main'
    themeState.themeBootReady = true
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <Provider store={store}>
          <MemoryRouter initialEntries={['/chat']}>
            <ChatPage />
          </MemoryRouter>
        </Provider>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-main'))
  })

  it('a fresh install lands on its first-run chat (the rule the desktop app relies on too)', async () => {
    themeState.firstRunSlot = 'chat-3'
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-3'))
  })

  it('once graduated, the main chat wins over the first-run chat', async () => {
    themeState.firstRunSlot = 'chat-3'
    themeState.mainSlot = 'chat-main'
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-main'))
  })

  it('a keyed prefill for the main chat lands in its composer when it opens, unsent', async () => {
    // The channel the "Ask in main chat" entries use (hooks/useMainChat.ts):
    // seed the keyed prefill, then activate the main chat.
    localStorage.setItem('mc-active-slot-chat', 'chat-1')
    const { store, getByLabelText } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
    writePrefill('chat-main', 'Change the job “Morning brief”: ')
    await act(async () => { await store.dispatch(switchSlot('chat-main')) })
    await waitFor(() => expect(getByLabelText('test chat input')).toHaveValue('Change the job “Morning brief”: '))
    expect(vi.mocked(api.sendChat)).not.toHaveBeenCalled()
  })

  it('ignores a main chat this surface does not list', async () => {
    themeState.mainSlot = 'chat-gone'
    const { store } = renderLanding()
    await waitFor(() => expect(store.getState().chat.activeSlot).toBe('chat-1'))
  })
})
