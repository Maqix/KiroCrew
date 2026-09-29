/**
 * The tray must never crowd out what the agent is saying.
 *
 * It shows a live card in full while that card is the newest thing in the
 * transcript, folds to a one-line bar ("<title> · needs you · Show") once
 * something newer lands or the user scrolls up to read, opens again in one
 * click, never folds under a focused field, and caps itself at a third of its
 * chat pane. Driven through MSW with the real SetupCard.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { useRef } from 'react'

import { server } from '../../integration/mocks/server'
import PendingSetupCards from '../components/setup/PendingSetupCards'
import SetupCardRow from '../components/setup/SetupCardRow'
import { highlightSetupCardRow, setupCardAtTail } from '../components/setup/setupCardTray'
import type { SetupCard as Card } from '../api/setupCards'
import type { ChatMessage } from '../types'

const SLOT = 'chat-1-1790000000'

function card(over: Partial<Card> & Pick<Card, 'id' | 'kind'>): Card {
  return {
    slot: SLOT, status: 'pending', stakes: 'low', hash: 'b'.repeat(64), payload: {},
    outcome: null, error: null, created_ts: 1790000000, decided_ts: null,
    classic: { kind: 'none', target: '' }, ...over,
  }
}

function serveCards(cards: Card[]) {
  const byId = new Map(cards.map(c => [c.id, c]))
  server.use(
    http.get('/api/setup/cards', () => HttpResponse.json({ cards: [...byId.values()] })),
    http.get('/api/setup/cards/:id', ({ params }) => HttpResponse.json(byId.get(String(params.id)))),
  )
}

const cardRow = (id: string): ChatMessage =>
  ({ role: 'inject', cls: '', content: 'model-visible summary', meta: { setupCard: { id, kind: 'profile' } } }) as ChatMessage
const say = (content: string): ChatMessage => ({ role: 'assistant', cls: '', content }) as ChatMessage

interface SurfaceProps {
  messages: ChatMessage[]
  atBottom?: boolean
  onLocate?: (id: string, behavior: ScrollBehavior) => void
  paneHeight?: number
}

function Surface({ messages, atBottom = true, onLocate, paneHeight }: SurfaceProps) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  return (
    <div
      data-setup-tray-pane=""
      ref={el => {
        if (el && paneHeight) Object.defineProperty(el, 'clientHeight', { configurable: true, value: paneHeight })
      }}
    >
      <div ref={scrollerRef} data-testid="scroller" />
      <PendingSetupCards slotKey={SLOT} messages={messages} atBottom={atBottom} scrollerRef={scrollerRef} onLocate={onLocate} />
    </div>
  )
}

function renderSurface(props: SurfaceProps) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  const wrap = (p: SurfaceProps) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter><Surface {...p} /></MemoryRouter>
    </QueryClientProvider>
  )
  const utils = render(wrap(props))
  return { ...utils, update: (p: SurfaceProps) => utils.rerender(wrap(p)) }
}

const tray = () => screen.getByTestId('setup-card-tray')
const cards = () => screen.getByTestId('setup-card-tray-cards')
const expectOpen = () => {
  expect(tray()).toHaveAttribute('data-expanded', 'true')
  expect(cards()).not.toHaveAttribute('inert')
}
const expectFolded = () => {
  expect(tray()).toHaveAttribute('data-expanded', 'false')
  expect(cards()).toHaveAttribute('inert')
  expect(cards()).toHaveAttribute('aria-hidden', 'true')
}

const ASKED = [say('Where should your crew live?'), cardRow('sc-a')]
const HERMES = say('While that builds: do you already use another agent, like Hermes? I can bring its memory over.')

beforeEach(() => {
  serveCards([card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } })])
})

describe('the tray folds when the transcript has something newer', () => {
  it('shows the card in full while it is the newest thing', async () => {
    renderSurface({ messages: ASKED })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    expectOpen()
    expect(screen.queryByTestId('setup-card-tray-bar')).toBeNull()
  })

  it('folds to a one-line bar when a newer agent message arrives, and opens in one click', async () => {
    const { update } = renderSurface({ messages: ASKED })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    update({ messages: [...ASKED, HERMES] })
    expectFolded()
    const bar = screen.getByTestId('setup-card-tray-bar')
    expect(bar).toHaveTextContent('Your profile')
    expect(bar).toHaveTextContent('needs you')
    const toggle = within(bar).getByRole('button', { name: 'Show' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls', cards().id)

    fireEvent.click(toggle)
    expectOpen()
    // Opened by hand, the bar stays as its header so it can be folded again.
    expect(within(screen.getByTestId('setup-card-tray-bar')).getByRole('button', { name: 'Hide' }))
      .toHaveAttribute('aria-expanded', 'true')
    // More of the same reply does not undo the click.
    update({ messages: [...ASKED, { ...HERMES, content: `${HERMES.content} It takes a minute.` }] })
    expectOpen()
  })

  it('ignores quiet rows after the card: tool lines and an empty streaming placeholder', async () => {
    renderSurface({
      messages: [...ASKED, { role: 'tool', cls: '', content: '🔧 wait' } as ChatMessage, { role: 'streaming', cls: '', content: '' } as ChatMessage],
    })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    expectOpen()
  })

  it('opens in full again when the next card becomes the newest thing', async () => {
    serveCards([
      card({ id: 'sc-a', kind: 'profile', payload: { fields: { role: 'SRE' } } }),
      card({ id: 'sc-b', kind: 'profile', payload: { fields: { role: 'PM' } } }),
    ])
    const { update } = renderSurface({ messages: [...ASKED, HERMES] })
    await screen.findByTestId('setup-card-tray')
    await waitFor(() => expect(screen.getByTestId('setup-card-tray-bar')).toHaveTextContent('2 cards'))
    expectFolded()
    update({ messages: [...ASKED, HERMES, cardRow('sc-b')] })
    expectOpen()
  })
})

describe('the tray folds while the user scrolls up to read', () => {
  it('folds on an upward scroll and opens again once they scroll back to the bottom', async () => {
    const { update } = renderSurface({ messages: ASKED })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    fireEvent.wheel(screen.getByTestId('scroller'), { deltaY: -120 })
    expectFolded()
    // Folding moves the transcript's bottom edge; that alone must not reopen it.
    update({ messages: ASKED, atBottom: true })
    expectFolded()
    update({ messages: ASKED, atBottom: false })
    fireEvent.wheel(screen.getByTestId('scroller'), { deltaY: 120 })
    expectFolded()
    update({ messages: ASKED, atBottom: true })
    expectOpen()
  })

  it('never folds under a focused field', async () => {
    const { update } = renderSurface({ messages: ASKED })
    const full = await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    act(() => within(full).getByTestId('setup-card-primary').focus())
    update({ messages: [...ASKED, HERMES] })
    expectOpen()
    act(() => (document.activeElement as HTMLElement).blur())
    expectFolded()
  })
})

describe('the bar points at the card’s own row', () => {
  it('asks the host to scroll to the row, smoothly unless motion is reduced', async () => {
    const onLocate = vi.fn()
    renderSurface({ messages: [...ASKED, HERMES], onLocate })
    const locate = await screen.findByTestId('setup-card-tray-locate')
    expect(locate).toHaveAccessibleName('Find “Your profile” in the chat')
    fireEvent.click(locate)
    expect(onLocate).toHaveBeenCalledWith('sc-a', expect.stringMatching(/^(smooth|auto)$/))
  })

  it('lights the located row up, then lets it go', () => {
    vi.useFakeTimers()
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter><SetupCardRow cardId="sc-a" placement="transcript" /></MemoryRouter>
        </QueryClientProvider>,
      )
      expect(document.querySelector('[data-setup-card-row="sc-a"]')).not.toBeNull()
      expect(screen.queryByTestId('setup-card-row-highlight')).toBeNull()
      act(() => highlightSetupCardRow('sc-a'))
      expect(screen.getByTestId('setup-card-row-highlight')).toHaveClass('animate-msg-highlight')
      act(() => { vi.advanceTimersByTime(2100) })
      expect(screen.queryByTestId('setup-card-row-highlight')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the tray is capped at a third of its pane', () => {
  it('caps its height from the measured pane', async () => {
    renderSurface({ messages: ASKED, paneHeight: 900 })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    expect(tray()).toHaveStyle({ maxHeight: '300px' })
    expect(tray()).toHaveClass('overflow-y-auto')
  })

  it('falls back to a third of the viewport where there is no pane to measure', async () => {
    renderSurface({ messages: ASKED })
    await within(await screen.findByTestId('setup-card-tray')).findByTestId('setup-card')
    expect(tray()).toHaveClass('max-h-[33dvh]')
    expect(tray().style.maxHeight).toBe('')
  })
})

describe('setupCardAtTail', () => {
  it('names the card when nothing a reader stops at follows it', () => {
    expect(setupCardAtTail(ASKED)).toBe('sc-a')
    expect(setupCardAtTail([...ASKED, { role: 'thinking', cls: '', content: 'hmm' } as ChatMessage])).toBe('sc-a')
    expect(setupCardAtTail([...ASKED, cardRow('sc-b')])).toBe('sc-b')
  })

  it('is null once the agent, the user or a notice says something after it', () => {
    expect(setupCardAtTail([...ASKED, HERMES])).toBeNull()
    expect(setupCardAtTail([...ASKED, { role: 'user', cls: '', content: 'ok' } as ChatMessage])).toBeNull()
    expect(setupCardAtTail([...ASKED, { role: 'notice', cls: '', content: 'Paused' } as ChatMessage])).toBeNull()
    expect(setupCardAtTail([])).toBeNull()
  })
})
