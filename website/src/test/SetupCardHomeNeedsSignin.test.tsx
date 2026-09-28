/**
 * The home card after a build that finished without the home's own Kiro sign-in
 * (setup_flow._home_built: phase `signin`, `outcome.needs_signin`): no Move in,
 * because the home's agent cannot answer yet; "Sign the home in to Kiro" is the
 * card's commit. Also the copy of the refusals that path and the build can meet.
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

const HASH = 'f'.repeat(64)
const PAYLOAD = {
  provider: { id: 'aws_ec2', label: 'Your AWS account' },
  simulated: false, region: 'eu-west-1', profile: 'default',
  size: { key: 'light', label: 'Light', instance_type: 't3.small', ram_gb: 2, vcpu: 2 },
  monthly_usd: 17, billed_by: 'AWS, to your own account', aws_signed_in: true, aws_account: '…1234',
  sign_in_commands: ['aws login'],
}
const STEPS = [
  { key: 'preflight', label: 'Check your AWS account', state: 'done', detail: '' },
  { key: 'provision', label: 'Create the server', state: 'done', detail: '' },
  { key: 'signin', label: 'Sign in to Kiro', state: 'skipped', detail: 'The sign-in code expired.' },
  { key: 'connect', label: 'Connect it', state: 'done', detail: '' },
]

function home(over: Partial<Card> = {}): Card {
  return {
    id: 'sc-needs0123456789a', slot: 'chat-1-1790000000', kind: 'home', status: 'pending', stakes: 'high',
    hash: HASH, payload: PAYLOAD,
    outcome: { job_id: 'job-1', status: 'done', steps: STEPS, error: '', ready: false, needs_signin: true },
    error: null, created_ts: 1790000000, decided_ts: null, classic: { kind: 'route', target: '/settings' },
    ...over,
  }
}

function serve(card: Card) {
  const gw = { bodies: [] as SetupDecideBody[] }
  server.use(
    http.get(`/api/setup/cards/${card.id}`, () => HttpResponse.json(card)),
    http.post(`/api/setup/cards/${card.id}/decide`, async ({ request }) => {
      gw.bodies.push((await request.json()) as SetupDecideBody)
      return HttpResponse.json({ ...card, status: 'waiting', outcome: { ...card.outcome, needs_signin: undefined } })
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

describe('SetupCard — home: built, but not signed in to Kiro', () => {
  it('says why there is no Move in, shows the steps, and offers the sign-in', async () => {
    const gw = serve(home())
    await renderHome(home())
    expect(screen.getByTestId('setup-card-home-needs-signin')).toHaveTextContent(
      'Your home is built, but it isn’t signed in to Kiro yet, so its agent can’t answer. Sign it in, then move in.',
    )
    const steps = screen.getByTestId('setup-card-steps')
    expect(steps).toHaveTextContent('The sign-in code expired.')
    expect(screen.queryByText('Move in')).toBeNull()
    const primary = screen.getByTestId('setup-card-primary')
    expect(primary).toHaveTextContent('Sign the home in to Kiro')
    await userEvent.click(primary)
    await waitFor(() => expect(gw.bodies).toEqual([{ decision: 'commit', hash: HASH }]))
  })

  it('a refused sign-in is shown in the owner’s words, and the button stays', async () => {
    const refused = home({ error: { code: 'login_target_unreadable', message: 'cannot read' } })
    serve(refused)
    await renderHome(refused)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'This version of Kiro Crew can’t read this home’s Kiro identity, so signing it in here would use the wrong account.',
    )
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Sign the home in to Kiro')
  })

  it('a home whose build is unknown here says so', async () => {
    const unknown = home({ error: { code: 'launch_job_not_found', message: 'not found' } })
    serve(unknown)
    await renderHome(unknown)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'This home can’t be signed in from this card; this computer doesn’t know its build.',
    )
  })

  it('an un-regioned Identity Center sign-in is refused before the build, in plain words', async () => {
    const build = home({
      outcome: null,
      error: { code: 'home_identity_region_unknown', message: 'region unknown' },
    })
    serve(build)
    await renderHome(build)
    expect(screen.getByTestId('setup-card-error')).toHaveTextContent(
      'This computer signs in to Kiro through IAM Identity Center, but its region could not be read. Sign in again with kiro-cli, then build again.',
    )
    expect(screen.getByTestId('setup-card-primary')).toHaveTextContent('Build my home')
  })
})
