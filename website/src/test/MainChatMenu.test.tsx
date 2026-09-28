/**
 * The session menu's main-chat entries (RFC §6.9), driven through the chat
 * header menu (the same SessionActionsMenu the sidebar rows render):
 *
 * - "Make this my main chat" on an eligible dashboard chat that is not the main
 *   chat: posts `/api/setup/main-chat`, moves `main_slot` in the boot cache (so
 *   the sidebar's Main chip moves), confirms in place; a refusal is a passive
 *   notice plus its menu-item hand-off. Hidden on the main chat itself and on
 *   chats the gateway would refuse (non-`chat-` keys, channel-born, Slack-linked,
 *   job- or app-minted).
 * - "Ask about this chat in main chat" on every other chat once a main chat
 *   exists: pre-fills (never sends) `About the chat "<title>": ` there.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Provider } from 'react-redux'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { createTestStore } from './helpers'
import { ApiError } from '../api/apiError'
import { ThemeProvider } from '../hooks/useTheme'

vi.mock('../api/client', () => ({
  api: {
    slackChannels: vi.fn().mockResolvedValue([]),
    mcpActive: vi.fn().mockResolvedValue([]),
    setSlotColor: vi.fn().mockResolvedValue({}),
    chatFolders: vi.fn().mockResolvedValue([]),
    themeBoot: vi.fn().mockResolvedValue({}),
    makeMainChat: vi.fn(),
    chatSlotDetail: vi.fn().mockResolvedValue({ messages: [], has_more: false }),
  },
}))

import { api } from '../api/client'
import { ChatHeaderMenu } from '../pages/chat/ChatPageMessageContent'
import { PREFILL_STORAGE_KEY } from '../utils/navIntent'
import { setActiveSlot } from '../store/chatSlice'
import type { RootState } from '../store'
import type { ChatSlot } from '../types'

const dashboardState = {
  status: {}, connected: true, slots: [], approvalMode: 'normal',
  channelTrusted: false, refreshTrigger: 0, unreadSlots: [], updateProgress: null,
  subagentRunning: {}, subagentDetails: {}, subagentText: {},
  sessionDefaultColor: null, sessionColorsMode: 'tint', sessionColorsPalette: 'horizon', sessionColorsIntensity: 'clear',
} as unknown as RootState['dashboard']

const SLOTS = [
  { key: 'chat-1-100', title: 'Release notes' },
  { key: 'chat-main-1', title: 'Nova', pinned: true },
  { key: 'chat-slack-1', title: 'From Slack', slack_linked: true },
  { key: 'chat-born-1', title: 'Born in Discord', links: [{ channel: 'discord', label: 'Discord', target: '…12', direction: 'origin', live: true }] },
  { key: 'chat-cron-1', title: 'Nightly', origin: 'cron' },
  { key: 'member-x', title: 'Member DM' },
] as unknown as ChatSlot[]

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname + loc.search}</div>
}

function renderMenu(slotKey: string, opts: { mainSlot?: string | null; route?: string; activeSlot?: string | null } = {}) {
  const store = createTestStore({ dashboard: { ...dashboardState, slots: SLOTS } })
  if (opts.activeSlot) store.dispatch(setActiveSlot(opts.activeSlot))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  qc.setQueryData(['theme-boot'], { mode: 'dark', main_slot: opts.mainSlot ?? null })
  const utils = render(
    <QueryClientProvider client={qc}>
      <Provider store={store}>
        <ThemeProvider>
          <MemoryRouter initialEntries={[opts.route ?? '/chat']}>
            <Routes>
              <Route path="*" element={<><ChatHeaderMenu activeSlot={slotKey} /><LocationProbe /></>} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </Provider>
    </QueryClientProvider>,
  )
  // Radix opens a dropdown on keyboard activation (the path happy-dom handles).
  fireEvent.keyDown(utils.container.querySelector('button')!, { key: 'Enter' })
  return { ...utils, qc, store }
}

beforeEach(() => {
  vi.mocked(api.makeMainChat).mockReset()
  sessionStorage.clear()
  localStorage.clear()
})

describe('Make this my main chat', () => {
  it('is offered on an eligible chat, posts the slot, moves main_slot and confirms in place', async () => {
    vi.mocked(api.makeMainChat).mockResolvedValue({ main_slot: 'chat-1-100' })
    // The boot re-read after the change reports the new main chat.
    vi.mocked(api.themeBoot).mockResolvedValue({ mode: 'dark', main_slot: 'chat-1-100' })
    const { qc } = renderMenu('chat-1-100', { mainSlot: 'chat-main-1' })
    const item = await screen.findByText('Make this my main chat')
    fireEvent.click(item)
    await waitFor(() => expect(api.makeMainChat).toHaveBeenCalledWith('chat-1-100'))
    // The menu stays open to say it worked.
    expect(await screen.findByTestId('session-menu-main-chat-done')).toHaveTextContent('This is now your main chat')
    await waitFor(() => expect((qc.getQueryData(['theme-boot']) as { main_slot?: string }).main_slot).toBe('chat-1-100'))
  })

  it('is offered when there is no main chat yet (an install without a first run)', async () => {
    renderMenu('chat-1-100', { mainSlot: null })
    expect(await screen.findByText('Make this my main chat')).toBeInTheDocument()
    // Nothing to ask in yet.
    expect(screen.queryByText('Ask about this chat in main chat')).toBeNull()
  })

  it('reports a refusal inside the menu, with the agent hand-off as its own item', async () => {
    vi.mocked(api.makeMainChat).mockRejectedValue(
      new ApiError(409, 'only one of your own dashboard chats can be the main chat', JSON.stringify({ error: 'x', code: 'slot_not_eligible' })),
    )
    renderMenu('chat-1-100', { mainSlot: null })
    fireEvent.click(await screen.findByText('Make this my main chat'))
    const notice = await screen.findByTestId('session-menu-main-chat-error')
    expect(notice).toHaveTextContent('Only one of your own dashboard chats can be the main chat.')
    // The hand-off is a real menu item described by the passive notice.
    const handoffs = screen.getAllByRole('menuitem').filter(el => el.getAttribute('aria-describedby') === notice.id)
    expect(handoffs).toHaveLength(1)
    expect(handoffs[0]).toHaveTextContent(/ask the agent/i)
  })

  it.each([
    ['the main chat itself', 'chat-main-1'],
    ['a Slack-linked chat', 'chat-slack-1'],
    ['a chat born in a channel', 'chat-born-1'],
    ['a job-minted chat', 'chat-cron-1'],
    ['a non-dashboard slot', 'member-x'],
  ])('is not offered on %s', async (_label, key) => {
    renderMenu(key, { mainSlot: 'chat-main-1' })
    // Wait for the menu to render, then assert the absence.
    await screen.findByText('Copy link')
    expect(screen.queryByText('Make this my main chat')).toBeNull()
  })
})

describe('Ask about this chat in main chat', () => {
  it('is not offered on the main chat itself', async () => {
    renderMenu('chat-main-1', { mainSlot: 'chat-main-1' })
    await screen.findByText('Copy link')
    expect(screen.queryByText('Ask about this chat in main chat')).toBeNull()
  })

  it('from another page: seeds the main chat’s composer (unsent) and opens it', async () => {
    const { store } = renderMenu('chat-1-100', { mainSlot: 'chat-main-1', route: '/schedule' })
    fireEvent.click(await screen.findByText('Ask about this chat in main chat'))
    const seeded = JSON.parse(sessionStorage.getItem(PREFILL_STORAGE_KEY) || '{}')
    expect(seeded).toMatchObject({ slotKey: 'chat-main-1', prompt: 'About the chat “Release notes”: ' })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/chat?sid=chat-main-1'))
    expect(store.getState().chat.activeSlot).toBe('chat-main-1')
  })

  it('keeps what the main chat’s composer already held, adding the draft after it', async () => {
    localStorage.setItem('mc-chat-drafts', JSON.stringify({ 'chat-main-1': 'half a thought' }))
    localStorage.setItem('mc-chat-drafts-ts', JSON.stringify({ 'chat-main-1': Date.now() }))
    renderMenu('chat-1-100', { mainSlot: 'chat-main-1', route: '/schedule' })
    fireEvent.click(await screen.findByText('Ask about this chat in main chat'))
    const seeded = JSON.parse(sessionStorage.getItem(PREFILL_STORAGE_KEY) || '{}')
    expect(seeded.prompt).toBe('half a thought\n\nAbout the chat “Release notes”: ')
  })

  it('with the main chat already open: merges into its live composer instead', async () => {
    const seen: string[] = []
    const onPrefill = (e: Event) => seen.push((e as CustomEvent).detail?.text)
    window.addEventListener('mc:prefill-composer', onPrefill)
    try {
      renderMenu('chat-1-100', { mainSlot: 'chat-main-1', route: '/chat', activeSlot: 'chat-main-1' })
      await act(async () => { fireEvent.click(await screen.findByText('Ask about this chat in main chat')) })
      expect(seen).toEqual(['About the chat “Release notes”: '])
      expect(sessionStorage.getItem(PREFILL_STORAGE_KEY)).toBeNull()
    } finally {
      window.removeEventListener('mc:prefill-composer', onPrefill)
    }
  })
})
