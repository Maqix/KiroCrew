/**
 * A failed home card whose build a gateway restart cut short: its words say parts
 * of the build may still be in AWS and billing (from `outcome.leftover`, or an
 * untracked build with `stopped: false`), and its "Remove what it created" posts
 * the `remove` decision and shows the removal through to "Removed".
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = '4'.repeat(64)
const LEFTOVER = { tag: 'kc-home-7f3a', stack: 'kirocrew-kc-home-7f3a', region: 'eu-north-1' }
const LEAD = 'Kiro Crew lost track of this home’s build when it restarted, so parts of it may still be in your AWS account and billing.'

function failed(outcome: Record<string, unknown>, error: { code: string; message: string }): Card {
  return {
    id: 'sc-leftover0123456789', slot: 'chat-1-1790000000', kind: 'home', status: 'failed', stakes: 'high',
    hash: HASH, payload: { simulated: false, region: 'eu-north-1' }, outcome, error, created_ts: 1790000000,
    decided_ts: 1790000100, classic: { kind: 'route', target: '/settings' },
  }
}

const INTERRUPTED = { code: 'home_build_failed', message: 'Interrupted — Kiro Crew restarted while this setup was running.' }
const UNTRACKED = { code: 'home_build_untracked', message: 'the home’s build could not be followed any more' }

function serve(card: Card, answer?: () => Response) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return answer ? answer() : HttpResponse.json({ ...card, outcome: { ...card.outcome, removal: { state: 'active' } } })
    }),
  )
  return gw
}

function renderCard(card: Card) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <SetupCard cardId={card.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return screen.findByTestId('setup-card-result')
}

describe('SetupCard — home: what a restart left in AWS', () => {
  it('a build stopped in this process keeps its own words and offers nothing to remove', async () => {
    const card = failed({ job_id: 'job-1', stopped: true }, UNTRACKED)
    serve(card)
    await renderCard(card)
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent(
      'Kiro Crew lost track of this home’s build, so it stopped it; anything it had created in AWS is being removed. You can build again.',
    )
    expect(screen.queryByTestId('setup-card-home-remove')).toBeNull()
  })

  it('an untracked build nothing stopped says where to remove it', async () => {
    const card = failed({ job_id: 'job-1', stopped: false }, UNTRACKED)
    serve(card)
    await renderCard(card)
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent(`${LEAD} Remove them from Remote Crew.`)
    expect(screen.queryByTestId('setup-card-home-remove-button')).toBeNull()
  })

  it('a build cut short names the stack and removes it on the owner’s click', async () => {
    const card = failed({ job_id: 'job-1', leftover: LEFTOVER }, INTERRUPTED)
    const gw = serve(card)
    await renderCard(card)
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent(`${LEAD} Remove them here, or from Remote Crew.`)
    expect(screen.getByTestId('setup-card-home-remove')).toHaveTextContent(
      'This deletes the stack kirocrew-kc-home-7f3a in eu-north-1 and everything in it.',
    )
    // Nothing is sent until the click.
    expect(gw.bodies).toEqual([])
    await userEvent.click(screen.getByTestId('setup-card-home-remove-button'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'remove', hash: HASH, input: { tag: 'kc-home-7f3a' } }]))
    // The decided card stays its result line, now showing the removal running.
    expect(await screen.findByTestId('setup-card-home-removing')).toHaveTextContent('Removing what it created…')
    expect(screen.getByTestId('setup-card-result')).toBeInTheDocument()
  })

  it('a finished removal says the stack is gone, and a failed one offers it again', async () => {
    const done = failed({ job_id: 'job-1', leftover: LEFTOVER, removal: { state: 'done' } }, INTERRUPTED)
    serve(done)
    await renderCard(done)
    expect(screen.getByTestId('setup-card-home-removed')).toHaveTextContent(
      'Removed: AWS confirms the stack kirocrew-kc-home-7f3a is gone.',
    )
    expect(screen.queryByTestId('setup-card-failed-error')).toBeNull()
    expect(screen.queryByTestId('setup-card-home-remove-button')).toBeNull()
  })

  it('a removal that did not finish says so and keeps the button', async () => {
    const card = failed({ job_id: 'job-1', leftover: LEFTOVER, removal: { state: 'failed' } }, INTERRUPTED)
    serve(card)
    await renderCard(card)
    expect(screen.getByTestId('setup-card-home-remove-failed')).toHaveTextContent(
      'The removal did not finish. Sign in to AWS again if it has expired, then try again, or remove it from Remote Crew.',
    )
    expect(screen.getByTestId('setup-card-home-remove-button')).toBeEnabled()
  })

  it('a refused click says why in plain words', async () => {
    const card = failed({ job_id: 'job-1', leftover: LEFTOVER }, INTERRUPTED)
    serve(card, () => HttpResponse.json({ error: 'its removal is already running or done', code: 'home_remove_running' }, { status: 409 }))
    await renderCard(card)
    await userEvent.click(screen.getByTestId('setup-card-home-remove-button'))
    expect(await screen.findByTestId('setup-card-home-remove-error')).toHaveTextContent('Its removal is already running or done.')
  })

  it('a leftover that does not name its own stack offers nothing to remove', async () => {
    const card = failed({ job_id: 'job-1', leftover: { ...LEFTOVER, stack: 'kirocrew-someone-else' } }, INTERRUPTED)
    serve(card)
    await renderCard(card)
    expect(screen.queryByTestId('setup-card-home-remove')).toBeNull()
    expect(screen.getByTestId('setup-card-failed-error')).toHaveTextContent('Interrupted — Kiro Crew restarted while this setup was running.')
  })
})
