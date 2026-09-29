/**
 * The home card's region picker (setup_flow._home_payload's `region_unknown` and
 * `region_choices`, answered by the `region` decision): shown when no region
 * answered for the signed-in account, preselecting the card's region, and
 * posting the owner's pick. After a pick AWS did not answer either, the owner may
 * still build in it.
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

const HASH = '3'.repeat(64)
const LITE = {
  key: 'lite', label: 'Lite', note: 'lite_tradeoffs', instance_type: 't4g.small', vcpu: 2, ram_gb: 2,
  monthly_usd: 14, free_plan_ok: true,
}
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't4g.xlarge', ram_gb: 16, vcpu: 4 },
  monthly_usd: 101, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: ['aws login'],
  size_options: [LITE], size_default: 'lite', plan: { type: 'PAID' },
}
const CHOICES = ['us-east-1', 'eu-west-1', 'eu-north-1', 'bad region']

function home(payload: Record<string, unknown>, over: Partial<Card> = {}): Card {
  return {
    id: 'sc-region0123456789a', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: { ...PAYLOAD, ...payload }, outcome: null, error: null, created_ts: 1790000000,
    decided_ts: null, classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

const UNKNOWN = home({ region_unknown: true, region_choices: CHOICES })

function serve(card: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json(card)
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

describe('SetupCard — home: the region picker', () => {
  it('asks in plain words, preselects the card’s region and posts the pick', async () => {
    const gw = serve(UNKNOWN)
    await renderHome(UNKNOWN)
    const pick = screen.getByTestId('setup-card-home-region-pick')
    expect(pick).toHaveTextContent('AWS didn’t say which region this account uses. Pick the one shown in your AWS console.')
    const select = screen.getByRole('combobox', { name: 'AWS didn’t say which region this account uses. Pick the one shown in your AWS console.' })
    expect(select).toHaveValue('eu-west-1')
    // Only real region codes are offered.
    expect([...select.querySelectorAll('option')].map(o => o.value)).toEqual(['us-east-1', 'eu-west-1', 'eu-north-1'])
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Use this region')
    await userEvent.selectOptions(select, 'eu-north-1')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'region', hash: HASH, input: { region: 'eu-north-1' } }]))
  })

  it('after a pick AWS did not answer either, builds there, or asks again for another', async () => {
    const card = home(
      { region: 'eu-north-1', region_unknown: true, region_choices: CHOICES },
      { error: { code: 'home_region_no_answer', message: 'AWS did not answer' } },
    )
    const gw = serve(card)
    await renderHome(card)
    expect(screen.getByTestId('setup-card')).toHaveTextContent(
      'AWS didn’t answer in that region either. If it is the region your AWS console shows, build there; otherwise pick another.',
    )
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
    await userEvent.selectOptions(screen.getByTestId('setup-card-home-region-select'), 'us-east-1')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Use this region')
    await userEvent.selectOptions(screen.getByTestId('setup-card-home-region-select'), 'eu-north-1')
    await userEvent.click(screen.getByTestId('setup-card-primary'))
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH, input: { size: 'lite' } }]))
  })

  it('a region that answered shows no picker', async () => {
    const card = home({})
    serve(card)
    await renderHome(card)
    expect(screen.queryByTestId('setup-card-home-region-pick')).toBeNull()
    expect(screen.getByTestId('setup-card')).toHaveTextContent('eu-west-1')
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
  })

  it('a region the card did not offer is refused in plain words', async () => {
    const card = home(
      { region_unknown: true, region_choices: CHOICES },
      { error: { code: 'home_region_not_offered', message: 'choose one of the regions on the card' } },
    )
    serve(card)
    await renderHome(card)
    expect(screen.getByTestId('setup-card')).toHaveTextContent('Choose one of the regions on this card.')
  })
})
