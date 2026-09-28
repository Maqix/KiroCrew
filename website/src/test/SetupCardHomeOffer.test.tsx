/**
 * The first run's "Where should your crew live?" step: the home card the gateway
 * shows on its own right after privacy (`payload.offer`). While it is still the
 * question it asks it, its decline says "Keep it on this machine", and a declined
 * step reads "Staying on this machine". Without `offer` (a `--home cloud` card, an
 * agent's proposal) and once a build starts, the card is the home as before.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { server } from '../../integration/mocks/server'
import SetupCard from '../components/setup/SetupCard'
import type { SetupCard as Card, SetupDecideBody } from '../api/setupCards'

const HASH = '1'.repeat(64)
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't3.small', ram_gb: 2, vcpu: 2 },
  monthly_usd: 17, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: ['aws login'],
}
const LEAD = 'It can stay on this machine, or live in a home in the cloud in your AWS account that keeps jobs running when this machine sleeps.'

function home(over: Partial<Card> = {}, payload: Record<string, unknown> = {}): Card {
  return {
    id: 'sc-offer0123456789a', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: { ...PAYLOAD, ...payload }, outcome: null, error: null, created_ts: 1790000000,
    decided_ts: null, classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

function serve(card: Card, after?: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json(after ?? card)
    }),
  )
  return gw
}

function renderHome(card: Card) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat']}>
        <SetupCard cardId={card.id} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return screen.findByTestId('setup-card')
}

describe('SetupCard — home: the "Where should your crew live?" step', () => {
  it('asks the question, keeps the cost and the build, and declines as "Keep it on this machine"', async () => {
    const offer = home({}, { offer: true })
    const gw = serve(offer, { ...offer, status: 'declined', decided_ts: 1790000100 })
    const el = await renderHome(offer)
    expect(within(el).getByRole('heading')).toHaveTextContent('Where should your crew live?')
    expect(screen.getByTestId('setup-card-home-offer')).toHaveTextContent(LEAD)
    expect(screen.getByTestId('setup-card-home-cost')).toHaveTextContent('About $17/month')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
    const decline = screen.getByTestId('setup-card-decline')
    expect(decline).toHaveTextContent('Keep it on this machine')
    await userEvent.click(decline)
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'decline', hash: HASH }]))
    // Declined, it says where the crew stays.
    const result = await screen.findByTestId('setup-card-result')
    expect(result).toHaveTextContent('Where should your crew live?')
    expect(result).toHaveTextContent('Staying on this machine')
    expect(result).not.toHaveTextContent('Skipped')
  })

  it('keeps the sign-in and sign-up paths on a machine not signed in to AWS', async () => {
    const offer = home({}, {
      offer: true, aws_signed_in: false, aws_account: '',
      signup_url: 'https://signin.aws.amazon.com/signup?request_type=register', signup_builder_id: false,
      aws_cli_installed: true,
    })
    serve(offer)
    await renderHome(offer)
    expect(screen.getByTestId('setup-card-home-offer')).toHaveTextContent(LEAD)
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign in to AWS')
    expect(screen.getByTestId('setup-card-home-aws-signup')).toHaveTextContent('Create an AWS account')
    expect(screen.getByTestId('setup-card-decline')).toHaveTextContent('Keep it on this machine')
  })

  it('without `offer` the card is the home as before', async () => {
    const plain = home()
    serve(plain, { ...plain, status: 'declined', decided_ts: 1790000100 })
    const el = await renderHome(plain)
    expect(within(el).getByRole('heading')).toHaveTextContent('Your home in the cloud')
    expect(screen.queryByTestId('setup-card-home-offer')).toBeNull()
    const decline = screen.getByTestId('setup-card-decline')
    expect(decline).toHaveTextContent('Not now')
    await userEvent.click(decline)
    expect(await screen.findByTestId('setup-card-result')).toHaveTextContent('Skipped')
  })

  it('once the build starts, the step is the home itself', async () => {
    const building = home(
      { status: 'waiting', outcome: { job_id: 'job-1', status: 'running', steps: [], error: '' } },
      { offer: true },
    )
    serve(building)
    const el = await renderHome(building)
    expect(within(el).getByRole('heading')).toHaveTextContent('Your home in the cloud')
    expect(screen.queryByTestId('setup-card-home-offer')).toBeNull()
  })
})
