/**
 * SetupCard — the channel card (Telegram first): a bot token typed into the
 * card, then a `/pair <code>` message from the owner's own account.
 *
 * Driven through MSW at the network boundary, like `SetupCard.test.tsx`:
 *
 *   - the token is posted as `input.token`, cleared from the field in the same
 *     tick, and never rendered or cached;
 *   - while waiting, the card shows the one-time pairing line the gateway
 *     returned, or says the code is gone when the gateway no longer holds one;
 *   - a refused token reads as a sentence the owner can act on;
 *   - a paired card names the account it paired, and a closed pairing fails
 *     through the error surface.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = 'b'.repeat(64)
const TOKEN = '110201543:AAHsentinelTokenValue5f1c9e2b'

function channelCard(over: Partial<Card> = {}): Card {
  return {
    id: 'sc-00000000000000c1',
    slot: 'chat-1-1790000000',
    kind: 'channel',
    status: 'pending',
    stakes: 'high',
    hash: HASH,
    payload: { channel: 'telegram', label: 'Telegram' },
    outcome: null,
    error: null,
    created_ts: 1790000000,
    decided_ts: null,
    classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

function serveCard(initial: Card, decide?: (body: SetupDecideBody, current: Card) => Card | Response) {
  const state = { card: initial, bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${initial.id}`, () => HttpResponse.json(state.card)),
    http.post(`/api/setup/cards/${initial.id}/decide`, async ({ request }) => {
      const body = (await request.json()) as SetupDecideBody
      state.bodies.push(body)
      const next = decide ? decide(body, state.card) : state.card
      if (next instanceof Response) return next
      state.card = next
      return HttpResponse.json(next)
    }),
  )
  return state
}

function renderCard(id = 'sc-00000000000000c1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <SetupCard cardId={id} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { qc, ...utils }
}

const ready = () => screen.findByTestId('setup-card')

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SetupCard — channel: the bot token', () => {
  it('says where the token goes without promising the vault', async () => {
    serveCard(channelCard())
    renderCard()
    await ready()
    expect(screen.getByText('Paste your Telegram bot’s token. It is stored where the agent can’t read it and never appears in the chat.')).toBeInTheDocument()
    expect(screen.getByText('Bot token')).toBeInTheDocument()
  })

  it('posts the token once, clears it, keeps it out of the DOM and every cache, then shows the pairing line', async () => {
    const gw = serveCard(channelCard(), (_body, current) => ({
      ...current,
      status: 'waiting',
      outcome: { channel: 'telegram', expires_ts: 1790000600, pair_code: '0482' },
    }))
    const { qc, container } = renderCard()
    await ready()
    const field = screen.getByTestId('setup-card-secret') as HTMLInputElement
    expect(field.type).toBe('password')
    expect(screen.getByTestId('setup-card-primary')).toBeDisabled()
    await userEvent.type(field, TOKEN)
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    expect(field.value).toBe('')
    await waitFor(() => expect(gw.bodies).toHaveLength(1))
    expect(gw.bodies[0]).toEqual({ decision: 'commit', hash: HASH, input: { token: TOKEN } })

    // A leading zero is part of the code, not formatting.
    expect(await screen.findByTestId('setup-card-pair')).toHaveTextContent('/pair 0482')
    expect(screen.getByText('To finish pairing, send the message below to your bot.')).toBeInTheDocument()
    expect(screen.getByText('The code works once, and only for the next few minutes.')).toBeInTheDocument()
    expect(screen.queryByTestId('setup-card-pair-lost')).toBeNull()

    expect(container.innerHTML).not.toContain(TOKEN)
    const queryData = JSON.stringify(qc.getQueryCache().getAll().map(q => q.state.data))
    const mutationVars = JSON.stringify(qc.getMutationCache().getAll().map(m => m.state.variables))
    expect(queryData).not.toContain(TOKEN)
    expect(mutationVars).not.toContain(TOKEN)
  })

  it.each([
    ['channel_token_empty', 'Paste the bot token first.'],
    ['channel_token_invalid', 'That doesn’t look like a bot token. Copy the whole token, including the numbers before the colon.'],
    ['channel_token_rejected', 'The token wasn’t accepted. Check it and paste it again.'],
  ])('a refused token (%s) reads as a sentence and leaves the card pending', async (code, sentence) => {
    serveCard(channelCard(), () => HttpResponse.json({ error: 'refused', code }, { status: 422 }) as unknown as Response)
    const { container } = renderCard()
    await ready()
    await userEvent.type(screen.getByTestId('setup-card-secret'), TOKEN)
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    expect(await screen.findByTestId('setup-card-error')).toHaveTextContent(sentence)
    expect(screen.getByTestId('setup-card')).toHaveAttribute('data-status', 'pending')
    expect(container.innerHTML).not.toContain(TOKEN)
  })
})

describe('SetupCard — channel: waiting for /pair', () => {
  it('names the bot when the gateway knows it', async () => {
    serveCard(channelCard({ status: 'waiting', outcome: { pair_code: '4821', bot_username: 'my_bot' } }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-pair')).toHaveTextContent('/pair 4821')
    expect(screen.getByText('To finish pairing, send the message below to @my_bot.')).toBeInTheDocument()
  })

  it('says the code is gone when the gateway no longer holds one', async () => {
    serveCard(channelCard({ status: 'waiting', outcome: { channel: 'telegram', expires_ts: 1790000600 } }))
    renderCard()
    const el = await ready()
    expect(within(el).getByTestId('setup-card-pair-lost')).toHaveTextContent('This pairing code is no longer valid. Ask in the chat for a new one.')
    expect(within(el).queryByTestId('setup-card-pair')).toBeNull()
    expect(within(el).queryByTestId('setup-card-secret')).toBeNull()
  })
})

describe('SetupCard — channel: settled', () => {
  it('a paired card names the account it paired', async () => {
    serveCard(channelCard({ status: 'committed', outcome: { channel: 'telegram', paired: true, username: '@owner' }, decided_ts: 1790000100 }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-result')).toHaveTextContent('Connect Telegram')
    expect(screen.getByTestId('setup-card-result-detail')).toHaveTextContent('Paired with @owner')
  })

  it('an account without a handle pairs just the same', async () => {
    serveCard(channelCard({ status: 'committed', outcome: { channel: 'telegram', paired: true, username: '' }, decided_ts: 1790000100 }))
    renderCard()
    await ready()
    expect(screen.getByTestId('setup-card-result-detail')).toHaveTextContent('Paired with your account')
  })

  it('a pairing closed by wrong codes fails through the error surface', async () => {
    serveCard(channelCard({
      status: 'failed',
      error: { code: 'pair_attempts', message: 'Too many wrong pairing codes were sent to the bot.' },
      decided_ts: 1790000100,
    }))
    renderCard()
    const el = await ready()
    expect(within(el).getByTestId('setup-card-failed-error')).toHaveTextContent('Too many wrong codes were sent to the bot, so pairing closed. Ask in the chat to try again.')
  })
})
