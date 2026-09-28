/**
 * `setup_card_update` (owner-only WebSocket frame, one-chat first run) folds the
 * pushed card into React Query: the card's own entry (`['setup-card', id]`, what
 * the transcript row renders from) and its slot's list (`['setup-cards', slot]`)
 * when that list is cached. A malformed frame writes nothing, a terminal card is
 * never regressed by a stale live frame, and a reconnect re-reads both families
 * because the frame has no replay.
 */
import { renderHook, act } from '@testing-library/react'
import { createElement } from 'react'
import { Provider } from 'react-redux'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createTestStore } from './helpers'
import { useWebSocket } from '../hooks/useWebSocket'
import type { SetupCard, SetupCardList } from '../api/setupCards'

vi.mock('../api/client', () => ({
  api: {
    chatSlots: vi.fn().mockResolvedValue([]),
    voiceConfig: vi.fn().mockResolvedValue({ autoSpeak: false }),
    approvals: vi.fn().mockResolvedValue([]),
    notifications: vi.fn().mockResolvedValue({ notifications: [], unread: 0 }),
    chatSlotDetail: vi.fn().mockResolvedValue({ messages: [], running: false, has_more: false, total: 0, queue: [] }),
  },
}))

const WS_INSTANCES: MockWebSocket[] = []

class MockWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  readyState = MockWebSocket.CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  send = vi.fn()
  close = vi.fn()

  constructor() { WS_INSTANCES.push(this) }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  simulateMessage(data: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

const SLOT = 'chat-1-1790000000'

function card(over: Partial<SetupCard> = {}): SetupCard {
  return {
    id: 'sc-0123456789abcdef',
    slot: SLOT,
    kind: 'connect',
    status: 'waiting',
    stakes: 'high',
    hash: 'h'.repeat(64),
    payload: { provider: { slug: 'github', name: 'GitHub', category: 'dev' } },
    outcome: { state: 'waiting', oauth_url: 'https://github.com/login/oauth/authorize' },
    error: null,
    created_ts: 1790000000,
    decided_ts: null,
    classic: { kind: 'route', target: '/connections' },
    ...over,
  }
}

describe('useWebSocket setup_card_update frame', () => {
  let testStore: ReturnType<typeof createTestStore>
  let qc: QueryClient

  beforeEach(() => {
    vi.clearAllMocks()
    WS_INSTANCES.length = 0
    testStore = createTestStore()
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  afterEach(() => { vi.unstubAllGlobals() })

  function wrapper({ children }: { children: React.ReactNode }) {
    return createElement(Provider, { store: testStore },
      createElement(QueryClientProvider, { client: qc }, children),
    )
  }

  function connect() {
    renderHook(() => useWebSocket(), { wrapper })
    const ws = WS_INSTANCES[0]
    act(() => { ws.simulateOpen() })
    return ws
  }

  it('writes the pushed card into its own cache entry', () => {
    const ws = connect()
    const pushed = card()
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: pushed } }) })
    expect(qc.getQueryData(['setup-card', pushed.id])).toEqual(pushed)
  })

  it('upserts the card into a cached slot list, and leaves an unobserved list uncreated', () => {
    const other = card({ id: 'sc-other', kind: 'privacy', status: 'committed', stakes: 'low' })
    qc.setQueryData<SetupCardList>(['setup-cards', SLOT], { cards: [other, card({ status: 'pending' })] })
    const ws = connect()
    const pushed = card({ status: 'committed', outcome: { state: 'granted' } })
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: pushed } }) })
    const list = qc.getQueryData<SetupCardList>(['setup-cards', SLOT])
    expect(list?.cards.map(c => [c.id, c.status])).toEqual([['sc-other', 'committed'], [pushed.id, 'committed']])

    // A new card on a slot this tab never listed: its entry is written, no list appears.
    const elsewhere = card({ id: 'sc-new', slot: 'chat-9' })
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: 'chat-9', card: elsewhere } }) })
    expect(qc.getQueryData(['setup-card', 'sc-new'])).toEqual(elsewhere)
    expect(qc.getQueryData(['setup-cards', 'chat-9'])).toBeUndefined()
  })

  it('appends a card the cached list does not hold yet', () => {
    qc.setQueryData<SetupCardList>(['setup-cards', SLOT], { cards: [] })
    const ws = connect()
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: card() } }) })
    expect(qc.getQueryData<SetupCardList>(['setup-cards', SLOT])?.cards).toHaveLength(1)
  })

  it('ignores a frame that is not card-shaped', () => {
    const ws = connect()
    act(() => {
      ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT } })
      ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: { id: '', status: 'pending', kind: 'x' } } })
      ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: 'sc-0123456789abcdef' } })
    })
    expect(qc.getQueryCache().findAll({ queryKey: ['setup-card'] })).toHaveLength(0)
  })

  it('never regresses a terminal card to a live status', () => {
    const decided = card({ status: 'committed', outcome: { state: 'granted' } })
    qc.setQueryData(['setup-card', decided.id], decided)
    const ws = connect()
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: card({ status: 'waiting' }) } }) })
    expect(qc.getQueryData<SetupCard>(['setup-card', decided.id])?.status).toBe('committed')
  })

  it('re-reads every setup card after a reconnect, since the frame has no replay', () => {
    const ws = connect()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => { ws.onclose?.(new CloseEvent('close')) })
    const reconnected = WS_INSTANCES[WS_INSTANCES.length - 1]
    act(() => { reconnected.simulateOpen() })
    const keys = spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))
    expect(keys).toContain(JSON.stringify(['setup-card']))
    expect(keys).toContain(JSON.stringify(['setup-cards']))
  })

  it('re-reads the boot flags when keeping a job graduates the first run (committed cron card)', () => {
    const ws = connect()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: card({ kind: 'cron', status: 'pending', stakes: 'low' }) } }) })
    expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).not.toContain(JSON.stringify(['theme-boot']))
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: card({ kind: 'connect', status: 'committed' }) } }) })
    expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).not.toContain(JSON.stringify(['theme-boot']))
    act(() => { ws.simulateMessage({ type: 'setup_card_update', data: { slot: SLOT, card: card({ id: 'sc-cron', kind: 'cron', status: 'committed', stakes: 'low' }) } }) })
    expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).toContain(JSON.stringify(['theme-boot']))
  })

  it('re-reads the boot flags when the main-chat notice arrives, and not for other assistant rows', () => {
    const ws = connect()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    act(() => { ws.simulateMessage({ type: 'chat_message', data: { slot: SLOT, role: 'assistant', content: 'hi', ts: '2026-09-28T00:00:00Z' } }) })
    expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).not.toContain(JSON.stringify(['theme-boot']))
    act(() => {
      ws.simulateMessage({ type: 'chat_message', data: {
        slot: SLOT, role: 'assistant', ts: '2026-09-28T00:00:01Z',
        content: 'Setup is done. This is your main chat with Nova: …', meta: { kind: 'main_chat' },
      } })
    })
    expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).toContain(JSON.stringify(['theme-boot']))
  })

  it('does not re-read setup cards on the first connect', () => {
    const spy = vi.spyOn(qc, 'invalidateQueries')
    connect()
    const keys = spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))
    expect(keys).not.toContain(JSON.stringify(['setup-card']))
  })
})
